.PHONY: dev test lint install package

PY ?= .venv/bin/python
PORT ?= 9488
ROOT := $(abspath .)

dev:
	bash scripts/dev.sh

test:
	$(PY) -m pytest tests -q
	$(PY) -m ruff check sidecar tests
	cd frontend && pnpm exec tsc --noEmit
	node --test shell/electron/protocol.test.js

lint: test

install:
	bash packaging/install.sh

package:
	bash packaging/package.sh
