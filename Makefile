.PHONY: install run run-http docker-build docker-run dev lint format test generate-data

install:  ## Create .venv and install the package with dev dependencies
	uv sync

run:  ## Run the server over stdio (normally a client launches it instead)
	uv run data-quality-mcp

run-http:  ## Serve over HTTP on 127.0.0.1:8000 (token from .env, see .env.example)
	uv run --env-file .env data-quality-mcp --transport http

docker-build:  ## Build the Docker image
	docker build -t data-quality-mcp .

docker-run:  ## Run the image on port 8000 (token from .env)
	docker run --rm --env-file .env -p 8000:8000 data-quality-mcp

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
