PYTHON ?= python3
UV ?= uv
API_URL ?= http://127.0.0.1:8000

.PHONY: install install-api install-mcp install-web test test-api test-rxp check-api test-benchmark benchmark benchmark-release test-web test-mcp verify openapi up down logs package

install: install-api install-mcp install-web

install-api:
	$(UV) sync --python 3.9 --extra dev

install-mcp:
	$(UV) sync --python 3.12 --project mcp_servers --extra dev

install-web:
	npm --prefix apps/web ci

test: test-api test-rxp check-api test-benchmark test-mcp test-web verify

test-api:
	$(UV) run --python 3.9 --extra dev pytest tests/api

test-rxp:
	$(UV) run --python 3.9 --extra dev pytest tests/protocols
	$(UV) run --python 3.9 --extra dev python -m protocols.rxp schema --check

check-api:
	$(UV) run --python 3.9 --extra dev ruff check apps/api protocols/rxp tests/api tests/protocols
	$(UV) run --python 3.9 --extra dev mypy apps/api protocols/rxp

test-benchmark:
	$(UV) run --python 3.9 --extra dev pytest tests/benchmarks
	$(UV) run --python 3.9 --extra dev ruff check benchmarks tests/benchmarks
	$(UV) run --python 3.9 --extra dev mypy benchmarks
	$(UV) run --python 3.9 --extra dev python -m benchmarks.runner --repetitions 2 --strict --output-json /tmp/rxp-bench-ci.json --output-md /tmp/rxp-bench-ci.md

benchmark:
	$(UV) run --python 3.9 --extra dev python -m benchmarks.runner --strict

benchmark-release:
	$(UV) run --python 3.9 --extra dev python -m benchmarks.runner --profiles agentteams-rxp-target --release-gate agentteams-rxp-target

test-web:
	npm --prefix apps/web test
	npm --prefix apps/web run build

test-mcp:
	$(UV) run --python 3.12 --project mcp_servers --extra dev pytest mcp_servers/tests tests/integration
	$(UV) run --python 3.12 --project mcp_servers --extra dev ruff check mcp_servers/src mcp_servers/tests tests/integration

verify:
	$(PYTHON) scripts/verify_submission.py

openapi:
	$(UV) run --python 3.9 --extra dev python scripts/export_openapi.py

up:
	docker compose up --build

down:
	docker compose down

logs:
	docker compose logs -f --tail=200

package: openapi
	$(PYTHON) scripts/build_submission.py
