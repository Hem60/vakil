PY ?= .venv/Scripts/python.exe
SEED ?= 20260824
LIMIT ?= 0
N ?= 300

.PHONY: help install data fixtures fit extract-gemini extract-claude extract-stub eval sweep test lint demo verify rules clean up down

help:
	@echo "make install   create .venv and install dependencies"
	@echo "make data      regenerate the synthetic corpus (SEED=$(SEED) N=$(N))"
	@echo "make fixtures  render the proof-of-delivery documents"
	@echo "make fit       fit the win model on data/train, write data/model/"
	@echo "make extract-stub   score extraction with no API key and no spend"
	@echo "make extract-gemini LIMIT=20   score extraction, Gemini free tier"
	@echo "make extract-claude LIMIT=20   score extraction, Claude"
	@echo "make test      run the unit tests"
	@echo "make eval      run the held-out evaluation, write evals/report.md"
	@echo "make sweep     cost sensitivity sweep, write evals/cost_sweep.md"
	@echo "make demo      assess one case end to end and verify the audit chain"
	@echo "make rules CODE=13.1   what the networks require, with citations"
	@echo "make up/down   start/stop postgres + api + web"

install:
	python -m venv .venv
	$(PY) -m pip install --upgrade pip
	$(PY) -m pip install -e ".[dev]"

data:
	$(PY) data/generator/generate.py --seed $(SEED) --n $(N)

fixtures:
	$(PY) data/generator/fixtures.py --seed $(SEED)

fit:
	$(PY) scripts/fit_win_model.py

extract-stub:
	$(PY) evals/extraction_eval.py --backend stub

extract-gemini:
	$(PY) evals/extraction_eval.py --backend gemini --limit $(LIMIT)

extract-claude:
	$(PY) evals/extraction_eval.py --backend claude --limit $(LIMIT)

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

rules:
	PYTHONPATH=src $(PY) -m vakil.cli rules $(CODE)

up:
	docker compose up -d

down:
	docker compose down

clean:
	rm -rf .pytest_cache .ruff_cache .mypy_cache ledger.jsonl evals/report.* evals/cost_sweep.*
