---
name: docker-smoke-test
description: Use when building, starting, validating, or handing off the Legal Assistant Docker Compose environment on a new machine.
---

# Docker Smoke Test

Prove that a clean developer machine can build and run the documented local stack without hidden state, credentials, or destructive cleanup.

## Preconditions

Confirm Docker Engine/Compose availability and read the Compose file plus `.env.example`. Use fixture or explicitly approved local data. Environment examples may name variables but never contain secrets or production URLs.

## Smoke-test contract

Build and start the declared services; wait on health checks rather than fixed sleeps. Verify API health, API-to-Qdrant connectivity, persistent-volume mounting, and one non-destructive fixture-backed search/chat request. Exercise SSE only when it is part of the compose stack. Capture image tags, compose/config versions, service status, logs relevant to failures, ports, and commands used.

## Safety boundaries

Do not use production, private-registry, LLM, database, Sentry, or deployment credentials without explicit authorization. Do not crawl/ingest external data, apply migrations, publish images, or remove/recreate volumes or persisted databases without separate approval. Stopping containers is not permission to delete their volumes.

## Handoff output

Report prerequisites, exact reproducible commands, observed health/endpoints, unresolved configuration, and a non-destructive stop command. Keep destructive teardown as a separately labelled, approved procedure.

## Common mistakes

- Passing because a pre-existing local index masks a missing initialization step.
- Logging secrets on startup failures.
- Calling `down -v` in a routine smoke test.