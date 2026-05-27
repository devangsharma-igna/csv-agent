# CSV Agent — How It Works
### Architecture & Flow Diagrams
> Prepared for Executive Presentation · Techno-Functional Audience

---

## Contents

1. [What Is IGNA CSV Agent?](#1-what-is-igna-csv-agent)
2. [System Components at a Glance](#2-system-components-at-a-glance)
3. [First-Time Setup vs Every-Day Use](#3-first-time-setup-vs-every-day-use)
4. [Full Query Pipeline - Step by Step](#4-full-query-pipeline---step-by-step)
5. [How the System Learns Your Table Once](#5-how-the-system-learns-your-table-once)
6. [Four Layers of Protection Against Bad Answers](#6-four-layers-of-protection-against-bad-answers)
7. [Circuit Breaker - What Happens if the Table Is Deleted](#7-circuit-breaker---what-happens-if-the-table-is-deleted)
8. [How Your Answer Is Shown](#8-how-your-answer-is-shown)
9. [Component Map - What Each Part Does](#9-component-map---what-each-part-does)

---

## 1. What Is IGNA CSV Agent?

IGNA CSV Agent lets a business user ask questions about their data **in plain English** - no SQL knowledge needed. It connects to a database table, understands the structure automatically, and returns answers as readable text, charts, or both.

```mermaid
flowchart LR
    USER["Business User"]
    APP["IGNA CSV Agent"]
    AI["AI Model - Azure OpenAI"]
    DB["Database"]
    OUT["Answer - Text and Charts"]

    USER -->|"Ask in plain English"| APP
    APP -->|"Generate safe SQL"| AI
    AI -->|"Return SQL"| APP
    APP -->|"Run query"| DB
    DB -->|"Return rows"| APP
    APP -->|"Plain-English answer"| OUT
    OUT --> USER

    style USER fill:#6366f1,color:#fff,stroke:#6366f1
    style APP fill:#0ea5e9,color:#fff,stroke:#0ea5e9
    style AI fill:#8b5cf6,color:#fff,stroke:#8b5cf6
    style DB fill:#10b981,color:#fff,stroke:#10b981
    style OUT fill:#f59e0b,color:#fff,stroke:#f59e0b
```

---

## 2. System Components at a Glance

Six specialised modules each own a single responsibility.

```mermaid
flowchart TB
    subgraph UI["User Interface"]
        WEBAPP["Web App"]
    end

    subgraph AGENTS["AI Agents"]
        CB["Context Builder"]
        GR["Scope Guardrail"]
        NLP["Question Parser"]
        VAL["SQL Validator"]
        EXE["Query Executor"]
        NLR["Answer Writer"]
    end

    subgraph MEMORY["Memory"]
        CTX["Table Memory - context.json"]
    end

    subgraph EXTERNAL["External Services"]
        AIAPI["Azure OpenAI GPT-4"]
        SUPA["PostgreSQL"]
    end

    WEBAPP --> CB
    WEBAPP --> GR
    WEBAPP --> NLP
    WEBAPP --> VAL
    WEBAPP --> EXE
    WEBAPP --> NLR
    CB --> AIAPI
    NLP --> AIAPI
    GR --> AIAPI
    NLR --> AIAPI
    CB --> SUPA
    EXE --> SUPA
    CB --> CTX
    NLP --> CTX
    VAL --> CTX

    style UI fill:#e0f2fe,stroke:#0ea5e9
    style AGENTS fill:#f3e8ff,stroke:#8b5cf6
    style MEMORY fill:#fef9c3,stroke:#ca8a04
    style EXTERNAL fill:#dcfce7,stroke:#16a34a
```

---

## 3. First-Time Setup vs Every-Day Use

The system does heavy learning **only once per table**. Every subsequent query skips straight to answering.

```mermaid
flowchart TD
    START(["User opens the app"])
    START --> Q1{"Table already configured?"}

    Q1 -->|"No - first visit"| SETUP
    Q1 -->|"Yes - returning"| Q2

    subgraph SETUP["One-Time Table Setup"]
        S1["User enters the table name"]
        S2["System confirms table exists"]
        S3["Table name saved"]
        S1 --> S2 --> S3
    end

    SETUP --> Q2

    Q2{"Schema and description cached?"}
    Q2 -->|"No - first query or after Rebuild"| LEARN
    Q2 -->|"Yes - use cache"| QUERY

    subgraph LEARN["One-Time Context Build"]
        L1["Fetch column names and types"]
        L2["Fetch 5 sample rows"]
        L3["AI writes plain-English description"]
        L4["Save to Table Memory"]
        L1 --> L2 --> L3 --> L4
    end

    LEARN --> QUERY

    subgraph QUERY["Every Query - Fast Path"]
        Q3["Load cached schema and description"]
        Q4["Run guardrail, parse, validate, execute"]
        Q5["Return answer to user"]
        Q3 --> Q4 --> Q5
    end

    style LEARN fill:#fef3c7,stroke:#d97706
    style QUERY fill:#dcfce7,stroke:#16a34a
    style SETUP fill:#e0f2fe,stroke:#0ea5e9
```

---

## 4. Full Query Pipeline - Step by Step

Every question travels through five sequential checkpoints. Any checkpoint can stop the pipeline early.

```mermaid
flowchart TD
    INPUT(["User submits a question"])
    INPUT --> STEP0

    STEP0{"Step 1 - Circuit Breaker: Table exists?"}
    STEP0 -->|"Table missing"| ERR0(["Error - Table not found"])
    STEP0 -->|"Table confirmed"| STEP1

    STEP1{"Step 2 - Context: Description cached?"}
    STEP1 -->|"Cached - skip"| STEP2
    STEP1 -->|"Not yet - build now"| BUILD["Build Context: Fetch schema and summary"]
    BUILD --> STEP2

    STEP2{"Step 3 - Scope Guardrail: In scope?"}
    STEP2 -->|"Off-topic"| ERR2(["Query Out of Scope - reason returned"])
    STEP2 -->|"Relevant"| STEP3

    STEP3["Step 4 - Question Parser: English to SQL"]
    STEP3 --> STEP4

    STEP4{"Step 5 - SQL Validator: Columns valid?"}
    STEP4 -->|"All columns valid"| STEP5
    STEP4 -->|"Unknown column - retry up to 2x"| STEP3

    STEP5["Step 6 - Executor: Run SQL on database"]
    STEP5 --> STEPDB

    STEPDB{"Table still exists?"}
    STEPDB -->|"Table gone - 42P01"| ERR5(["Circuit Breaker - config cleared"])
    STEPDB -->|"Rows returned"| STEP6

    STEP6["Step 7 - Format Answer: Text and charts"]
    STEP6 --> OUTPUT(["Answer shown to user"])

    style ERR0 fill:#fee2e2,stroke:#dc2626,color:#7f1d1d
    style ERR2 fill:#fef9c3,stroke:#ca8a04,color:#78350f
    style ERR5 fill:#fee2e2,stroke:#dc2626,color:#7f1d1d
    style OUTPUT fill:#dcfce7,stroke:#16a34a,color:#14532d
    style BUILD fill:#fef3c7,stroke:#d97706
    style STEP3 fill:#f3e8ff,stroke:#7c3aed
    style STEP6 fill:#f3e8ff,stroke:#7c3aed
```

---

## 5. How the System Learns Your Table Once

The Context Build happens once, saves everything, and is reused on every query until you ask for a rebuild.

```mermaid
sequenceDiagram
    participant User
    participant App as Web App
    participant AI as AI Model
    participant DB as Database
    participant Mem as Table Memory

    User->>App: Runs first query on a new table
    App->>Mem: Is schema already saved?
    Mem-->>App: Not found

    App->>DB: SELECT column names and types FROM information_schema
    DB-->>App: Column names and data types

    App->>DB: SELECT * FROM table LIMIT 5
    DB-->>App: 5 sample rows

    App->>AI: Here are the columns and samples. Write a plain-English summary.
    AI-->>App: This table contains support tickets with status, priority, assignee...

    App->>Mem: Save columns and summary to context.json
    App-->>User: Context ready - processing your question now
```

---

## 6. Four Layers of Protection Against Bad Answers

Four independent checkpoints prevent hallucinated column names and off-topic answers.

```mermaid
flowchart TD
    Q(["Question enters the pipeline"])
    Q --> L1

    subgraph L1["Layer 1 - Scope Guardrail"]
        G1["AI checks question against table description and column list"]
        G2{"Answerable with this table?"}
        G1 --> G2
    end

    G2 -->|"No - stops here"| BLOCK1(["Query Out of Scope - user told why"])
    G2 -->|"Yes"| L2

    subgraph L2["Layer 2 - Column-Constrained SQL Generation"]
        P1["AI receives question plus full list of valid column names"]
        P2["AI instructed: use ONLY columns in this list"]
        P3["SQL generated referencing only real columns"]
        P1 --> P2 --> P3
    end

    P3 --> L3

    subgraph L3["Layer 3 - SQL Validator - Hard Check"]
        V1["Every column name in SQL checked against schema"]
        V2{"Unknown column names?"}
        V1 --> V2
    end

    V2 -->|"All columns real"| L4
    V2 -->|"Unknown column - retry up to 2 times"| L2
    V2 -->|"Still invalid after 2 retries"| BLOCK3(["Cannot answer safely - user asked to rephrase"])

    subgraph L4["Layer 4 - Live Execution"]
        E1["Validated SQL runs on real database"]
        E2{"Successful?"}
        E1 --> E2
    end

    E2 -->|"Rows returned"| SUCCESS(["Answer delivered"])
    E2 -->|"Table gone mid-execution"| BLOCK4(["Circuit Breaker - config cleared"])

    style BLOCK1 fill:#fef9c3,stroke:#ca8a04,color:#78350f
    style BLOCK3 fill:#fee2e2,stroke:#dc2626,color:#7f1d1d
    style BLOCK4 fill:#fee2e2,stroke:#dc2626,color:#7f1d1d
    style SUCCESS fill:#dcfce7,stroke:#16a34a,color:#14532d
    style L1 fill:#fef9c3,stroke:#ca8a04
    style L2 fill:#f3e8ff,stroke:#7c3aed
    style L3 fill:#e0f2fe,stroke:#0ea5e9
    style L4 fill:#dcfce7,stroke:#16a34a
```

---

## 7. Circuit Breaker - What Happens if the Table Is Deleted

If a table is deleted at any point, the system detects it instantly and stops cleanly.

```mermaid
flowchart TD
    subgraph BEFORE["Scenario A - Table deleted BEFORE query runs"]
        B1["User submits question"]
        B2["Check table exists via information_schema"]
        B3{"Table found?"}
        B4(["Stopped - user told to re-enter table"])
        B5["Continue to answer"]
        B1 --> B2 --> B3
        B3 -->|"No"| B4
        B3 -->|"Yes"| B5
    end

    subgraph DURING["Scenario B - Table deleted WHILE query is running"]
        D1["SQL reaches the database"]
        D2{"PostgreSQL returns error 42P01"}
        D3["System detects the specific error"]
        D4["TableNotFoundError raised"]
        D5["Table config wiped from memory"]
        D6(["User sees clear error - redirected to setup"])
        D1 --> D2 --> D3 --> D4 --> D5 --> D6
    end

    subgraph GUARANTEE["Guarantees"]
        G1["No partial or misleading answer shown"]
        G2["Stale config cleared automatically"]
        G3["User guided to re-enter a valid table"]
        G4["No polling delay - fires on the exact failing query"]
    end

    style BEFORE fill:#fef9c3,stroke:#ca8a04
    style DURING fill:#fee2e2,stroke:#dc2626
    style GUARANTEE fill:#dcfce7,stroke:#16a34a
    style B4 fill:#fee2e2,stroke:#dc2626,color:#7f1d1d
    style D6 fill:#fee2e2,stroke:#dc2626,color:#7f1d1d
```

---

## 8. How Your Answer Is Shown

Every query returns three things: raw data, a plain-English explanation, and auto-generated charts.

```mermaid
flowchart TD
    RESULT["Query result returned from database"]

    RESULT --> RAW
    RESULT --> NL
    RESULT --> FIG

    subgraph RAW["Raw Data Table"]
        R1["All result rows in a scrollable table"]
    end

    subgraph NL["Plain-English Answer"]
        NL1["AI reads the rows and writes a concise summary"]
        NL2["Example: The highest-rated restaurant is Sukkubhai Biriyani at 4.4"]
        NL1 --> NL2
    end

    subgraph FIG["Auto-Generated Chart"]
        FIG1["Chart type auto-detected from column types"]
        FIG2["Categorical and numeric = Bar chart"]
        FIG3["Date and numeric = Line chart"]
        FIG4["Two numeric columns = Scatter"]
        FIG1 --> FIG2
        FIG1 --> FIG3
        FIG1 --> FIG4
    end

    style RAW fill:#e0f2fe,stroke:#0284c7
    style NL fill:#f3e8ff,stroke:#7c3aed
    style FIG fill:#dcfce7,stroke:#16a34a
    style RESULT fill:#fef3c7,stroke:#d97706
```

---

## 9. Component Map - What Each Part Does

Every file has exactly one responsibility.

```mermaid
flowchart TB
    subgraph UI["User Interface"]
        WEB["Web App - handles user interaction and orchestrates pipeline"]
    end

    subgraph AGLAYER["AI Agent Layer - agents/"]
        CB["context_builder.py"]
        GR["guardrail.py"]
        NLP["nl_parser.py"]
        VAL["sql_validator.py"]
        EXE["executor.py"]
        NLR["nl_responder.py"]
    end

    subgraph UTILS["Utilities - utils/"]
        MCP["mcp_client.py"]
        LLM["llm_client.py"]
        CTX["context_io.py"]
        ROW["row_parser.py"]
        FIG["figure_builder.py"]
    end

    subgraph STORE["Persistent State"]
        JSON["context.json"]
    end

    subgraph EXT["External Services"]
        OPENAI["Azure OpenAI GPT-4"]
        SUPABASE["PostgreSQL"]
    end

    WEB --> CB
    WEB --> GR
    WEB --> NLP
    WEB --> VAL
    WEB --> EXE
    WEB --> NLR
    WEB --> FIG
    CB --> MCP
    CB --> LLM
    CB --> CTX
    GR --> LLM
    NLP --> LLM
    NLP --> CTX
    VAL --> CTX
    EXE --> MCP
    EXE --> ROW
    NLR --> LLM
    MCP --> DATABASE
    LLM --> OPENAI
    CTX --> JSON
    JSON --> CTX

    style UI fill:#e0f2fe,stroke:#0284c7
    style AGLAYER fill:#f3e8ff,stroke:#7c3aed
    style UTILS fill:#fef9c3,stroke:#ca8a04
    style STORE fill:#f1f5f9,stroke:#64748b
    style EXT fill:#dcfce7,stroke:#16a34a
```

---

## Summary - Key Design Decisions

| Decision | Why |
|---|---|
| **Context built once, cached forever** | Avoids repeated schema lookups on every query. Rebuild is user-controlled. |
| **Guardrail checks column list, not just topic** | Stops questions about data that does not exist before any SQL is generated. |
| **SQL validator is a hard rule-based check** | Not probabilistic - every column name is checked against a known list. AI cannot bypass it. |
| **TableNotFoundError propagates uncaught** | The pipeline crashes instantly and cleanly on mid-query table deletion. No partial answers. |
| **All DB access via MCP connector** | Single entry point for all database calls. Error detection (42P01) is centralised in one place. |

---

_IGNA CSV Agent - Internal Architecture Documentation_
