"""
Guardrail — checks whether a user query is semantically relevant to the
table described in the context before the NL parser runs.

A single fast LLM call; no ReAct loop needed.
Returns (True, "") when the query is in scope.
Returns (False, reason) when it is out of scope.
"""

from utils.llm_client import chat

_SYSTEM_PROMPT = """\
You are a strict relevance classifier for a data query system.

You will be given:
1. A semantic description of a database table (what data it holds).
2. The EXACT list of column names that exist in the table.
3. A user's natural language question.

Your ONLY job is to decide whether the question is answerable using the data
in that table. Respond with exactly one of:

  VERDICT: IN_SCOPE
  REASON: <one sentence explaining which column(s) make it answerable>

or

  VERDICT: OUT_OF_SCOPE
  REASON: <one sentence explaining what columns exist and why the question cannot be answered>

Rules:
- A question is IN_SCOPE only if it can be answered using the EXACT columns listed.
  Do not assume columns exist that are not in the list.
- A question is OUT_OF_SCOPE if it asks about data attributes not present in the column list,
  even if the topic sounds related to the table's domain.
- General exploratory questions ("show me the data", "what's in this table?",
  "describe the dataset") are always IN_SCOPE.
- SQL / data-manipulation instructions ("delete rows", "drop table", "update") are OUT_OF_SCOPE.
- Never add anything after REASON.\
"""


def check_query_scope(
    user_query: str,
    semantic_summary: str,
    column_names: list[str] | None = None,
) -> tuple[bool, str]:
    """
    Returns (True, "") if the query is in scope.
    Returns (False, reason_string) if it is out of scope.

    column_names — the exact column list from context["columns"]. When provided,
    the guardrail checks against real columns, not just the prose summary.
    """
    col_list_text = (
        f"\nExact column names in this table:\n{column_names}\n"
        if column_names
        else ""
    )
    user_msg = (
        f"Table description:\n{semantic_summary}"
        f"{col_list_text}\n\n"
        f"User question: {user_query}"
    )

    print(f"[guardrail] Checking scope for query: {user_query!r}")

    messages = [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": user_msg},
    ]

    response = chat(messages, max_tokens=120, temperature=0.0)
    print(f"[guardrail] LLM response: {response}")

    # Parse verdict
    verdict_in_scope = "VERDICT: IN_SCOPE" in response.upper()
    verdict_out_of_scope = "VERDICT: OUT_OF_SCOPE" in response.upper()

    # Extract reason line
    reason = ""
    for line in response.splitlines():
        if line.strip().upper().startswith("REASON:"):
            reason = line.split(":", 1)[1].strip()
            break

    if verdict_out_of_scope:
        print(f"[guardrail] OUT_OF_SCOPE — {reason}")
        return False, reason

    if verdict_in_scope:
        print(f"[guardrail] IN_SCOPE — {reason}")
        return True, ""

    # Ambiguous response — default to allowing the query through
    print(f"[guardrail] Ambiguous response, defaulting to IN_SCOPE.")
    return True, ""
