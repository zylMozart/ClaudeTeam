#!/usr/bin/env python3
"""
招聘辅助脚本 — ClaudeTeam

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

sys.path.insert(0, os.path.dirname(__file__))
from config import PROJECT_ROOT, load_runtime_config, save_runtime_config

LARK_CLI = ["npx", "@larksuite/cli"]

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

def load_team():
    team_file = os.path.join(PROJECT_ROOT, "team.json")
    with open(team_file) as f:
        return json.load(f)

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

# ── 命令：setup-feishu ───────────────────────────────────────

def cmd_setup_feishu(agent_name):
    """在飞书 Bitable 中创建该 Agent 的工作空间表，并更新 runtime_config.json。"""
    validate_name(agent_name)

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

    fields = json.dumps([
        {"name": "类型", "type": "text"},
        {"name": "内容", "type": "text"},
        {"name": "时间", "type": "date_time"},
        {"name": "关联对象", "type": "text"},
    ], ensure_ascii=False)
    d = _lark(["base", "+table-create", "--base-token", bt,
               "--name", f"{agent_name}（{role}）工作空间",
               "--fields", fields, "--as", "bot"],
              label="创建工作空间表")
    if not d or not d.get("table_id"):
        print(f"❌ 创建工作空间表失败")
        sys.exit(1)

    tid = d["table_id"]
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
        _lark(["base", "+record-batch-create", "--base-token", bt,
               "--table-id", st, "--json", payload, "--as", "bot"],
              label="写入初始状态")
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

    subprocess.run(["tmux", "send-keys", "-t", f"{session}:{agent_name}",
                    f"claude --dangerously-skip-permissions --name {agent_name}", "Enter"],
                   capture_output=True)
    print(f"⏳ 等待 Claude 启动...")
    time.sleep(3)

    from tmux_utils import inject_when_idle
    init_msg = (
        f"你是团队的 {agent_name}。\n\n"
        f"【必读】请读取：agents/{agent_name}/identity.md — 了解你的角色和通讯规范\n"
        f"【然后立即执行】\n"
        f"1. python3 scripts/feishu_msg.py inbox {agent_name}    # 查看收件箱\n"
        f"2. python3 scripts/feishu_msg.py status {agent_name} 进行中 \"初始化完成，待命中\"\n\n"
        f"准备好后，简短汇报：你是谁、当前状态、有无未读消息。"
    )
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
