# Project: grounded-rag-assistant
Read SPEC.md before any work. Implement only the milestone requested.

## Conventions
- Python 3.12, FastAPI, async SQLAlchemy 2, Pydantic v2. Type hints everywhere; mypy strict on app/.
- Ruff for lint+format. Tests with pytest; new logic needs tests.
- All tunables in app/config.py (pydantic-settings), never hardcoded.
- Prompts live in app/generation/prompts/*.md, never inline strings.
- Never log secrets or full document text. Log chunk ids, not content.
- ACL filter must be applied in SQL for every retrieval path. Add a test for any new path.
- Frontend: TypeScript strict, server components by default, shadcn/ui.

## Commands
- make up / make down / make test / make lint / make eval / make ingest-demo

## Definition of done
Tests pass, lint passes, milestone acceptance criteria in SPEC.md met, short summary of changes written.

## MVP scope
Follow the MVP cut in SPEC.md §14 (NOT the full milestone table in §12): upload + web connectors only, OpenAI + Anthropic + local embeddings, Keycloak with Alice/Bob demo, 30-question eval, Terraform for Azure only, structured logs instead of OpenTelemetry. Skip M7 and M8.
Do not implement anything marked "Later" in SPEC.md unless explicitly asked.

## Delegation
- Before building correctness-critical logic, consult the design-critic subagent.
- After finishing a milestone, run the milestone-reviewer subagent and fix every FAIL before summarising.

## Working style
- Implement ONLY the milestone named in the prompt. Stop when its acceptance criteria pass.
- Never weaken, skip or delete tests to make them pass. If a criterion seems wrong, stop and say so.
- End each milestone with: files changed, how to run/verify, anything left open.
