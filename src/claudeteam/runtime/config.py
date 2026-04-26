#!/usr/bin/env python3
"""Runtime configuration center for ClaudeTeam.

CONFIG_FILE dual-mode:
  dev  (no CLAUDETEAM_RUNTIME_ROOT): PROJECT_ROOT/scripts/runtime_config.json
  prod (CLAUDETEAM_RUNTIME_ROOT set): $CLAUDETEAM_RUNTIME_ROOT/state/runtime_config.json
"""
import sys
import os
import json
from pathlib import Path

# ── paths ─────────────────────────────────────────────────────────────────────

PROJECT_ROOT = str(Path(__file__).resolve().parents[3])

TEAM_FILE = (
    os.environ.get("CLAUDETEAM_TEAM_FILE", "").strip()
    or os.path.join(PROJECT_ROOT, "team.json")
)


def _config_file() -> str:
    root = os.environ.get("CLAUDETEAM_RUNTIME_ROOT", "").strip()
    if root:
        return os.path.join(root, "state", "runtime_config.json")
    return os.path.join(PROJECT_ROOT, "scripts", "runtime_config.json")


CONFIG_FILE = _config_file()

# ── agent team definition ─────────────────────────────────────────────────────

def _load_team():
    team_file = TEAM_FILE
    if not os.path.exists(team_file):
        print("⚠️  team.json 尚未创建。", file=sys.stderr)
        print("   如果你正在首次使用 ClaudeTeam，请用 Claude Code 打开本项目，", file=sys.stderr)
        print("   它会自动引导你完成团队配置。", file=sys.stderr)
        print(f"   或手动创建: {team_file}", file=sys.stderr)
        return {"agents": {}, "session": "ClaudeTeam"}
    try:
        with open(team_file) as f:
            return json.load(f)
    except OSError as e:
        print(f"⚠️  team.json 无法读取: {e}", file=sys.stderr)
        print("   当前按空团队继续；生产启动脚本仍会校验 team.json。", file=sys.stderr)
        return {"agents": {}, "session": "ClaudeTeam"}


_TEAM = _load_team()
AGENTS = _TEAM.get("agents", {})
TMUX_SESSION = _TEAM.get("session", "ClaudeTeam")

# ── runtime_config.json access ────────────────────────────────────────────────

_runtime_cfg = None


def load_runtime_config():
    """Load runtime_config.json (with in-memory cache)."""
    global _runtime_cfg
    if _runtime_cfg is None:
        cfg_file = _config_file()
        if os.path.exists(cfg_file):
            with open(cfg_file) as f:
                _runtime_cfg = json.load(f)
        else:
            print(f"❌ 未找到 runtime_config.json，请先运行 python3 scripts/setup.py")
            sys.exit(1)
    return _runtime_cfg


def save_runtime_config(cfg):
    """Save runtime_config.json and refresh in-memory cache."""
    global _runtime_cfg
    _runtime_cfg = cfg
    cfg_file = _config_file()
    os.makedirs(os.path.dirname(cfg_file), exist_ok=True)
    with open(cfg_file, "w") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)


# ── lark-cli profile isolation ────────────────────────────────────────────────

def _detect_lark_profile():
    env_profile = os.environ.get("LARK_CLI_PROFILE", "").strip()
    if env_profile:
        return env_profile
    cfg_file = _config_file()
    if os.path.exists(cfg_file):
        try:
            with open(cfg_file) as f:
                val = json.load(f).get("lark_profile")
            return val or None
        except Exception:
            pass
    return None


def get_lark_cli(profile=None):
    """Return lark-cli command prefix list with optional --profile."""
    p = profile or _detect_lark_profile()
    base = ["npx", "@larksuite/cli"]
    return base + ["--profile", p] if p else base


LARK_CLI = get_lark_cli()

# ── per-role model resolution ─────────────────────────────────────────────────

ALLOWED_MODELS = frozenset({
    "opus", "sonnet", "haiku",
    "opus-4-7", "sonnet-4-6", "haiku-4-5",
    "claude-opus-4-7", "claude-opus-4-6",
    "claude-sonnet-4-6", "claude-haiku-4-5-20251001",
    "gpt-5.4",
})

DEFAULT_MODEL = "opus"


class InvalidModelError(ValueError):
    """Model not in ALLOWED_MODELS whitelist."""


def _read_team_fresh():
    team_file = TEAM_FILE
    if not os.path.exists(team_file):
        return {"agents": {}}
    with open(team_file) as f:
        return json.load(f)


def _validate_model(model, source):
    if model not in ALLOWED_MODELS:
        allowed = ", ".join(sorted(ALLOWED_MODELS))
        raise InvalidModelError(
            f"非法模型 {model!r} (来自 {source}); 允许的模型: {allowed}"
        )
    return model


def resolve_model_for_agent(agent_name):
    """Resolve model ID for agent with fallback chain.

    1. team.json agents.<name>.model
    2. env CLAUDETEAM_DEFAULT_MODEL
    3. team.json default_model
    4. DEFAULT_MODEL constant
    """
    team = _read_team_fresh()

    agent_info = team.get("agents", {}).get(agent_name, {}) or {}
    model = agent_info.get("model")
    if model:
        return _validate_model(model, f"team.json agents.{agent_name}.model")

    env_model = os.environ.get("CLAUDETEAM_DEFAULT_MODEL", "").strip()
    if env_model:
        return _validate_model(env_model, "env CLAUDETEAM_DEFAULT_MODEL")

    team_default = team.get("default_model")
    if team_default:
        return _validate_model(team_default, "team.json default_model")

    return _validate_model(DEFAULT_MODEL, "hardcoded DEFAULT_MODEL")


# ── thinking level resolution ─────────────────────────────────────────────────

ALLOWED_THINKING = frozenset({"high", "default", "low", "off"})
DEFAULT_THINKING = "default"


class InvalidThinkingError(ValueError):
    """Thinking level not in ALLOWED_THINKING."""


def resolve_thinking_for_agent(agent_name):
    """Resolve thinking level for agent with fallback chain.

    1. team.json agents.<name>.thinking
    2. env CLAUDETEAM_DEFAULT_THINKING
    3. team.json default_thinking
    4. DEFAULT_THINKING constant
    """
    team = _read_team_fresh()

    agent_info = team.get("agents", {}).get(agent_name, {}) or {}
    thinking = agent_info.get("thinking")
    if thinking:
        if thinking not in ALLOWED_THINKING:
            raise InvalidThinkingError(
                f"非法 thinking {thinking!r} (来自 team.json agents.{agent_name}.thinking); "
                f"允许: {', '.join(sorted(ALLOWED_THINKING))}"
            )
        return thinking

    env_val = os.environ.get("CLAUDETEAM_DEFAULT_THINKING", "").strip()
    if env_val:
        if env_val not in ALLOWED_THINKING:
            raise InvalidThinkingError(
                f"非法 thinking {env_val!r} (来自 env CLAUDETEAM_DEFAULT_THINKING)"
            )
        return env_val

    team_default = team.get("default_thinking")
    if team_default:
        if team_default not in ALLOWED_THINKING:
            raise InvalidThinkingError(
                f"非法 thinking {team_default!r} (来自 team.json default_thinking)"
            )
        return team_default

    return DEFAULT_THINKING


# ── CLI entry ─────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    _argv = sys.argv[1:]
    if len(_argv) == 2 and _argv[0] == "resolve-model":
        try:
            print(resolve_model_for_agent(_argv[1]))
        except InvalidModelError as e:
            print(f"❌ {e}", file=sys.stderr)
            sys.exit(1)
        except Exception as e:
            print(f"❌ 解析 {_argv[1]} 模型失败: {e}", file=sys.stderr)
            sys.exit(1)
    elif len(_argv) == 2 and _argv[0] == "resolve-thinking":
        try:
            print(resolve_thinking_for_agent(_argv[1]))
        except InvalidThinkingError as e:
            print(f"❌ {e}", file=sys.stderr)
            sys.exit(1)
        except Exception as e:
            print(f"❌ 解析 {_argv[1]} thinking 失败: {e}", file=sys.stderr)
            sys.exit(1)
    else:
        print("用法: python3 -m claudeteam.runtime.config {resolve-model|resolve-thinking} <agent_name>",
              file=sys.stderr)
        sys.exit(2)
