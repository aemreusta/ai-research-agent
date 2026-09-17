# Developer shortcuts. Everything here is also documented as a plain command in the README.
.DEFAULT_GOAL := help
SHELL := /bin/bash

# A disposable Postgres for the integration tests, separate from the compose database.
TEST_PG_CONTAINER ?= research-agent-test-pg
TEST_PG_PORT ?= 55432
TEST_DATABASE_URL ?= postgresql://research:research@localhost:$(TEST_PG_PORT)/research

.PHONY: help
help: ## list targets
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-20s\033[0m %s\n", $$1, $$2}'

.PHONY: up
up: ## start the whole stack (profiles from .env)
	docker compose up -d --build

.PHONY: up-light
up-light: ## start without Langfuse (lightweight mode)
	COMPOSE_PROFILES= docker compose up -d --build

.PHONY: down
down: ## stop the stack, keep the data
	docker compose --profile observability --profile local-llm down

.PHONY: reset
reset: ## stop the stack and delete all volumes
	docker compose --profile observability --profile local-llm --profile test down -v

.PHONY: logs
logs: ## follow application logs
	docker compose logs -f api agent dispatcher

.PHONY: test
test: ## unit + contract tests (no database needed)
	uv run pytest -q -m "not integration"
	cd dispatcher && go test ./...

.PHONY: test-db
test-db: ## start the disposable test Postgres
	@docker inspect $(TEST_PG_CONTAINER) >/dev/null 2>&1 || docker run -d --name $(TEST_PG_CONTAINER) \
		-e POSTGRES_USER=research -e POSTGRES_PASSWORD=research -e POSTGRES_DB=research \
		-p $(TEST_PG_PORT):5432 postgres:16-alpine >/dev/null
	@until docker exec $(TEST_PG_CONTAINER) pg_isready -U research >/dev/null 2>&1; do sleep 1; done

.PHONY: test-integration
test-integration: test-db ## everything, including Postgres integration tests (Python first: it migrates)
	TEST_DATABASE_URL=$(TEST_DATABASE_URL) uv run pytest -q
	cd dispatcher && TEST_DATABASE_URL=$(TEST_DATABASE_URL) go test ./...

.PHONY: test-docker
test-docker: ## the Python suite inside the compose network
	docker compose --profile test run --rm --build tests

.PHONY: lint
lint: ## ruff, mypy --strict, gofmt, go vet
	uv run ruff check .
	uv run ruff format --check .
	uv run mypy agent/src agent/tests migrations
	cd dispatcher && test -z "$$(gofmt -l .)" && go vet ./...

.PHONY: verify
verify: ## sequential local acceptance checks; no paid provider calls
	$(MAKE) lint
	$(MAKE) test-integration
	cd dispatcher && TEST_DATABASE_URL=$(TEST_DATABASE_URL) go test -race ./...
	uv run python evals/run.py --output /tmp/apilex-evidence-baseline.json

.PHONY: fmt
fmt: ## format Python and Go
	uv run ruff check --fix .
	uv run ruff format .
	cd dispatcher && gofmt -w .

# The four case examples (D12). Needs provider keys in .env; writes examples/<slug>/.
EXAMPLE_QUESTIONS := \
	"kvkk-2026-saas-action-plan|Türkiye'deki SaaS şirketleri için 2026 KVKK uyum aksiyon planı nedir?" \
	"apilexai-products-partnerships|ApilexAI'ın ürünleri, iş ortaklıkları ve stratejik yönü nedir?" \
	"eu-ai-act-timeline|What changed in the EU AI Act implementation timeline?" \
	"legal-tech-market-size|What is the size of the European legal tech market and how fast is it growing?"

.PHONY: examples
examples: ## run the four example questions with your keys (docker) into examples/
	@for item in $(EXAMPLE_QUESTIONS); do \
		slug=$${item%%|*}; question=$${item#*|}; \
		echo "==> $$slug"; \
		docker compose run --rm --no-deps --user "$$(id -u):$$(id -g)" \
			-v "$(CURDIR)/examples:/app/examples" \
			-e AGENT_RUNNER=graph agent run "$$question" --out "examples/$$slug" --quiet || exit 1; \
	done

.PHONY: demo
demo: ## an offline run (no keys) recorded in the database, to explore the UI
	docker compose run --rm --no-deps agent run "What is the EU AI Act implementation timeline?" --simulate --persist --quiet
