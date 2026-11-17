# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project status

This repository is greenfield: when this file was written it contained no code, and no language, framework, or tooling had been chosen. The project is an MCP (Model Context Protocol) server that exposes Microsoft Dataverse views — system views (`savedquery`) and personal views (`userquery`), each defined by `fetchxml` and `layoutxml` — to MCP clients.

When the stack is chosen and scaffolded, update this file in the same PR with:

- the build, lint, type-check, and test commands, including how to run a single test
- the actual architecture, once it differs from or refines the intended one below

Do not add commands or structure here that do not exist in the repo.

## Non-negotiable requirements

Every change must keep the app **scalable, secure, and maintainable**, and every task or PR must ship with tests. The sections below define what those mean for this project.

### Testing (required for every task and PR)

- No task or PR is complete without tests covering the behavior it adds or changes. A bug fix starts with a test that fails for that bug.
- Write the test first, watch it fail, then implement.
- Run the full test suite, lint, and type-check before claiming work is done, and report the actual output.
- Unit tests must not touch a real Dataverse environment. Put the Dataverse Web API behind a client interface and fake it at that boundary.
- Each MCP tool needs tests for: the success path, input validation rejections, Dataverse error responses (401, 403, 404, 429, 5xx), and paging where it applies.
- Tests that hit a live environment are a separate, opt-in suite driven by environment variables, never part of the default run.
- Do not weaken, skip, or delete a failing test to get a green run. Fix the code, or raise the conflict with the user.

### Security

- Authenticate to Dataverse through Microsoft Entra ID. Secrets, client credentials, and tokens come from the environment or a secret store, never from source, fixtures, or committed config.
- Never log tokens, authorization headers, connection strings, or full record payloads.
- Validate every tool input against a schema before use. Treat all tool arguments as untrusted, since they originate from an LLM.
- Never build FetchXML, OData filters, or URLs by string concatenation of caller input. Use a builder or escape per the target syntax, and validate GUIDs and logical names against a strict pattern.
- Request the least privilege needed. Read-only tools must not be able to write. Any tool that creates, modifies, or deletes a view must be clearly named and described as such and declare the appropriate MCP tool annotations.
- Return sanitized errors to the MCP client: no stack traces, tenant details, or internal URLs.
- On stdio transport, stdout carries only JSON-RPC protocol messages. All logging goes to stderr.

### Scalability

- Keep the server stateless per request. Anything cached (tokens, entity metadata) must be bounded, have an expiry, and be safe under concurrent requests.
- Honor Dataverse service protection limits: on 429, respect `Retry-After`, with bounded retries and backoff. Do not retry non-idempotent writes blindly.
- Always page results and cap page size. No tool may return an unbounded result set. Select only the columns needed.
- Set a timeout on every outbound call and propagate cancellation from the MCP request.
- Reuse one HTTP client or connection pool rather than creating one per request.

### Maintainability

- Keep three layers separate, with dependencies pointing inward:
  1. MCP layer: tool definitions, input schemas, and result formatting
  2. Domain layer: view logic (listing, resolving, parsing `fetchxml` and `layoutxml`, executing a view)
  3. Dataverse client: authentication, HTTP, retries, paging
- One tool per file or module, with its schema and tests next to it. Adding a tool should not require editing other tools.
- Use strict typing and no untyped escape hatches at module boundaries.
- Configuration is read and validated once at startup, failing fast with a clear message when something is missing.
- Add a dependency only when it clearly earns its place, and pin versions.
- Tool names, descriptions, and input schemas are a public contract with MCP clients. Treat changes to them as breaking changes.
