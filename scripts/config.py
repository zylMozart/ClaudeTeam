#!/usr/bin/env python3
"""
配置中心 — ClaudeTeam 项目

Agent 团队定义从项目根目录 team.json 读取。
飞书认证由 lark-cli 管理（lark-cli config init）。
每个项目使用独立的 lark-cli profile，避免多项目部署冲突。
"""
import sys as _sys, os as _os, json as _json

# 项目根目录
PROJECT_ROOT = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
CONFIG_FILE  = _os.path.join(PROJECT_ROOT, "scripts", "runtime_config.json")

# ── Agent 团队定义（从 team.json 读取）─────────────────────────

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

# ── runtime_config.json 统一访问 ────────────────────────────────

_runtime_cfg = None

def load_runtime_config():
    """加载 runtime_config.json（带内存缓存）。"""
    global _runtime_cfg
    if _runtime_cfg is None:
        if _os.path.exists(CONFIG_FILE):
            with open(CONFIG_FILE) as _f:
                _runtime_cfg = _json.load(_f)
        else:
            print("❌ 未找到 runtime_config.json，请先运行 python3 scripts/setup.py")
            _sys.exit(1)
    return _runtime_cfg

def save_runtime_config(cfg):
    """保存 runtime_config.json 并刷新内存缓存。"""
    global _runtime_cfg
    _runtime_cfg = cfg
    with open(CONFIG_FILE, "w") as _f:
        _json.dump(cfg, _f, indent=2, ensure_ascii=False)


# ── lark-cli profile 隔离 ──────────────────────────────────────

def _detect_lark_profile():
    """解析当前应使用的 lark-cli profile 名称。

    优先级:
      1) LARK_CLI_PROFILE 环境变量 (显式 override,用于多团队同机部署)
      2) runtime_config.json 的 lark_profile 字段 (setup.py 初始化时写入)
      3) None = 让 lark-cli 使用其默认 profile

    runtime_config.json 里 lark_profile 可能被写成 null (历史遗留),读出来
    是 Python None,直接拼进命令行会变成 "--profile None" 让 lark-cli 报错。
    这里用 `or None` 把空串和 None 都规范化,上游就不会看到歧义值。
    """
    # 1) 环境变量 override — 支持 `LARK_CLI_PROFILE=xxx python3 scripts/setup.py`
    env_profile = _os.environ.get("LARK_CLI_PROFILE", "").strip()
    if env_profile:
        return env_profile
    # 2) runtime_config.json
    if _os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE) as _f:
                val = _json.load(_f).get("lark_profile")
            return val or None
        except Exception:
            pass
    return None


def get_lark_cli(profile=None):
    """返回带 --profile 的 lark-cli 命令前缀列表。"""
    p = profile or _detect_lark_profile()
    base = ["npx", "@larksuite/cli"]
    return base + ["--profile", p] if p else base


# 全局常量：其他脚本 from config import LARK_CLI 即可使用
LARK_CLI = get_lark_cli()


# ── per-role 模型解析 ──────────────────────────────────────────
# 每个 agent 可以在 team.json agents.<name>.model 单独声明想用的模型;
# 不声明则依次 fallback 到环境变量 CLAUDETEAM_DEFAULT_MODEL → team.json
# 顶层 default_model → 硬编码常量 DEFAULT_MODEL。任一档的值都必须落在
# ALLOWED_MODELS 白名单内,非法值直接抛 InvalidModelError,不做静默降级
# ——宁可启动失败,也不要"用错模型"的静默故障。

# 白名单 = 当前允许出现在 team.json / 环境变量里的模型标识。
# 同时收短别名 (opus/sonnet/haiku) 和精确全名 (claude-sonnet-4-6 等)。
# - 短别名随 Claude Code 升级跟随最新版本,日常使用门槛低
# - 精确全名用于生产钉版本,避免无意升级
# 新增模型必须经过 review 再加进来,不允许在调用点就地加 string。
ALLOWED_MODELS = frozenset({
    # 短别名(推荐,跟随版本)
    "opus", "sonnet", "haiku",
    # 全名(精确钉版本)
    "claude-opus-4-6", "claude-sonnet-4-6", "claude-haiku-4-5-20251001",
})

# 兜底默认。用短别名 'opus' 是刻意的:Opus 4.6 1M-context 模式的完整 ID
# 是 'claude-opus-4-6[1m]' 带方括号后缀,短别名让 Claude Code 自己决定
# 是否启用 1M 上下文,避免这个歧义把 CLI 启动搞挂。
DEFAULT_MODEL = "opus"


class InvalidModelError(ValueError):
    """team.json 或环境变量里指定了白名单外的模型。

    单独建类是为了让 start-team.sh / hire_agent.py 捕获时能区分
    "模型配置出错"和其它 ValueError。调用方看到这个异常应当 loud-fail
    并提示用户去修 team.json,而不是静默 fallback —— 静默 fallback 会
    掩盖配置错误,用户本来想跑 sonnet 结果跑了 opus 还不自知。
    """


def _read_team_fresh():
    """每次都重读 team.json,不使用 _TEAM 模块级缓存。

    hire_agent.py 刚写过 team.json 后立刻调 resolve_model_for_agent,
    如果吃模块缓存就会读到旧快照,新 agent 的 model 字段看不到。
    成本是每次多一次小文件 I/O,可接受。
    """
    team_file = _os.path.join(PROJECT_ROOT, "team.json")
    if not _os.path.exists(team_file):
        return {"agents": {}}
    with open(team_file) as f:
        return _json.load(f)


def _validate_model(model, source):
    """检查 model 是否在白名单,不在则抛 InvalidModelError。

    source 用来拼错误信息,让用户知道非法值是从哪里来的
    (team.json 某 agent / env var / team.json default_model / 硬编码默认)。
    """
    if model not in ALLOWED_MODELS:
        allowed = ", ".join(sorted(ALLOWED_MODELS))
        raise InvalidModelError(
            f"非法模型 {model!r} (来自 {source}); 允许的模型: {allowed}"
        )
    return model


def resolve_model_for_agent(agent_name):
    """解析 agent 启动时应使用的模型 ID。

    fallback 链(前面匹配就立即返回,后面的不再评估):
      1. team.json agents.<agent_name>.model
      2. 环境变量 CLAUDETEAM_DEFAULT_MODEL
      3. team.json 顶层 default_model
      4. DEFAULT_MODEL 常量 ("opus")

    每一档都要过白名单校验,非法值立即 raise InvalidModelError。
    fallback 不吞异常 —— 我们宁愿启动失败也不要"静默降级到默认"。
    返回值是已通过白名单校验的模型 ID 字符串。
    """
    team = _read_team_fresh()

    # 1. 该 agent 的专属 model
    agent_info = team.get("agents", {}).get(agent_name, {}) or {}
    model = agent_info.get("model")
    if model:
        return _validate_model(model, f"team.json agents.{agent_name}.model")

    # 2. 环境变量
    env_model = _os.environ.get("CLAUDETEAM_DEFAULT_MODEL", "").strip()
    if env_model:
        return _validate_model(env_model, "env CLAUDETEAM_DEFAULT_MODEL")

    # 3. 团队级 default
    team_default = team.get("default_model")
    if team_default:
        return _validate_model(team_default, "team.json default_model")

    # 4. 兜底常量(本身就在白名单里,但再校验一次以防有人改错 DEFAULT_MODEL)
    return _validate_model(DEFAULT_MODEL, "hardcoded DEFAULT_MODEL")


# ── CLI 入口:让 bash (start-team.sh) 取单个 agent 的模型 ──────────
# 用法: python3 scripts/config.py resolve-model <agent_name>
#   成功 → stdout 打印模型 ID, 退出码 0
#   失败 → stderr 打印错误, 退出码 1 (InvalidModelError / 其它异常)
#
# 刻意不提供 silent fallback: 配置出错时 bash 脚本应当 exit 1 让整个
# start-team.sh 中止,避免"用错模型"的静默故障。
if __name__ == "__main__":
    _argv = _sys.argv[1:]
    if len(_argv) == 2 and _argv[0] == "resolve-model":
        try:
            print(resolve_model_for_agent(_argv[1]))
        except InvalidModelError as _e:
            print(f"❌ {_e}", file=_sys.stderr)
            _sys.exit(1)
        except Exception as _e:
            print(f"❌ 解析 {_argv[1]} 模型失败: {_e}", file=_sys.stderr)
            _sys.exit(1)
    else:
        print("用法: python3 scripts/config.py resolve-model <agent_name>",
              file=_sys.stderr)
        _sys.exit(2)
