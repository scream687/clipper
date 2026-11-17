"""Ask an AI for structured JSON, through whichever provider Settings picked.

The default is Claude through the user's Claude Code login (`claude -p`) with every
plugin, hook, MCP server and skill switched off: that keeps a call to a few seconds
instead of loading the whole interactive setup. Every other provider is reached
through its OpenAI-compatible API (the `openai` package), with a key saved per API
address, so a key is only ever sent to the address it was entered for.
"""

import json
import shutil
import subprocess
import tempfile
from pathlib import Path

from app import SETTINGS_FILE

CLAUDE_MODEL = "sonnet"
CLAUDE_TIMEOUT_S = 600
LIST_TIMEOUT_S = 20
CLAUDE_CODE = "claude-code"
PROVIDERS = {
    CLAUDE_CODE: {"label": "Claude Code login (no key)", "base_url": "", "needs_key": False},
    "openai": {"label": "OpenAI", "base_url": "https://api.openai.com/v1", "needs_key": True},
    "anthropic": {"label": "Anthropic API", "base_url": "https://api.anthropic.com/v1/", "needs_key": True},
    "gemini": {"label": "Google Gemini", "base_url": "https://generativelanguage.googleapis.com/v1beta/openai/", "needs_key": True},
    "groq": {"label": "Groq", "base_url": "https://api.groq.com/openai/v1", "needs_key": True},
    "openrouter": {"label": "OpenRouter", "base_url": "https://openrouter.ai/api/v1", "needs_key": True},
    "ollama": {"label": "Ollama on this Mac", "base_url": "http://127.0.0.1:11434/v1", "needs_key": False},
    "custom": {"label": "Other OpenAI-compatible API", "base_url": "", "needs_key": False},
}
EDITABLE_URL = {"ollama", "custom"}
CLAUDE_CODE_MODELS = [
    {"id": "sonnet", "description": "Latest Sonnet. Balanced; the default."},
    {"id": "opus", "description": "Latest Opus. Most capable, uses the most of your plan."},
    {"id": "haiku", "description": "Latest Haiku. Fastest and lightest."},
]
LIMIT_HINTS = ("usage limit", "rate limit", "limit reached", "not logged in", "please run /login", "credit balance", "overloaded")


class AIUnavailable(RuntimeError):
    """The AI cannot answer right now (limit, login, key, network). The caller may fall back."""


class AIError(RuntimeError):
    """The AI answered, but not with usable output."""


def load_config() -> dict:
    """The `ai` part of settings.json: provider, model, base_url and keys (per base_url)."""
    try:
        return json.loads(SETTINGS_FILE.read_text(encoding="utf-8")).get("ai") or {}
    except FileNotFoundError:
        return {}
    except (OSError, json.JSONDecodeError, AttributeError) as error:
        raise AIUnavailable(f"Could not read the AI settings: {error}") from error


def ask_ai(prompt: str, schema: dict, system: str) -> dict:
    """The one entry point the app uses; routes to the provider picked in Settings."""
    config = load_config()
    provider = config.get("provider", CLAUDE_CODE)
    if provider == CLAUDE_CODE:
        return ask_claude(prompt, schema, system, config.get("model") or CLAUDE_MODEL)
    base_url = config.get("base_url", "")
    return ask_api(provider, base_url, config.get("keys", {}).get(base_url, ""), config.get("model", ""),
                   prompt, schema, system)


def _client(provider: str, base_url: str, api_key: str, timeout: float):
    from openai import OpenAI
    # Anthropic's OpenAI-compatible layer answers chat with a Bearer key, but its model list wants x-api-key.
    headers = {"x-api-key": api_key, "anthropic-version": "2023-06-01"} if provider == "anthropic" else None
    return OpenAI(base_url=base_url, api_key=api_key or "none", timeout=timeout, max_retries=2, default_headers=headers)


def _unavailable_errors():
    import openai
    return (openai.APIConnectionError, openai.RateLimitError, openai.AuthenticationError,
            openai.PermissionDeniedError, openai.NotFoundError, openai.InternalServerError)


def _label(provider: str) -> str:
    return PROVIDERS.get(provider, {}).get("label", provider)


def list_models(provider: str, base_url: str, api_key: str) -> list[dict]:
    """Every model the provider offers, with whatever details it returns (context length, pricing, ...)."""
    if provider == CLAUDE_CODE:
        return CLAUDE_CODE_MODELS
    import openai
    query = {"limit": 1000} if provider == "anthropic" else None  # Anthropic pages 20 at a time by default
    try:
        page = _client(provider, base_url, api_key, LIST_TIMEOUT_S).models.list(extra_query=query)
        models = [m.to_dict() for m in page]
    except openai.APIError as error:
        raise AIUnavailable(f"{_label(provider)} did not list its models: {_short(error)}") from None
    return sorted(models, key=lambda m: str(m.get("id", "")))


def ask_api(provider: str, base_url: str, api_key: str, model: str, prompt: str, schema: dict, system: str) -> dict:
    if not base_url or not model:
        raise AIUnavailable("No AI model is picked. Choose one in Settings.")
    import openai
    client = _client(provider, base_url, api_key, CLAUDE_TIMEOUT_S)
    messages = [{"role": "system", "content": f"{system}\n\nReply with one JSON object that matches this JSON schema:\n"
                                              f"{json.dumps(schema)}"},
                {"role": "user", "content": prompt}]
    strict = {"type": "json_schema", "json_schema": {"name": "answer", "schema": schema}}
    try:
        try:
            reply = client.chat.completions.create(model=model, messages=messages, response_format=strict)
        except openai.BadRequestError:  # ponytail: not every model takes a schema; the prompt still carries it
            reply = client.chat.completions.create(model=model, messages=messages)
    except _unavailable_errors() as error:
        raise AIUnavailable(f"{_label(provider)} is unavailable: {_short(error)}") from None
    except openai.APIError as error:
        raise AIError(f"{_label(provider)} reported an error: {_short(error)}") from None
    text = reply.choices[0].message.content if reply.choices else ""
    return parse_json(text or "")


def parse_json(text: str) -> dict:
    """The outermost {...} of a reply; tolerates code fences and a sentence before or after."""
    start, end = text.find("{"), text.rfind("}")
    try:
        answer = json.loads(text[start:end + 1]) if 0 <= start < end else None
    except json.JSONDecodeError:
        answer = None
    if not isinstance(answer, dict):
        raise AIError(f"The AI's answer was not the requested JSON: {text.strip()[:200]}")
    return answer


def _short(error: Exception) -> str:
    """The provider's own explanation. Gemini wraps its error body in a list, which hides it from the SDK."""
    body = getattr(error, "body", None)
    body = body[0] if isinstance(body, list) and body else body
    if isinstance(body, dict):
        detail = body.get("error", body)
        message = detail.get("message") if isinstance(detail, dict) else detail
        if message:
            return str(message)[:300]
    return str(getattr(error, "message", None) or error)[:300]


def ask_claude(prompt: str, schema: dict, system: str, model: str = CLAUDE_MODEL) -> dict:
    claude = shutil.which("claude")
    if not claude:
        raise AIUnavailable("The claude command is not installed.")
    with tempfile.TemporaryDirectory(prefix="clipper-ai-") as work:
        empty_mcp = Path(work) / "mcp.json"
        empty_mcp.write_text('{"mcpServers": {}}')
        cmd = [claude, "-p", "--model", model, "--setting-sources", "", "--strict-mcp-config",
               "--mcp-config", str(empty_mcp), "--disable-slash-commands", "--tools", "", "--no-session-persistence",
               "--system-prompt", system, "--output-format", "json", "--json-schema", json.dumps(schema)]
        try:
            result = subprocess.run(cmd, input=prompt, capture_output=True, text=True, cwd=work, timeout=CLAUDE_TIMEOUT_S)
        except subprocess.TimeoutExpired as error:
            raise AIUnavailable(f"Claude did not answer within {CLAUDE_TIMEOUT_S // 60} minutes.") from error
    try:
        reply = json.loads(result.stdout)
    except json.JSONDecodeError:
        message = (result.stderr or result.stdout).strip()[-400:]
        if any(hint in message.lower() for hint in LIMIT_HINTS) or result.returncode != 0:
            raise AIUnavailable(f"Claude is unavailable: {message}") from None
        raise AIError(f"Claude returned something unreadable: {message}") from None
    if reply.get("is_error"):
        message = str(reply.get("result", ""))[:400]
        if any(hint in message.lower() for hint in LIMIT_HINTS):
            raise AIUnavailable(f"Claude is unavailable: {message}")
        raise AIError(f"Claude reported an error: {message}")
    structured = reply.get("structured_output")
    if not isinstance(structured, dict):
        raise AIError("Claude's answer did not match the requested format.")
    return structured
