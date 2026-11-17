# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A read-only MCP server (Python, official `mcp` SDK 2.x, stdio transport) that lets an AI agent list, describe, count, and read **approved** Microsoft Dataverse system views, and look up records by name. It signs in to Dataverse as a service identity. It may be used by a government organization.

Read `docs/PRD.md` for requirements, `SECURITY.md` for the security design, and `Tasks.md` for status and what changed during the build.

## Commands

Uses [uv](https://docs.astral.sh/uv/). Python 3.12 or later.

```sh
uv sync --locked                                  # install from the hash-pinned lockfile
uv run pytest                                     # all unit tests (no network)
uv run pytest tests/test_fetchxml.py              # one file
uv run pytest tests/test_fetchxml.py::test_describes_a_joined_view   # one test
uv run pytest --cov                               # coverage; fails under 90%
uv run ruff check . && uv run ruff format .       # lint (includes security rules) and format
uv run mypy                                       # strict type-check of src and scripts
uv run bandit -c pyproject.toml -r src scripts    # static security analysis
uv run dataverse-views-mcp                        # start the server over stdio (needs settings; see README)
uv run python scripts/smoke_test.py               # one read-only call to confirm access
uv run python scripts/consistency_check.py --view-id <id>   # the PRD acceptance test
DATAVERSE_INTEGRATION=1 uv run pytest tests/integration     # opt-in tests against real Dataverse
```

After changing dependencies in `pyproject.toml`, run `uv lock` and commit `uv.lock`. CI installs with `--locked` and fails if the lockfile is stale.

## Architecture

Three layers, with dependencies pointing inward. Keep them separate.

1. **MCP layer**: `server.py` registers the five tools, and `run_tool` wraps every call to write the audit line and turn errors into plain messages. `tools/*.py` hold one tool each: a `DESCRIPTION` and one async function that takes `Services` and returns a plain `dict`.
2. **Domain layer**:
   - `views.py`: view lookup and the approved list.
   - `fetchxml.py`: parse, describe, and edit a view's stored query. `parse` also gives every join an explicit alias, which the rest of the code relies on for result column names.
   - `filters.py`: the structured filter model and its checks.
   - `view_query.py`: which columns may be filtered, and `load_runnable_view`, the single entry point `count_view` and `run_view` use to get a checked view.
   - `metadata.py`: table names and column types.
   - `paging.py`: signed page markers.
3. **Dataverse client**: `dataverse_client.py` (the only module that makes HTTP calls) and `auth.py` (MSAL).

`services.py` builds everything once at startup. `config.py` validates all settings once and is the only place that reads the environment. `errors.py` defines the error types; each carries a message that is safe to show a user.

### How a count flows

`count_view` → `load_runnable_view` (approved-list check, fetch the stored query, parse it, refuse views that depend on the caller, fetch table metadata, check filters) → `fetchxml.apply_filters` → `fetchxml.to_count` (distinct count of record IDs) → `DataverseClient.fetch` → one number. `run_view` is the same with `to_page` and row shaping.

## Rules that must not be broken

These are the security properties the tests defend. A change that weakens one needs the user's explicit agreement.

- **Read-only, enforced twice.** `DataverseClient` exposes only read methods, and the `_enforce_read_only` request hook refuses anything but a GET or a `$batch` holding exactly one GET. Never add a write method or relax the hook.
- **Approved views only.** The approved-list check in `ViewService.get_view` runs before any request and must stay first. Never add a tool that accepts query text, SQL, or FetchXML from the caller.
- **Never build queries from text.** Edit FetchXML through the parsed tree in `fetchxml.py`. Parse with `defusedxml`. Names and IDs that go into a request address must first match `LOGICAL_NAME_RE` or `GUID_RE`.
- **Filters stay inside the view.** A column is filterable only if the view displays it or it is in `DATAVERSE_EXTRA_FILTER_COLUMNS`. A column the view only filters on is not filterable. Do not widen this without the data owner's say.
- **Nothing sensitive leaves.** Error messages to the client are fixed text. Never relay Dataverse's or the sign-in service's own message, and never log tokens, secrets, or record contents.
- **Every tool call is audited, and auditing fails closed.** Register new tools through `run_tool`; `AuditedServer.call_tool` covers calls the SDK rejects before a tool runs. The audit log records what was asked, never what was returned (no row values or result counts), and each line is size-capped.
- **Settings are never read implicitly from a file.** A settings file is read only when `DATAVERSE_ENV_FILE` names it.
- **Page markers are opaque.** They are encrypted and bound to their request in `paging.py`; never put a raw Dataverse paging cookie in a result.
- **Stdout is the protocol.** All logging goes to stderr. Never `print` in `src`.
- **Everything is bounded.** New results, caches, loops, and retries need a cap and a timeout.
- **Nothing specific to an organization in the repository.** No real URLs, tenant IDs, table names, view names, record values, or figures, in code, tests, fixtures, docs, or commit messages. Fixtures use made-up tables such as `thing` and `office`.
- **Tool names, descriptions, and input schemas are a public contract.** Keep input schemas flat: no `$ref`, `anyOf`, or type lists, because Copilot Studio drops tools that use them. `tests/test_server.py` checks this.

## Testing (required for every task and PR)

- No task or PR is complete without tests for the behavior it adds or changes. A bug fix starts with a test that fails for that bug.
- Write the test first and watch it fail, then implement.
- Before claiming work is done, run tests, lint, type-check, and Bandit, and report the actual output.
- Unit tests never touch a real environment. Fake Dataverse at the client boundary with `tests/fakes.py` (`FakeDataverse`, `make_services`, `add_view`, `add_table`), or at the HTTP boundary with `httpx.MockTransport` as `tests/test_dataverse_client.py` does. `tests/conftest.py` clears `DATAVERSE_*` variables and changes directory so no test sees a developer's `.env`.
- Each tool needs tests for: success, input rejection, refusal (unapproved view, caller-dependent view), Dataverse failures, and paging where it applies. Add a protocol-level case in `tests/test_server.py` and an audit case in `tests/test_audit.py`.
- For anything that takes caller input, include hostile inputs (injection attempts, oversized values, control characters).
- Do not weaken, skip, or delete a failing test to get a green run. Fix the code, or raise the conflict with the user.

## Pull requests

One pull request per task group, merged by squash once CI is green. CI runs lint, format check, strict mypy, tests with the coverage floor on three Python versions, Bandit, and `pip-audit`. GitHub Actions are pinned to commit SHAs; keep them pinned.
