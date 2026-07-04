"""OpenAI Codex CLI adapter.

Codex only accepts OpenAI-native model names (gpt-/o1/o3/o4/codex prefixes);
other aliases (sonnet/opus/haiku) are silently dropped so Codex falls back
to its configured default.
"""
from __future__ import annotations

import shlex
from pathlib import Path

from .base import AuthSlots, CliAdapter
from claudeteam.runtime.paths import agent_home


def codex_home(agent: str) -> str:
    """Per-agent CODEX_HOME: `<agent_home>/.codex`. Isolates each pane's
    trust config and AGENTS.md memory so sibling codex panes don't clobber
    one shared ~/.codex.
    """
    return f"{agent_home(agent)}/.codex"


def ensure_workdir_trusted(workdir: Path,
                           config_path: Path | None = None) -> None:
    """Pre-trust `workdir` in CODEX_HOME/config.toml so the first-run
    "Do you trust this directory?" prompt doesn't block a freshly-spawned
    pane. Idempotent: a no-op if the entry already exists.

    `config_path` is injectable for tests (and per-agent provisioning).
    """
    cfg = config_path or (Path.home() / ".codex" / "config.toml")
    entry = f'[projects."{workdir}"]\ntrust_level = "trusted"\n'
    if cfg.exists():
        existing = cfg.read_text(encoding="utf-8")
        if f'[projects."{workdir}"]' in existing:
            return
        cfg.write_text(existing.rstrip() + "\n\n" + entry, encoding="utf-8")
    else:
        cfg.parent.mkdir(parents=True, exist_ok=True)
        cfg.write_text(entry, encoding="utf-8")


_OPENAI_PREFIXES = ("gpt-", "o1", "o3", "o4", "codex")


class CodexCliAdapter(CliAdapter):
    def spawn_cmd(self, agent: str, model: str) -> str:
        args = ["--dangerously-bypass-approvals-and-sandbox"]
        if model and any(model.startswith(p) for p in _OPENAI_PREFIXES):
            args += ["--model", model]
        quoted = " ".join(shlex.quote(a) for a in args)
        return (f"CODEX_HOME={shlex.quote(codex_home(agent))} "
                f"CODEX_AGENT={shlex.quote(agent)} codex {quoted}")

    def display_model(self, model: str) -> str:
        # Only OpenAI-prefixed models reach codex via --model; anything
        # else is dropped and codex runs its own configured default, so
        # don't label the agent with a model it isn't running.
        if model and any(model.startswith(p) for p in _OPENAI_PREFIXES):
            return model
        return "codex 自身配置"

    def acp_argv(self, agent: str, model: str) -> list[str]:
        # Zed's codex adapter (npm i -g @zed-industries/codex-acp).
        return ["codex-acp"]

    def acp_env(self, agent: str, model: str) -> dict[str, str]:
        # Same isolation as spawn_cmd; model stays with codex's own config
        # (only OpenAI-prefixed names are meaningful — mirrors display_model).
        return {"CODEX_HOME": codex_home(agent), "CODEX_AGENT": agent}

    def native_memory_path(self, agent: str) -> str:
        # Codex reads $CODEX_HOME/AGENTS.md as global memory at session
        # start (AGENTS.override.md wins if present; we don't write it).
        # It does NOT re-read from disk after its own context compaction,
        # so a mid-session anchor change still needs a reidentify inject.
        return f"{codex_home(agent)}/AGENTS.md"

    def ready_markers(self) -> list[str]:
        # Banner lines after CLI 0.124+ becomes interactive.  Avoids matching
        # the spawn-command echo that includes "gpt-5".
        return ["OpenAI Codex", "permissions: YOLO"]

    def process_name(self) -> str:
        return "codex"

    def auth_slots(self) -> AuthSlots:
        # codex reads auth.json itself via CODEX_HOME (login = file present);
        # a token / api key blanks it so neither overrides the file.
        return AuthSlots(
            token_env="CODEX_ACCESS_TOKEN",
            api_key_envs=("OPENAI_API_KEY",),
            login_credfile=".codex/auth.json",
        )

    def submit_keys(self) -> list[str]:
        # Codex's TUI submits on plain Enter; Ctrl+J inserts a newline (verified
        # against codex-rs's tui chat_composer.rs: the KeyCode::Enter arm returns
        # Submitted, C-j inserts "\n"). Alt/Meta-Enter is NOT a reliable submit
        # under tmux, and C-j would only pile up newlines — so codex leads with
        # real Enter, with C-m (== Enter) as the lone safe escalation. Combined
        # with the settle-before-inject in wake.inject_and_confirm, this fixes the
        # first-wake "text sits in the composer unsubmitted / ♥ never" race: the
        # initial Enter is eaten by codex's paste-burst heuristic, and the
        # re-nudge then lands a standalone Enter once the banner has settled.
        return ["Enter", "C-m"]
