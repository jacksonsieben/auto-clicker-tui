.DEFAULT_GOAL := help

PYTHON  ?= .venv/bin/python
VERSION ?= $(shell sed -n 's/__version__ = "\(.*\)"/\1/p' src/autoclicker/version.py)

.PHONY: help
help:
	@echo "Advanced Auto Clicker (TUI) $(VERSION)"
	@echo ""
	@echo "  make venv      Create .venv and install the project"
	@echo "  make run       Run the app from source"
	@echo "  make test      Run the test suite"
	@echo "  make check     Report whether input control works on this machine"
	@echo "  make build     Build a one-file binary for this platform"
	@echo "  make onedir    Build the zipped folder fallback"
	@echo "  make clean     Remove build artifacts"

.PHONY: venv
venv:
	python3 -m venv .venv
	.venv/bin/pip install -e '.[build]'
	.venv/bin/pip install pytest
	@# pynput's Linux metadata wants evdev, which ships source-only; the X11
	@# backend never imports it, so install pynput without its dependencies.
	@if [ "$$(uname)" = "Linux" ]; then .venv/bin/pip install --no-deps pynput; fi

.PHONY: run
run:
	$(PYTHON) -m autoclicker

.PHONY: test
test:
	$(PYTHON) -m pytest -q

.PHONY: check
check:
	$(PYTHON) -m autoclicker --check

.PHONY: build
build:
	$(PYTHON) scripts/build.py

.PHONY: onedir
onedir:
	$(PYTHON) scripts/build.py --onedir

.PHONY: clean
clean:
	rm -rf build dist *.spec
	find . -type d -name __pycache__ -not -path './.venv/*' -exec rm -rf {} +
