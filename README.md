# dataverse-views-mcp

A small, read-only [Model Context Protocol](https://modelcontextprotocol.io) (MCP) server
that lets an AI agent use the system views stored in Microsoft Dataverse.

The agent can list approved views, read their definitions, count the records they return,
and read their rows. Answers come from view definitions the organization already
maintains, not from queries the agent writes, so the same question returns the same
number every time.

- Requirements: [docs/PRD.md](docs/PRD.md)
- Design: [docs/Architecture.md](docs/Architecture.md)
- Security design and control mapping: [SECURITY.md](SECURITY.md)
- Task list and status: [Tasks.md](Tasks.md)

## Status

The MVP is built: five tools, tests, and CI. It has **not yet been run against a real
Dataverse environment**. The acceptance test (the same count, 10 runs out of 10, equal to
the app) is ready to run once the prerequisites in [Tasks.md](Tasks.md) are in place.

## What it does and does not do

| It does | It does not |
| --- | --- |
| Read approved system views | Create, update, or delete anything |
| Count records in Dataverse | Run SQL or query text written by the model |
| Add structured, checked filters | Use views that are not on the approved list |
| Log every call for audit | Log or cache record contents |

## Tools

All five tools only read. Each result names the view and filters it is based on.

| Tool | Input | Returns |
| --- | --- | --- |
| `list_views` | `table` | Approved views for the table: ID, name, description, last-modified date |
| `describe_view` | `view_id` | Columns, built-in filters, sort order, joined tables, filterable columns, and each filterable choice column's labels and values |
| `find_record` | `table`, `search_text` | Matching records with ID and name: zero, one, or several |
| `count_view` | `view_id`, `filters` (optional) | One number, computed by Dataverse |
| `run_view` | `view_id`, `filters`, `page_marker`, `page_size` (all but the first optional) | One page of rows and a marker for the next page |

A typical question, "How many open cases does the North office have?":

1. `find_record(table="office", search_text="North")` returns one record ID, or several
   candidates for the user to choose from.
2. `list_views(table="case")` returns the approved views; `describe_view` explains one.
3. `count_view(view_id=..., filters=[{"column": "officeid", "operator": "eq", "value": "<record ID>"}])`
   returns the count, the view name, the filters applied, and the view's last-modified date.

### Filters

A filter is a column, an operator from a fixed list, and a value:

```json
[{"column": "officeid", "operator": "eq", "value": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"}]
```

- Operators: `eq`, `ne`, `gt`, `ge`, `lt`, `le`, `in`, `null`, `not_null`, `begins_with`,
  `contains`, `on`, `on_or_after`, `on_or_before`.
- To filter a joined table, add `"table": "<joined table name from describe_view>"`.
- A column can be filtered only if the view displays it, or an administrator has listed
  it in `DATAVERSE_EXTRA_FILTER_COLUMNS`. If the approved views do not display the office
  column, list it there so counts can be filtered by office.
- The value must match the column's type. A related record, such as an office, must be
  given as a record ID from `find_record`, never as a name. A choice column takes the
  choice's number. `describe_view` lists each choice's label beside its number (for a yes/no
  column, beside `true` or `false`), so a question about "closed" records can be turned
  into the right value.

## Install

Requires Python 3.12 or later and [uv](https://docs.astral.sh/uv/).

```sh
uv sync --locked
```

## Set up access in Dataverse

Someone with admin rights on the Dataverse environment does this once.

1. Register an application in Microsoft Entra ID. Add a certificate (preferred) or a
   client secret.
2. In the Power Platform admin center, create an **application user** for that app in
   the environment.
3. Give the application user a custom security role that can only read: the tables the
   approved views use (including joined tables), view definitions (`savedquery`), and
   table definitions (metadata). Grant no create, write, delete, append, or assign rights.
4. Agree the approved views with the data owner and collect their IDs.

The service identity sees the same data for every user. Limit its role to what the
approved views need, and use it only on data every user of the agent may see.

## Configure

Settings come from environment variables, or from a settings file that Git ignores. Copy
`.env.example` to `.env`, fill it in, and tell the server where it is:

```sh
export DATAVERSE_ENV_FILE=/full/path/to/.env
```

The server reads a settings file only when `DATAVERSE_ENV_FILE` names one. It never picks
up a `.env` from the folder it happens to start in, so a file left there cannot change
which views are approved or where the audit log goes. The server refuses to start, naming
the setting at fault, when a required setting is missing or malformed.

| Setting | Required | Default | Purpose |
| --- | --- | --- | --- |
| `DATAVERSE_ENV_FILE` | No | none | Full path to a settings file to read |
| `DATAVERSE_URL` | Yes | | Environment address: `https://` and host only |
| `DATAVERSE_TENANT_ID` | Yes | | Entra tenant ID or domain |
| `DATAVERSE_CLIENT_ID` | Yes | | Application (client) ID |
| `DATAVERSE_CLIENT_SECRET` | One of these | | Client secret |
| `DATAVERSE_CLIENT_CERTIFICATE_PATH` | One of these | | Path to the certificate's private key (PEM). Preferred |
| `DATAVERSE_CLIENT_CERTIFICATE_THUMBPRINT` | With a certificate | | Certificate SHA-1 thumbprint |
| `DATAVERSE_AUTHORITY_HOST` | No | `https://login.microsoftonline.com` | Sign-in address |
| `DATAVERSE_API_VERSION` | No | `v9.2` | Web API version |
| `DATAVERSE_APPROVED_VIEW_IDS` | In practice | empty | Comma-separated IDs of approved views |
| `DATAVERSE_ALLOW_ALL_SYSTEM_VIEWS` | No | `false` | Local testing only: allow every active system view |
| `DATAVERSE_SEARCHABLE_TABLES` | No | empty | Tables `find_record` may search. Empty turns it off |
| `DATAVERSE_EXTRA_FILTER_COLUMNS` | No | empty | Extra filterable columns, as `table.column` |
| `DATAVERSE_MAX_PAGE_SIZE` | No | `50` | Most rows per page (1 to 250) |
| `DATAVERSE_FIND_RECORD_LIMIT` | No | `10` | Most matches `find_record` returns (1 to 50) |
| `DATAVERSE_VIEW_CACHE_TTL_SECONDS` | No | `60` | How long view definitions are cached. 0 is off |
| `DATAVERSE_HTTP_TIMEOUT_SECONDS` | No | `30` | Timeout for each call to Dataverse |
| `DATAVERSE_MAX_RETRIES` | No | `3` | Retries when throttled or unreachable (0 to 6) |
| `DATAVERSE_MAX_RETRY_WAIT_SECONDS` | No | `30` | Longest single wait Dataverse may ask for |
| `DATAVERSE_AUDIT_LOG_PATH` | No | stderr | File to write the audit log to |
| `DATAVERSE_AUDIT_LOG_VALUES` | No | `true` | `false` logs a fingerprint in place of filter values and search text |
| `DATAVERSE_LOG_LEVEL` | No | `INFO` | `DEBUG`, `INFO`, `WARNING`, or `ERROR` |

### Microsoft cloud

The sign-in address and the environment address are both settings, so a different cloud
needs no code change. Confirm which cloud your tenant is on.

| Cloud | `DATAVERSE_AUTHORITY_HOST` | Environment address ends with |
| --- | --- | --- |
| Commercial | `https://login.microsoftonline.com` | `.crm.dynamics.com` |
| GCC | `https://login.microsoftonline.com` | `.crm9.dynamics.com` |
| GCC High | `https://login.microsoftonline.us` | `.crm.microsoftdynamics.us` |
| DoD | `https://login.microsoftonline.us` | `.crm.appsplatform.us` |

### Add a view to the approved list

1. The data owner confirms the view gives the right answer.
2. Copy the view's ID (the `savedqueryid`, visible in the view's address in the maker
   portal) into `DATAVERSE_APPROVED_VIEW_IDS`, separated from the others by a comma.
3. Restart the server. The view appears in `list_views` on the next call.

A view added to or changed in the app needs no code change. Edits to an approved view
take effect within the cache time, and every answer carries the view's last-modified
date. Tell the app owner that approved views are now in use by the agent.

## Run

Confirm the identity can reach Dataverse with one read-only call:

```sh
uv run python scripts/smoke_test.py
```

Start the server over stdio:

```sh
uv run dataverse-views-mcp
```

### Connect a local client

With [MCP Inspector](https://github.com/modelcontextprotocol/inspector):

```sh
npx @modelcontextprotocol/inspector uv run dataverse-views-mcp
```

With Cursor, Claude Code, or any client that takes a JSON server entry:

```json
{
  "mcpServers": {
    "dataverse-views": {
      "command": "uv",
      "args": ["run", "--directory", "/path/to/dataverse-views-mcp", "dataverse-views-mcp"],
      "env": {"DATAVERSE_ENV_FILE": "/path/to/dataverse-views-mcp/.env"}
    }
  }
}
```

## Acceptance test

Once access exists, run the PRD's acceptance test: the same count, 10 times.

```sh
uv run python scripts/consistency_check.py \
  --view-id <view ID> \
  --filters '[{"column": "officeid", "operator": "eq", "value": "<record ID>"}]' \
  --expected <count shown in the app>
```

It passes when all 10 results are identical and equal the expected count. The same check
runs as a test with `DATAVERSE_INTEGRATION=1 uv run pytest tests/integration`.

Do not record names or figures from the real system in this repository.

## Audit log

Every tool call writes one JSON line, whether it succeeds, is refused, or fails:

```json
{"time": "2026-11-16T12:30:45.123+00:00", "event": "tool_call", "event_id": "…",
 "server": "dataverse-views-mcp", "server_version": "0.1.0", "environment": "<host>",
 "tool": "count_view",
 "caller": {"client_declared": "inspector", "client_version": "3.1", "os_user": "…",
            "service_identity": "<client ID>", "request_id": "4"},
 "view_id": "…", "view_name": "…", "table": "…",
 "filters": [{"column": "officeid", "operator": "eq", "value": "…"}],
 "outcome": "success", "error_code": "", "duration_ms": 182}
```

- `outcome` is `success`, `refused` (the server correctly said no), or `error`.
- It records what was asked, never what came back: no row values, no result counts, and
  no credentials.
- What was asked includes filter values and `find_record`'s search text, which may be a
  person's or office's name. Set `DATAVERSE_AUDIT_LOG_VALUES=false` to log a fingerprint
  of those instead.
- Calls rejected before they reach a tool (wrong arguments, unknown tool names) are
  logged too. Each line is capped at 16 KB.
- If a line cannot be written, the call returns an error and no data.
- `client_declared` is the name the MCP client gives itself and is not proof of identity.
  Under the MVP's service identity, the server cannot know which person asked. Per-user
  sign-in, the next step after the MVP, closes this gap.
- Lines go to stderr unless `DATAVERSE_AUDIT_LOG_PATH` is set. Protect, forward, and
  retain the log according to your organization's policy.

## Known limits

- **Counts stop at 50,000 records.** Dataverse will not count more in one query. The tool
  returns a clear error; add a filter to narrow the count.
- **Page size.** `run_view` returns at most `DATAVERSE_MAX_PAGE_SIZE` rows per page, and
  shortens long text so a page stays under 400 KB.
- **System views only.** Personal views are not available.
- **Views that depend on who is asking.** "My records" style views are flagged by
  `describe_view` and refused by `count_view` and `run_view`, because the server signs in
  as a service, not as the person asking.
- **Views it will not run.** Views that already total their rows, are limited to a
  fixed number of top rows, or select every column instead of naming their columns.
- **Views that join other tables.** `count_view` counts each record once. `run_view`
  returns one row per match, so a record can appear more than once, and it says so. A
  sort on a joined table's column is not applied; the rows are the same in another order.
- **One service identity.** Everyone gets the same data through it.
- **Local only.** The server runs over stdio. It is not hosted and is not yet connected
  to a Copilot Studio agent.

## Develop

```sh
uv run pytest                 # all unit tests (no network)
uv run pytest tests/test_fetchxml.py::test_describes_a_joined_view   # one test
uv run pytest --cov           # with coverage (fails under 90%)
uv run ruff check .           # lint, including security rules
uv run ruff format .          # format
uv run mypy                   # strict type-check
uv run bandit -c pyproject.toml -r src scripts   # static security analysis
```

CI runs all of these on Python 3.12, 3.13, and 3.14, plus a known-vulnerability audit of
the locked dependencies. Every pull request must include tests for what it changes.

## Next steps after the MVP

1. Run the acceptance test against the real environment.
2. Per-user sign-in, so each request runs with the asker's own permissions.
3. Hosting in Azure over Streamable HTTP, with credentials in Key Vault or a managed
   identity.
4. Connecting to the Copilot Studio agent, and tuning it to prefer these tools.
5. Counts past the Dataverse ceiling, totals other than a count, and personal views.

[SECURITY.md](SECURITY.md) lists what must be in place before the server is hosted.
