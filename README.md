# dataverse-views-mcp

A small, read-only [Model Context Protocol](https://modelcontextprotocol.io) server that lets an
AI agent use the system views stored in Microsoft Dataverse. The agent can list approved views,
read their definitions, count the rows they return, and read those rows. Answers come from view
definitions the organization already maintains, not from queries the agent writes.

See [docs/PRD.md](docs/PRD.md) for the requirements and
[docs/Architecture.md](docs/Architecture.md) for the design.

## Install

Requires Python 3.12 or later and [uv](https://docs.astral.sh/uv/).

```sh
uv sync --locked
```

## Develop

```sh
uv run pytest                 # all unit tests (no network)
uv run pytest tests/test_package.py::test_package_exposes_version   # one test
uv run pytest --cov           # with coverage (fails under 90%)
uv run ruff check .           # lint, including security rules
uv run ruff format .          # format
uv run mypy                   # strict type-check
uv run bandit -c pyproject.toml -r src scripts   # static security analysis
```

## Configure

Settings come from environment variables or a local `.env` file (ignored by Git). Copy
`.env.example` to `.env` and fill it in. The server refuses to start, with a message naming
the setting at fault, when a required setting is missing or malformed.
