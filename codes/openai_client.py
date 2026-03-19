import os

from openai import OpenAI


def _normalize_base_url(raw_base_url: str) -> str:
    """Return an OpenAI-compatible base URL for OpenAI or Azure."""
    base_url = raw_base_url.rstrip("/")

    if base_url.endswith("/openai/v1"):
        return f"{base_url}/"
    if base_url.endswith("/openai"):
        return f"{base_url}/v1/"
    if ".openai.azure.com" in base_url or ".services.ai.azure.com" in base_url:
        return f"{base_url}/openai/v1/"
    return f"{base_url}/"


def _resolve_api_key() -> str:
    api_key = os.getenv("OPENAI_API_KEY") or os.getenv("AZURE_OPENAI_API_KEY")
    if api_key:
        return api_key
    raise RuntimeError(
        "OPENAI_API_KEY is not set. For Azure, you can also set AZURE_OPENAI_API_KEY."
    )


def _resolve_base_url() -> str | None:
    raw_base_url = os.getenv("OPENAI_BASE_URL") or os.getenv("AZURE_OPENAI_ENDPOINT")
    if not raw_base_url:
        return None
    return _normalize_base_url(raw_base_url)


def create_openai_client() -> OpenAI:
    """Create an OpenAI client that also works with Azure Foundry v1."""
    api_key = _resolve_api_key()
    base_url = _resolve_base_url()

    if base_url:
        return OpenAI(api_key=api_key, base_url=base_url)
    return OpenAI(api_key=api_key)
