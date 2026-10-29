SHELL := /bin/zsh

.PHONY: setup install build dev serve test typecheck check run run-cached warm-linkedin warm-indeed reset-indeed

setup: install build

install:
	uv sync
	npm --prefix web install

build:
	npm --prefix web run build

dev:
	npm --prefix web run dev

serve:
	uv run jobscout serve

test:
	uv run pytest -q

typecheck:
	npm --prefix web run typecheck

check: test typecheck

run:
	uv run jobscout run

run-cached:
	uv run jobscout run --cached

warm-linkedin:
	uv run jobscout warm linkedin

warm-indeed:
	uv run jobscout warm indeed

reset-indeed:
	uv run jobscout warm indeed --clean
