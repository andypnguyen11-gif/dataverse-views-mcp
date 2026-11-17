# Architecture: Dataverse Views MCP Server (MVP)

Dashed boxes and dashed lines mark things that come after the MVP.

## 1. Codebase and connections

```mermaid
flowchart TB
  subgraph CLIENTS["Clients"]
    LOCAL["Local MCP client<br/>MCP Inspector or Cursor<br/>MVP"]
    AGENT["Copilot Studio agent<br/>used from Teams<br/>after MVP"]
  end

  ENV[".env or environment variables<br/>address, IDs, secret, approved views"]

  subgraph SERVER["MCP server: dataverse_views_mcp (Python)"]
    ENTRY["server.py<br/>MCP SDK, registers tools"]
    CFG["config.py<br/>settings, read by every module"]
    subgraph TOOLS["tools/ (all read-only)"]
      T1["list_views"]
      T2["describe_view"]
      T3["find_record"]
      T4["count_view"]
      T5["run_view"]
    end
    VIEWS["views.py<br/>view lookup, approved list, cache"]
    FX["fetchxml.py<br/>read and edit view queries"]
    FIL["filters.py<br/>validate structured filters"]
    DC["dataverse_client.py<br/>read-only HTTP, paging, retry"]
    AUTH["auth.py<br/>token via MSAL"]
    AUD["audit.py<br/>one log line per call"]
    ERR["errors.py<br/>plain messages, used by every module"]
  end

  LOG[("Audit log")]

  subgraph MS["Microsoft cloud"]
    ENTRA["Microsoft Entra ID<br/>sign-in for the service identity"]
    DV[("Dataverse Web API<br/>tables and stored views")]
  end

  LOCAL -->|"stdio"| ENTRY
  AGENT -.->|"Streamable HTTP"| ENTRY
  ENV -->|"read at startup"| CFG
  ENTRY --> TOOLS
  TOOLS --> VIEWS
  TOOLS --> FX
  TOOLS --> DC
  TOOLS --> AUD
  FX --> FIL
  VIEWS --> DC
  DC --> AUTH
  AUTH -->|"token request"| ENTRA
  DC -->|"read-only HTTPS + FetchXML"| DV
  AUD -->|"writes"| LOG

  classDef later fill:#F3F3F3,stroke:#9A9A9A,color:#555555,stroke-dasharray: 4 3;
  classDef ext fill:#FFF4DC,stroke:#D98E04,color:#1F2A2E;
  class AGENT later;
  class ENTRA,DV ext;
```

## 2. Client and server interaction

```mermaid
sequenceDiagram
  autonumber
  actor U as User
  participant A as Agent or MCP client
  participant S as MCP server
  participant E as Entra ID
  participant D as Dataverse

  U->>A: How many records for office X?
  A->>S: find_record(table, "X")
  S->>E: Request token (first call only)
  E-->>S: Token
  S->>D: Read: search the name column
  D-->>S: Matching records
  S-->>A: One record, or candidates to choose from
  A->>S: list_views(table)
  S->>D: Read: system views for the table
  D-->>S: View names and IDs
  Note over S: Keep only approved views
  S-->>A: Approved views
  A->>S: count_view(view, filter: office = record ID)
  S->>D: Read: the view's stored query
  D-->>S: FetchXML
  Note over S: Validate filter, add it, convert to a distinct count
  S->>D: Read: count query
  D-->>S: One number
  Note over S: Write audit log line
  S-->>A: Count, view name, filters, last-modified date
  A-->>U: Answer, naming the view and filters used
```

## 3. Tooling and what comes later

```mermaid
flowchart TB
  subgraph DEV["Development and testing (MVP)"]
    GH["GitHub repo<br/>one pull request per task group"]
    CI["CI workflow<br/>lint and tests on every PR"]
    PYT["pytest + made-up fixtures<br/>unit tests, no network"]
    SMOKE["scripts/smoke_test.py<br/>one read call to confirm access"]
    CHECK["scripts/consistency_check.py<br/>count_view 10 times, compare"]
    INSP["MCP Inspector or Cursor<br/>call tools by hand"]
  end

  SERVER["MCP server<br/>dataverse_views_mcp"]

  subgraph LATER["After MVP"]
    AZ["Azure hosting<br/>Container Apps or Functions"]
    KV["Key Vault or managed identity<br/>replaces the local secret"]
    OBO["Per-user sign-in<br/>each request runs as the asker"]
    CS["Copilot Studio agent<br/>connects over Streamable HTTP"]
  end

  GH --> CI --> PYT
  PYT -.->|"tests"| SERVER
  SMOKE -.->|"checks access"| SERVER
  CHECK -.->|"acceptance test"| SERVER
  INSP -->|"stdio"| SERVER
  SERVER -.-> AZ
  SERVER -.-> KV
  SERVER -.-> OBO
  SERVER -.-> CS

  classDef later fill:#F3F3F3,stroke:#9A9A9A,color:#555555,stroke-dasharray: 4 3;
  class AZ,KV,OBO,CS later;
```