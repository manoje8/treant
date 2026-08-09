.PHONY: lint, format

lint:
	ruff check .
	ruff format --check .

format:
	ruff check --fix .
	ruff format .