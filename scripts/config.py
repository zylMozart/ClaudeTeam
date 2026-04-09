"""
飞书配置中心 — ClaudeTeam 项目

Agent 团队定义从项目根目录 team.json 读取。
敏感值从 .env 文件读取，参见 .env.example。
"""
import sys as _sys, os as _os, json as _json

# 项目根目录
import os
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_FILE  = os.path.join(PROJECT_ROOT, "scripts", "runtime_config.json")

def _load_env():
    """从项目根目录的 .env 加载环境变量（优先用 python-dotenv，否则手动解析）。"""
    _env_path = _os.path.join(PROJECT_ROOT, ".env")
    try:
        from dotenv import load_dotenv
        load_dotenv(_env_path)
    except ImportError:
        if _os.path.exists(_env_path):
            with open(_env_path) as _f:
                for _line in _f:
                    _line = _line.strip()
                    if _line and not _line.startswith("#") and "=" in _line:
                        _k, _, _v = _line.partition("=")
                        _os.environ.setdefault(_k.strip(), _v.strip())

_load_env()

def _require_env(key):
    val = _os.environ.get(key, "")
    if not val:
        print(f"❌ 环境变量 {key} 未设置，请检查 .env 文件或环境变量", file=_sys.stderr)
        _sys.exit(1)
    return val

# Feishu App
APP_ID     = _require_env("FEISHU_APP_ID")
APP_SECRET = _require_env("FEISHU_APP_SECRET")
BASE       = _os.environ.get("FEISHU_BASE", "https://open.feishu.cn/open-apis")

# Agent 团队定义（从 team.json 读取）
def _load_team():
    _team_file = _os.path.join(PROJECT_ROOT, "team.json")
    if not _os.path.exists(_team_file):
        print("⚠️  team.json 尚未创建。", file=_sys.stderr)
        print("   如果你正在首次使用 ClaudeTeam，请用 Claude Code 打开本项目，", file=_sys.stderr)
        print("   它会自动引导你完成团队配置。", file=_sys.stderr)
        print(f"   或手动创建: {_team_file}", file=_sys.stderr)
        return {"agents": {}, "session": "ClaudeTeam"}
    with open(_team_file) as _f:
        return _json.load(_f)

_TEAM = _load_team()
AGENTS = _TEAM.get("agents", {})
TMUX_SESSION = _TEAM.get("session", "ClaudeTeam")

# Router 轮询间隔（秒）
ROUTER_POLL_INTERVAL = 3   # 每 3 秒轮询群消息
