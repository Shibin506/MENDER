.PHONY: install test lint eval up down

install:
	python -m venv .venv && .venv/bin/pip install -e ".[dev]"

test:
	pytest -q

lint:
	ruff check .

eval:
	python eval/run_eval.py --set tune && python eval/run_eval.py --set holdout

up:
	docker compose up --build

down:
	docker compose down
