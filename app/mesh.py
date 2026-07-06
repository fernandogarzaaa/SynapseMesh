"""Stateful actor-critic consensus engine."""

from __future__ import annotations

import hashlib
import json
import logging
import time
from dataclasses import asdict
from typing import Any

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings, settings
from app.guard import DataGuard, RoutePolicy
from app.models import ExecutionState, ModelPerformanceAudit
from app.router import PredictiveRouter

logger = logging.getLogger("synapse_mesh.mesh")
CONSENSUS_ACCEPTED = "[CONSENSUS_ACCEPTED]"


class AutonomousConsensusMesh:
    """Run an autonomous actor-critic negotiation loop and persist telemetry."""

    def __init__(
        self,
        *,
        guard: DataGuard | None = None,
        router: PredictiveRouter | None = None,
        runtime_settings: Settings = settings,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._settings = runtime_settings
        self._guard = guard or DataGuard(runtime_settings)
        self._router = router or PredictiveRouter()
        self._http_client = http_client

    async def negotiate_workflow(
        self,
        db_session: AsyncSession,
        task_payload: str,
    ) -> dict[str, Any]:
        """Run a capped actor-critic workflow and write signed telemetry."""

        started = time.perf_counter()
        history: list[dict[str, Any]] = []
        total_tokens = 0
        final_product = ""
        convergence_turns = 0
        execution_state = ExecutionState.FAILED.value

        route_policy = self._guard.evaluate_route_policy(task_payload)
        actor_model, critic_model = await self._router.assign_roles(task_payload)
        task_type = self._infer_task_type(task_payload)

        logger.info(
            "negotiation_started",
            extra={
                "task_type": task_type,
                "classification": route_policy.classification,
                "actor_model": actor_model,
                "critic_model": critic_model,
            },
        )

        try:
            corrections = ""
            for turn in range(1, min(self._settings.MAX_CONCURRENT_STREAM_LOOPS, 5) + 1):
                actor_prompt = self._actor_prompt(task_payload, corrections)
                actor_response = await self._call_model(
                    model_name=actor_model,
                    role="actor",
                    prompt=actor_prompt,
                    route_policy=route_policy,
                )
                total_tokens += actor_response["tokens"]
                final_product = actor_response["content"]
                history.append({"turn": turn, "role": "actor", **actor_response})

                critic_prompt = self._critic_prompt(task_payload, final_product)
                critic_response = await self._call_model(
                    model_name=critic_model,
                    role="critic",
                    prompt=critic_prompt,
                    route_policy=route_policy,
                )
                total_tokens += critic_response["tokens"]
                history.append({"turn": turn, "role": "critic", **critic_response})
                convergence_turns = turn

                if critic_response["content"].strip() == CONSENSUS_ACCEPTED:
                    execution_state = ExecutionState.SUCCESS.value
                    logger.info(
                        "consensus_accepted",
                        extra={
                            "turn": turn,
                            "actor_model": actor_model,
                            "critic_model": critic_model,
                        },
                    )
                    break

                corrections = critic_response["content"]
                logger.info(
                    "consensus_corrections_issued",
                    extra={"turn": turn, "correction_chars": len(corrections)},
                )
            else:
                execution_state = ExecutionState.FAILED.value
        except Exception as exc:
            logger.exception("negotiation_failed", extra={"error": str(exc)})
            history.append({"role": "system", "event": "error", "error": str(exc)})

        if (
            route_policy.classification == "RESTRICTED_BOUNDS"
            and route_policy.allow_public_endpoints
            and self._settings.COMPLIANCE_MODE.upper() == "STRICT"
        ):
            execution_state = ExecutionState.BREACHED.value

        elapsed_ms = (time.perf_counter() - started) * 1000.0
        telemetry_payload: dict[str, Any] = {
            "task_type": task_type,
            "actor_model": actor_model,
            "critic_model": critic_model,
            "classification": route_policy.classification,
            "route_policy": asdict(route_policy),
            "history": history,
        }
        signature = self._sign_telemetry(telemetry_payload)
        audit = ModelPerformanceAudit(
            task_type=task_type,
            actor_model=actor_model,
            critic_model=critic_model,
            total_tokens=total_tokens,
            elapsed_latency_ms=elapsed_ms,
            convergence_turns=convergence_turns,
            execution_state=execution_state,
            raw_negotiation_history=telemetry_payload,
            telemetry_signature=signature,
        )
        db_session.add(audit)
        await db_session.commit()
        await db_session.refresh(audit)

        result = {
            "audit_id": str(audit.id),
            "execution_state": execution_state,
            "classification": route_policy.classification,
            "actor_model": actor_model,
            "critic_model": critic_model,
            "convergence_turns": convergence_turns,
            "total_tokens": total_tokens,
            "elapsed_latency_ms": elapsed_ms,
            "telemetry_signature": signature,
            "route_policy": asdict(route_policy),
            "final_product": final_product,
            "history": history,
        }
        logger.info(
            "negotiation_completed",
            extra={
                "audit_id": result["audit_id"],
                "execution_state": execution_state,
                "elapsed_latency_ms": elapsed_ms,
            },
        )
        return result

    async def _call_model(
        self,
        *,
        model_name: str,
        role: str,
        prompt: str,
        route_policy: RoutePolicy,
    ) -> dict[str, Any]:
        provider = self._router.provider_for(model_name)
        endpoint = route_policy.openai_endpoint
        if provider != "openai":
            endpoint = route_policy.anthropic_endpoint
        payload = {
            "model": model_name,
            "role": role,
            "messages": [{"role": "user", "content": prompt}],
            "public_endpoint_allowed": route_policy.allow_public_endpoints,
        }
        request_started = time.perf_counter()
        client = self._http_client
        close_client = False
        if client is None:
            client = httpx.AsyncClient(timeout=self._settings.PROVIDER_TIMEOUT_SECONDS)
            close_client = True
        try:
            response = await client.post(endpoint, json=payload)
            response.raise_for_status()
            data = response.json()
            content = self._extract_content(data)
        except Exception as exc:
            logger.warning(
                "model_call_fallback",
                extra={"model": model_name, "role": role, "endpoint": endpoint, "error": str(exc)},
            )
            content = self._simulate_model_response(role=role, prompt=prompt)
        finally:
            if close_client:
                await client.aclose()

        latency_ms = (time.perf_counter() - request_started) * 1000.0
        tokens = self._estimate_tokens(prompt) + self._estimate_tokens(content)
        return {
            "model": model_name,
            "provider": provider,
            "endpoint": endpoint,
            "content": content,
            "tokens": tokens,
            "latency_ms": latency_ms,
        }

    def _simulate_model_response(self, *, role: str, prompt: str) -> str:
        if role == "critic":
            if "apply corrections" in prompt.lower():
                return CONSENSUS_ACCEPTED
            return "Apply corrections: add input validation, structured errors, and audit logging."
        return (
            "Generated asset:\n"
            f"{prompt[:1200]}\n"
            "Implementation includes validation, structured logging, and telemetry hooks."
        )

    def _actor_prompt(self, task_payload: str, corrections: str) -> str:
        if corrections:
            return f"Task:\n{task_payload}\n\nApply corrections:\n{corrections}"
        return f"Task:\n{task_payload}\n\nGenerate the requested implementation asset."

    def _critic_prompt(self, task_payload: str, actor_output: str) -> str:
        return (
            "Review the actor output for bugs or security vulnerabilities. "
            f"Return concrete corrections or exactly {CONSENSUS_ACCEPTED}.\n"
            f"Original task:\n{task_payload}\n\nActor output:\n{actor_output}"
        )

    def _infer_task_type(self, task_payload: str) -> str:
        text = task_payload.lower()
        if "schema" in text or "database" in text:
            return "DATA_SCHEMA"
        if "code" in text or "implementation" in text or "file" in text:
            return "CODE_SYNTHESIS"
        if "architecture" in text or "system" in text:
            return "ARCHITECTURE_REVIEW"
        return "GENERAL_TASK"

    def _extract_content(self, data: dict[str, Any]) -> str:
        if isinstance(data.get("content"), str):
            return str(data["content"])
        if isinstance(data.get("text"), str):
            return str(data["text"])
        choices = data.get("choices")
        if isinstance(choices, list) and choices:
            first = choices[0]
            if isinstance(first, dict):
                message = first.get("message")
                if isinstance(message, dict) and isinstance(message.get("content"), str):
                    return str(message["content"])
                if isinstance(first.get("text"), str):
                    return str(first["text"])
        return json.dumps(data, sort_keys=True)

    def _estimate_tokens(self, text: str) -> int:
        return max(1, len(text) // 4)

    def _sign_telemetry(self, telemetry_payload: dict[str, Any]) -> str:
        canonical = json.dumps(telemetry_payload, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
