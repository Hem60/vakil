PY ?= .venv/Scripts/python.exe
SEED ?= 20260824
N ?= 300

.PHONY: help install data eval sweep test lint demo verify clean up down

help:
	@echo "make install   create .venv and install dependencies"
	@echo "make data      regenerate the synthetic corpus (SEED=$(SEED) N=$(N))"
	@echo "make test      run the unit tests"
	@echo "make eval      run the held-out evaluation, write evals/report.md"
	@echo "make sweep     cost sensitivity sweep, write evals/cost_sweep.md"
	@echo "make demo      assess one case end to end and verify the audit chain"
	@echo "make up/down   start/stop postgres + api + web"

install:
	python -m venv .venv
	$(PY) -m pip install --upgrade pip
	$(PY) -m pip install -e ".[dev]"

data:
	$(PY) data/generator/generate.py --seed $(SEED) --n $(N)

test:
	PYTHONPATH=src $(PY) -m pytest tests -q

lint:
	PYTHONPATH=src $(PY) -m ruff check src tests evals data
	PYTHONPATH=src $(PY) -m mypy

eval:
	$(PY) evals/run_eval.py

sweep:
	$(PY) evals/cost_sweep.py

demo:
	@rm -f ledger.jsonl
	PYTHONPATH=src $(PY) -m vakil.cli assess-case $$(ls data/test/case_*.json | head -1)
	PYTHONPATH=src $(PY) -m vakil.cli verify

verify:
	PYTHONPATH=src $(PY) -m vakil.cli verify

up:
	docker compose up -d

down:
	docker compose down

clean:
	rm -rf .pytest_cache .ruff_cache .mypy_cache ledger.jsonl evals/report.* evals/cost_sweep.*
