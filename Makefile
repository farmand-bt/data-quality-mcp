.PHONY: install run dev lint format test generate-data

install:  ## Create .venv and install the package with dev dependencies
	uv sync

run:  ## Run the server over stdio (normally a client launches it instead)
	uv run data-quality-mcp

dev:  ## Open the MCP Inspector against the server (needs Node.js/npx)
	uv run mcp dev src/data_quality_mcp/server.py:app

lint:  ## Lint and check formatting without changing files (what CI runs)
	uv run ruff check .
	uv run ruff format --check .

format:  ## Auto-fix lint issues and format
	uv run ruff check --fix .
	uv run ruff format .

test:  ## Run the test suite with coverage
	uv run pytest --cov

generate-data:  ## Regenerate the synthetic test/example datasets
	uv run python scripts/generate_messy_data.py
