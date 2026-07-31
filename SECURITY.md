# Security Policy

## Supported Version

The latest commit on `main` is supported. This repository is an internal-style
portfolio system and is not intended to be exposed directly to the public
internet without authentication, network controls, and deployment-specific
hardening.

## Reporting

Report a potential vulnerability through GitHub private vulnerability
reporting when enabled, or contact the repository owner privately. Do not place
credentials, private data, or exploit payloads in a public issue.

## Current Controls

- Raw ASR and NLU sources are read-only.
- Agent tools do not expose confirmation, publish, or rollback actions.
- High-risk mutation requests are blocked before LLM planning.
- LLM telemetry excludes prompts, answers, API keys, and tokens.
- CI runs Ruff, Mypy, tests, dependency consistency, `pip-audit`, and a Docker
  image build.
- Dependabot monitors Python and GitHub Actions dependencies weekly.

## Temporary ChromaDB Advisory Exception

`pip-audit` currently reports `PYSEC-2026-311` for ChromaDB 1.5.9. The advisory
affects the unauthenticated Chroma HTTP server collection endpoint when an
attacker can submit a model repository with `trust_remote_code=true`.

This project only constructs embedded `chromadb.PersistentClient` instances in
the application process. It does not start or expose a Chroma server or that
HTTP endpoint. CI therefore temporarily ignores only `PYSEC-2026-311`; every
other known vulnerability remains blocking. Remove this exception as soon as a
fixed ChromaDB release is available.