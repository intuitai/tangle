.PHONY: dev test test-unit test-all lint typecheck fmt bench docker clean

dev:
	uv sync

test-unit:
	uv run pytest tests/ --ignore=tests/integration --ignore=tests/benchmarks -x -v --timeout=30

test-integration:
	uv run pytest tests/integration/ -v --timeout=60 -m integration

test-all:
	docker compose up --build --abort-on-container-exit test-runner

test: test-unit

lint:
	uv run ruff check src/ tests/

typecheck:
	uv run mypy src/tangle

fmt:
	uv run ruff format src/ tests/

bench:
	uv run pytest tests/benchmarks/ -v --benchmark-only

coverage:
	uv run pytest tests/ --ignore=tests/benchmarks --cov=tangle --cov-report=term-missing --cov-report=html

docker:
	docker build -t tangle:latest .

clean:
	docker compose down -v
	rm -rf .venv/ dist/ htmlcov/ .mypy_cache/ .pytest_cache/ *.db
