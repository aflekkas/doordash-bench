"""Small, isolated model CLI adapters. No SDKs or direct HTTP model calls.

CLI usage numbers are kept in their original schema: providers count cached and
reasoning tokens differently. Missing usage is None, never an invented zero.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import tempfile
import time
from typing import Any


@dataclass(frozen=True)
class ModelSpec:
    id: str
    label: str
    cli: str
    model: str


DEFAULT_MODELS = (
    ModelSpec("muse", "Meta Muse Spark", "opencode", "meta/muse-spark-1.3-contributor"),
    ModelSpec("opus", "Claude Opus 5.5", "claude", "claude-opus-5-5"),
    ModelSpec("sonnet", "Claude Sonnet 5.5", "claude", "claude-sonnet-5-5"),
    ModelSpec("sol", "GPT Sol", "codex", "gpt-6.1-sol"),
    ModelSpec("astra", "GPT Astra", "codex", "gpt-6-astra"),
)

SYSTEM_PROMPT = (
    "You predict food cravings from the supplied clues. Follow the requested JSON "
    "format. Use only this prompt. Do not use tools, files, the internet, memory, "
    "or other agents. Give a short final answer without commentary."
)


def find_cli(name: str) -> str | None:
    """Honor PATH first, then conventional user-local installation locations."""
    resolved = shutil.which(name)
    if resolved:
        return resolved
    candidates = [Path.home() / ".local/bin" / name]
    if name == "opencode":
        candidates.append(Path.home() / ".opencode/bin/opencode")
    return next((str(p) for p in candidates if p.is_file() and os.access(p, os.X_OK)), None)


def _stop_process_tree(process: subprocess.Popen) -> None:
    try:
        if os.name == "posix":
            os.killpg(process.pid, signal.SIGKILL)
        else:
            process.kill()
    except ProcessLookupError:
        pass


def _execute(argv: list[str], prompt: str, cwd: Path, env: dict[str, str], timeout: float):
    process = subprocess.Popen(
        argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, cwd=cwd, env=env, start_new_session=True,
    )
    try:
        stdout, stderr = process.communicate(input=prompt, timeout=timeout)
    except subprocess.TimeoutExpired:
        _stop_process_tree(process)
        process.communicate()
        raise
    return process.returncode, stdout, stderr


def _numeric_usage(value: Any) -> dict | None:
    if not isinstance(value, dict):
        return None
    clean = {}
    for key, number in value.items():
        if isinstance(number, (int, float)) and not isinstance(number, bool):
            clean[key] = number
        elif isinstance(number, dict):
            nested = _numeric_usage(number)
            if nested:
                clean[key] = nested
    return clean or None


def _sum_usage(left: dict | None, right: dict | None) -> dict | None:
    if right is None:
        return left
    result = dict(left or {})
    for key, value in right.items():
        if isinstance(value, dict):
            result[key] = _sum_usage(result.get(key), value)
        else:
            result[key] = result.get(key, 0) + value
    return result


def _error_summary(message: str, code: int | None = None) -> str:
    """Do not publish arbitrary stderr: it may contain secrets or private paths."""
    lowered = message.lower()
    categories = (
        (("unsupported", "not supported", "invalid effort"), "CLI rejected a model option; check model and effort support."),
        (("model not found", "unknown model", "model_not_found", "does not exist"), "Requested model is unavailable; no fallback was used."),
        (("unauthorized", "authentication", "not logged in", "login", "api key", "api_key", "401"), "CLI authentication failed; sign in using the installed CLI."),
        (("rate limit", "rate_limit", "429"), "Provider rate limit reached."),
        (("budget", "cost limit", "max_budget"), "CLI spending budget reached."),
        (("permission", "403"), "Provider denied this model or request."),
    )
    for terms, summary in categories:
        if any(term in lowered for term in terms):
            return summary
    return f"CLI failed{f' with exit code {code}' if code is not None else ''}; check local CLI configuration."


def _parse_response(cli: str, stdout: str) -> dict:
    text_parts: list[str] = []
    usage = None
    reported_model = None
    tools_used = False
    completed = False
    error = None
    records = []
    # Claude's --output-format json returns one complete JSON object. The other
    # CLIs return JSONL; parsing a full object first also tolerates pretty JSON.
    try:
        parsed = json.loads(stdout)
        records = parsed if isinstance(parsed, list) else [parsed]
    except json.JSONDecodeError:
        for line in stdout.splitlines():
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    for event in records:
        if not isinstance(event, dict):
            continue
        kind = event.get("type")
        if cli == "codex":
            item = event.get("item", {})
            if isinstance(item, dict):
                item_type = item.get("type", "")
                if item_type in {"command_execution", "mcp_tool_call", "web_search", "collab_tool_call", "image_generation", "file_change"} or "tool_call" in item_type:
                    tools_used = True
                if kind == "item.completed" and item_type == "agent_message":
                    text_parts.append(item.get("text", ""))
            if kind == "turn.completed":
                completed = True
                usage = _sum_usage(usage, _numeric_usage(event.get("usage")))
            if kind in {"turn.failed", "error"}:
                error = _error_summary(json.dumps(event))
            reported_model = event.get("model", reported_model)
        elif cli == "claude":
            if kind == "result":
                completed = event.get("subtype") == "success" and not event.get("is_error", False)
                if isinstance(event.get("result"), str):
                    text_parts.append(event["result"])
                usage = _numeric_usage(event.get("usage"))
                model_usage = event.get("modelUsage", {})
                if isinstance(model_usage, dict) and model_usage:
                    reported_model = ",".join(model_usage)
                tools_used = bool(event.get("permission_denials"))
                if not completed:
                    error = _error_summary(json.dumps(event))
            message = event.get("message", {})
            if isinstance(message, dict):
                tools_used |= any(isinstance(part, dict) and part.get("type") == "tool_use" for part in message.get("content", []))
        elif cli == "opencode":
            part = event.get("part", {})
            if not isinstance(part, dict):
                part = {}
            if kind == "text" and isinstance(part.get("text"), str):
                text_parts.append(part["text"])
            if kind in {"tool_use", "tool", "tool_call"} or part.get("type") == "tool":
                tools_used = True
            if kind == "step_finish":
                usage = _sum_usage(usage, _numeric_usage(part.get("tokens")))
                if part.get("reason") in {"stop", "end-turn", "end_turn"}:
                    completed = True
                elif part.get("reason") in {"tool-calls", "tool_calls"}:
                    tools_used = True
            if kind == "error":
                error = _error_summary(json.dumps(event))
            model = part.get("model") or event.get("model")
            if isinstance(model, str):
                reported_model = model
    if tools_used:
        error = "Prediction invalid: the CLI attempted a tool call."
    answer = "\n".join(part for part in text_parts if isinstance(part, str)).strip()
    if not error and (not completed or not answer):
        error = "CLI did not produce a completed final answer."
    return {"text": answer if not error else "", "usage": usage,
            "reported_model": reported_model, "error": error,
            "status": "invalid" if tools_used else ("error" if error else "ok")}


def _opencode_environment(binary: str, cwd: Path, env: dict[str, str]) -> dict[str, str]:
    """Keep provider definitions/auth, discard global instructions and agents.

    Read resolved config privately through the CLI. Copy only its provider
    definitions into an isolated config directory; auth remains in the CLI's
    original data store. Debug output redacts secrets, so restore provider
    options from original local config references in memory. Neither config
    nor credentials are written to disk.
    """
    original = dict(env)
    original.update({"OPENCODE_PURE": "1", "OPENCODE_DISABLE_AUTOUPDATE": "1"})
    code, output, _ = _execute([binary, "debug", "config", "--pure"], "", cwd, original, 15)
    if code:
        raise ValueError("OpenCode provider configuration could not be loaded.")
    try:
        config = json.loads(output)
    except json.JSONDecodeError:
        raise ValueError("OpenCode provider configuration was not valid JSON.") from None
    providers = config.get("provider", {})
    _restore_provider_options(providers, env)
    benchmark_config = {
        "$schema": "https://opencode.ai/config.json",
        "provider": providers,
        "instructions": [], "plugin": [], "permission": "deny",
        "tools": {"*": False}, "autoupdate": False,
        "agent": {"benchmark": {"mode": "primary", "prompt": SYSTEM_PROMPT,
                                "permission": "deny", "tools": {"*": False}, "steps": 1}},
    }
    isolated = dict(env)
    for key in ("OPENCODE_CONFIG", "OPENCODE_CONFIG_CONTENT", "OPENCODE_CONFIG_DIR"):
        isolated.pop(key, None)
    isolated.update({
        "XDG_CONFIG_HOME": str(cwd / "config"),
        "OPENCODE_CONFIG_DIR": str(cwd / "config/opencode"),
        "OPENCODE_CONFIG_CONTENT": json.dumps(benchmark_config),
        "OPENCODE_DISABLE_PROJECT_CONFIG": "1", "OPENCODE_DISABLE_CLAUDE_CODE": "1",
        "OPENCODE_DISABLE_EXTERNAL_SKILLS": "1", "OPENCODE_DISABLE_AUTOUPDATE": "1",
        "OPENCODE_PURE": "1",
    })
    return isolated


def _parse_jsonc(source: str) -> dict:
    """Handle local JSONC comments/trailing commas without altering strings."""
    output = []
    index = 0
    in_string = False
    while index < len(source):
        char = source[index]
        if in_string:
            output.append(char)
            if char == "\\" and index + 1 < len(source):
                index += 1
                output.append(source[index])
            elif char == '"':
                in_string = False
        elif char == '"':
            in_string = True
            output.append(char)
        elif source[index:index + 2] == "//":
            end = source.find("\n", index)
            index = len(source) if end < 0 else end
            output.append("\n")
            continue
        elif source[index:index + 2] == "/*":
            end = source.find("*/", index + 2)
            if end < 0:
                raise ValueError("Unclosed JSONC comment")
            output.append(" ")
            index = end + 2
            continue
        else:
            output.append(char)
        index += 1
    cleaned = "".join(output)
    # JSON string tokens take precedence, so commas inside strings stay intact.
    cleaned = re.sub(r'"(?:\\.|[^"\\])*"|,\s*(?=[}\]])',
                     lambda match: match.group(0) if match.group(0).startswith('"') else "", cleaned)
    result = json.loads(cleaned)
    if not isinstance(result, dict):
        raise ValueError("OpenCode config must be an object")
    return result


def _absolute_config_reference(value: Any, directory: Path) -> Any:
    """Keep secret references for the CLI to resolve; never read key files."""
    if isinstance(value, dict):
        return {key: _absolute_config_reference(item, directory) for key, item in value.items()}
    if isinstance(value, list):
        return [_absolute_config_reference(item, directory) for item in value]
    if isinstance(value, str):
        def substitute(match):
            kind, argument = match.groups()
            if kind == "env":
                return match.group(0)
            path = Path(os.path.expanduser(argument))
            if not path.is_absolute():
                path = directory / path
            return "{file:" + str(path) + "}"
        return re.sub(r"\{(file|env):([^}]+)\}", substitute, value)
    return value


def _restore_provider_options(providers: dict, env: dict[str, str]) -> None:
    """Restore debug-redacted provider options using the CLI's config sources.

    Only provider options are read; project/agent instructions never enter the
    isolated invocation. CLI-managed OAuth/API auth remains in its auth store.
    """
    user_home = Path(env.get("HOME", str(Path.home())))
    config_home = Path(env.get("XDG_CONFIG_HOME", str(user_home / ".config"))) / "opencode"
    paths = [config_home / filename for filename in ("config.json", "opencode.json", "opencode.jsonc")]
    if env.get("OPENCODE_CONFIG"):
        paths.append(Path(env["OPENCODE_CONFIG"]).expanduser())
    if env.get("OPENCODE_CONFIG_DIR"):
        paths.extend(Path(env["OPENCODE_CONFIG_DIR"]).expanduser() / filename
                     for filename in ("opencode.json", "opencode.jsonc"))
    sources = []
    for path in paths:
        if path.is_file():
            sources.append((_parse_jsonc(path.read_text()), path.parent))
    if env.get("OPENCODE_CONFIG_CONTENT"):
        sources.append((_parse_jsonc(env["OPENCODE_CONFIG_CONTENT"]), config_home))
    def restore(redacted, original):
        if redacted == "***":
            return original
        if isinstance(redacted, dict) and isinstance(original, dict):
            return {key: restore(value, original[key]) if key in original else value
                    for key, value in redacted.items()}
        return redacted
    def has_redaction(value):
        if isinstance(value, dict):
            return any(has_redaction(item) for item in value.values())
        if isinstance(value, list):
            return any(has_redaction(item) for item in value)
        return value == "***"
    for config, directory in reversed(sources):
        for provider_id, definition in config.get("provider", {}).items():
            if provider_id not in providers or not isinstance(definition, dict):
                continue
            options = definition.get("options")
            existing = providers[provider_id].get("options", {})
            if isinstance(options, dict) and has_redaction(existing):
                providers[provider_id]["options"] = restore(existing,
                    _absolute_config_reference(options, directory))
    # Never make a request with a debug placeholder pretending to be a key.
    for definition in providers.values():
        options = definition.get("options", {})
        if has_redaction(options):
            raise ValueError("OpenCode provider options remain redacted; configure a local credential reference.")


def _command(spec: ModelSpec, binary: str, cwd: Path) -> list[str]:
    if spec.cli == "codex":
        instructions = cwd / "predictor.txt"
        instructions.write_text(SYSTEM_PROMPT)
        command = [binary, "exec", "--ignore-user-config", "--ignore-rules", "--ephemeral",
                   "--skip-git-repo-check", "--json", "-s", "read-only", "-m", spec.model,
                   "-c", 'model_reasoning_effort="low"', "-c", 'service_tier="default"',
                   "-c", "project_doc_max_bytes=0", "-c", 'web_search="disabled"',
                   "-c", f"model_instructions_file={json.dumps(str(instructions))}"]
        for feature in ("shell_tool", "unified_exec", "plugins", "apps", "multi_agent",
                        "browser_use", "computer_use", "image_generation", "skill_search",
                        "memories", "hooks", "view_image", "sleep_tool", "goals"):
            command.extend(["--disable", feature])
        return command + ["-"]
    if spec.cli == "claude":
        return [binary, "--print", "--model", spec.model, "--output-format", "json",
                "--tools", "", "--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}',
                "--disable-slash-commands", "--no-session-persistence", "--effort", "low",
                "--setting-sources", "", "--system-prompt", SYSTEM_PROMPT,
                "--max-budget-usd", "0.5", "--no-chrome", "--safe-mode"]
    if spec.cli == "opencode":
        return [binary, "run", "--pure", "--format", "json", "--model", spec.model,
                "--variant", "minimal", "--agent", "benchmark"]
    raise ValueError(f"Unsupported CLI: {spec.cli}")


def run_model(spec: ModelSpec, prompt: str, timeout: float = 120) -> dict:
    """One prediction, one requested model, no retries or silent fallbacks."""
    result = {"id": spec.id, "model": spec.model, "cli": spec.cli, "status": "error",
              "text": "", "latency_seconds": 0.0, "usage": None,
              "reported_model": None, "error": None}
    binary = find_cli(spec.cli)
    if not binary:
        result["status"] = "unavailable"
        result["error"] = f"Required CLI is not installed: {spec.cli}."
        return result
    started = time.monotonic()
    try:
        with tempfile.TemporaryDirectory(prefix="doordash-benchmark-") as folder:
            cwd = Path(folder)
            env = dict(os.environ)
            # Prevent an enclosing agent session from changing CLI startup.
            env.pop("CLAUDECODE", None)
            if spec.cli == "opencode":
                env = _opencode_environment(binary, cwd, env)
            remaining = timeout - (time.monotonic() - started)
            if remaining <= 0:
                raise subprocess.TimeoutExpired(binary, timeout)
            code, stdout, stderr = _execute(_command(spec, binary, cwd), prompt, cwd, env, remaining)
            result.update(_parse_response(spec.cli, stdout))
            if code:
                result.update(status="error", text="", error=_error_summary(stderr + stdout, code))
    except subprocess.TimeoutExpired:
        result.update(status="timeout", error=f"CLI exceeded the {timeout:g}s timeout.")
    except (OSError, ValueError):
        result.update(error="CLI could not start or load its isolated configuration.")
    result["latency_seconds"] = round(time.monotonic() - started, 3)
    return result
