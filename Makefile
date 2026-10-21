COV_MIN ?= 80

.PHONY: setup lint test eval eval-data run docker data gi

setup:
	uv sync --all-groups
	uv run pre-commit install

lint:
	uv run ruff check .
	uv run ruff format --check .
	uv run mypy

test:
	uv run pytest --cov=src --cov-fail-under=$(COV_MIN)

# uv reads .env only when the file exists, so `make eval` also works with the key exported.
ENV_FILE := $(if $(wildcard .env),--env-file .env)

eval:
	uv run $(ENV_FILE) --group eval python evals/run_evals.py --suite grounding

eval-data:
	uv run python evals/run_evals.py --suite data_accuracy

run:
	uv run nutrition-mcp

docker:
	docker build -t nutrition-mcp .
	docker run --rm -i nutrition-mcp

data:
	uv run --group build-data python scripts/build_ingredients.py

gi:
	uv run --group build-data python scripts/extract_gi.py
