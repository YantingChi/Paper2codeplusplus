"""Select the LLM API provider and key for Paper2Code scripts.

Sample usage:

1. Use the default auto mode from `codes/llm_config.json`:

       export OPENAI_API_KEY="sk-..."
       python codes/1_planning.py --gpt_version gpt-5.4 ...

   Auto mode uses Azure when `AZURE_OPENAI_ENDPOINT` is set; otherwise it uses
   the standard OpenAI API with `OPENAI_API_KEY`.

2. Force the standard OpenAI API:

       export PAPER2CODE_LLM_PROVIDER=openai
       export OPENAI_API_KEY="sk-..."
       python codes/api_key_selector.py

3. Force Azure OpenAI:

       export PAPER2CODE_LLM_PROVIDER=azure
       export AZURE_OPENAI_API_KEY="..."
       export AZURE_OPENAI_ENDPOINT="https://your-resource.openai.azure.com/"
       python codes/api_key_selector.py

4. Use a different config file:

       export PAPER2CODE_LLM_CONFIG="/path/to/llm_config.json"
       python codes/api_key_selector.py

Provider priority:
- Environment variables override values in the config file.
- `PAPER2CODE_LLM_PROVIDER` or `LLM_PROVIDER` may be `auto`, `openai`, or
  `azure`.
- Keep real API keys in environment variables when possible instead of storing
  them in config files.
"""

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any


DEFAULT_CONFIG_PATH = Path(__file__).with_name("llm_config.json")
DEFAULT_AZURE_API_VERSION = "2024-12-01-preview"
VALID_PROVIDERS = {"auto", "openai", "azure"}


@dataclass(frozen=True)
class ApiKeySelection:
    provider: str
    api_key: str
    api_key_source: str
    base_url: str | None = None
    azure_endpoint: str | None = None
    azure_api_version: str = DEFAULT_AZURE_API_VERSION


def _first_nonempty(*values: str | None) -> str | None:
    for value in values:
        if value and value.strip():
            return value.strip()
    return None


def _config_string(config: dict[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = config.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _resolve_config_path(config_path: str | Path | None = None) -> Path:
    raw_path = config_path or os.getenv("PAPER2CODE_LLM_CONFIG") or DEFAULT_CONFIG_PATH
    return Path(raw_path).expanduser()


def load_llm_config(config_path: str | Path | None = None) -> dict[str, Any]:
    path = _resolve_config_path(config_path)
    if not path.exists():
        return {}

    text = path.read_text(encoding="utf-8").strip()
    if not text:
        return {}

    if path.suffix.lower() in {".yaml", ".yml"}:
        try:
            import yaml
        except ImportError as exc:
            raise RuntimeError(
                f"Cannot read YAML config {path}: install PyYAML or use JSON."
            ) from exc
        data = yaml.safe_load(text)
    else:
        data = json.loads(text)

    if data is None:
        return {}
    if not isinstance(data, dict):
        raise RuntimeError(f"LLM config {path} must contain an object/dictionary.")
    return data


def _resolve_provider(config: dict[str, Any]) -> str:
    provider = _first_nonempty(
        os.getenv("PAPER2CODE_LLM_PROVIDER"),
        os.getenv("LLM_PROVIDER"),
        _config_string(config, "provider"),
        "auto",
    )
    provider = provider.lower()
    if provider not in VALID_PROVIDERS:
        raise RuntimeError(
            f"Invalid LLM provider {provider!r}; expected one of: "
            f"{', '.join(sorted(VALID_PROVIDERS))}."
        )
    return provider


def select_api_key(config_path: str | Path | None = None) -> ApiKeySelection:
    config = load_llm_config(config_path)
    provider = _resolve_provider(config)

    openai_key = _first_nonempty(
        os.getenv("OPENAI_API_KEY"),
        _config_string(config, "openai_api_key", "api_key"),
    )
    azure_key = _first_nonempty(
        os.getenv("AZURE_OPENAI_API_KEY"),
        _config_string(config, "azure_openai_api_key", "azure_api_key"),
    )
    openai_base_url = _first_nonempty(
        os.getenv("OPENAI_BASE_URL"),
        _config_string(config, "openai_base_url", "base_url"),
    )
    azure_endpoint = _first_nonempty(
        os.getenv("AZURE_OPENAI_ENDPOINT"),
        _config_string(config, "azure_openai_endpoint", "azure_endpoint"),
    )
    azure_api_version = _first_nonempty(
        os.getenv("AZURE_OPENAI_API_VERSION"),
        _config_string(config, "azure_openai_api_version", "azure_api_version"),
        DEFAULT_AZURE_API_VERSION,
    )

    if provider == "auto":
        provider = "azure" if azure_endpoint else "openai"

    if provider == "azure":
        api_key = azure_key or openai_key
        if not azure_endpoint:
            raise RuntimeError(
                "AZURE_OPENAI_ENDPOINT is required when provider is 'azure'. "
                "Set it in the environment or in the LLM config file."
            )
        if not api_key:
            raise RuntimeError(
                "AZURE_OPENAI_API_KEY is required when provider is 'azure'. "
                "OPENAI_API_KEY is accepted as a fallback for existing setups."
            )
        return ApiKeySelection(
            provider="azure",
            api_key=api_key,
            api_key_source="AZURE_OPENAI_API_KEY" if azure_key else "OPENAI_API_KEY",
            azure_endpoint=azure_endpoint,
            azure_api_version=azure_api_version,
        )

    if not openai_key:
        raise RuntimeError(
            "OPENAI_API_KEY is required when provider is 'openai'. "
            "Set it in the environment or in the LLM config file."
        )
    return ApiKeySelection(
        provider="openai",
        api_key=openai_key,
        api_key_source="OPENAI_API_KEY",
        base_url=openai_base_url,
        azure_api_version=azure_api_version,
    )


if __name__ == "__main__":
    selection = select_api_key()
    print(f"provider={selection.provider}")
    print(f"api_key_source={selection.api_key_source}")
    if selection.base_url:
        print(f"base_url={selection.base_url}")
    if selection.azure_endpoint:
        print(f"azure_endpoint={selection.azure_endpoint}")
        print(f"azure_api_version={selection.azure_api_version}")
