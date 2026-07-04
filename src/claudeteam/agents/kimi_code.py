"""Moonshot Kimi Code adapter."""
from __future__ import annotations

import shlex
import sys
from pathlib import Path

if sys.version_info >= (3, 11):
    import tomllib  # type: ignore[import-not-found]
else:  # pragma: no cover — project pins >=3.10 but stdlib tomllib is 3.11+
    import tomli as tomllib  # type: ignore[no-redef]

from .base import CliAdapter


# Substrings that mark a model name as kimi/Moonshot-native (vs the team's
# claude/gpt default alias, which kimi can't run). Used to decide whether a
# team/argv `model` is safe to pass through to `kimi -m`.
_KIMI_MODEL_HINTS = ("kimi", "moonshot", "k2")


def _is_kimi_model(name: str) -> bool:
    low = name.lower()
    return any(h in low for h in _KIMI_MODEL_HINTS)


def _kimi_config_path() -> Path:
    """kimi-cli's config file (its `--config-file` default), under the pane's
    HOME. kimi uses the ambient HOME (no per-agent isolation), which
    on the container is /root → /root/.kimi/config.toml. Its own module
    function so tests can patch the location."""
    return Path.home() / ".kimi" / "config.toml"


def _kimi_default_model() -> str:
    """Best-effort read of `default_model` from kimi's config.toml so spawn
    can force-apply it via -m. Returns '' on any error (missing file / parse
    failure / unset key) → caller then omits -m."""
    try:
        path = _kimi_config_path()
        if not path.exists():
            return ""
        with open(path, "rb") as fh:
            data = tomllib.load(fh)
        val = data.get("default_model", "")
        return val.strip() if isinstance(val, str) else ""
    except Exception:
        return ""


class KimiCodeAdapter(CliAdapter):
    def spawn_cmd(self, agent: str, model: str) -> str:
        # kimi-cli 1.47 does NOT re-apply config.toml `default_model` on a
        # respawned / resumed session — first `up` works, but /restart &
        # fire→hire land on "LLM not set, send /login" (the persisted
        # ~/.kimi/sessions make kimi skip model bootstrap). So pass the
        # model EXPLICITLY with -m rather than relying on kimi reading
        # default_model itself.
        # Precedence: a kimi-valid team/argv model wins; else the config's
        # own default_model, force-applied; else omit -m (the old auto path —
        # buggy on respawn but no worse than before).
        chosen = model.strip() if (model and _is_kimi_model(model)) else _kimi_default_model()
        model_arg = f" -m {shlex.quote(chosen)}" if chosen else ""
        return (f"DISABLE_UPDATE_CHECK=1 KIMI_AGENT={shlex.quote(agent)} "
                f"kimi --yolo{model_arg}")

    def resubmit_on_idle(self) -> bool:
        # kimi-cli's TUI reads inject_and_confirm's re-sent submit key as an
        # interrupt ("Interrupted by user" + context 0.0%) — unlike
        # claude/codex where the re-nudge is clean. So kimi opts out of the
        # autosubmit re-nudge and gets a plain single inject until kimi's
        # submit/interrupt key semantics are handled on the kimi track.
        return False

    def display_model(self, model: str) -> str:
        # model is a no-op for kimi (see spawn_cmd) — it runs whatever its
        # own config selects, so don't label the agent with the team alias.
        return "kimi 自身配置"

    def native_memory_path(self, agent: str) -> str | None:
        # Deliberately no native memory file (stays the base None).
        #
        # Unlike codex/gemini/qwen, kimi has no HOME-global memory file —
        # it only loads AGENTS.md by walking the git-root→cwd chain. Giving
        # each pane an isolated, non-colliding AGENTS.md would mean moving
        # the pane's cwd off the real repo into a per-agent dir (+ --add-dir
        # the repo back), which is a real UX regression for a coding agent
        # (relative paths / ls / git default to an empty home).
        #
        # So kimi keeps cwd = repo and skips the native file. The intent
        # anchor still reaches it via the one-shot init prompt; kimi writes
        # that startup-rendered system prompt back into context after its
        # own /compact, so the anchor survives compaction. Mid-session
        # task-change refresh is handled by the reidentify fallback shared
        # with codex/qwen (none of which re-read a disk file either).
        return None

    def ready_markers(self) -> list[str]:
        return [
            "Welcome to Kimi Code CLI",
            "Send /help for help information",
            "── input",
            "context:",
        ]

    def process_name(self) -> str:
        return "kimi"

    def submit_keys(self) -> list[str]:
        # kimi-cli 1.47's TUI submits on plain Enter; M-Enter (the old
        # primary via MULTILINE_SUBMIT_KEYS) is NOT a submit in this
        # version, so `tmux.inject` — which presses only submit_keys[0] —
        # left every routed message sitting in the composer forever
        # (acceptance F-1: worker_kimi never submitted, identity turn never
        # ran; a manual bare Enter committed it immediately). C-m stays as
        # the equivalent escalation for inject_and_confirm paths.
        return ["Enter", "C-m"]
