"""Ask an AI for structured JSON.

Claude runs through the user's Claude Code login (`claude -p`) with every plugin,
hook, MCP server and skill switched off: that keeps a call to a few seconds
instead of loading the whole interactive setup. The local llama.cpp backup
arrives in a later phase.
"""

import json
import shutil
import subprocess
import tempfile
from pathlib import Path

CLAUDE_MODEL = "sonnet"
CLAUDE_TIMEOUT_S = 600
LIMIT_HINTS = ("usage limit", "rate limit", "limit reached", "not logged in", "please run /login", "credit balance", "overloaded")


class AIUnavailable(RuntimeError):
    """Claude cannot answer right now (limit, login, missing CLI). The caller may fall back."""


class AIError(RuntimeError):
    """Claude answered, but not with usable output."""


def ask_claude(prompt: str, schema: dict, system: str) -> dict:
    claude = shutil.which("claude")
    if not claude:
        raise AIUnavailable("The claude command is not installed.")
    with tempfile.TemporaryDirectory(prefix="clipper-ai-") as work:
        empty_mcp = Path(work) / "mcp.json"
        empty_mcp.write_text('{"mcpServers": {}}')
        cmd = [claude, "-p", "--model", CLAUDE_MODEL, "--setting-sources", "", "--strict-mcp-config",
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
