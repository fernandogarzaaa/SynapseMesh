"""Data classification and security guardrails."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

from app.config import Settings, settings

logger = logging.getLogger("synapse_mesh.guard")


@dataclass(frozen=True)
class RoutePolicy:
    """Provider endpoint policy after compliance evaluation."""

    classification: str
    allow_public_endpoints: bool
    openai_endpoint: str
    anthropic_endpoint: str
    reasons: tuple[str, ...]


class DataGuard:
    """Classify inbound task content and isolate restricted workloads."""

    restricted_patterns: tuple[tuple[str, re.Pattern[str]], ...] = (
        (
            "restricted_text",
            re.compile(r"\b(restricted|confidential|top secret|classified)\b", re.I),
        ),
        (
            "internal_financial_metrics",
            re.compile(
                r"\b(arr|mrr|ebitda|gross margin|runway|cash burn|internal forecast)\b",
                re.I,
            ),
        ),
        (
            "unauthorized_system_access",
            re.compile(
                r"\b(root shell|credential dump|exfiltrate|bypass auth|unauthorized)\b",
                re.I,
            ),
        ),
    )

    def __init__(self, runtime_settings: Settings = settings) -> None:
        self._settings = runtime_settings

    def classify(self, content: str) -> tuple[str, tuple[str, ...]]:
        """Return the classification label and matched reasons for content."""

        reasons = tuple(
            name for name, pattern in self.restricted_patterns if pattern.search(content)
        )
        classification = "RESTRICTED_BOUNDS" if reasons else "STANDARD_BOUNDS"
        logger.info(
            "payload_classified",
            extra={"classification": classification, "reasons": list(reasons)},
        )
        return classification, reasons

    def evaluate_route_policy(self, content: str) -> RoutePolicy:
        """Apply strict compliance routing policy for the content."""

        classification, reasons = self.classify(content)
        strict = self._settings.COMPLIANCE_MODE.upper() == "STRICT"
        restricted = classification == "RESTRICTED_BOUNDS"
        if restricted and strict:
            logger.warning(
                "restricted_payload_isolated",
                extra={"classification": classification, "reasons": list(reasons)},
            )
            return RoutePolicy(
                classification=classification,
                allow_public_endpoints=False,
                openai_endpoint=self._settings.INTERNAL_OPENAI_API_URL,
                anthropic_endpoint=self._settings.INTERNAL_ANTHROPIC_API_URL,
                reasons=reasons,
            )

        return RoutePolicy(
            classification=classification,
            allow_public_endpoints=True,
            openai_endpoint=self._settings.OPENAI_API_URL,
            anthropic_endpoint=self._settings.ANTHROPIC_API_URL,
            reasons=reasons,
        )
