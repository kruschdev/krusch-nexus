VENV_PYTHON := $(shell if [ -n "$$VIRTUAL_ENV" ]; then echo "$$VIRTUAL_ENV/bin/python"; elif [ -f ./mcp_env/bin/python ]; then echo ./mcp_env/bin/python; else which python3; fi)
VENV_PYTEST := $(shell if [ -n "$$VIRTUAL_ENV" ]; then echo "$$VIRTUAL_ENV/bin/pytest"; elif [ -f ./mcp_env/bin/pytest ]; then echo ./mcp_env/bin/pytest; else which pytest 2>/dev/null || echo "python3 -m pytest"; fi)

PYTHON ?= $(VENV_PYTHON)
PYTEST ?= $(VENV_PYTEST)

.PHONY: test eval verify clean

test:
	PYTHONPATH=src $(PYTEST) tests/

eval:
	PYTHONPATH=src $(PYTEST) tests/eval/

verify:
	PYTHONPATH=src $(PYTHON) -m krusch_nexus.cli verify
	PYTHONPATH=src $(PYTEST) tests/

clean:
	rm -rf .pytest_cache *.egg-info build dist
	find . -type d -name __pycache__ -exec rm -rf {} +
