# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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
