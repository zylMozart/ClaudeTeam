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
    env_str, error_exit, maybe_print_help, pop_bool_flag, pop_flag,
    reject_extra_args,
)


USAGE = ("usage: claudeteam init [--session NAME] [--force] [--upgrade] "
         "[--no-connect] [--quick]")


# ── default schema as a string template (preserves comments) ─────


_DEFAULT_TOML_TEMPLATE = """\
# ClaudeTeam 配置（单文件替代 team.json + runtime_config.json）
# 每个字段都可被同名 env var 覆盖：
#   CLAUDETEAM_<PATH>_<KEY>  例 router.stale_event_threshold_s
#                            → CLAUDETEAM_ROUTER_STALE_EVENT_THRESHOLD_S
# 优先级: env > 本文件 > 代码硬编码默认

# ── 部署常量 ────────────────────────────────────────────────
chat_id      = ""                         # 由 `claudeteam feishu connect` 注册建群后自动写入
lark_profile = ""                         # lark-cli profile 名, 空字符串走默认
default_model = "opus"                    # team.json agent 没指定 model 时回退到这里
# App ID / App Secret 不写在这里：`feishu connect` 写入 state/feishu_app.json (0600)，
# 同时供 sidecar 入站 + lark-cli 出站使用；env (FEISHU_APP_*) 仍可覆盖（Docker）。

# ── [team]  团队成员 ──────────────────────────────────────
[team]
session = "{session}"

# 每个 agent 一个 [team.agents.<name>]
#   cli         必填  claude-code | codex-cli | gemini-cli | kimi-code | qwen-code
#                     | minimax | opencode | codewhale | openclaw | trae | hermes | pi
#   role        必填  渲染进 identity.md
#   model       可选  缺省走 default_model
#   specialty   可选  list of strings, manager 派单时参考
#   tone        可选  字符串, 渲染进 identity 影响 LLM 输出风格
#   notes       可选  字符串, 任意 prompt 加料
#   playbook    可选  指向一个 .md (相对本配置文件), 作为该 agent 的角色手册;
#                     渲染进 identity (叠加在团队协议之上)。现成模板见 templates/
#   card_color  可选  飞书 v2 色: blue/green/red/yellow/purple/orange/grey
#   lazy        可选  true=首消息触发起 CLI; 默认 false
#   runner      可选  acp | tmux。缺省: CLI 有 ACP 适配器 (claude-code /
#                     codex-cli, 需 npm i -g @zed-industries/claude-code-acp
#                     或 codex-acp) 走 acp——投递有 ACK、状态精确、/stop 确定；
#                     其余 CLI 自动走 tmux。钉 "tmux" 可强制旧行为
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

# 默认就上面这两个 claude-code —— 装了 claude 就能直接跑，零额外登录（agent 复用你
# 本地的 claude 登录）。ClaudeTeam 的真正价值是混用多种 agent CLI，但那是【可选】的：
# 你本机装了哪些、登录了哪些 CLI，就解开下面注释、按需加 worker（codex / gemini / …）。
# 例（装了 codex 再加；没有就别管它）：
# [team.agents.worker_codex]
# cli   = "codex-cli"
# model = "gpt-5.5"
# role  = "Codex 员工"
# card_color = "purple"

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

# ── [standup]  定时进度巡视汇报 ───────────────────────────
# 有活儿在干时, router 每隔 interval_minutes 让 target 巡视全员并向老板
# 汇报 (每人在干什么/整体进度/卡点/下一步)。空闲时静默。/standup 立即触发。
[standup]
enabled = true
interval_minutes = 10
activity_window_minutes = 45
target = "manager"

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
# stale_event_threshold_s — 多久没事件就 self-SIGTERM 让 watchdog 重生.
# 注释掉则用平台默认 (Darwin 120 / Linux 600). 显式设了就用你的值.
# 为什么 macOS 默认更紧: lark-cli 1.0.23 macOS WebSocket subscribe 会
# silent-drop 不重连; 紧阈值让 self-restart + catchup 在 ~2 min 内补回
# 漏的事件而不是 ~10 min. Linux WebSocket 稳定, 600s 避免空闲群被反复
# 重启 (180s 太紧, 1200s 太松, 都踩过坑).
# stale_event_threshold_s     = 600
lark_call_timeout_s            = 90     # 单次 lark-cli 调用超时
alarm_card_color               = "red"  # 守护进入 cooldown 时报警卡片颜色
seen_max_lines                 = 5000   # router.seen 去重表 trim 阈值
subscribe_watchdog_period_s    = 20.0   # 内部订阅子进程健康检查周期

# ── [watchdog]  daemon 守护循环 ────────────────────────────
[watchdog]
check_interval_s        = 30    # 守护 tick 周期 (查 router 是否还活)
cred_check_interval_s   = 300   # 多久查一次 Claude OAuth 是否快过期
cred_refresh_ahead_s    = 1800  # 剩余 < 此值时强制 refresh OAuth

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


def _should_autoconnect(no_connect: bool, have_creds: bool) -> bool:
    """Whether `init` should auto-drive `feishu connect`. Only on a real
    interactive terminal — NEVER in CI / scripts / tests (no TTY), where an
    interactive QR scan would hang the process. Off-TTY callers just get the
    printed `claudeteam feishu connect` step instead. (A function so tests can
    patch it without faking a TTY.)"""
    import sys
    return not no_connect and not have_creds and sys.stdin.isatty()


def main(argv: list[str]) -> int:
    rest = list(argv)
    if maybe_print_help(rest, USAGE):
        return 0
    force = pop_bool_flag(rest, "--force")
    upgrade = pop_bool_flag(rest, "--upgrade")
    no_connect = pop_bool_flag(rest, "--no-connect")
    quick = pop_bool_flag(rest, "--quick")
    session = pop_flag(rest, "--session") or _config.default_session_name()
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
        return 0

    # First-run bot registration — replaces the old manual Playwright
    # bot-creator + `lark-cli config init`. Unless creds already exist or
    # --no-connect (CI / scripted), drop straight into `feishu connect`
    # (guided self-built app → scopes → group + creds + chat_id). `up` is
    # deliberately NOT the hook: it's idempotent / headless / watchdog-driven,
    # so an interactive prompt there would break restarts. `init` is the
    # one-time interactive entry.
    from claudeteam.feishu import lark as _lark
    have_creds = bool(_lark.load_app_creds().get("app_id")
                      or env_str("FEISHU_APP_ID"))
    if _should_autoconnect(no_connect, have_creds):
        from claudeteam.commands import feishu as _feishu
        # --quick → the one-scan PersonalAgent device-flow QR; else the guided
        # self-built-app flow. Both create the app + group + write chat_id.
        rc = _feishu.main(["connect", "--quick"] if quick else ["connect"])
        if rc != 0:
            print("\n⚠️  注册未完成；稍后重跑 `claudeteam feishu connect` 即可。")
            return rc
        print("\nNext:")
        print("  - claudeteam install-hooks   # write .claude/commands/*.md")
        print("  - claudeteam up              # 启动团队 + router + watchdog")
        print("  - claudeteam health          # verify green")
        return 0

    print("Next:")
    if not have_creds:  # --no-connect: register later, by hand
        print("  - claudeteam feishu connect  # 引导注册自建应用 + 建群（--quick 走扫码个人版）")
    else:  # creds came from env (Docker .env) — but the group/chat_id still isn't set
        print("  - 设置 chat_id：把团队群的 chat_id 填进 claudeteam.toml")
        print("      （没有群？在一台能开浏览器的机器上跑 `claudeteam feishu connect` 建群，")
        print("       把输出的 oc_... 复制进来——`up` 没有 chat_id 会直接报错）")
    print("  - claudeteam install-hooks   # write .claude/commands/*.md")
    print(f"  - claudeteam up              # tmux session '{session}' + router + watchdog")
    print("  - claudeteam health          # verify green")
    return 0
