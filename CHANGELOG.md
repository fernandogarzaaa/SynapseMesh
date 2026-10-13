# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.1.1] - 2026-10-13

### Security
- Optional API key: set `SYNAPSEMESH_API_KEY` to require `X-API-Key` /
  `Authorization: Bearer` on `/v1/bus/*` and `/v1/route/select` (constant-time
  compare). Previously anyone who could reach the port could publish events and
  claim, renew or release any task lock. Unset keeps the old open behaviour and
  logs a warning.
- `/v1/vla/commands` reads uploads in 1 MiB chunks and rejects anything over
  `VLA_MAX_UPLOAD_BYTES` (default 16 MiB) with 413, instead of buffering an
  unbounded body in memory.

### Fixed
- `/v1/vla/commands`: a non-numeric `drift_score` (`"abc"`, `null`, a list) or a
  non-finite one (`NaN`, `inf`) now returns 422 instead of a 500 / NaN motor
  commands; malformed JSON returns 400 instead of 500.

## [0.1.0] - 2026-10-13

First tagged release.

### Added
- Lease renewal: `DistributedAgentLocker.renew_task_lock()` (owner-checked Lua
  `PEXPIRE`) and `POST /v1/bus/renew`. Previously a task running longer than
  `LOCK_TTL_MS` silently lost its lock and a second agent could claim it.
- `POST /v1/bus/release`; `release_task_lock()` now returns whether the caller
  owned the lock. HTTP clients no longer have to wait for the TTL to expire.

### Added / Changed
- Dependabot config for uv and GitHub Actions (weekly)
- uv.lock for reproducible installs
- .env.example listing the settings read by app/config.py
- SECURITY.md with private reporting contact
