VENV := .venv
BIN  := $(VENV)/bin

.PHONY: setup clean test e2e

setup:
	python3 -m venv $(VENV)
	$(BIN)/pip install -e ".[dev]"
	@echo ""
	@echo "Add to ~/.zshrc"
	@echo '  export PATH="$(CURDIR)/$(BIN):$$PATH"'
	@echo "Reload shell:"
	@echo "  source ~/.zshrc"

clean:
	rm -rf $(VENV) src/*.egg-info build dist

test:
	$(BIN)/pytest tests/ --ignore=tests/e2e

e2e:
	PATH="$(CURDIR)/$(BIN):$$PATH" $(BIN)/pytest tests/e2e/
