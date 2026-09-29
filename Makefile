.PHONY: up down test lint fmt eval ingest-demo retrieve ask retrieval-smoke e2e generate-corpus eval-retrieval tune-threshold crawl docs

up:
	docker compose up -d --build

down:
	docker compose down

test:
	cd backend && uv run pytest -q
	cd frontend && pnpm test

lint:
	cd backend && uv run ruff format --check . ../eval && uv run ruff check . ../eval && uv run mypy
	cd frontend && pnpm lint && pnpm typecheck

fmt:
	cd backend && uv run ruff format . && uv run ruff check --fix .

# Ingest the demo corpus through the worker (needs `make up`). Writes results/ingestion/*.json
ingest-demo:
	docker compose run --rm api python -m app.cli ingest-demo --enqueue

# Full pipeline as a demo user (needs ANTHROPIC_API_KEY or OPENAI_API_KEY in your shell/.env)
ask:
	docker compose run --rm api python -m app.cli ask --as $(or $(AS_USER),alice) "$(Q)"

# Real-model retrieval smoke check (needs `make up` + `make ingest-demo`). Writes results/retrieval/smoke.json
retrieval-smoke:
	docker compose run --rm api python -m app.cli retrieval-smoke

# End-to-end browser test against the real stack, with the offline extractive test mode instead
# of an LLM (no API key needed). Starts the stack, ingests the demo corpus, runs Playwright.
e2e:
	LLM_PROVIDER=extractive docker compose up -d --build
	@for i in $$(seq 1 200); do \
		curl -sf http://localhost:8000/ready >/dev/null && curl -sf http://localhost:3000 >/dev/null && exit 0; \
		sleep 3; \
	done; echo "stack did not become ready in time" >&2; exit 1
	$(MAKE) ingest-demo
	cd frontend && pnpm exec playwright test

# Crawl a website into a collection (operator command; obeys robots.txt, refuses private
# addresses, stays on the seed host):  make crawl URL=https://docs.example.com/ COLLECTION=company
crawl:
	docker compose run --rm api python -m app.cli crawl "$(URL)" --collection "$(COLLECTION)"

# Try retrieval: make retrieve AS_USER=bob Q="salary band for L5 engineers"
retrieve:
	docker compose run --rm api python -m app.cli retrieve --as $(or $(AS_USER),alice) "$(Q)"

# Retrieval-only eval: real embedder + reranker, no LLM/API key. Writes eval/reports/*.md
eval-retrieval:
	cd backend && PYTHONPATH=.. uv run python -m eval.run_eval --retrieval-only

# Full eval with a real LLM (needs ANTHROPIC_API_KEY or OPENAI_API_KEY in the repo-root .env or
# your shell; COSTS MONEY). Prints an estimate and stops unless you add --yes:
#   make eval EVAL_ARGS=--yes
# Citation precision needs a judge: set EVAL_JUDGE_PROVIDER (else it shows "not run").
eval:
	cd backend && PYTHONPATH=.. uv run python -m eval.run_eval $(EVAL_ARGS)

# Re-tune the stage-1 threshold from DEV-split retrieval scores and write it into app/config.py
tune-threshold:
	cd backend && PYTHONPATH=.. uv run python -m eval.threshold --apply

# Regenerate the committed demo corpus (deterministic content; binary bytes may differ).
generate-corpus:
	cd backend && uv run python ../demo-corpus/generate.py

# Refill the results blocks in README.md and sample-deliverable/POC_HANDOVER.md from the newest report
docs:
	cd backend && PYTHONPATH=.. uv run python -m eval.readme_results
