# CSV Agent — How It Works
### Architecture & Flow Diagrams
> Prepared for Executive Presentation · Techno-Functional Audience

---

## Contents

1. [What Is CSV Agent?](#1-what-is-csv-agent)
2. [System Components at a Glance](#2-system-components-at-a-glance)
3. [First-Time Setup vs Every-Day Use](#3-first-time-setup-vs-every-day-use)
4. [Full Query Pipeline — Step by Step](#4-full-query-pipeline--step-by-step)
5. [How the System Learns Your Table Once](#5-how-the-system-learns-your-table-once)
6. [Four Layers of Protection Against Bad Answers](#6-four-layers-of-protection-against-bad-answers)
7. [Circuit Breaker — What Happens if the Table Is Deleted](#7-circuit-breaker--what-happens-if-the-table-is-deleted)
8. [Three Ways to See Your Answer](#8-three-ways-to-see-your-answer)
9. [Component Map — What Each Part Does](#9-component-map--what-each-part-does)

---

## 1. What Is CSV Agent?

CSV Agent lets a business user ask questions about their data **in plain English** — no SQL knowledge needed. It connects to a database table (originally uploaded from a CSV), understands the structure of the data automatically, and returns answers as readable text, charts, or both.

```mermaid
flowchart LR
    USER["👤 Business User\nAsk anything in plain English"]
    APP["🖥️ CSV Agent\nWeb Application"]
    AI["🤖 AI Model\nAzure OpenAI GPT-4"]
    DB["🗄️ Database\nSupabase"]
    OUT["📊 Answer\nText · Charts · Table"]

    USER -->|"What are the top 10 restaurants\nby rating?"| APP
    APP <-->|"Understand the data,\ngenerate safe SQL"| AI
    APP <-->|"Run the query\nvia secure connector"| DB
    APP -->|"Plain-English answer\n+ interactive charts"| OUT
    OUT --> USER

    style USER fill:#6366f1,color:#fff,stroke:none
    style APP fill:#0ea5e9,color:#fff,stroke:none
    style AI fill:#8b5cf6,color:#fff,stroke:none
    style DB fill:#10b981,color:#fff,stroke:none
    style OUT fill:#f59e0b,color:#fff,stroke:none
```

---

## 2. System Components at a Glance

Six specialised modules each own a single responsibility. No module does more than one job.

```mermaid
flowchart TB
    subgraph UI["🖥️  User Interface"]
        WEBAPP["Web App\nStreamlit"]
    end

    subgraph AGENTS["🤖  AI Agents  — each runs once per query"]
        CB["Context Builder\nLearns the table schema once"]
        GR["Scope Guardrail\nBlocks irrelevant questions"]
        NLP["Question Parser\nConverts English → SQL"]
        VAL["SQL Validator\nChecks every column name"]
        EXE["Query Executor\nRuns SQL on the database"]
        NLR["Answer Writer\nExplains results in plain English"]
    end

    subgraph MEMORY["💾  Memory & Config"]
        CTX["Table Memory\ncontext.json — cached schema + summary"]
    end

    subgraph EXTERNAL["☁️  External Services"]
        AIAPI["Azure OpenAI\nGPT-4"]
        SUPA["Supabase\nPostgreSQL Database"]
    end

    WEBAPP --> CB & GR & NLP & VAL & EXE & NLR
    CB & NLP & GR & NLR <-->|"AI calls"| AIAPI
    CB & EXE <-->|"DB calls via\nsecure connector"| SUPA
    CB <-->|"read / write"| CTX
    NLP -->|"reads"| CTX
    VAL -->|"reads"| CTX

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

    START --> Q1{"Is a table\nalready configured?"}

    Q1 -->|"No — first visit"| SETUP
    Q1 -->|"Yes — returning"| Q2

    subgraph SETUP["One-Time Table Setup"]
        S1["User enters the table name"]
        S2["System confirms the table exists\nin the database"]
        S3["Table name saved"]
        S1 --> S2 --> S3
    end

    SETUP --> Q2

    Q2{"Is the table's schema\nand description cached?"}

    Q2 -->|"No — first query\nor user clicked Rebuild"| LEARN
    Q2 -->|"Yes — use cache"| QUERY

    subgraph LEARN["One-Time Context Build  ⟵ runs only when needed"]
        L1["Fetch all column names and types\nfrom the database"]
        L2["Fetch 5 sample rows\nto understand the data"]
        L3["AI writes a plain-English\ndescription of the table"]
        L4["Save everything to\nTable Memory"]
        L1 --> L2 --> L3 --> L4
    end

    LEARN --> QUERY

    subgraph QUERY["Every Query — Fast Path"]
        Q3["Load cached schema + description"]
        Q4["Run guardrail + parse + validate + execute"]
        Q5["Return answer to user"]
        Q3 --> Q4 --> Q5
    end

    style LEARN fill:#fef3c7,stroke:#d97706
    style QUERY fill:#dcfce7,stroke:#16a34a
    style SETUP fill:#e0f2fe,stroke:#0ea5e9
```

---

## 4. Full Query Pipeline — Step by Step

Every question travels through five sequential checkpoints. Any checkpoint can stop the pipeline early with a clear reason.

```mermaid
flowchart TD
    INPUT(["👤 User submits a question"])

    INPUT --> STEP0

    STEP0{"Step 1 · Circuit Breaker\nDoes the table still\nexist in the database?"}
    STEP0 -->|"❌ Table missing"| ERR0(["⛔ Error\nTable not found\nAsk user to re-enter table name"])
    STEP0 -->|"✅ Table confirmed"| STEP1

    STEP1{"Step 2 · Context\nIs the table description\nalready in memory?"}
    STEP1 -->|"✅ Cached — skip"| STEP2
    STEP1 -->|"Not yet — build now"| BUILD["Build Context\nFetch schema · sample rows · AI summary\nSaved for all future queries"]
    BUILD --> STEP2

    STEP2{"Step 3 · Scope Guardrail\nIs the question actually\nabout this table's data?"}
    STEP2 -->|"❌ Off-topic"| ERR2(["⛔ Query Out of Scope\nReturns reason to user\nNo SQL generated"])
    STEP2 -->|"✅ Relevant"| STEP3

    STEP3["Step 4 · Question Parser\nAI converts the English question\ninto a SQL query using\nONLY the known column names"]
    STEP3 --> STEP4

    STEP4{"Step 5 · SQL Validator\nDoes every column name in the\nSQL exist in the real schema?"}
    STEP4 -->|"✅ All columns valid"| STEP5
    STEP4 -->|"❌ Unknown column\nself-correct + retry up to 2x"| STEP3

    STEP5["Step 6 · Executor\nRun the validated SQL\nagainst the live database"]
    STEP5 --> STEPDB

    STEPDB{"Did the table disappear\nduring execution?"}
    STEPDB -->|"❌ Table gone\n42P01 detected"| ERR5(["⛔ Circuit Breaker Fires\nConfig cleared automatically\nUser redirected to setup"])
    STEPDB -->|"✅ Rows returned"| STEP6

    STEP6["Step 7 · Format Answer\nAI writes plain-English response\nCharts auto-generated from data"]
    STEP6 --> OUTPUT(["✅ Answer shown to user"])

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

The Context Build is the system's "reading session" on your data. It happens once, saves everything, and is reused on every query until you explicitly ask for a rebuild.

```mermaid
sequenceDiagram
    actor User
    participant App as Web App
    participant AI as AI Model
    participant DB as Database
    participant Mem as Table Memory

    User->>App: Runs first query on a new table

    App->>Mem: Check — is schema already saved?
    Mem-->>App: Not found

    Note over App,DB: Step 1 — Learn the structure

    App->>DB: SELECT column names and types<br/>FROM information_schema
    DB-->>App: 11 columns with names and data types

    Note over App,DB: Step 2 — See real examples

    App->>DB: SELECT * FROM table LIMIT 5
    DB-->>App: 5 sample rows

    Note over App,AI: Step 3 — Understand the meaning

    App->>AI: Here are the columns and sample rows.<br/>Write a plain-English summary of what this table is about.
    AI-->>App: "This table contains restaurant data for Chennai,<br/>including name, cuisine, location, rating..."

    App->>Mem: Save columns + summary to context.json

    Note over Mem: Cached — will not rebuild<br/>unless user clicks Rebuild Context

    App-->>User: Context ready — processing your question now
```

---

## 6. Four Layers of Protection Against Bad Answers

The system cannot hallucinate column names or answer off-topic questions. Four independent checkpoints enforce this.

```mermaid
flowchart TD
    Q(["Question enters the pipeline"])

    Q --> L1

    subgraph L1["Layer 1 · Scope Guardrail"]
        G1["AI checks the question against\nthe table's description AND\nthe exact list of column names"]
        G2{"Is this question\nanswerable with\nthis table's columns?"}
        G1 --> G2
    end

    G2 -->|"❌ No — stops here"| BLOCK1(["⛔ Query Out of Scope\nUser told why"])
    G2 -->|"✅ Yes"| L2

    subgraph L2["Layer 2 · Column-Constrained SQL Generation"]
        P1["AI receives the question PLUS\nan explicit list of every valid column name"]
        P2["AI is instructed:\nuse ONLY the columns in this list\nnever invent new ones"]
        P3["AI generates SQL referencing\nonly real columns"]
        P1 --> P2 --> P3
    end

    P3 --> L3

    subgraph L3["Layer 3 · SQL Validator — Hard Check"]
        V1["Every identifier in the generated SQL\nis checked against the schema"]
        V2{"Any unknown\ncolumn names?"}
        V1 --> V2
    end

    V2 -->|"✅ All columns real"| L4
    V2 -->|"❌ Unknown column found\nSend back with correct list\nRetry up to 2 times"| L2

    V2 -->|"❌ Still invalid after 2 retries"| BLOCK3(["⛔ Cannot answer safely\nUser asked to rephrase"])

    subgraph L4["Layer 4 · Live Execution"]
        E1["Validated SQL runs against\nthe real database"]
        E2{"Successful?"}
        E1 --> E2
    end

    E2 -->|"✅ Rows returned"| SUCCESS(["✅ Answer delivered"])
    E2 -->|"❌ Table gone\nmid-execution"| BLOCK4(["⛔ Circuit Breaker\nConfig cleared automatically"])

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

## 7. Circuit Breaker — What Happens if the Table Is Deleted

If a database table is deleted at any point — before, during, or after a query — the system detects it instantly and brings everything to a clean stop. No stale data is served. No silent failures.

```mermaid
flowchart TD
    subgraph BEFORE["Scenario A — Table deleted BEFORE the query runs"]
        B1["User submits question"]
        B2["Step 1: Check table exists\nvia information_schema"]
        B3{"Table found?"}
        B4(["⛔ Stopped immediately\nUser told to re-enter a table"])
        B5["Continue to answer"]
        B1 --> B2 --> B3
        B3 -->|No| B4
        B3 -->|Yes| B5
    end

    subgraph DURING["Scenario B — Table deleted WHILE the query is running"]
        D1["SQL reaches the database\nfor execution"]
        D2{"PostgreSQL returns\nerror code 42P01\nrelation does not exist"}
        D3["System detects this specific\ndatabase error instantly"]
        D4["TableNotFoundError raised\npropagates immediately\nthrough entire pipeline"]
        D5["Table config wiped\nfrom memory automatically"]
        D6(["⛔ User sees clear error\nRedirected to table setup form"])
        D1 --> D2 --> D3 --> D4 --> D5 --> D6
    end

    subgraph GUARANTEE["What is guaranteed"]
        G1["✅ No partial or misleading answer is shown"]
        G2["✅ Stale table config is cleared automatically"]
        G3["✅ User is guided to re-enter a valid table"]
        G4["✅ No polling delay — fires on the exact failing query"]
    end

    style BEFORE fill:#fef9c3,stroke:#ca8a04
    style DURING fill:#fee2e2,stroke:#dc2626
    style GUARANTEE fill:#dcfce7,stroke:#16a34a
    style B4 fill:#fee2e2,stroke:#dc2626,color:#7f1d1d
    style D6 fill:#fee2e2,stroke:#dc2626,color:#7f1d1d
```

---

## 8. Three Ways to See Your Answer

After a query executes, the user chooses how they want the result presented. All three modes show the raw data table in a collapsible section below.

```mermaid
flowchart LR
    RESULT["Query result\nreturned from database"]

    RESULT --> MODE{"User's chosen\nformat"}

    MODE -->|"NL"| NL
    MODE -->|"Figures"| FIG
    MODE -->|"NL + Figures"| BOTH

    subgraph NL["📝 Natural Language"]
        NL1["AI reads the rows\nand writes a concise\nplain-English answer"]
        NL2["Example:\n'The highest-rated restaurant\nis Sukkubhai Biriyani at 4.4'"]
        NL1 --> NL2
    end

    subgraph FIG["📊 Figures"]
        FIG1["Chart type is auto-detected\nbased on column types"]
        FIG2["Categorical + numeric → Bar chart\nDate + numeric → Line chart\nTwo numeric cols → Scatter\nDistribution → Histogram"]
        FIG1 --> FIG2
    end

    subgraph BOTH["📝 + 📊 NL and Figures"]
        B1["AI-written answer shown first"]
        B2["Chart displayed below"]
        B3["Raw data in\ncollapsible section"]
        B1 --> B2 --> B3
    end

    style NL fill:#f3e8ff,stroke:#7c3aed
    style FIG fill:#e0f2fe,stroke:#0284c7
    style BOTH fill:#dcfce7,stroke:#16a34a
    style RESULT fill:#fef3c7,stroke:#d97706
```

---

## 9. Component Map — What Each Part Does

Every file in the project has exactly one responsibility. Arrows show which components call which.

```mermaid
flowchart TB
    subgraph UI["🖥️  User Interface  ·  main.py"]
        WEB["Streamlit Web App\nHandles all user interaction,\norchestrates the pipeline,\ndisplays results"]
    end

    subgraph AGLAYER["🤖  AI Agent Layer  ·  agents/"]
        CB["context_builder.py\nFetches schema + samples\nGenerates semantic summary\nRuns once per table"]
        GR["guardrail.py\nScope checker\nBlocks off-topic questions\nbefore SQL is generated"]
        NLP["nl_parser.py\nEnglish → SQL\nColumn-constrained\nReAct reasoning loop"]
        VAL["sql_validator.py\nChecks every column name\nin generated SQL\nagainst known schema"]
        EXE["executor.py\nRuns validated SQL\nParses result rows\nPropagates table errors"]
        NLR["nl_responder.py\nReads result rows\nWrites plain-English answer"]
    end

    subgraph UTILS["⚙️  Utilities  ·  utils/"]
        MCP["mcp_client.py\nSecure connector to Supabase\nDetects 42P01 table errors\nRaises TableNotFoundError"]
        LLM["llm_client.py\nAll calls to Azure OpenAI\nSingle entry point for AI"]
        CTX["context_io.py\nRead and write context.json\nSchema and summary cache"]
        ROW["row_parser.py\nParses all MCP response shapes\nExtracts rows from DB wrapper"]
        FIG["figure_builder.py\nAuto-detects chart type\nBuilds Plotly figures"]
    end

    subgraph STORE["💾  Persistent State"]
        JSON["context.json\nTable name\nColumn list\nSample rows\nSemantic summary"]
    end

    subgraph EXT["☁️  External Services"]
        OPENAI["Azure OpenAI\nGPT-4 Deployment"]
        SUPABASE["Supabase\nPostgreSQL"]
    end

    WEB --> CB & GR & NLP & VAL & EXE & NLR
    CB --> MCP & LLM & CTX
    GR --> LLM
    NLP --> LLM & CTX
    VAL --> CTX
    EXE --> MCP & ROW
    NLR --> LLM
    WEB --> FIG
    MCP --> SUPABASE
    LLM --> OPENAI
    CTX <--> JSON

    style UI fill:#e0f2fe,stroke:#0284c7
    style AGLAYER fill:#f3e8ff,stroke:#7c3aed
    style UTILS fill:#fef9c3,stroke:#ca8a04
    style STORE fill:#f1f5f9,stroke:#64748b
    style EXT fill:#dcfce7,stroke:#16a34a
```

---

## Summary — Key Design Decisions

| Decision | Why |
|---|---|
| **Context built once, cached forever** | Avoids repeated schema lookups on every query. Rebuild is user-controlled. |
| **Guardrail checks column list, not just topic** | Stops questions about data that doesn't exist before any SQL is generated. |
| **SQL validator is a hard rule-based check** | Not probabilistic — every column name is checked against a known list. AI cannot bypass it. |
| **TableNotFoundError propagates uncaught** | The pipeline crashes instantly and cleanly on mid-query table deletion. No partial answers. |
| **Three response modes** | Accommodates different audiences: executives want NL, analysts want charts, engineers want raw data. |
| **All DB access via MCP connector** | Single entry point for all database calls. Error detection (42P01) is centralised in one place. |

---

_CSV Agent · Internal Architecture Documentation_
