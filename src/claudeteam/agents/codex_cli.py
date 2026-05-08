"""OpenAI Codex CLI adapter.

Codex only accepts OpenAI-native model names (gpt-/o1/o3/o4/codex prefixes);
other aliases (sonnet/opus/haiku) are silently dropped so Codex falls back
to its configured default.
"""
from __future__ import annotations

import shlex
from pathlib import Path

from .base import CliAdapter, MULTILINE_SUBMIT_KEYS, SPINNER_CHARS


def ensure_workdir_trusted(workdir: Path,
                           config_path: Path | None = None) -> None:
    """Pre-trust `workdir` in ~/.codex/config.toml so the first-run
    "Do you trust this directory?" prompt doesn't block a freshly-spawned
    pane. Idempotent: a no-op if the entry already exists.

    `config_path` is injectable for tests.
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
        return f"CODEX_AGENT={shlex.quote(agent)} codex {quoted}"

    def ready_markers(self) -> list[str]:
        # Banner lines after CLI 0.124+ becomes interactive.  Avoids matching
        # the spawn-command echo that includes "gpt-5".
        return ["OpenAI Codex", "permissions: YOLO"]

    def busy_markers(self) -> list[str]:
        return ["esc to interrupt", "Booting MCP server", *SPINNER_CHARS]

    def process_name(self) -> str:
        return "codex"

    def submit_keys(self) -> list[str]:
        return list(MULTILINE_SUBMIT_KEYS)

    def rate_limit_markers(self) -> list[str]:
        return ["rate limit", "429", "RateLimitError", "you exceeded your"]
