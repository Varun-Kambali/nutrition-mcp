COV_MIN ?= 0

.PHONY: setup lint test eval eval-data run docker data

setup:
	uv sync --all-groups
	uv run pre-commit install

lint:
	uv run ruff check .
	uv run ruff format --check .
	uv run mypy src

test:
	uv run pytest --cov=src --cov-fail-under=$(COV_MIN)

eval:
	uv run python evals/run_evals.py --suite grounding

eval-data:
	uv run python evals/run_evals.py --suite data_accuracy

run:
	uv run nutrition-mcp

docker:
	docker build -t nutrition-mcp .
	docker run --rm -i nutrition-mcp

data:
	uv run --group build-data python scripts/build_ingredients.py
