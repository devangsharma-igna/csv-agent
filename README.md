# NL → SQL Query Tool

## Overview

A multi-agent proof-of-concept that accepts a natural language question from a user and returns query results from a Supabase table previously imported from a CSV file. The pipeline uses Azure OpenAI (GPT-4.1) and the Supabase MCP server to build table context, parse the question into SQL, validate column references, and execute the query — all orchestrated through a Streamlit UI.

---

## Prerequisites

- **Python 3.11+**
- **Node.js 18+** (required for `npx` to run the Supabase MCP server)
- A **Supabase project** with at least one table imported from CSV
- An **Azure OpenAI** resource with a GPT-4.1 deployment

---

## Setup

```bash
git clone <your-repo-url>
cd nl_sql_tool

# Copy and fill in environment variables
cp .env.example .env
# Edit .env with your Azure OpenAI and Supabase credentials

# Install Python dependencies
pip install -r requirements.txt

# Run the app
streamlit run main.py
```

---

## How to import a CSV into Supabase

1. Go to your [Supabase Dashboard](https://supabase.com/dashboard).
2. Select your project and navigate to **Table Editor**.
3. Click **New table**.
4. In the table creation dialog, choose **Import data from CSV**.
5. Upload your CSV file and confirm the column types.
6. Note the exact **table name** — you will enter it in the tool on first run.

---

## Example query

**Table:** `sales_data`

**Question:** "What were the top 5 products by total revenue last quarter?"

**Expected SQL:**
```sql
SELECT product_name, SUM(revenue) AS total_revenue
FROM sales_data
WHERE sale_date >= '2024-10-01'
  AND sale_date <  '2025-01-01'
GROUP BY product_name
ORDER BY total_revenue DESC
LIMIT 5
```

---

## Architecture

The pipeline runs four phases on every query:

| Phase | Component | Description |
|-------|-----------|-------------|
| 0 | Table Check | Verifies the target table exists in Supabase; prompts for table name on first run |
| 1 | Context Builder | ReAct agent — introspects schema and sample rows, produces a semantic summary; result cached in `context.json` |
| 2 | NL Parser | ReAct agent — translates the user question into SQL using the cached context; no live DB calls |
| 3 | SQL Validator | Pure Python — checks every column reference against the known schema; triggers correction retries if invalid |
| 4 | Executor | Deterministic MCP call — runs the validated SQL and returns rows |

All inter-agent state flows through a single file: `context.json`.

---

## Known limitations (PoC scope)

- **No multi-table JOIN support** — the tool is designed for single-table queries only.
- **No query history or session persistence** — each page load starts fresh.
- **Single-table context** — `context.json` holds context for one table at a time; use "Change table" in the sidebar and "Rebuild context" when switching tables.
- **No authentication** — the Streamlit app is open with no login layer (PoC only).
- **CSV import is manual** — the tool does not automate CSV uploading to Supabase.
