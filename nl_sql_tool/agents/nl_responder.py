import json
from utils.llm_client import chat

_SYSTEM_PROMPT = """\
You are a data analyst assistant. You are given:
- The user's original question
- The SQL query that was executed
- The result rows from the database
- A semantic summary of the table

Your job is to answer the user's question in clear, concise natural language
using ONLY the data in the result rows. Do not invent or extrapolate beyond the data.

Guidelines:
- Lead with the direct answer to the question.
- Use specific numbers, names, or values from the results.
- If the result is a list, summarise the top items and note the total count.
- If the result has aggregates (counts, sums, averages), state them precisely.
- If the result is empty, say so clearly.
- Keep your response under 150 words unless the data genuinely requires more.
- Do not mention SQL, table names, or technical details unless the user asked about them.\
"""


class NLResponderError(Exception):
    pass


def generate_nl_response(
    user_query: str,
    sql: str,
    rows: list[dict],
    semantic_summary: str = "",
) -> str:
    """
    Generates a natural-language answer to user_query given the result rows.
    Passes at most 30 rows to the LLM to keep token usage bounded.
    Returns the answer string.
    """
    if not rows:
        return "The query returned no results."

    # Cap rows sent to LLM; note if truncated
    sample = rows[:30]
    truncation_note = (
        f"\n(Showing first 30 of {len(rows)} rows in the data below.)"
        if len(rows) > 30
        else ""
    )

    user_msg = (
        f"User question: {user_query}\n\n"
        f"SQL executed:\n{sql}\n\n"
        f"Table context: {semantic_summary}{truncation_note}\n\n"
        f"Result rows ({len(sample)}):\n{json.dumps(sample, indent=2, default=str)}"
    )

    print(f"[nl_responder] Generating NL response for {len(rows)} rows...")
    messages = [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": user_msg},
    ]
    response = chat(messages, max_tokens=400, temperature=0.3)
    print(f"[nl_responder] Response: {response}")
    return response
