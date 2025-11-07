# services/openai_bridge.py
import os
import json
import httpx
from openai import OpenAI
from dotenv import load_dotenv

# Load environment variables
load_dotenv()


def openai_client() -> OpenAI:
    """
    Create a properly configured OpenAI client using HTTPS proxy.
    Works with older and newer versions of httpx.
    """
    api_key = os.getenv("OPENAI_API_KEY")
    base_url = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
    proxy_url = os.getenv("HTTPS_PROXY") or os.getenv("HTTP_PROXY")

    print("🔹 OpenAI base URL:", base_url)
    print("🔹 Proxy URL:", proxy_url or "(none)")

    # ---- Fix: older httpx compatibility ----
    if proxy_url:
        transport = httpx.HTTPTransport(proxy=proxy_url, verify=True)
        httpx_client = httpx.Client(transport=transport, timeout=90.0)
    else:
        httpx_client = httpx.Client(timeout=90.0)

    return OpenAI(
        api_key=api_key,
        base_url=base_url,
        http_client=httpx_client,
    )


# -------------------------------------------------
# Embeddings Helper
# -------------------------------------------------
def embed_texts(texts: list[str]):
    client = openai_client()
    try:
        res = client.embeddings.create(
            model="text-embedding-3-small",
            input=texts,
        )
        return [item.embedding for item in res.data]
    except Exception as e:
        print("❌ OpenAI embed_texts error:", e)
        raise


# -------------------------------------------------
# Chat Helpers
# -------------------------------------------------
# -------------------------------------------------
# Chat Helpers
# -------------------------------------------------
def chat_complete(
    messages: list[dict],
    model: str = "gpt-4o-mini",
    system: str | None = None
) -> str:
    """
    Returns the plain text content of the model's reply.
    If `system` is provided, prepends it as a system message.
    """
    client = openai_client()
    try:
        if system:
            messages = [{"role": "system", "content": system}] + messages
        res = client.chat.completions.create(
            model=model,
            messages=messages,
        )
        return res.choices[0].message.content
    except Exception as e:
        print("❌ OpenAI chat_complete error:", e)
        raise

def chat_complete_stream(
    messages: list[dict],
    model: str = "gpt-4o-mini",
    system: str | None = None
):
    """
    Generator that yields chunks of the model's reply as they're generated (streaming).
    Also yields the full accumulated text for each chunk.
    Yields: (chunk: str, full_text: str)
    If `system` is provided, prepends it as a system message.
    """
    client = openai_client()
    try:
        if system:
            messages = [{"role": "system", "content": system}] + messages
        
        stream = client.chat.completions.create(
            model=model,
            messages=messages,
            stream=True,
        )
        
        full_text = ""
        for chunk in stream:
            if chunk.choices[0].delta.content is not None:
                content = chunk.choices[0].delta.content
                full_text += content
                yield content, full_text
    except Exception as e:
        print("❌ OpenAI chat_complete_stream error:", e)
        raise


def chat_json(
    messages: list[dict] | str,
    model: str = "gpt-4o-mini",
    system: str | None = None
) -> dict:
    """
    Returns parsed JSON output when the model's reply is structured JSON.
    Accepts either a list of messages or a single user string.
    """
    client = openai_client()
    try:
        # Allow shorthand call: chat_json("summarize this", system="...")
        if isinstance(messages, str):
            user_messages = [{"role": "user", "content": messages}]
        else:
            user_messages = messages

        if system:
            user_messages = [{"role": "system", "content": system}] + user_messages

        res = client.chat.completions.create(
            model=model,
            messages=user_messages,
            response_format={"type": "json_object"},
        )
        import json
        return json.loads(res.choices[0].message.content)
    except Exception as e:
        print("❌ OpenAI chat_json error:", e)
        raise
