Tasks: Dataverse Views MCP Server (MVP)

Task list generated from the PRD. Work is split into 12 pull requests, in the order they should be merged. Check off subtasks as you go, and check the PR heading when it is merged.

Assumptions

Language is Python 3.12, the PRD's recommended option. The language decision is still open in the PRD. If you pick TypeScript or C#, the PR breakdown stays the same and the file names change.
The fifth tool is named run_view, as in the PRD.
All five tools are in the MVP, per your decision.
The MVP runs locally over stdio. Hosting and connecting to the Copilot Studio agent are out of scope.
Nothing specific to the organization goes in the repository: no URLs, tenant IDs, table names, view names, or secrets.
Progress
[ ] PR 1: Project scaffold and tooling
[ ] PR 2: Configuration and sign-in
[ ] PR 3: Read-only Dataverse client
[ ] PR 4: View access and the approved list
[ ] PR 5: FetchXML reading and editing
[ ] PR 6: MCP server with list_views and describe_view
[ ] PR 7: find_record tool
[ ] PR 8: count_view tool
[ ] PR 9: run_view tool
[ ] PR 10: Audit logging and plain error messages
[ ] PR 11: Acceptance test (10-run consistency check)
[ ] PR 12: Documentation and handoff
File structure
dataverse-views-mcp/
├── README.md
├── Tasks.md
├── pyproject.toml
├── .gitignore
├── .env.example
├── docs/
│   └── PRD.md
├── .github/
│   └── workflows/
│       └── ci.yml
├── src/
│   └── dataverse_views_mcp/
│       ├── __init__.py
│       ├── server.py             # MCP server entry point, registers the tools
│       ├── config.py             # settings read from environment variables
│       ├── auth.py               # sign-in and token handling (MSAL)
│       ├── dataverse_client.py   # read-only HTTP client for the Dataverse Web API
│       ├── views.py              # look up views, apply the approved list
│       ├── fetchxml.py           # parse, describe, and modify a view's query
│       ├── filters.py            # structured filter model and validation
│       ├── audit.py              # audit log of tool calls
│       ├── errors.py             # error types and plain messages
│       └── tools/
│           ├── __init__.py
│           ├── list_views.py
│           ├── describe_view.py
│           ├── find_record.py
│           ├── count_view.py
│           └── run_view.py
├── scripts/
│   ├── smoke_test.py             # one hand-run API call to confirm access
│   └── consistency_check.py      # runs a count 10 times and compares
└── tests/
    ├── conftest.py
    ├── fixtures/
    │   ├── view_simple.xml           # made-up view, one table
    │   ├── view_joined.xml           # made-up view with a join
    │   ├── view_user_relative.xml    # made-up "my records" style view
    │   └── responses/                # made-up API responses (JSON)
    ├── test_config.py
    ├── test_auth.py
    ├── test_dataverse_client.py
    ├── test_views.py
    ├── test_fetchxml.py
    ├── test_filters.py
    ├── test_tool_list_views.py
    ├── test_tool_describe_view.py
    ├── test_tool_find_record.py
    ├── test_tool_count_view.py
    ├── test_tool_run_view.py
    ├── test_audit.py
    └── integration/
        └── test_consistency.py   # skipped unless real credentials are set
Prerequisites (not code)

These depend on other people, so start them early. PRs 1, 3, 4, and 5 can be built and tested without them.

[ ] Request a read-only identity for the Dataverse environment: an app registration, plus an application user with a read-only role on the needed tables and on view definitions
[ ] Confirm which Microsoft cloud the tenant is on, since sign-in and API addresses differ
[ ] Agree with the data owner on the first approved views, including the one used for the acceptance test
[ ] Confirm where the repository should live (the PRD recommends the organization's private GitHub)
[ ] Get the count shown in the app for the acceptance test's view and office, to compare against
PR 1: Project scaffold and tooling

Goal: an empty, installable project with tests and CI running.

Files created: README.md, Tasks.md, pyproject.toml, .gitignore, .env.example, docs/PRD.md, .github/workflows/ci.yml, src/dataverse_views_mcp/__init__.py, tests/conftest.py

Files edited: none

[ ] Create the repository from the team's standard template
[ ] Add pyproject.toml with the package name, Python 3.12, and dependencies: the MCP SDK, MSAL, an HTTP client, an XML parser, and pytest
[ ] Add .gitignore covering .env, virtual environments, caches, and logs
[ ] Add .env.example with placeholder names only and no real values
[ ] Add the PRD as docs/PRD.md and this file as Tasks.md
[ ] Add a CI workflow that installs the project and runs linting and tests on every pull request
[ ] Add a placeholder test so CI passes
[ ] Write a short README.md: what the project is and how to install it

Done when: a fresh clone installs, and CI is green on the pull request.

PR 2: Configuration and sign-in

Goal: the server can read its settings and obtain a token for Dataverse.

Files created: src/dataverse_views_mcp/config.py, src/dataverse_views_mcp/auth.py, src/dataverse_views_mcp/errors.py, scripts/smoke_test.py, tests/test_config.py, tests/test_auth.py

Files edited: .env.example, README.md

[ ] In config.py, define settings read from environment variables: environment address, tenant ID, client ID, client secret, sign-in authority address, and log level
[ ] Fail at startup with a clear message when a required setting is missing
[ ] Keep the sign-in authority and API address as settings, so a different Microsoft cloud needs no code change
[ ] In auth.py, get a token with MSAL using the service identity, and reuse it until it nears expiry
[ ] In errors.py, add error types for configuration, sign-in, and permission problems
[ ] Add scripts/smoke_test.py that makes one read call (the "who am I" request) and prints the result
[ ] Test config.py for missing and malformed settings
[ ] Test auth.py with MSAL mocked: success, failure, and token reuse
[ ] Update .env.example with every setting name
[ ] Document the settings and the smoke test in README.md

Done when: unit tests pass, and the smoke test succeeds once the read-only identity exists.

PR 3: Read-only Dataverse client

Goal: one module that makes every call to Dataverse, and can only read.

Files created: src/dataverse_views_mcp/dataverse_client.py, tests/test_dataverse_client.py, tests/fixtures/responses/ (sample JSON)

Files edited: src/dataverse_views_mcp/errors.py

[ ] Build a client that attaches the token and standard headers to each request
[ ] Allow read requests only. Raise an error if code ever asks for a write, so read-only is enforced in one place
[ ] Add a method to fetch rows with a FetchXML query
[ ] Request display labels for coded columns, so status and type values come back readable
[ ] Handle paging: return one page and a marker for the next
[ ] Use the API's batch form when a query is too long for a web address
[ ] Honor the retry signal when Dataverse throttles requests
[ ] Map API errors to the plain error types: not found, permission denied, bad request
[ ] Test each behavior with mocked responses: paging, throttling and retry, long query, error mapping, and write refusal

Done when: tests pass with no real network calls.

PR 4: View access and the approved list

Goal: find views in Dataverse and limit them to the approved set.

Files created: src/dataverse_views_mcp/views.py, tests/test_views.py

Files edited: src/dataverse_views_mcp/config.py, .env.example, README.md

[ ] Add a setting for the approved view list, and a setting that allows all system views for local testing
[ ] List the system views for a table: ID, name, description, and last-modified date
[ ] Leave out behind-the-scenes view types such as quick find and lookup views
[ ] Fetch one view by ID, including its stored query
[ ] Refuse any view that is not on the approved list, with a clear message
[ ] Include views the app does not display, when they are on the approved list
[ ] Cache view definitions briefly, so repeated questions do not re-read them
[ ] Test listing, filtering by type, the approved list, the unknown view case, and cache expiry
[ ] Document the approved-list setting in README.md and .env.example

Done when: tests pass, and a view outside the approved list cannot be fetched.

PR 5: FetchXML reading and editing

Goal: understand a view's stored query and change it safely. This is the hardest logic in the project, so it gets its own PR and the most tests.

Files created: src/dataverse_views_mcp/fetchxml.py, src/dataverse_views_mcp/filters.py, tests/test_fetchxml.py, tests/test_filters.py, tests/fixtures/view_simple.xml, tests/fixtures/view_joined.xml, tests/fixtures/view_user_relative.xml

Files edited: src/dataverse_views_mcp/errors.py

[ ] Parse a view's query with an XML parser, never with text replacement
[ ] Describe a query in plain terms: the main table, columns, filters, sort order, and joined tables
[ ] In filters.py, define a structured filter: a column, an operator from a fixed list, and a value
[ ] Validate each filter: allowed operator, well-formed column name, and a value of the right type
[ ] Add filters to a query on the main table
[ ] Add filters to a query on a joined table, such as a related office
[ ] Convert a query into a count of distinct records, so joins cannot inflate the total
[ ] Set the page and page size on a query for paging
[ ] Detect filters that depend on who is asking, such as "my records", and flag them
[ ] Test every function against all three fixtures
[ ] Test rejection of bad filters, including values that try to inject query text

Done when: tests pass, and the joined-view fixture produces a distinct count query.

PR 6: MCP server with list_views and describe_view

Goal: a running MCP server with the first two tools, usable from a local client.

Files created: src/dataverse_views_mcp/server.py, src/dataverse_views_mcp/tools/__init__.py, src/dataverse_views_mcp/tools/list_views.py, src/dataverse_views_mcp/tools/describe_view.py, tests/test_tool_list_views.py, tests/test_tool_describe_view.py

Files edited: pyproject.toml, README.md

[ ] Create the server with the MCP SDK, using stdio transport
[ ] Add a command-line entry point in pyproject.toml to start it
[ ] Implement list_views: input is a table name, output is approved views with ID, name, and description
[ ] Implement describe_view: input is a view ID, output is the plain description from PR 5, plus the last-modified date and any "depends on who is asking" flag
[ ] Write clear tool descriptions, since the agent chooses tools by reading them
[ ] Test both tools with the client and views modules mocked
[ ] Connect with MCP Inspector or Cursor and call both tools by hand
[ ] Document how to start the server and connect a local client in README.md

Done when: both tools return correct results from a local client.

PR 7: find_record tool

Goal: turn a typed name into one record, or a short list to choose from.

Files created: src/dataverse_views_mcp/tools/find_record.py, tests/test_tool_find_record.py

Files edited: src/dataverse_views_mcp/server.py, src/dataverse_views_mcp/dataverse_client.py, README.md

[ ] Add a client method to search a table's name column for matching text
[ ] Implement find_record: input is a table name and search text, output is matching records with ID and display name
[ ] Return zero, one, or several matches as normal results, not errors
[ ] Cap the number of matches returned
[ ] Register the tool in server.py with a clear description
[ ] Test no match, one match, several matches, and the cap
[ ] Add the tool to README.md

Done when: an ambiguous name returns the candidates, and an exact name returns one record.

PR 8: count_view tool

Goal: a count computed by Dataverse from a stored view. This is the tool that fixes the consistency problem.

Files created: src/dataverse_views_mcp/tools/count_view.py, tests/test_tool_count_view.py

Files edited: src/dataverse_views_mcp/server.py, src/dataverse_views_mcp/fetchxml.py, src/dataverse_views_mcp/dataverse_client.py, README.md

[ ] Implement count_view: input is a view ID and optional filters, output is one number
[ ] Build the count query with the functions from PR 5
[ ] Return the view name, the filters applied, and the view's last-modified date with the number
[ ] Refuse views that depend on who is asking, with a message explaining why
[ ] Return a clear error when the count exceeds the Dataverse ceiling. Splitting large counts is out of scope
[ ] Register the tool in server.py, with a description telling the agent to use it for all count questions
[ ] Test a plain count, a filtered count, a joined view, a refused view, and the ceiling error
[ ] Add the tool to README.md

Done when: the same call returns the same number every time against a mocked client, and against real data once access exists.

PR 9: run_view tool

Goal: read the rows a view returns, one page at a time.

Files created: src/dataverse_views_mcp/tools/run_view.py, tests/test_tool_run_view.py

Files edited: src/dataverse_views_mcp/server.py, src/dataverse_views_mcp/config.py, .env.example, README.md

[ ] Add a setting for the maximum page size
[ ] Implement run_view: input is a view ID, optional filters, and a page marker; output is rows and a marker for the next page
[ ] Return only the columns the view defines, with display labels
[ ] Return the view name and filters applied with the rows
[ ] Refuse views that depend on who is asking
[ ] State in the tool description that this tool only reads
[ ] Register the tool in server.py
[ ] Test the first page, the next page, the last page, the page cap, and filters
[ ] Add the tool to README.md

Done when: paging through a mocked result set returns every row exactly once.

PR 10: Audit logging and plain error messages

Goal: every tool call is recorded, and every failure is understandable.

Files created: src/dataverse_views_mcp/audit.py, tests/test_audit.py

Files edited: src/dataverse_views_mcp/server.py, every file in src/dataverse_views_mcp/tools/, src/dataverse_views_mcp/errors.py, src/dataverse_views_mcp/config.py, .env.example, README.md

[ ] In audit.py, write one structured log line per tool call: time, tool, view, filters, caller, and outcome
[ ] Never log record contents or credentials
[ ] Add a setting for where the log is written
[ ] Call the audit log from every tool, on success and on failure
[ ] Review each error path and make its message short and plain: missing view, view not approved, bad filter, permission denied, throttled
[ ] Test that each tool writes a log line, and that no log line contains row data
[ ] Document the log format in README.md

Done when: a run of all five tools produces five log lines with no record contents.

PR 11: Acceptance test (10-run consistency check)

Goal: prove the MVP against the PRD's acceptance test.

Files created: scripts/consistency_check.py, tests/integration/test_consistency.py

Files edited: README.md, Tasks.md

[ ] Write scripts/consistency_check.py: take a view ID and a filter from settings or arguments, call count_view 10 times, and report whether all results match
[ ] Add an optional expected value, and report whether the count equals it
[ ] Add tests/integration/test_consistency.py, skipped unless real credentials and a test view are configured
[ ] Run the check against the agreed view and office
[ ] Compare the result with the count shown in the app
[ ] Record the outcome in README.md in generic terms: pass or fail, and the number of runs. Do not record names or figures from the real system
[ ] If it fails, open follow-up tasks here before moving on

Done when: all 10 runs return the same number, and it equals the count in the app.

PR 12: Documentation and handoff

Goal: someone else can install, configure, run, and maintain the server.

Files created: none

Files edited: README.md, docs/PRD.md, Tasks.md, tool descriptions in src/dataverse_views_mcp/tools/

[ ] Finish README.md: purpose, setup, every setting, how to start the server, how to connect a local client, and each tool with an example
[ ] Document known limits: the count ceiling, page size, system views only, and views that depend on who is asking
[ ] Document how to add a view to the approved list
[ ] Refine tool descriptions based on what worked in local testing
[ ] Update docs/PRD.md with decisions made during the build
[ ] List next steps in README.md: per-user sign-in, hosting, and connecting to the Copilot Studio agent
[ ] Check the whole repository once more for anything specific to the organization
[ ] Mark this task list complete

Done when: a teammate can follow the README from a fresh clone to a working local server.

After the MVP (not tasked yet)

From the PRD's out-of-scope list, in likely order:

Per-user sign-in
Hosting in Azure, with secrets in a vault or a managed identity
Connecting to the Copilot Studio agent, and tuning it to prefer these tools
Counts past the Dataverse ceiling
Totals other than a count
Personal views