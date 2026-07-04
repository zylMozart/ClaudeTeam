"""CliAdapter — abstract base for agent CLI integrations.

Each concrete adapter knows how to:
  - build the shell command that spawns the CLI in a tmux pane,
  - declare which strings indicate the CLI is ready vs. busy,
  - declare its process name (for /proc walkers),
  - declare which keys submit a queued line of input.

Stripped of the old-tree extras (env_overrides, thinking_init_hint,
CliCapabilities dataclass, proxy prefix wiring).  Those return when a
concrete capability needs them, not before.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


# Submit-key sequence for multi-line CLIs (Codex / Kimi use Ink + prompt_toolkit
# style multi-line input where Enter inserts a newline, M-Enter commits the
# buffer). Plain `Enter` is kept as a fallback for single-line edge cases.
MULTILINE_SUBMIT_KEYS = ("M-Enter", "Enter", "C-m", "C-j")


@dataclass(frozen=True)
class AuthSlots:
    """Where a CLI looks for its credential, by mode. `runtime.agent_auth`
    reads this off the adapter and resolves which one to use, by priority
    long-term token > login > api_key. Per-CLI *data* lives here on the
    adapter (next to spawn_cmd / ready_markers); the *resolution* (priority,
    secrets-file read, conflict-blanking) is the CLI-agnostic algorithm in
    agent_auth.

      token_env       — env var holding a long-term token, or None if the CLI
                        has no such mode.
      api_key_envs    — env var(s) holding an API key, in preference order.
      login_credfile  — the CLI's own login creds file, relative to the agent
                        HOME (present = "logged in"), or None.
      login_token_env — if set, materialise the login file's token into this
                        env on spawn (claude's keychain-avoidance trick).
    """
    token_env: str | None
    api_key_envs: tuple[str, ...]
    login_credfile: str | None
    login_token_env: str | None = None


# Shared auth for the OpenAI-compatible workers (everything except claude-code /
# codex / kimi). They take a custom API key via OPENAI_API_KEY — the `api_key`
# tier (tier 3 of agent_auth's token > login > api_key). The *endpoint* itself is
# NOT a credential and is read from `$OPENAI_BASE_URL` by the adapter, never
# hardcoded. Returning this from `auth_slots()` makes the key flow through
# runtime.agent_auth (resolved per-agent, conflicting vars blanked, sourced from
# a private file) — the same path claude/codex use — instead of being baked into
# the adapter. The operator points OPENAI_BASE_URL / OPENAI_API_KEY at whatever
# provider they use (DeepSeek, OpenAI, a local server, …).
OPENAI_COMPAT_AUTH = AuthSlots(
    token_env=None, api_key_envs=("OPENAI_API_KEY",), login_credfile=None)


class CliAdapter(ABC):
    @abstractmethod
    def spawn_cmd(self, agent: str, model: str) -> str:
        """Full shell command (will be sent to a tmux pane via send-keys)."""

    @abstractmethod
    def ready_markers(self) -> list[str]:
        """If any string here appears in the pane, the CLI UI has booted.

        Readiness only (spawn boot-wait + lazy-wake gate). Busy / idle / dead
        are detected without markers by `runtime.pane_probe`.
        """

    @abstractmethod
    def process_name(self) -> str:
        """/proc/<pid>/comm value; used to find the CLI process under a pane."""

    def auth_slots(self) -> "AuthSlots | None":
        """Where this CLI's credential lives, for runtime.agent_auth's
        token > login > api_key resolution. Default None = "unmanaged": the
        orchestrator places / blanks nothing (e.g. kimi, which shares the
        operator's ~/.kimi with no per-agent isolation). CLIs with an
        isolated per-agent HOME override to declare their slots."""
        return None

    def submit_keys(self) -> list[str]:
        """Tmux keys to try in order to commit a line of input.

        Default: plain Enter / C-m / C-j.  Multi-line CLIs (Codex, Kimi)
        override to lead with M-Enter.
        """
        return ["Enter", "C-m", "C-j"]

    def interrupt_keys(self) -> list[str]:
        """Tmux key(s) that interrupt the CLI's CURRENT action — what `/stop`
        sends to halt an agent's in-flight turn WITHOUT killing its CLI.

        Default `Escape`, uniform across every CLI we target. Both
        claude-code and codex label Esc as the interrupt in their own UI
        ("esc to interrupt") and Esc cleanly cancels the turn (claude
        prints "Interrupted", codex "Model interrupted"); the Ink-based
        TUIs (gemini / qwen / kimi) cancel the running generation on Esc
        as well. Ctrl-C — the old `/stop` key —
        was neither consistent nor safe: a 2nd Ctrl-C quits codex, Ink CLIs
        treat it as EOF/exit, and on claude it only ambiguously interrupts
        (it dumps the typed line back into the prompt). An adapter whose CLI
        interrupts with a different key overrides this.
        """
        return ["Escape"]

    def resubmit_on_idle(self) -> bool:
        """Whether wake.inject_and_confirm may RE-SEND the submit key if the
        pane looks idle after the post-spawn identity inject (the autosubmit
        re-nudge for a pane that didn't autosubmit on respawn).

        Default True — safe on claude-code + codex (the re-nudge only lands
        when the agent isn't busy, and a stray submit on an empty prompt is
        a harmless blank line). kimi-cli overrides to False: its TUI reads
        the re-sent submit key as an interrupt ("Interrupted by user"), so
        for kimi inject_and_confirm does a plain single inject (no re-nudge)
        until kimi's submit/interrupt semantics are handled on the kimi
        track.
        """
        return True

    def clear_command(self) -> str | None:
        """The CLI's in-REPL command to clear / reset the conversation
        context. claude-code / codex / gemini / qwen / kimi all expose
        `/clear`, so that's the default; override (or return None) if a CLI
        names it differently or has none."""
        return "/clear"

    def compact_command(self) -> str | None:
        """The CLI's in-REPL command to compact / summarise context into a
        shorter form. claude-code / codex / kimi call it `/compact` (the
        default); gemini / qwen call it `/compress` and override. None if the
        CLI has no compaction command."""
        return "/compact"

    def display_model(self, model: str) -> str:
        """Human-facing model label for the agent's identity file.

        Default: the resolved team/argv `model` verbatim — correct for
        CLIs that actually run it (claude-code). CLIs that ignore that
        value and pick their own model from env/config (gemini / qwen /
        kimi) or only honour it conditionally (codex) override this, so a
        worker isn't told it's running 'opus' when it's really on its
        CLI's own configured model.
        """
        return model or "默认"

    def native_memory_reloads(self) -> bool:
        """True if the CLI re-reads its native memory file from disk on its
        own, mid-session, after a context compaction — so rewriting the
        on-disk anchor (`identity.refresh_native_memory`) actually reaches
        the running agent.

        Default False: most CLIs only load the file once at session start
        and never re-read it, so a mid-session anchor rewrite is invisible
        to the live context. Such CLIs need a reidentify re-inject to push
        a refreshed anchor (see `commands.task._refresh_anchor`). claude-code
        (re-reads CLAUDE.md after /compact) and gemini (every-prompt +
        /memory reload) override to True.
        """
        return False

    def acp_argv(self, agent: str, model: str) -> list[str] | None:
        """Argv for this CLI's ACP adapter subprocess (JSON-RPC over stdio),
        or None if the CLI has no ACP support — the agent then runs on the
        tmux-pane runner. Overriders return e.g. ["claude-code-acp"].

        When this returns non-None the agent DEFAULTS to `runner = "acp"`
        (see config.agent_runner); the operator can pin `runner = "tmux"`
        per agent to opt out."""
        return None

    def acp_env(self, agent: str, model: str) -> dict[str, str]:
        """Extra env for the ACP subprocess, merged over the inherited
        environment + the agent_auth credential resolution. Same duties as
        the env prefix in spawn_cmd (per-agent HOME, model selection,
        silence-banners flags) but as a dict — no shell quoting."""
        return {}

    def native_memory_path(self, agent: str) -> str | None:
        """Absolute path to this CLI's own always-loaded memory file
        (e.g. claude-code's ~/.claude/CLAUDE.md), or None if the CLI has
        no such file. When set, `agents.identity.write` renders the
        agent's identity + standing remember policy + memory digest there
        so it's loaded natively on every session and survives the CLI's
        /compact (unlike the one-shot init prompt).

        Default None — an adapter opts in by overriding once it has a
        per-agent HOME to anchor the file into (otherwise AGENTS.md /
        GEMINI.md in the shared working dir would collide across panes).
        Adapters that don't override keep relying on the init-prompt
        memory injection instead.
        """
        return None
