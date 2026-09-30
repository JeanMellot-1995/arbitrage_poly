VENV ?= .venv
PIP_INDEX_URL ?= https://pypi.org/simple

# Platform-specific settings: Windows (cmd.exe, .venv\Scripts\*.exe) vs macOS/Linux.
ifeq ($(OS),Windows_NT)
SHELL := cmd.exe
BIN := $(subst /,\,$(VENV)/Scripts/)
EXE := .exe
HOST_PYTHON ?= py -3
PYTHON_BOOTSTRAP ?= py -3.12
OUT_DIR ?= data/tmp
else
SHELL := /bin/zsh
BIN := $(VENV)/bin/
EXE :=
HOST_PYTHON ?= python3
PYTHON_BOOTSTRAP ?= /opt/homebrew/opt/python@3.12/bin/python3.12
OUT_DIR ?= /tmp
endif

PYTHON := $(BIN)python$(EXE)
MAKE_TOOLS := $(HOST_PYTHON) scripts/make_tools.py

# Live decision recorder (Binance only, no Polymarket access needed).
LIVE_OUTPUT ?= $(OUT_DIR)/live-decisions.csv
LIVE_OFFSET_S ?= 120

# Historical backtest / calibration.
BACKTEST_OUTPUT ?= $(OUT_DIR)/backtest.csv
BACKTEST_REPORT ?= $(OUT_DIR)/backtest.json
BACKTEST_OFFSET_S ?= 120
CALIBRATION_BINS ?= $(OUT_DIR)/calibration-bins.csv
CALIBRATION_DAILY ?= $(OUT_DIR)/calibration-daily.csv
CALIBRATION_REPORT ?= $(OUT_DIR)/calibration.json

# Oracle results over the last DAYS days of the historical markets file.
DAYS ?= 3
ORACLE_OFFSET_S ?= $(BACKTEST_OFFSET_S)
ORACLE_PREFIX ?= $(OUT_DIR)/oracle-last-$(DAYS)d-t$(ORACLE_OFFSET_S)

.PHONY: help venv install test lint format format-check check \
	live live-decisions backtest calibration oracle-results binance-windows clean

help: ## List available targets
	@$(MAKE_TOOLS) help $(firstword $(MAKEFILE_LIST))

venv: ## Create the virtualenv (Homebrew python@3.12 on macOS, py -3.12 on Windows)
	$(PYTHON_BOOTSTRAP) -m venv $(VENV)

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

# Command arguments live in variables so recipes stay single-line (cmd.exe has no line continuation).
LIVE_ARGS = --min-net-edge 0.01 \
	--slippage-buffer 0.01 \
	--fixed-stake-usd 10 \
	--refresh-interval-s 1

LIVE_DECISIONS_ARGS = --output $(LIVE_OUTPUT) \
	--prediction-offset-s $(LIVE_OFFSET_S)

BACKTEST_ARGS = --prediction-offset-s $(BACKTEST_OFFSET_S) \
	--output $(BACKTEST_OUTPUT) \
	--report $(BACKTEST_REPORT)

CALIBRATION_ARGS = --input $(BACKTEST_OUTPUT) \
	--bins-output $(CALIBRATION_BINS) \
	--daily-output $(CALIBRATION_DAILY) \
	--report $(CALIBRATION_REPORT)

ORACLE_BACKTEST_ARGS = --last-days $(DAYS) \
	--prediction-offset-s $(ORACLE_OFFSET_S) \
	--output $(ORACLE_PREFIX).csv \
	--report $(ORACLE_PREFIX).json

ORACLE_CALIBRATION_ARGS = --input $(ORACLE_PREFIX).csv \
	--bins-output $(ORACLE_PREFIX)-bins.csv \
	--daily-output $(ORACLE_PREFIX)-daily.csv \
	--report $(ORACLE_PREFIX)-calibration.json

BINANCE_WINDOWS_ARGS = --days $(DAYS) \
	--prediction-offset-s $(ORACLE_OFFSET_S) \
	--output $(OUT_DIR)/binance-windows-$(DAYS)d-t$(ORACLE_OFFSET_S).csv

live: ## Run the full read-only paper loop (Binance + Polymarket book required)
	$(BIN)arbitrage-poly-live$(EXE) $(LIVE_ARGS)

live-decisions: ## Record Oracle UP/DOWN decisions at T-LIVE_OFFSET_S (default T-120s) from live Binance data only
	$(BIN)arbitrage-poly-live-decisions$(EXE) $(LIVE_DECISIONS_ARGS)

backtest: ## Score historical Oracle predictions at T-BACKTEST_OFFSET_S (default T-120s)
	$(BIN)arbitrage-poly-backtest$(EXE) $(BACKTEST_ARGS)

calibration: ## Compute calibration bins/daily stats from a backtest CSV
	$(BIN)arbitrage-poly-calibration$(EXE) $(CALIBRATION_ARGS)

oracle-results: ## Oracle results at T-ORACLE_OFFSET_S (default T-120s) on the last DAYS days (e.g. make oracle-results DAYS=5)
	$(BIN)arbitrage-poly-backtest$(EXE) $(ORACLE_BACKTEST_ARGS)
	$(BIN)arbitrage-poly-calibration$(EXE) $(ORACLE_CALIBRATION_ARGS)
	@$(MAKE_TOOLS) daily $(ORACLE_PREFIX)-daily.csv --prefix $(ORACLE_PREFIX)

binance-windows: ## Oracle at T-ORACLE_OFFSET_S (default T-120s) vs Binance outcome, last DAYS days (e.g. make binance-windows DAYS=2)
	$(PYTHON) -m arbitrage_poly.apps.binance_windows $(BINANCE_WINDOWS_ARGS)

clean: ## Remove caches and bytecode
	$(MAKE_TOOLS) clean
