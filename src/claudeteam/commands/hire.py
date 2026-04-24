"""招聘辅助脚本 — ClaudeTeam

功能描述:
  为 /hire skill 提供飞书和 tmux 操作的辅助子命令。
  Skill 负责 team.json、目录创建、identity.md 等文本操作，
  本脚本负责需要 API 调用和进程操作的步骤。

输入输出:
  CLI 子命令:
    setup-feishu <agent_name>   — 创建飞书工作空间表，更新 runtime_config.json
    start-tmux <agent_name>     — 创建 tmux 窗口，启动 Claude，发送初始化消息

依赖:
  Python 3.6+, lark-cli (base 命令), config.py, tmux_utils.py
  底层通过 lark-cli 执行飞书 API 操作。
"""
import sys, os, json, time, re, subprocess
from pathlib import Path

_SRC_DIR = str(Path(__file__).resolve().parents[3])
if _SRC_DIR not in sys.path:
    sys.path.insert(0, _SRC_DIR)
from claudeteam.runtime.config import (PROJECT_ROOT, load_runtime_config, save_runtime_config,
                                        LARK_CLI, resolve_model_for_agent, InvalidModelError,
                                        ALLOWED_MODELS)
from claudeteam.cli_adapters import adapter_for_agent

# ── 基础工具 ──────────────────────────────────────────────────

def _lark(args, label="", timeout=30):
    r = subprocess.run(LARK_CLI + args, capture_output=True, text=True, timeout=timeout)
    if r.returncode != 0:
        print(f"   ⚠️ {label}: {r.stderr.strip()[:200]}")
        return None
    try:
        full = json.loads(r.stdout) if r.stdout.strip() else {}
        return full.get("data", full)
    except json.JSONDecodeError:
        return None

from claudeteam.commands._team_io import load_team  # noqa: F401 (re-exported)

def load_cfg():
    try:
        return load_runtime_config()
    except SystemExit:
        print("❌ 未找到 runtime_config.json，跳过飞书操作")
        return None

save_cfg = save_runtime_config

def validate_name(name):
    if not re.match(r'^[a-z0-9_-]+$', name):
        print(f"❌ 角色名 '{name}' 不合法，只允许小写字母、数字、下划线和连字符")
        sys.exit(1)

# 飞书卡片支持的 header template 颜色(剔除 grey,留给 fallback)。
# 顺序 = 分配优先级,靠前的先被用掉,从而让前几个 agent 颜色差异最大。
FEISHU_CARD_COLORS = [
    "blue",      # 主管通常占这个
    "turquoise",
    "orange",
    "purple",
    "green",
    "carmine",
    "indigo",
    "violet",
    "wathet",
    "yellow",
    "red",
]

def ensure_color(agent_name):
    """确保 team.json 里 agent_name 有独立的 color 字段。

    Bug:原 /hire skill 模板只要求写 role + emoji 不写 color,导致所有新 agent
    在群聊消息卡片上都是默认 grey,视觉上分不清楚谁发的。这个函数在 setup-feishu
    里兜底:如果该 agent 没有 color,从 FEISHU_CARD_COLORS 里挑一个当前团队还没
    用过的颜色写回 team.json。skill 已经分配了 color 的情况下 no-op。
    """
    team_file = os.path.join(PROJECT_ROOT, "team.json")
    with open(team_file) as f:
        team = json.load(f)
    agents = team.get("agents", {})
    info = agents.get(agent_name)
    if not info or info.get("color"):
        return  # 不存在或已有颜色 → 不动
    used = {a.get("color") for a in agents.values() if a.get("color")}
    pick = next((c for c in FEISHU_CARD_COLORS if c not in used), None)
    if pick is None:
        # 11 种颜色全用完了才会走到这里(超过 11 人),循环复用
        pick = FEISHU_CARD_COLORS[len(agents) % len(FEISHU_CARD_COLORS)]
    info["color"] = pick
    with open(team_file, "w") as f:
        json.dump(team, f, indent=2, ensure_ascii=False)
    print(f"🎨 为 {agent_name} 分配卡片颜色: {pick}")

def set_agent_model(agent_name, model):
    """把 --model 参数写到 team.json agents.<name>.model。

    在 setup-feishu 里调用,这样 start-tmux 之后 resolve_model_for_agent
    读 team.json 直接就有值。非法 model 在这里先 raise,避免先写了一半
    团队元数据再报错回滚。白名单检查和 config.py 保持一致。
    """
    if model not in ALLOWED_MODELS:
        allowed = ", ".join(sorted(ALLOWED_MODELS))
        print(f"❌ --model {model!r} 不在白名单内; 允许: {allowed}")
        sys.exit(1)
    team_file = os.path.join(PROJECT_ROOT, "team.json")
    with open(team_file) as f:
        team = json.load(f)
    info = team.get("agents", {}).get(agent_name)
    if not info:
        print(f"❌ {agent_name} 不在 team.json 中, 无法设置 model")
        sys.exit(1)
    old = info.get("model")
    if old == model:
        return
    info["model"] = model
    with open(team_file, "w") as f:
        json.dump(team, f, indent=2, ensure_ascii=False)
    print(f"🤖 {agent_name} 模型: {old or '(未设置)'} → {model}")

# ── 命令：setup-feishu ───────────────────────────────────────

def cmd_setup_feishu(agent_name, model=None):
    """在飞书 Bitable 中创建该 Agent 的工作空间表，并更新 runtime_config.json。"""
    validate_name(agent_name)

    # --model 传入时先持久化进 team.json,后面 start-tmux 会通过
    # resolve_model_for_agent 读到它。非法值在 set_agent_model 里立刻
    # sys.exit,不会污染后面的 ensure_color / Bitable 创建流程。
    if model:
        set_agent_model(agent_name, model)

    # 兜底:skill 忘记写 color 时自动补一个独立色
    ensure_color(agent_name)

    cfg = load_cfg()
    if cfg is None:
        sys.exit(1)

    team = load_team()
    agent_info = team["agents"].get(agent_name)
    if not agent_info:
        print(f"❌ {agent_name} 不在 team.json 中，请先添加")
        sys.exit(1)

    role = agent_info.get("role", agent_name)
    bt = cfg["bitable_app_token"]

    ws_tables = cfg.get("workspace_tables", {})
    if agent_name in ws_tables:
        print(f"⚠️  {agent_name} 的工作空间表已存在: {ws_tables[agent_name]}，跳过创建")
        return

    # 先建空表，再逐个添加字段（规避 AddField 限流）
    ws_fields = [
        {"name": "类型", "type": "text"},
        {"name": "内容", "type": "text"},
        {"name": "时间", "type": "date_time"},
        {"name": "关联对象", "type": "text"},
    ]
    d = _lark(["base", "+table-create", "--base-token", bt,
               "--name", f"{agent_name}（{role}）工作空间", "--as", "bot"],
              label="创建工作空间表")
    tid = ""
    if d:
        if isinstance(d.get("table"), dict):
            tid = d["table"].get("id", d["table"].get("table_id", ""))
        else:
            tid = d.get("table_id", "")
    if not tid:
        print(f"❌ 创建工作空间表失败: {d}")
        sys.exit(1)
    # ADR silent_swallow_remaining P1 ⑤: 字段创建失败必须 loud 退出,
    # 避免表只有一半字段却被当作成功 —— /hire 完成后 agent 用 ws_log 时才
    # 发现字段缺失,代价远高于现在 sys.exit 回退。
    for field in ws_fields:
        time.sleep(1)
        fd = _lark(["base", "+field-create", "--base-token", bt,
                    "--table-id", tid,
                    "--json", json.dumps(field, ensure_ascii=False), "--as", "bot"],
                   label=f"添加字段 {field['name']}")
        if fd is None:
            print(f"❌ 字段 {field['name']} 创建失败,{agent_name} 的工作空间表 "
                  f"({tid}) 残缺。请手动 drop table 后重跑 /hire {agent_name}.",
                  file=sys.stderr)
            sys.exit(1)
    ws_tables[agent_name] = tid
    cfg["workspace_tables"] = ws_tables
    save_cfg(cfg)
    print(f"✅ 飞书工作空间表已创建: {agent_name} → {tid}")

    # 在状态表中插入初始状态行
    st = cfg.get("sta_table_id")
    if st:
        payload = json.dumps({
            "fields": ["Agent名称", "状态", "当前任务"],
            "rows": [[agent_name, "待命", "刚入职，等待初始化"]]
        }, ensure_ascii=False)
        d_init = _lark(["base", "+record-batch-create", "--base-token", bt,
                        "--table-id", st, "--json", payload, "--as", "bot"],
                       label="写入初始状态")
        if d_init is None:
            # 初始状态行不是致命的(agent 首次 status 调用会自动 create),
            # 但要 loud 告警让用户知道状态表暂时空了一行。
            print(f"   ⚠️ 状态表初始行写入失败,{agent_name} 首次 status "
                  f"调用会自动补写", file=sys.stderr)
        else:
            print(f"✅ 状态表已添加 {agent_name} 初始记录")

# ── 命令：start-tmux ─────────────────────────────────────────

def cmd_start_tmux(agent_name):
    """创建 tmux 窗口，启动 Claude，发送初始化消息。"""
    validate_name(agent_name)

    team = load_team()
    session = team.get("session", "ClaudeTeam")

    r = subprocess.run(["tmux", "has-session", "-t", session],
                       capture_output=True, timeout=5)
    if r.returncode != 0:
        print(f"⚠️  tmux session '{session}' 不存在，跳过 tmux 启动")
        print(f"   请先运行: bash scripts/start-team.sh")
        return

    r = subprocess.run(["tmux", "has-session", "-t", f"{session}:{agent_name}"],
                       capture_output=True, timeout=5)
    if r.returncode == 0:
        print(f"⚠️  tmux 窗口 {agent_name} 已存在，跳过创建")
        return

    subprocess.run(["tmux", "new-window", "-t", session, "-n", agent_name,
                    "-c", PROJECT_ROOT], capture_output=True)
    print(f"✅ tmux 窗口 {agent_name} 已创建")

    adapter = adapter_for_agent(agent_name)
    model = resolve_model_for_agent(agent_name)
    subprocess.run(["tmux", "send-keys", "-t", f"{session}:{agent_name}",
                    adapter.spawn_cmd(agent_name, model), "Enter"],
                   capture_output=True)
    print(f"⏳ 等待 CLI 启动...")
    time.sleep(3)

    # 验证 Claude UI 确实起来了 (Bug 11 的后续防御)。
    # 没起来的时候 tmux 窗口只剩 bash prompt,后面 inject_when_idle 会把 init
    # 消息写进 bash,看起来"成功"但 agent 实际是死的。
    probe = subprocess.run(
        ["tmux", "capture-pane", "-t", f"{session}:{agent_name}", "-p", "-S", "-60"],
        capture_output=True, text=True)
    if not any(m in probe.stdout for m in adapter.ready_markers()):
        print(f"❌ {agent_name}: CLI 未能在 3 秒内进入 UI,窗口当前内容:")
        for line in probe.stdout.strip().splitlines()[-8:]:
            print(f"     | {line}")
        if "root/sudo privileges" in probe.stdout:
            print("   ↳ Claude 拒绝以 root 启动 --dangerously-skip-permissions。")
            print("     检查 IS_SANDBOX=1 是否被 shell 过滤,或改用非 root 用户。")
        else:
            print(f"   ↳ 可能 PATH 里没有 {adapter.process_name()},或参数被 shell 吞掉。")
        print(f"   ↳ 已中止 {agent_name} 的初始化,请修好后手动重试。")
        return

    from claudeteam.runtime.tmux_utils import inject_when_idle
    from claudeteam.runtime.config import resolve_thinking_for_agent
    init_msg = (
        f"你是团队的 {agent_name}。\n\n"
        f"【必读】请读取：agents/{agent_name}/identity.md — 了解你的角色和通讯规范\n"
        f"【然后立即执行】\n"
        f"1. python3 scripts/feishu_msg.py inbox {agent_name}    # 查看收件箱\n"
        f"2. python3 scripts/feishu_msg.py status {agent_name} 进行中 \"初始化完成，待命中\"\n\n"
        f"准备好后，简短汇报：你是谁、当前状态、有无未读消息。"
    )
    try:
        thinking = resolve_thinking_for_agent(agent_name)
        hint = adapter.thinking_init_hint(thinking)
        if hint:
            init_msg += f"\n\n【Thinking 指引】{hint}"
    except Exception:
        pass
    ok = inject_when_idle(session, agent_name, init_msg, wait_secs=15)
    if ok:
        print(f"✅ 初始化消息已发送到 {agent_name}")
    else:
        print(f"⚠️  初始化消息发送失败，请手动初始化 {agent_name}")

# ── main ──────────────────────────────────────────────────────

def main():
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        sys.exit(0)

    cmd = args[0]

    if cmd == "setup-feishu":
        if len(args) < 2:
            print("用法: setup-feishu <agent_name>"); sys.exit(1)
        cmd_setup_feishu(args[1])

    elif cmd == "start-tmux":
        if len(args) < 2:
            print("用法: start-tmux <agent_name>"); sys.exit(1)
        cmd_start_tmux(args[1])

    else:
        print(f"未知命令: {cmd}"); sys.exit(1)

if __name__ == "__main__":
    main()
