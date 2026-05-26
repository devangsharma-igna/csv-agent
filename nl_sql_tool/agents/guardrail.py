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
2. A user's natural language question.

Your ONLY job is to decide whether the question is answerable using the data
in that table. Respond with exactly one of:

  VERDICT: IN_SCOPE
  REASON: <one sentence explaining why it is relevant>

or

  VERDICT: OUT_OF_SCOPE
  REASON: <one sentence explaining what the table covers and why the question falls outside it>

Rules:
- A question is IN_SCOPE if it can be answered (even partially) by querying
  the described table — regardless of how the question is phrased.
- A question is OUT_OF_SCOPE if it asks about topics, entities, or time periods
  that the table does not cover at all.
- General exploratory questions ("show me the data", "what's in this table?",
  "describe the dataset") are always IN_SCOPE.
- SQL / data-manipulation instructions ("delete rows", "drop table") are OUT_OF_SCOPE.
- Never add anything after REASON.\
"""


def check_query_scope(user_query: str, semantic_summary: str) -> tuple[bool, str]:
    """
    Returns (True, "") if the query is in scope.
    Returns (False, reason_string) if it is out of scope.
    """
    user_msg = (
        f"Table description:\n{semantic_summary}\n\n"
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
