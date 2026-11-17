# Security

This server gives an AI agent read access to organizational data, and may be used by a
government organization. This document describes how it is built to be safe, what it
relies on, and what is still open.

It is not an authorization to operate. The mappings below show which controls the code
supports; your organization's own assessment decides whether that is sufficient.

## Reporting a vulnerability

Use GitHub's private vulnerability reporting on this repository (Security tab, "Report a
vulnerability"). Do not open a public issue for a security problem.

## What the server protects

| Asset | Threat | Protection |
| --- | --- | --- |
| Dataverse data | The agent, or text that manipulates it, changes or deletes data | The server can only read, enforced in two places (below) |
| Data outside the approved views | The agent reads data the data owner has not approved | Approved-view list, checked before any request; no free-form queries |
| Columns a view does not show | Filters are used to infer hidden values | Filters are limited to columns the view displays |
| Credentials and tokens | Leak through logs, errors, redirects, or the repository | Never logged or returned; redirects not followed; secrets only in settings |
| The agent's reasoning | Text in a record reads like an instruction | Results are labeled as data; text is cleaned and length-capped |
| Accountability | Access cannot be reconstructed afterwards | One audit line per call; a call that cannot be logged returns no data |

## Trust boundaries

1. **MCP client to server.** Tool arguments are written by a language model and are
   treated as hostile. Every argument is validated before use.
2. **Server to Dataverse.** The server is a client of Dataverse with a fixed, read-only
   identity. Dataverse's security role is the final boundary on what can be read.
3. **Dataverse to the model.** View names, descriptions, and row values were written by
   other people. They are untrusted when they reach the model.

## How each protection works

**Read-only, enforced twice.** The Dataverse client exposes only read methods. Separately,
a hook on the HTTP client inspects every outgoing request and refuses anything that is not
a GET, or a batch envelope holding exactly one GET (Dataverse requires a POST envelope for
queries too long for an address). The application user's security role should grant read
rights only, which makes it three layers.

**Approved views only.** A view ID is checked against `DATAVERSE_APPROVED_VIEW_IDS` before
any request is made. The refusal is the same whether or not the view exists, so the server
cannot be used to discover views. Quick find, lookup, and other internal view types are
refused.

**No query text from the model.** The model supplies structured filters: a column, an
operator from a fixed list of 14, and a value. The stored query is handled as a parsed XML
tree. Filter values are set as XML attribute values and text nodes, which the serializer
escapes, so a value cannot add elements or attributes. Table names and IDs must match
strict patterns before they are placed in a request address.

**Filters cannot probe hidden columns.** A column can be filtered only if the view
displays it, or an administrator has listed it. A column the view merely filters on is
not filterable, because a count combined with a "begins with" filter could otherwise
recover its values. Values must match the column's type, and a related record must be a
record ID.

**Safe XML parsing.** Stored queries are parsed with `defusedxml`, with DTDs, entities,
and external references disabled.

**Page markers cannot be read or forged.** A marker passes through the model, so it is
untrusted on return and must not be readable on the way. Each is encrypted and
authenticated with a key held only in memory, and bound to the view, filters, and page
size it was issued for.

**Nothing sensitive in errors.** Expected failures return a short, fixed message. Text
from Dataverse and from the sign-in service is never relayed. Unexpected failures return
a generic message, and only the exception's type is logged.

**Nothing sensitive in logs.** Tokens, secrets, and record contents are never logged. The
audit log records what was asked, not what was returned, and that includes calls rejected
before they reach a tool. Caller-supplied values are cleaned and bounded before logging,
so they cannot forge a log line or flood the log. What was asked includes filter values
and search text; `DATAVERSE_AUDIT_LOG_VALUES=false` replaces those with fingerprints.

**Settings cannot be planted.** A settings file is read only when `DATAVERSE_ENV_FILE`
names it, never from the folder the server starts in. Addresses must match a strict
`https://host` pattern.

**Bounded resource use.** Page size, match count, filter count, result size, caches,
retries, waits, and request timeouts are all capped.

**Transport.** HTTPS only, with certificate verification on. Redirects are never followed,
so a token cannot be carried to another host. On stdio, stdout carries only protocol
messages and all logging goes to stderr.

## OWASP Top 10:2025

| Category | How it is addressed |
| --- | --- |
| A01 Broken Access Control | Approved-view list; read-only role; no free-form queries; filterable columns limited to the view; `find_record` off unless tables are listed |
| A02 Security Misconfiguration | Settings validated at startup and fail closed; risky settings (`ALLOW_ALL_SYSTEM_VIEWS`) off by default and warned about |
| A03 Software Supply Chain Failures | Hash-pinned lockfile; `pip-audit` in CI; actions pinned by commit SHA; Dependabot; minimal dependencies |
| A04 Cryptographic Failures | HTTPS with verification; certificate credential supported; no secrets in code, logs, or errors; signed page markers |
| A05 Injection | Parsed-XML query building; strict patterns for names and IDs; wildcard escaping; hardened XML parser |
| A06 Insecure Design | Read-only by construction; views run as stored; threat model above; limits on every result |
| A07 Authentication Failures | MSAL client credentials to Entra ID; token never logged or forwarded. Inbound authentication is not applicable on stdio (see open items) |
| A08 Software or Data Integrity Failures | Pinned CI; locked dependencies; stored view definitions treated as data and validated before use |
| A09 Security Logging and Alerting Failures | One audit line per call with who, what, when, where, and outcome; fails closed. Alerting is the operator's responsibility |
| A10 Mishandling of Exceptional Conditions | Every Dataverse failure mapped to a plain error; throttling honored; unexpected errors reveal nothing |

## OWASP MCP Top 10 (beta)

| Risk | How it is addressed |
| --- | --- |
| MCP01 Token Mismanagement and Secret Exposure | Secrets only in settings; tokens held in memory by MSAL; never logged or returned |
| MCP02 Privilege Escalation via Scope Creep | Five fixed read-only tools; scope is the single environment; no write tool can be added by configuration |
| MCP03 Tool Poisoning | Tool names and descriptions are fixed in code, not loaded from data |
| MCP04 Supply Chain Attacks and Dependency Tampering | As A03 above |
| MCP05 Command Injection and Execution | The server runs no shell commands and evaluates no caller input |
| MCP06 Prompt Injection via Contextual Payloads | Results state that values are data; text is cleaned and capped. The agent must also treat tool results as data |
| MCP07 Insufficient Authentication and Authorization | Local stdio only for the MVP; see open items for hosting |
| MCP08 Lack of Audit and Telemetry | Audit log as above |
| MCP09 Shadow MCP Servers | Out of the server's control; register it in your MCP inventory |
| MCP10 Context Injection and Over-Sharing | Only the view's own columns are returned; pages are capped; no data is cached between calls |

## NIST SP 800-53 Rev. 5 controls the code supports

| Control | Support in this server |
| --- | --- |
| AC-3 Access Enforcement | Approved-view list and filter-column rules enforced on every call |
| AC-6 Least Privilege | Read-only by construction; intended to run with a read-only Dataverse role |
| AU-2, AU-3 Event Logging and Content | Every tool call logged with type, time, where (environment, server), source (client, OS user, service identity), outcome, and the view and filters involved |
| AU-5 Response to Audit Failures | A call whose audit line cannot be written returns no data |
| AU-9 Protection of Audit Information | Log file created owner-only and written append-only. Forwarding and retention are the operator's |
| IA-5 Authenticator Management | Certificate credential supported; no authenticators in the repository |
| IA-9 Service Identification and Authentication | The server authenticates to Entra ID as a named application |
| SC-8 Transmission Confidentiality and Integrity | HTTPS only, certificates verified, redirects refused |
| SC-13 Cryptographic Protection | TLS and signing are provided by the platform's OpenSSL through Python. See the FIPS note below |
| SI-10 Information Input Validation | Every tool argument and setting is validated |
| SI-11 Error Handling | Errors reveal no internal detail |
| SA-11, SR-3 Developer Testing and Supply Chain | Tests, static analysis, dependency audit, and pinned dependencies in CI |

**FIPS note.** This project contains no cryptographic implementation of its own. Whether
the cryptography in use is FIPS 140 validated depends on the Python build and operating
system it runs on. Where FIPS is required, run it on a FIPS-enabled host. Page markers
(AES-128-CBC with HMAC-SHA-256, via Fernet) and MSAL certificate sign-in both use the
`cryptography` package, which is backed by OpenSSL.

## Open items before production use

These are known and deliberate limits of the MVP. Resolve each before the server is
hosted or used on sensitive data.

1. **Per-user identity.** The MVP signs in as one service identity, so Dataverse's
   per-user permissions do not apply and the audit log cannot name the person who asked.
   Use it only on data every user of the agent may see, until per-user sign-in is built.
2. **Inbound authentication.** Over stdio, the operating system limits access to the
   local user. A hosted (Streamable HTTP) deployment must authenticate every request,
   validate that each token was issued for this server, validate the `Origin` header, and
   must never pass a caller's token through to Dataverse unchanged.
3. **Credential storage.** `.env` is for local testing. In hosting, use a managed
   identity or Key Vault.
4. **Rate limiting per caller.** The server bounds each call but does not limit how many
   calls a client makes. Dataverse's own service protection limits apply. Add a limit at
   the hosting layer.
5. **Audit log handling.** Forward the log to your logging system and set retention and
   alerting there.
6. **Acceptance test.** Not yet run against a real environment.
7. **Behaviour not yet confirmed against real Dataverse.** The query handling was checked
   against Microsoft's documentation and with tests that fake Dataverse. These points
   should be confirmed in the first real run: the names Dataverse gives joined columns in
   results, a filter on a joined table across an outer join, and the batch form used for
   very long queries.
8. **Independent assessment.** The control mappings here are the developer's own. One
   independent code review was done during the build (see `Tasks.md`); it is not a
   substitute for your organization's assessment.
