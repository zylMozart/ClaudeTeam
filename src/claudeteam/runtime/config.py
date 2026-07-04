"""Team and runtime configuration.

Two files:
  team.json       — static team layout (which agents, which CLI, which model)
  runtime_config.json — per-deployment runtime values (chat_id, lark profile)

Both paths come from env so tests get isolation by setting CLAUDETEAM_TEAM_FILE
and CLAUDETEAM_RUNTIME_CONFIG.

Schema (team.json):
    {
      "session": "ClaudeTeam",
      "agents": {
        "manager":      {"cli": "claude-code", "model": "opus", "role": "..."},
        "worker_cc":    {"cli": "claude-code", "model": "sonnet"},
        "worker_codex": {"cli": "codex-cli",   "model": "gpt-5.5"},
        "worker_kimi":  {"cli": "kimi-code"}
      },
      "default_model": "opus"
    }

Reading is no-cache (re-read on every call) so editing team.json picks up
without restart.  Writes are explicit via save_runtime_config().
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from claudeteam.util import env_path, env_str, read_json, write_json


# ── path resolution ───────────────────────────────────────────────


def team_file() -> Path:
    return env_path("CLAUDETEAM_TEAM_FILE") or Path.cwd() / "team.json"


def runtime_config_file() -> Path:
    return env_path("CLAUDETEAM_RUNTIME_CONFIG") or Path.cwd() / "runtime_config.json"


# ── team.json ────────────────────────────────────────────────────


_DEFAULT_TEAM: dict = {"session": "ClaudeTeam", "agents": {}, "default_model": "opus"}


def _read_json_lenient(path: Path, default: dict, label: str) -> dict:
    """Like util.read_json but degrades gracefully on parse / I/O errors —
    prints a stderr warning and returns the default dict instead of
    raising. Used at config load points where a malformed or unreadable
    team.json / runtime_config.json shouldn't kill every claudeteam
    command; the operator sees the warning + can still run
    `claudeteam health` to get a structured corruption report.

    Catches:
      - JSONDecodeError: file present but not valid JSON
      - OSError: PermissionError, file vanished mid-read, encoding
        errors. Ditto for "cannot access this config file"; CLI
        should still answer.
    """
    try:
        return read_json(path, dict(default))
    except json.JSONDecodeError as e:
        print(f"  ⚠️ {label} ({path}) is not valid JSON: {e}", file=sys.stderr)
    except OSError as e:
        print(f"  ⚠️ {label} ({path}) unreadable: {e}", file=sys.stderr)
    return dict(default)


def load_team() -> dict:
    """Return team config in legacy shape `{session, agents, default_model}`.

    Prefers `claudeteam.toml` `[team]` section; falls back to legacy
    `team.json` so existing deployments keep working until they migrate
    via `claudeteam init --upgrade`.
    """
    from claudeteam.runtime import tunables
    toml_team = tunables.load().get("team")
    if isinstance(toml_team, dict) and toml_team:
        return {
            "session": toml_team.get("session") or default_session_name(),
            "agents": dict(toml_team.get("agents", {})),
            "default_model": toml_team.get("default_model", "opus"),
        }
    return _read_json_lenient(team_file(), _DEFAULT_TEAM, "team.json")


# ── roster mutation (fire / hire) ────────────────────────────────
#
# `remove_agent` / `add_agent` edit whichever source `load_team` reads —
# `claudeteam.toml [team.agents.<name>]` in every real deployment, with a
# legacy `team.json` fallback. The project ships zero third-party deps and
# stdlib `tomllib` is read-only, so the toml path is a *surgical line edit*
# (excise / insert the `[team.agents.<name>]` block) that leaves the rest
# of the file — the operator's hand-written comments and formatting —
# byte-for-byte intact. No `tomli_w` / `tomlkit`.


def _toml_agent_block_span(lines: list[str], name: str) -> tuple[int, int] | None:
    """Line span [start, end) of the `[team.agents.<name>]` table within
    `lines`, or None if absent. `end` is the next section header / EOF,
    BACKED OFF past any trailing blank+comment run that contains a comment
    so comments belonging to the *following* section aren't collateral.

    Two comment cases, both "preserve, don't delete":
      • a comment ABOVE the header — span starts at the header, so it's left
        in place (often a group/divider banner that must survive);
      • a comment BELOW the block's last key and ABOVE the next header — as
        likely to document the following agent as the removed one (tester
        F-1: firing `codex` once ate `kimi`'s comment under codex's keys).
    A slightly-orphaned comment always beats a silently-deleted one."""
    header = f"[team.agents.{name}]"
    start = next((i for i, ln in enumerate(lines) if ln.strip() == header), None)
    if start is None:
        return None
    end = len(lines)
    for j in range(start + 1, len(lines)):
        if lines[j].lstrip().startswith("["):
            end = j
            break
    tail = end
    while tail > start + 1 and (
            lines[tail - 1].strip() == "" or lines[tail - 1].lstrip().startswith("#")):
        tail -= 1
    if any(lines[k].lstrip().startswith("#") for k in range(tail, end)):
        end = tail
    return (start, end)


def _toml_remove_agent_block(text: str, name: str) -> tuple[str, bool]:
    """Excise the `[team.agents.<name>]` table (header + key/value body +
    blank separators) from `text`. Returns (new_text, found). Never deletes
    a comment line — see `_toml_agent_block_span`."""
    lines = text.splitlines(keepends=True)
    span = _toml_agent_block_span(lines, name)
    if span is None:
        return text, False
    del lines[span[0]:span[1]]
    return "".join(lines), True


def _toml_extract_agent_block(text: str, name: str) -> str | None:
    """Return the raw text of the `[team.agents.<name>]` block (same span
    `_toml_remove_agent_block` would delete), or None if absent. Used to
    stash a fired agent's block VERBATIM so rehire restores it byte-for-byte
    — alignment, comments inside the block, and multi-line triple-quoted
    values all survive (the dict round-trip via `_toml_agent_block` cannot)."""
    lines = text.splitlines(keepends=True)
    span = _toml_agent_block_span(lines, name)
    if span is None:
        return None
    return "".join(lines[span[0]:span[1]])


def _toml_escape_str(s: str) -> str:
    """Escape a Python str into a valid TOML basic-string body. Critically
    escapes control chars — a multi-line value (embedded `\\n`) must NOT be
    emitted with a raw newline, which makes the whole file unparseable and
    silently drops the agent to config defaults on the next read (the
    fire→hire roster-corruption bug seen with a kimi worker)."""
    s = s.replace("\\", "\\\\").replace('"', '\\"')
    s = s.replace("\n", "\\n").replace("\t", "\\t").replace("\r", "\\r")
    return "".join(c if ord(c) >= 0x20 else f"\\u{ord(c):04x}" for c in s)


def _toml_format_value(v) -> str:
    """Minimal TOML value serializer for the agent-config value types
    (str / bool / int / float / list-of-those). No nested tables — agent
    blocks are flat key=value. NOTE: this dict-based path is the FALLBACK;
    the primary rehire path re-inserts the verbatim stashed block, which
    needs no serialization at all."""
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return str(v)
    if isinstance(v, list):
        return "[" + ", ".join(_toml_format_value(x) for x in v) + "]"
    return f'"{_toml_escape_str(str(v))}"'


def _toml_agent_block(name: str, cfg: dict) -> str:
    out = [f"[team.agents.{name}]\n"]
    for k, val in cfg.items():
        out.append(f"{k} = {_toml_format_value(val)}\n")
    return "".join(out)


def _toml_insert_block_text(text: str, block: str) -> str:
    """Insert a ready-made `[team.agents.<name>]` block (verbatim text) right
    after the last existing `[team.agents.*]` block (keeps agents grouped);
    appends at EOF if there are none. Rest of the file preserved exactly."""
    if not block.endswith("\n"):
        block += "\n"
    lines = text.splitlines(keepends=True)
    last_end = None
    i = 0
    while i < len(lines):
        if lines[i].lstrip().startswith("[team.agents."):
            j = i + 1
            while j < len(lines) and not lines[j].lstrip().startswith("["):
                j += 1
            last_end = j
            i = j
        else:
            i += 1
    if last_end is None:
        sep = "" if text == "" or text.endswith("\n\n") else (
            "\n" if text.endswith("\n") else "\n\n")
        return text + sep + block
    # Ensure a blank line separates the re-inserted block from the one above.
    insert = block if (last_end > 0 and lines[last_end - 1].strip() == "") else "\n" + block
    lines.insert(last_end, insert)
    return "".join(lines)


def _toml_insert_agent_block(text: str, name: str, cfg: dict) -> str:
    """Build a block from `cfg` (fallback path) and insert it."""
    return _toml_insert_block_text(text, _toml_agent_block(name, cfg))


def _toml_team_active() -> dict | None:
    """The `[team]` dict if claudeteam.toml is the active roster source,
    else None (→ legacy team.json)."""
    from claudeteam.runtime import tunables
    toml_team = tunables.load().get("team")
    return toml_team if (isinstance(toml_team, dict) and toml_team) else None


def remove_agent(name: str) -> tuple[bool, str]:
    """Delete `name` from the active team roster. Returns (removed, message).

    Powers the 裁员 half of `claudeteam fire`: with the agent gone from the
    roster, `start`/`up` never re-provision it (root fix for the revival
    regression). Surgical for toml (preserves the rest of the file);
    dict-delete + atomic rewrite for legacy team.json.
    """
    toml_team = _toml_team_active()
    if toml_team is not None:
        from claudeteam.runtime import paths, tunables
        if name not in toml_team.get("agents", {}):
            return (False, f"{name} is not in claudeteam.toml [team.agents]")
        cf = paths.config_file()
        try:
            text = cf.read_text(encoding="utf-8")
        except OSError as e:
            return (False, f"cannot read {cf}: {e}")
        new_text, found = _toml_remove_agent_block(text, name)
        if not found:
            return (False, f"[team.agents.{name}] block not found in {cf}")
        try:
            cf.write_text(new_text, encoding="utf-8")
        except OSError as e:
            # Guard the write like the read above: a failed rewrite must
            # report (so `fire` prints "roster removal skipped") rather than
            # crash AFTER the pane is killed + workspace archived — which
            # would strand the agent in the roster and let start/up revive it.
            return (False, f"cannot write {cf}: {e}")
        tunables.reset_cache()
        return (True, f"removed [team.agents.{name}] from {cf}")
    path = team_file()
    data = _read_json_lenient(path, _DEFAULT_TEAM, "team.json")
    agents = data.get("agents", {})
    if name not in agents:
        return (False, f"{name} is not in the team roster")
    del agents[name]
    write_json(path, data)
    return (True, f"removed {name} from {path}")


def add_agent(name: str, cfg: dict) -> tuple[bool, str]:
    """Add (or re-add) `name` to the active team roster with `cfg`.

    Powers the rehire half of `claudeteam hire`: a fired agent removed by
    `remove_agent` is restored from its archived `_roster.json` stash.
    Inserts a `[team.agents.<name>]` block into claudeteam.toml (or sets the
    key in legacy team.json)."""
    toml_team = _toml_team_active()
    if toml_team is not None:
        from claudeteam.runtime import paths, tunables
        cf = paths.config_file()
        text = cf.read_text(encoding="utf-8") if cf.exists() else ""
        # Idempotent: drop any existing block for `name` before inserting so
        # a re-add overwrites instead of emitting a second [team.agents.<name>]
        # table (two same-named tables make tomllib reject the whole file).
        text, _ = _toml_remove_agent_block(text, name)
        try:
            cf.write_text(_toml_insert_agent_block(text, name, dict(cfg)),
                          encoding="utf-8")
        except OSError as e:
            return (False, f"cannot write {cf}: {e}")
        tunables.reset_cache()
        return (True, f"added [team.agents.{name}] to {cf}")
    path = team_file()
    data = _read_json_lenient(path, _DEFAULT_TEAM, "team.json")
    data.setdefault("agents", {})[name] = dict(cfg)
    write_json(path, data)
    return (True, f"added {name} to {path}")


def extract_agent_block(name: str) -> str:
    """Raw toml text of the `[team.agents.<name>]` block, for stashing at
    fire so rehire can restore it VERBATIM (perfect field + formatting
    fidelity, immune to serialization edge cases like multi-line values).
    Returns "" when claudeteam.toml isn't the active source (legacy json →
    the dict stash is enough) or the block isn't found."""
    if _toml_team_active() is None:
        return ""
    from claudeteam.runtime import paths
    cf = paths.config_file()
    if not cf.exists():
        return ""
    try:
        block = _toml_extract_agent_block(cf.read_text(encoding="utf-8"), name)
    except OSError:
        return ""
    return block or ""


def restore_agent_block(name: str, block_text: str) -> tuple[bool, str]:
    """Re-insert a verbatim toml block (from the fire-time archive stash) for
    rehire. Returns (ok, message). (False, ...) when toml isn't the active
    source — the caller then falls back to `add_agent` with the dict stash.
    Idempotent: any existing same-named block is dropped first so we never
    emit a duplicate table."""
    if _toml_team_active() is None:
        return (False, "claudeteam.toml is not the active roster source")
    from claudeteam.runtime import paths, tunables
    cf = paths.config_file()
    text = cf.read_text(encoding="utf-8") if cf.exists() else ""
    text, _ = _toml_remove_agent_block(text, name)
    try:
        cf.write_text(_toml_insert_block_text(text, block_text), encoding="utf-8")
    except OSError as e:
        return (False, f"cannot write {cf}: {e}")
    tunables.reset_cache()
    return (True, f"restored [team.agents.{name}] block to {cf}")


def default_session_name() -> str:
    """Per-team default tmux session name, derived from the config's location —
    the same identity `state_dir` uses (减2). Without this two teams on one host
    both default to the literal "ClaudeTeam" on the shared tmux socket, so team
    B's `down`/`fire` would kill team A's panes. Used as `init`'s default AND
    `session_name()`'s fallback. Pure (no toml read) so `init` can call it
    before the toml exists."""
    from claudeteam.runtime import paths
    stem = "".join(c if (c.isalnum() or c in "_-") else "-"
                   for c in paths.config_file().parent.name) or "team"
    return f"ClaudeTeam-{stem}"


def session_name() -> str:
    return (load_team().get("session") or "").strip() or default_session_name()


def agent_names() -> list[str]:
    return sorted(load_team().get("agents", {}))


def agent_config(agent: str) -> dict:
    """Return the per-agent dict from team.json. Raises KeyError on miss."""
    agents = load_team().get("agents", {})
    if agent not in agents:
        raise KeyError(f"agent {agent!r} not in claudeteam.toml")
    return dict(agents[agent])


def agent_cli(agent: str) -> str:
    """Return the CLI identifier for an agent (defaults to 'claude-code')."""
    return agent_config(agent).get("cli", "claude-code")


def agent_runner(agent: str) -> str:
    """Which runner drives this agent: "acp" (JSON-RPC subprocess hosted by
    the router's AcpHost) or "tmux" (legacy pane + send-keys).

    Explicit per-agent `runner = "acp" | "tmux"` in claudeteam.toml wins;
    otherwise agents whose CLI has an ACP adapter default to "acp" and the
    rest fall back to "tmux". An unknown `cli` value also resolves "tmux"
    so the error surfaces on the (existing) adapter-lookup path instead of
    here."""
    try:
        cfg = agent_config(agent)
    except KeyError:
        return "tmux"
    v = str(cfg.get("runner") or "").strip().lower()
    if v in ("acp", "tmux"):
        return v
    from claudeteam.agents import get_adapter
    try:
        adapter = get_adapter(cfg.get("cli", "claude-code"))
    except KeyError:
        return "tmux"
    return "acp" if adapter.acp_argv(agent, "") is not None else "tmux"


def agent_model(agent: str) -> str:
    """Resolve model: agent-specific → CLAUDETEAM_DEFAULT_MODEL → team default → 'opus'."""
    cfg = agent_config(agent)
    return (cfg.get("model")
            or env_str("CLAUDETEAM_DEFAULT_MODEL")
            or load_team().get("default_model", "opus"))


# ── runtime_config.json ──────────────────────────────────────────


def load_runtime_config() -> dict:
    return _read_json_lenient(runtime_config_file(), {}, "runtime_config.json")


def save_runtime_config(cfg: dict) -> None:
    write_json(runtime_config_file(), cfg)


def chat_id() -> str:
    """Prefer claudeteam.toml `chat_id` (top-level), fall back to legacy
    runtime_config.json."""
    from claudeteam.runtime import tunables
    toml_val = tunables.load().get("chat_id")
    if toml_val:
        return str(toml_val)
    return load_runtime_config().get("chat_id", "")


def set_chat_id(value: str) -> tuple[bool, str]:
    """Persist the top-level `chat_id` — written by `feishu connect` after the
    bot auto-creates the team group. Replaces claudeteam.toml's top-level
    `chat_id = "..."` value in place (preserving its trailing comment), falling
    back to runtime_config.json when toml isn't the active config. Resets the
    tunables cache so a same-process read sees the new value. Idempotent."""
    import re
    from claudeteam.runtime import paths, tunables
    cf = paths.config_file()
    if cf.exists():
        text = cf.read_text(encoding="utf-8")
        pat = re.compile(r'^(chat_id\s*=\s*)"[^"]*"(.*)$', re.MULTILINE)
        new_text, n = pat.subn(lambda m: f'{m.group(1)}"{value}"{m.group(2)}', text)
        if n == 0:  # no chat_id line present — prepend one
            new_text = f'chat_id = "{value}"\n' + text
        try:
            cf.write_text(new_text, encoding="utf-8")
        except OSError as e:
            return (False, f"cannot write {cf}: {e}")
        tunables.reset_cache()
        return (True, f"set chat_id in {cf}")
    rc = load_runtime_config()
    rc["chat_id"] = value
    save_runtime_config(rc)
    return (True, f"set chat_id in {runtime_config_file()}")


def lark_profile() -> str:
    """Resolve the lark-cli profile name. Priority: env > toml > legacy json."""
    if env := env_str("LARK_CLI_PROFILE"):
        return env
    from claudeteam.runtime import tunables
    toml_val = tunables.load().get("lark_profile")
    if toml_val is not None:
        return str(toml_val)
    return load_runtime_config().get("lark_profile", "")
