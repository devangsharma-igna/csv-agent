import os
from openai import AzureOpenAI


client = AzureOpenAI(
    azure_endpoint=os.environ["AZURE_OPENAI_ENDPOINT"],
    api_key=os.environ["AZURE_OPENAI_API_KEY"],
    api_version=os.environ.get("AZURE_OPENAI_API_VERSION", "2024-12-01-preview"),
)

DEPLOYMENT = os.environ["AZURE_OPENAI_DEPLOYMENT_NAME"]


def chat(
    messages: list[dict],
    max_tokens: int = 2000,
    temperature: float = 0.2,
) -> str:
    """
    Calls Azure OpenAI chat completions.
    Returns the assistant message content as a plain string.
    Raises LLMError on failure.
    """
    try:
        response = client.chat.completions.create(
            model=DEPLOYMENT,
            messages=messages,
            max_tokens=max_tokens,
            temperature=temperature,
        )
        return response.choices[0].message.content
    except Exception as e:
        raise LLMError(f"Azure OpenAI call failed: {e}") from e


class LLMError(Exception):
    pass
