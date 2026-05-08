"""`claudeteam init [--session NAME] [--force] [--upgrade]`

First-time bootstrap: writes `claudeteam.toml` (the unified config file
that replaces team.json + runtime_config.json) with sensible defaults
and inline comments.

`--upgrade` mode: scans for legacy `team.json` + `runtime_config.json`
in cwd, merges them into a `claudeteam.toml`, leaves the originals as
backup. Lets existing deployments migrate without losing their team
config.

Refuses to overwrite an existing `claudeteam.toml` unless --force.
"""
from __future__ import annotations

from claudeteam.runtime import config as _config, paths
from claudeteam.util import (
    error_exit, maybe_print_help, pop_bool_flag, pop_flag,
    reject_extra_args,
)


USAGE = "usage: claudeteam init [--session NAME] [--force] [--upgrade]"


# ── default schema as a string template (preserves comments) ─────


_DEFAULT_TOML_TEMPLATE = """\
# ClaudeTeam 配置（单文件替代 team.json + runtime_config.json）
# 每个字段都可被同名 env var 覆盖：
#   CLAUDETEAM_<PATH>_<KEY>  例 router.stale_event_threshold_s
#                            → CLAUDETEAM_ROUTER_STALE_EVENT_THRESHOLD_S
# 优先级: env > 本文件 > 代码硬编码默认

# ── 部署常量（必填）─────────────────────────────────────────
chat_id      = ""                         # 飞书群 chat_id（机器人加群后用 lark-cli 取）
lark_profile = ""                         # lark-cli profile 名, 空字符串走默认
default_model = "opus"                    # team.json agent 没指定 model 时回退到这里

# ── [team]  团队成员 ──────────────────────────────────────
[team]
session = "{session}"

# 每个 agent 一个 [team.agents.<name>]
#   cli         必填  claude-code | codex-cli | gemini-cli | kimi-code | qwen-code
#   role        必填  渲染进 identity.md
#   model       可选  缺省走 default_model
#   specialty   可选  list of strings, manager 派单时参考
#   tone        可选  字符串, 渲染进 identity 影响 LLM 输出风格
#   notes       可选  字符串, 任意 prompt 加料
#   card_color  可选  飞书 v2 色: blue/green/red/yellow/purple/orange/grey
#   lazy        可选  true=首消息触发起 CLI; 默认 false
[team.agents.manager]
cli   = "claude-code"
model = "opus"
role  = "团队主管"
card_color = "blue"

[team.agents.worker_cc]
cli   = "claude-code"
model = "sonnet"
role  = "Claude Code 员工"
card_color = "green"

[team.agents.worker_codex]
cli   = "codex-cli"
model = "gpt-5.5"
role  = "Codex 员工"
card_color = "purple"

# ── [chat.publish]  群里能看到什么消息 ─────────────────────
# sender→receiver 维度过滤; 角色: user (老板) / manager / worker
# 值: true=进群发卡  false=只走 send/inbox 不进群  "always"=不可关
# 默认全 true / "always" — 测试 / 早期阶段尽量多看到事实, 减少静默漏消息
# 的认知盲区。生产化后再针对噪声大的通道 (worker_to_manager 等) 调 false。
[chat.publish]
user_to_manager   = "always"
manager_to_user   = "always"
manager_to_worker = true
worker_to_manager = true
worker_to_user    = true
worker_to_worker  = true

# ── [limits]  消息长度上限 ────────────────────────────────
[limits]
max_card_body_chars         = 4000
auto_split_long_messages    = true
tmux_capture_default_lines  = 10
tmux_capture_max_lines      = 2000
inbox_unread_warn_threshold = 50

# ── [wake]  Pane 唤醒时序 ──────────────────────────────────
[wake]
lazy_wake_timeout_s    = 30
ready_marker_timeout_s = 60

# ── [router]  路由器守护进程 ───────────────────────────────
[router]
stale_event_threshold_s = 600
lark_call_timeout_s     = 90
alarm_card_color        = "red"

# ── [feishu]  飞书桥接 ─────────────────────────────────────
[feishu]
send_as          = "bot"
no_proxy         = true
cli_bin          = ""
broadcast_tokens = ["@team", "@all", "@everyone"]
"""


def _render_template(session: str) -> str:
    return _DEFAULT_TOML_TEMPLATE.format(session=session)


# ── --upgrade: merge legacy team.json + runtime_config.json ──────


def _upgrade_from_legacy(session: str) -> str:
    """Read existing team.json + runtime_config.json from cwd, merge
    into a single claudeteam.toml string. Caller is responsible for
    writing it.

    Strategy: start from the default template, override the relevant
    sections from legacy files. Comments preserved by string-substituting
    only known fields.
    """
    legacy_team = _config.load_team()                 # via legacy reader
    legacy_runtime = _config.load_runtime_config()    # via legacy reader

    template = _render_template(legacy_team.get("session") or session)

    # Replace chat_id / lark_profile lines
    if cid := legacy_runtime.get("chat_id"):
        template = template.replace(
            'chat_id      = ""                         #',
            f'chat_id      = "{cid}"  #', 1)
    if lp := legacy_runtime.get("lark_profile"):
        template = template.replace(
            'lark_profile = ""                         #',
            f'lark_profile = "{lp}"  #', 1)
    if dm := legacy_team.get("default_model"):
        if dm != "opus":
            template = template.replace(
                'default_model = "opus"',
                f'default_model = "{dm}"', 1)

    # Replace agent block. Drop the 3 default agents and rebuild from legacy.
    legacy_agents = legacy_team.get("agents", {})
    if legacy_agents:
        # Cut from "[team.agents.manager]" through next top-level section
        agents_start = template.find("[team.agents.manager]")
        agents_end = template.find("\n# ── [chat.publish]", agents_start)
        if agents_start != -1 and agents_end != -1:
            new_agent_block = ""
            for name, cfg in legacy_agents.items():
                lines = [f"[team.agents.{name}]"]
                lines.append(f'cli   = "{cfg.get("cli","claude-code")}"')
                if model := cfg.get("model"):
                    lines.append(f'model = "{model}"')
                if role := cfg.get("role"):
                    lines.append(f'role  = "{role}"')
                if cfg.get("lazy"):
                    lines.append("lazy  = true")
                # default card_color by name prefix
                color = ("blue" if name == "manager"
                         else "purple" if "codex" in name
                         else "orange" if "kimi" in name
                         else "yellow" if "gemini" in name
                         else "green")
                lines.append(f'card_color = "{color}"')
                new_agent_block += "\n".join(lines) + "\n\n"
            template = (template[:agents_start]
                        + new_agent_block.rstrip() + "\n"
                        + template[agents_end:])

    return template


# ── main ─────────────────────────────────────────────────────────


def main(argv: list[str]) -> int:
    rest = list(argv)
    if maybe_print_help(rest, USAGE):
        return 0
    force = pop_bool_flag(rest, "--force")
    upgrade = pop_bool_flag(rest, "--upgrade")
    session = pop_flag(rest, "--session") or "ClaudeTeam"
    if (rc := reject_extra_args(rest, USAGE)) is not None:
        return rc

    cfg_path = paths.config_file()

    if cfg_path.exists() and not force:
        return error_exit(
            f"❌ {cfg_path} already exists; pass --force to overwrite")

    if upgrade:
        # Sanity check legacy files actually exist before running merge,
        # otherwise --upgrade gives no value over plain init.
        team_path = _config.team_file()
        rt_path = _config.runtime_config_file()
        if not team_path.exists() and not rt_path.exists():
            return error_exit(
                f"❌ --upgrade: neither {team_path.name} nor {rt_path.name} "
                f"found in cwd; nothing to migrate")
        content = _upgrade_from_legacy(session)
    else:
        content = _render_template(session)

    cfg_path.write_text(content, encoding="utf-8")
    print(f"✅ wrote {cfg_path}")
    print()
    if upgrade:
        team_path = _config.team_file()
        rt_path = _config.runtime_config_file()
        print(f"  legacy {team_path.name} + {rt_path.name} preserved as backup;")
        print(f"  remove them once you've verified `claudeteam health` is green.")
    else:
        print("Next:")
        print(f"  - edit {cfg_path.name} to set chat_id + adjust agents")
        print("  - claudeteam install-hooks   # write .claude/commands/*.md")
        print(f"  - claudeteam up              # tmux session '{session}' + router + watchdog")
        print("  - claudeteam health          # verify green")
    return 0
