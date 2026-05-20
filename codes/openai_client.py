import os

from openai import AzureOpenAI, OpenAI

from api_key_selector import DEFAULT_AZURE_API_VERSION, select_api_key


AZURE_API_VERSION = os.getenv("AZURE_OPENAI_API_VERSION", DEFAULT_AZURE_API_VERSION)


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


def _is_azure_base_url(base_url: str | None) -> bool:
    if not base_url:
        return False
    return ".openai.azure.com/" in base_url or ".services.ai.azure.com/" in base_url


def _resolve_api_key(base_url: str | None = None) -> str:
    openai_key = os.getenv("OPENAI_API_KEY")
    azure_key = os.getenv("AZURE_OPENAI_API_KEY")

    if _is_azure_base_url(base_url):
        api_key = azure_key or openai_key
    else:
        api_key = openai_key or azure_key

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


def _resolve_azure_endpoint() -> str | None:
    return os.getenv("AZURE_OPENAI_ENDPOINT")


def create_openai_client(config_path: str | None = None) -> OpenAI:
    """Create an OpenAI client for OpenAI or Azure."""
    selection = select_api_key(config_path)
    if selection.provider == "azure":
        return AzureOpenAI(
            api_key=selection.api_key,
            api_version=selection.azure_api_version,
            azure_endpoint=selection.azure_endpoint,
        )

    base_url = _normalize_base_url(selection.base_url) if selection.base_url else None
    if base_url:
        return OpenAI(api_key=selection.api_key, base_url=base_url)
    return OpenAI(api_key=selection.api_key)
