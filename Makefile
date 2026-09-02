.PHONY: test test-python test-edge lint fmt validate build-shared build-core help

help:
	@echo "make test        - pytest + node --test (edge-guard)"
	@echo "make test-python - pytest (pyproject の testpaths 全体)"
	@echo "make test-edge   - node --test (CloudFront Function)"
	@echo "make lint        - ruff check + format --check"
	@echo "make fmt         - ruff format"
	@echo "make validate    - sam validate + cfn-lint (全テンプレート)"

test: test-python test-edge

test-python:
	uv run pytest

test-edge:
	node --test "infra/edge/functions/*.test.mjs"

lint:
	uv run ruff check .
	uv run ruff format --check .

fmt:
	uv run ruff format .

validate:
	bash scripts/validate-templates.sh
