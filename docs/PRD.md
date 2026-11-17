PRD: Dataverse Views MCP Server (MVP)
Nov 16, 2026 · @Andy
A small read-only MCP server that lets an AI agent use the views stored in Dataverse for a Dynamics 365 app. The agent can list the views, read their definitions, read the rows they return, and get counts from them. This covers views the app shows to staff and views it does not show. Answers then come from definitions the organization already maintains, not from queries the agent writes. Today the agent writes a new query for every question and guesses at columns, so the same count question returns different numbers across runs (100, 85, 90 in one case). The MVP is done when a single-office count question returns the same number 10 runs out of 10 and that number matches the app.
Users and user stories
Five kinds of users touch this server. The first two never see it; they see its effect through the agent.
User
Story
We know it works when
Business user asking the agent questions
As a business user, I want a count for one office to be the same every time I ask, so I can use it in an official response.
The same question returns the same number on 10 consecutive runs.
Business user
As a business user, I want to see which view and filters produced a number, so I can check it against the app.
Every answer names the view and the filters used.
Business user
As a business user, I want the agent to ask me which office I mean when a name matches more than one, so I do not get a count for the wrong office.
An ambiguous name returns the candidate records, not a count.
Data owner who vouches for the numbers
As the data owner, I want the agent's counts to come only from views I have approved, whether or not the app displays them, so there is one version of the truth.
The agent uses only approved views, and each count equals the result of that view.
App owner who administers the Dynamics app
As the app owner, I want the server to be read-only and to need no changes to my app, so it cannot alter or break the data.
The server has no write tools, and its identity holds a read-only role.
Agent builder (the team maintaining the Copilot Studio agent)
As the agent builder, I want a small set of clearly described tools, so the agent picks the right one without me writing a query per question.
The agent calls count_view for count questions without extra prompting.
Agent builder
As the agent builder, I want new or changed views in the app to be available without code changes, so upkeep stays low.
A view added in the app appears in list_views on the next call.
Developer building and running the server
As the developer, I want all environment details in configuration, so the code holds nothing specific to the organization.
The repository contains no URLs, tenant IDs, table names, or secrets.
Developer
As the developer, I want an automated test for the single-office count, so I can prove the fix and catch regressions.
One command runs the 10-run consistency check and reports pass or fail.
Security or IT reviewer
As a reviewer, I want every tool call logged with who asked and what ran, so access to the data can be audited.
Each call writes a log line with time, tool, view, filters, and caller, and no record contents.
MVP features
The MVP is five read-only tools plus the rules every tool follows. It runs on a work machine against a local MCP client; hosting comes later.
Tools
Tool
Input
Returns
Notes
list_views
Table name
View names, IDs, and descriptions for that table
Approved system views only, including ones the app does not display
describe_view
View ID
The view's columns, filters, sort order, and joined tables, in plain terms
Lets the agent choose a view and explain it
count_view
View ID, optional filters
One number, computed by Dataverse
The core of the consistency fix
run_view
View ID, optional filters, page
Rows from the view, one page at a time, with a marker for the next page
Capped page size so results stay small
find_record
Table name, search text
Matching records with ID and display name
Zero, one, or several matches are all valid results
Rules for every tool
• Read-only. No tool creates, updates, or deletes anything.
• Views run as stored. The server reads the view's saved query from Dataverse. It never asks the model to write a query. Only views on an approved list, kept in settings, are available.
• Filters are structured. An extra filter is a column, an operator from a fixed list, and a value. The server checks each one before adding it to the query.
• Answers show their work. Each result includes the view name and the filters applied.
• Names resolve to IDs. Filters on a related record, such as an office, use the record ID from find_record, never free text.
• Configuration only. The environment address and credentials come from settings. Nothing specific to the organization is in the code.
• Every call is logged. Time, tool, view, filters, and caller. No record contents.
• Errors are plain. A missing view, a bad filter, or a permission problem returns a short message the agent can relay.
Acceptance test
1.  Find the view staff use for the record type in question.
2.  Resolve the office name to one office record with find_record.
3.  Call count_view with that office as a filter, 10 times.
4.  Pass when all 10 results are identical and equal the count shown for the same view and office in the app.
Tech stack
The existing stack stays as it is: the Copilot Studio agent, Teams, and the Dynamics app on Dataverse. This project adds one small service beside the agent. Choices marked "your call" are covered in the next section.
Layer
Recommended
Why
Alternatives
Language
Python 3.12 (your call)
Fits your retrieval and eval work; mature MCP support
TypeScript; C# if the team prefers .NET
MCP framework
Official MCP SDK for the chosen language
Handles the protocol, so the code is plain functions
None worth the extra work
Transport
studio for local testing; Streamable HTTP when hosted
Local clients use stdio; a cloud agent needs a web address
None
Dataverse access
Dataverse Web API over HTTPS, with FetchXML
Runs views as stored and supports counts in Dataverse
The .NET Dataverse SDK, only if C# is chosen
Sign-in
Microsoft's MSAL library, service identity for the MVP
Simplest to stand up for a proof
Per-user sign-in, planned for after the MVP
Dataverse identity
App registration plus a Dataverse application user with a read-only role
Standard way for a service to reach Dataverse
Managed identity once hosted in Azure
Query editing
An XML parser to read and modify the view's FetchXML
Text replacement breaks on joins and nested filters
None
Configuration
Environment variables, with a local settings file excluded from Git
Keeps environment details out of the code
None
Tests
pytest (or the language's standard), with made-up view definitions as fixtures
Unit tests run without touching real data
None
Local test client
MCP Inspector, or the MCP support in Cursor
Exercise the tools before any agent is involved
VS Code
Repository
Private repo in the organization's GitHub, from the team's standard template
Avoids ownership and policy questions about a personal public repo
Public later, if the organization agrees
Hosting (after MVP)
A small Azure service such as Container Apps or Functions
The organization already uses Azure (to confirm), so hosting, secrets, and identity stay in one place
Pick the service the Azure team already supports
Pitfalls and decisions
Five choices still need your call before or during the build. The size of the MVP is decided: all five tools. The pitfalls after them are the risks I would plan around.
Decisions for you
Decision
Options
My lean
Why it matters
Language
Python, TypeScript, C#
Python
C# is worth a look if developers who know .NET will maintain this, since Microsoft's Dataverse library is strongest there.
Size of the MVP
Five tools, or three (drop describe_view and run_view)
Five
You want the agent to read views, not only count them. Decided: build all five.
Identity for the MVP
One service identity, or each user's own identity
Service identity, on non-sensitive data only
Per-user sign-in is the hardest part of the build. It should be in place before real use.
Where the code lives
Organization's private repo, or personal public repo
Organization's private repo
Work done for an employer's system is likely the employer's property, and a public repo risks leaking system details.
Dataverse MCP server
Keep it beside this server, or remove it from the agent
Keep it, limited to read tools
It is useful for open-ended questions, but it also exposes tools that change and delete data.
Azure hosting service
Container Apps, Functions, or another
Defer until the MVP passes
Depends on what the Azure team supports.
Pitfalls: Dataverse and views
• Joins can inflate counts. A view that joins to a table with many matches returns one row per match. The count must be of distinct records, and checked against the app.
• Counts have a ceiling. Dataverse limits how many records one count can span. An older source puts it at 50,000; the current figure needs confirming. Past it, split the count by date range and add the parts.
• Rows come back 5,000 at a time. run_view needs proper paging, and a page cap so results stay small.
• Some views depend on who is asking. "My records" views return the service identity's records, which is wrong for every real user. Flag these in describe_view and leave them out of the MVP.
• Two kinds of views. System views and personal views are stored separately. The MVP covers system views only. Some system views are also hidden from the app, so the server can read views that staff never see. Which views the agent may use should be agreed with the data owner and kept as a setting. A hidden view has no screen to compare against, so the data owner confirms its count instead.
• Views become a dependency. If the app owner edits a view, the agent's answers change. Return the view's last-modified date with each answer, and tell the app owner the views are now in use.
• Not every question has a view. When none fits, the agent should say so and not fall back to guessing.
• Stored values are not display values. Status and type columns hold codes. Request the display labels, or filters and results will be unreadable.
• Long queries can exceed address limits. A view with many columns and joins may be too long to send in a web address. Use the API's batch form for those.
• Rate limits. Dataverse throttles heavy callers. Honor its retry signal instead of retrying immediately.
Pitfalls: sign-in and security
• A service identity ignores per-user permissions. Everyone gets the same data through it. Limit its role to the tables the views need.
• Cloud environment. If the tenant is on a national or sovereign Microsoft cloud, the sign-in and API addresses differ. Keep both as settings, and confirm which cloud applies.
• Access depends on the app owner. Someone with admin rights on the Dataverse environment must create the identity and its read-only role. Ask early.
• Secrets. None in the repo. Use Azure Key Vault or a managed identity once hosted.
• Logs. Record what was asked and which view ran, never the records returned.
• Record text is untrusted. Rows returned by run_view may contain text that reads like instructions. The agent should treat tool results as data.
Pitfalls: MCP and the agent
• The agent may ignore the new tools. It can keep writing its own query through the Dataverse MCP server. Tool descriptions and an instruction to use count_view for counts are the fix, and the 10-run test verifies it.
• Connecting to Copilot Studio is unverified. Whether the environment allows a custom MCP server, which transport and sign-in it requires, and whether data policies block it all need confirming before hosting work starts.
• Large results crowd the model's context. Cap page sizes and return only the view's columns.
• Too many views. List by table, and cache view definitions briefly so each question does not re-read them.
Pitfalls: the project
• Custom code was the team's first concern. Keep the server small, tested, and documented so it does not become the maintenance burden they want to avoid.
• Scope creep toward free-form queries. A tool that runs any query brings back the original problem. Hold the line at views.
Out of scope
The first list is what the MVP leaves out by design. The second is what this first phase will not spend effort on, even where the MVP touches it.
Not in the MVP
• Any write to Dataverse. Never in scope, in this phase or later.
• Free-form queries. No tool that runs SQL or query text written by the model.
• Per-user sign-in. Planned as the next step after the MVP passes.
• Hosting in Azure and connecting to the Copilot Studio agent. The MVP runs locally against a test client.
• Personal views. System views only.
• Totals other than a count. Sums, averages, and group-by breakdowns come later.
• File export. No CSV or JSON download of large results.
• Trend and recurring-issue analysis. A later phase, where Power BI or Fabric may fit.
• Search over notes and narrative text. A separate retrieval problem.
• Changes to the source app, including new views, column descriptions, or search settings.
• Synonyms and glossary entries in Copilot Studio.
Not a focus for this first phase
• Coverage. One table and one question first: the count of one record type for one office. Other views follow once that passes.
• Performance. Correct and repeatable comes before fast. Caching is optional.
• Very large counts. Handling totals past Dataverse's count ceiling waits until a real question needs it.
• Polish. Error messages and documentation stay minimal: a README and clear tool descriptions.
• Agent tuning. Getting the Copilot Studio agent to prefer these tools is tested after the server is proven.
• Approvals for production. Security review, hosting requests, and data policy checks start once there is a result to show.
• Reuse by other agents or teams. The design allows it, but nothing is built for it yet.
Decisions made during the build (2026-11-16)
These record what was built. Items marked "your call" earlier in this document are resolved as follows.
• Language: Python 3.12 or later, as recommended. Tested on 3.12, 3.13, and 3.14.
• Size of the MVP: all five tools.
• Identity for the MVP: one service identity, with a client secret or a certificate. Per-user sign-in remains the next step.
• Where the code lives: a public repository in a personal GitHub account, at the owner's request. This differs from this document's recommendation of the organization's private GitHub. The repository holds no URLs, tenant IDs, table names, view names, or secrets. Confirm with the organization that a public personal repository is acceptable, or move it.
• Count ceiling: confirmed at 50,000 records against current Microsoft documentation. Past it, count_view returns a clear error.
• Filters: limited to columns the view displays, plus any an administrator lists. This was added so filters cannot be used to learn about columns an approved view does not show. If the approved views do not display the office column, the administrator lists it so counts can be filtered by office.
• find_record: off until an administrator lists the tables it may search.
• Audit log: also records the environment, the operating-system user, and the outcome. A call that cannot be logged returns no data.
• Long queries: sent in the API's batch form when too long for a web address, as planned.
• Views not supported: views that depend on who is asking, views limited to a fixed number of top rows, views that already total their rows, and views that select every column.
• Joined views: count_view counts each record once. run_view returns one row per match and says so.
• Security review: an independent review during the build found 11 gaps, all fixed. See Tasks.md.
• Copilot Studio: research during the build found it connects to custom MCP servers over Streamable HTTP only, through a Power Platform connector, and drops tools whose input schemas use certain constructs. The tool schemas were built to avoid those. Whether custom MCP servers are available in US government clouds could not be confirmed from Microsoft documentation and still needs checking.
• Acceptance test: built, not yet run against a real environment.
Sources
Limits quoted in this document come from the pages below and from search summaries of them. Confirm each against current Microsoft documentation before relying on it.
• Dataverse MCP server: understanding the new tool shape (Microsoft)
• View (SavedQuery) table reference (Microsoft Learn)
• Saved queries (Microsoft Learn)
• Join tables using FetchXML (Microsoft Learn)
• Page results using FetchXML (Microsoft Learn)
• Use lists of rows in flows (Microsoft Learn)
• Limitations with CRM FetchXML (KingswaySoft, 2013)