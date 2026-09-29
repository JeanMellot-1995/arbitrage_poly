SHELL := /bin/zsh

VENV ?= .venv
PYTHON := $(VENV)/bin/python
PIP_INDEX_URL ?= https://pypi.org/simple

# Live decision recorder (Binance only, no Polymarket access needed).
LIVE_OUTPUT ?= /tmp/live-decisions.csv
LIVE_OFFSET_S ?= 60

# Historical backtest / calibration.
BACKTEST_OUTPUT ?= /tmp/backtest.csv
BACKTEST_REPORT ?= /tmp/backtest.json
BACKTEST_OFFSET_S ?= 60
CALIBRATION_BINS ?= /tmp/calibration-bins.csv
CALIBRATION_DAILY ?= /tmp/calibration-daily.csv
CALIBRATION_REPORT ?= /tmp/calibration.json

# Oracle results over the last DAYS days of the historical markets file.
DAYS ?= 3
ORACLE_OFFSET_S ?= $(BACKTEST_OFFSET_S)
ORACLE_PREFIX ?= /tmp/oracle-last-$(DAYS)d-t$(ORACLE_OFFSET_S)

.PHONY: help venv install test lint format format-check check \
	live live-decisions backtest calibration oracle-results binance-windows clean

help: ## List available targets
	@awk 'BEGIN {FS = ":.*## "} /^[a-zA-Z0-9_-]+:.*## /{printf "  %-18s %s\n", $$1, $$2}' $(MAKEFILE_LIST)

venv: ## Create the virtualenv (Homebrew python@3.12)
	/opt/homebrew/opt/python@3.12/bin/python3.12 -m venv $(VENV)

install: ## Editable install with dev dependencies (bypasses a broken global pip index)
	$(PYTHON) -m pip install --index-url $(PIP_INDEX_URL) --upgrade pip
	$(PYTHON) -m pip install --index-url $(PIP_INDEX_URL) -e ".[dev]"

test: ## Run the unit test suite
	$(PYTHON) -m pytest -q

lint: ## Check lint rules with ruff
	$(PYTHON) -m ruff check src tests

format: ## Auto-format with ruff
	$(PYTHON) -m ruff format src tests

format-check: ## Check formatting without modifying files
	$(PYTHON) -m ruff format --check src tests

check: lint format-check test ## Run lint, format-check and tests

live: ## Run the full read-only paper loop (Binance + Polymarket book required)
	$(VENV)/bin/arbitrage-poly-live \
		--min-net-edge 0.01 \
		--slippage-buffer 0.01 \
		--fixed-stake-usd 10 \
		--refresh-interval-s 1

live-decisions: ## Record Oracle UP/DOWN decisions at t-LIVE_OFFSET_S from live Binance data only
	$(VENV)/bin/arbitrage-poly-live-decisions \
		--output $(LIVE_OUTPUT) \
		--prediction-offset-s $(LIVE_OFFSET_S)

backtest: ## Score historical Oracle predictions against resolved Polymarket markets
	$(VENV)/bin/arbitrage-poly-backtest \
		--prediction-offset-s $(BACKTEST_OFFSET_S) \
		--output $(BACKTEST_OUTPUT) \
		--report $(BACKTEST_REPORT)

calibration: ## Compute calibration bins/daily stats from a backtest CSV
	$(VENV)/bin/arbitrage-poly-calibration \
		--input $(BACKTEST_OUTPUT) \
		--bins-output $(CALIBRATION_BINS) \
		--daily-output $(CALIBRATION_DAILY) \
		--report $(CALIBRATION_REPORT)

oracle-results: ## Oracle results on the last DAYS days of data (e.g. make oracle-results DAYS=5 ORACLE_OFFSET_S=120)
	$(VENV)/bin/arbitrage-poly-backtest \
		--last-days $(DAYS) \
		--prediction-offset-s $(ORACLE_OFFSET_S) \
		--output $(ORACLE_PREFIX).csv \
		--report $(ORACLE_PREFIX).json
	$(VENV)/bin/arbitrage-poly-calibration \
		--input $(ORACLE_PREFIX).csv \
		--bins-output $(ORACLE_PREFIX)-bins.csv \
		--daily-output $(ORACLE_PREFIX)-daily.csv \
		--report $(ORACLE_PREFIX)-calibration.json
	@echo
	@awk -F, '{ sub(/\r$$/, "") } NR==1 { for (i = 1; i <= NF; i++) c[$$i] = i; \
		printf "%-10s %5s %7s %7s %8s %6s %9s %8s\n", "utc_day", "n", "acc", "brier", "logloss", "bets", "pnl_usd", "roi"; next } \
		{ printf "%-10s %5d %6.1f%% %7.4f %8.4f %6d %+9.2f %+7.2f%%\n", \
		$$c["utc_day"], $$c["n"], 100 * $$c["accuracy"], $$c["brier_score"], $$c["log_loss"], \
		$$c["pnl_bets"], $$c["total_pnl_usd"], 100 * $$c["roi"] }' $(ORACLE_PREFIX)-daily.csv
	@echo "Files: $(ORACLE_PREFIX).{csv,json} and $(ORACLE_PREFIX)-{bins,daily}.csv"

binance-windows: ## Oracle vs Binance outcome per 5-min window, last DAYS days via Binance REST (e.g. make binance-windows DAYS=2 ORACLE_OFFSET_S=120)
	$(PYTHON) -m arbitrage_poly.apps.binance_windows \
		--days $(DAYS) \
		--prediction-offset-s $(ORACLE_OFFSET_S) \
		--output /tmp/binance-windows-$(DAYS)d-t$(ORACLE_OFFSET_S).csv

clean: ## Remove caches and bytecode
	find . -type d -name '__pycache__' -exec rm -rf {} +
	rm -rf .pytest_cache .ruff_cache
