# Niuu single-binary build pipeline
#
# Targets:
#   make build-web      — build web-next SPA, copy into src/cli/web/dist/
#   make build-postgres — compile PostgreSQL + pgvector from source
#   make build-cli      — Nuitka --onefile compilation → dist/niuu
#   make build          — all of the above
#
# Parameters (for future binary targets):
#   BINARY_NAME  — output binary name       (default: niuu)
#   ENTRY_POINT  — Python entry point file   (default: src/cli/__main__.py)

BINARY_NAME  ?= niuu
ENTRY_POINT  ?= src/cli/__main__.py
OUTPUT_DIR   ?= dist
WEB_DIR      := web-next
WEB_APP_DIST := $(WEB_DIR)/apps/niuu/dist
WEB_DEST     := src/cli/web/dist
MIG_DIR      := migrations
MIG_DEST     := src/cli/migrations/volundr
TING_MIG_DEST := src/cli/migrations/ting

# PostgreSQL build — versions read from the single source of truth
PG_VERSIONS_PY := src/niuu/pg_versions.py
POSTGRES_VERSION := $(shell python3 -c "exec(open('$(PG_VERSIONS_PY)').read()); print(POSTGRES_VERSION)")
PGVECTOR_VERSION := $(shell python3 -c "exec(open('$(PG_VERSIONS_PY)').read()); print(PGVECTOR_VERSION)")
PGINSTALL_DIR    := build/pginstall

.PHONY: build build-web build-postgres build-cli build-cli-bin build-ravn copy-migrations clean lint test verify \
       test-integration test-integration-volundr test-integration-ting test-integration-sleipnir \
       test-e2e test-e2e-ui test-all test-ravn

# --------------------------------------------------------------------------
# Full build: web assets → migrations → PostgreSQL → Nuitka binary
# --------------------------------------------------------------------------
build: build-web copy-migrations build-postgres build-cli

# --------------------------------------------------------------------------
# Web UI: pnpm build + copy dist/ into the cli package data directory
# --------------------------------------------------------------------------
build-web:
	cd $(WEB_DIR) && pnpm install --frozen-lockfile && pnpm build
	rm -rf $(WEB_DEST)
	cp -r $(WEB_APP_DIST) $(WEB_DEST)

# --------------------------------------------------------------------------
# PostgreSQL + pgvector: compile from source into build/pginstall/
# --------------------------------------------------------------------------
build-postgres:
	POSTGRES_VERSION=$(POSTGRES_VERSION) \
	PGVECTOR_VERSION=$(PGVECTOR_VERSION) \
	INSTALL_PREFIX=$(PGINSTALL_DIR) \
	scripts/build_postgres.sh

# --------------------------------------------------------------------------
# Migrations: copy SQL files into the cli package data directory
# --------------------------------------------------------------------------
copy-migrations:
	rm -rf $(MIG_DEST)/*.sql $(TING_MIG_DEST)/*.sql
	mkdir -p $(MIG_DEST) $(TING_MIG_DEST)
	cp $(MIG_DIR)/*.sql $(MIG_DEST)/
	cp $(MIG_DIR)/ting/*.sql $(TING_MIG_DEST)/

# --------------------------------------------------------------------------
# Nuitka single-binary compilation
# --------------------------------------------------------------------------
build-cli: build-web build-cli-bin

# Compile only — CI calls this after downloading the prebuilt web-assets
# artifact into web-next/apps/niuu/dist; the binary runners have no pnpm.
build-cli-bin:
	uv run --extra cli python -m cli.build \
		--name $(BINARY_NAME) \
		--entry $(ENTRY_POINT) \
		--output-dir $(OUTPUT_DIR)

# --------------------------------------------------------------------------
# Ravn Nuitka single-binary compilation (no postgres/web assets)
# --------------------------------------------------------------------------
build-ravn:
	uv run python -m ravn.build \
		--output-dir $(OUTPUT_DIR)

# --------------------------------------------------------------------------
# Quality gates
# --------------------------------------------------------------------------
lint:
	uv run ruff check src/ tests/
	uv run ruff format --check src/ tests/

test:
	uv run pytest tests/ -v --tb=short \
		--cov=src --cov-report=term-missing --cov-fail-under=85

verify: lint test

# --------------------------------------------------------------------------
# Integration & E2E tests
# --------------------------------------------------------------------------
test-integration:
	uv run pytest tests/integration/ -v --tb=short -m integration

test-integration-volundr:
	uv run pytest tests/integration/volundr/ -v --tb=short -m integration

test-integration-ting:
	uv run pytest tests/integration/ting/ -v --tb=short -m integration

test-integration-sleipnir:
	uv run pytest tests/integration/sleipnir/ -v --tb=short -m broker --override-ini="addopts="

test-e2e:
	cd $(WEB_DIR) && pnpm install --frozen-lockfile && pnpm test:e2e

test-e2e-ui:
	cd $(WEB_DIR) && pnpm install --frozen-lockfile && pnpm exec playwright test --ui

test-all: test test-integration test-e2e

# --------------------------------------------------------------------------
# Ravn-specific tests with coverage
# --------------------------------------------------------------------------
test-ravn:
	uv run --extra test pytest tests/ravn/ tests/test_ravn/ -v --tb=short \
		--cov=src/ravn --cov-report=term-missing --cov-fail-under=85

# --------------------------------------------------------------------------
# Cleanup
# --------------------------------------------------------------------------
clean:
	rm -rf $(OUTPUT_DIR) $(WEB_DEST) build/ *.build/ *.dist/ *.onefile-build/
	rm -rf $(MIG_DEST)/*.sql $(TING_MIG_DEST)/*.sql
	rm -rf $(PGINSTALL_DIR)
