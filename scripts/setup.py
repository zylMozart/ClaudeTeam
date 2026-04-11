#!/usr/bin/env python3
"""
一键初始化：创建飞书群组、Bitable、工作空间表，保存配置
底层通过 lark-cli 执行飞书 API 操作（im/base 命令）。
运行：python3 scripts/setup.py
"""
import sys, os, json, time, subprocess

sys.path.insert(0, os.path.dirname(__file__))
from config import AGENTS, CONFIG_FILE, TMUX_SESSION, save_runtime_config

LARK_CLI = ["npx", "@larksuite/cli"]


def _lark(args, label="", timeout=30):
    """执行 lark-cli 命令，返回 data 层 JSON。失败时打印错误并返回 None。"""
    r = subprocess.run(LARK_CLI + args, capture_output=True, text=True, timeout=timeout)
    if r.returncode != 0:
        print(f"   ⚠️ {label}: {r.stderr.strip()[:200]}")
        return None
    try:
        full = json.loads(r.stdout) if r.stdout.strip() else {}
        # lark-cli 返回 {ok, identity, data: {...}}，提取 data 层
        return full.get("data", full)
    except json.JSONDecodeError:
        return None


def create_bitable():
    """创建 Bitable，返回 base_token。"""
    print("📊 创建 Bitable...")
    d = _lark(["base", "+base-create", "--name", f"{TMUX_SESSION}-通讯中心", "--as", "bot"],
              label="创建 Bitable")
    if not d:
        print(f"❌ 创建 Bitable 失败"); sys.exit(1)
    base = d.get("base", d.get("app", d))
    base_token = base.get("base_token", base.get("app_token", ""))
    if not base_token:
        print(f"❌ 创建 Bitable 失败: 无法获取 base_token: {d}"); sys.exit(1)
    print(f"   base_token: {base_token}")
    return base_token


def create_inbox_table(base_token):
    """创建消息收件箱表，返回 table_id。"""
    print("📬 创建消息收件箱表...")
    fields = json.dumps([
        {"name": "消息内容", "type": "text"},
        {"name": "收件人", "type": "text"},
        {"name": "发件人", "type": "text"},
        {"name": "优先级", "type": "text"},
        {"name": "已读", "type": "checkbox"},
        {"name": "时间", "type": "date_time"},
    ], ensure_ascii=False)
    d = _lark(["base", "+table-create", "--base-token", base_token,
               "--name", "消息收件箱", "--fields", fields, "--as", "bot"],
              label="创建收件箱表")
    msg_table = (d or {}).get("table_id", "")
    if not msg_table:
        print(f"❌ 创建收件箱表失败: {d}"); sys.exit(1)
    print(f"   table_id: {msg_table} ✅\n")
    return msg_table


def create_status_table(base_token):
    """创建 Agent 状态表，返回 table_id。"""
    print("📋 创建 Agent 状态表...")
    fields = json.dumps([
        {"name": "Agent名称", "type": "text"},
        {"name": "角色", "type": "text"},
        {"name": "状态", "type": "text"},
        {"name": "当前任务", "type": "text"},
        {"name": "阻塞原因", "type": "text"},
        {"name": "更新时间", "type": "date_time"},
    ], ensure_ascii=False)
    d = _lark(["base", "+table-create", "--base-token", base_token,
               "--name", "Agent状态", "--fields", fields, "--as", "bot"],
              label="创建状态表")
    sta_table = (d or {}).get("table_id", "")
    if not sta_table:
        print(f"❌ 创建状态表失败: {d}"); sys.exit(1)

    # 写入初始状态
    rows = [[n, info["role"], "待命", "等待启动"] for n, info in AGENTS.items()]
    if rows:
        payload = json.dumps({"fields": ["Agent名称", "角色", "状态", "当前任务"],
                              "rows": rows}, ensure_ascii=False)
        _lark(["base", "+record-batch-create", "--base-token", base_token,
               "--table-id", sta_table, "--json", payload, "--as", "bot"],
              label="写入初始状态")
    print(f"   table_id: {sta_table} ✅\n")
    return sta_table


def create_kanban_table(base_token):
    """创建项目看板表，返回 table_id。"""
    print("📊 创建项目看板表...")
    fields = json.dumps([
        {"name": "任务ID", "type": "text"},
        {"name": "标题", "type": "text"},
        {"name": "状态", "type": "text"},
        {"name": "负责人", "type": "text"},
        {"name": "Agent当前状态", "type": "text"},
        {"name": "Agent当前任务", "type": "text"},
        {"name": "任务更新时间", "type": "date_time"},
        {"name": "Agent状态更新", "type": "date_time"},
    ], ensure_ascii=False)
    d = _lark(["base", "+table-create", "--base-token", base_token,
               "--name", "项目看板", "--fields", fields, "--as", "bot"],
              label="创建看板表")
    tid = (d or {}).get("table_id", "")
    if not tid:
        print("⚠️  创建项目看板表失败（跳过）")
        return ""
    print(f"   table_id: {tid} ✅\n")
    return tid


def create_workspace_tables(base_token):
    """为每个 Agent 创建工作空间表，返回 {agent_name: table_id}。"""
    print("🗂  创建工作空间表...")
    ws_tables = {}
    ws_fields = json.dumps([
        {"name": "类型", "type": "text"},
        {"name": "内容", "type": "text"},
        {"name": "时间", "type": "date_time"},
        {"name": "关联对象", "type": "text"},
    ], ensure_ascii=False)
    for agent_name, info in AGENTS.items():
        d = _lark(["base", "+table-create", "--base-token", base_token,
                   "--name", f"{agent_name}（{info['role']}）工作空间",
                   "--fields", ws_fields, "--as", "bot"],
                  label=f"创建 {agent_name} 工作空间")
        tid = (d or {}).get("table_id", "")
        if not tid:
            print(f"   ⚠️ {agent_name}: 创建失败")
            continue
        ws_tables[agent_name] = tid
        print(f"   {agent_name}: {tid} ✅")
        time.sleep(0.3)
    print()
    return ws_tables


def create_chat_group():
    """创建飞书群组，返回 chat_id。"""
    print("💬 创建飞书群组...")
    d = _lark(["im", "+chat-create",
               "--name", f"🤖 {TMUX_SESSION} 协作团队",
               "--description", "ClaudeTeam 多智能体协作团队",
               "--type", "private",
               "--set-bot-manager", "--as", "bot"],
              label="创建群组")
    chat_id = (d or {}).get("chat_id", "")
    if not chat_id:
        print("⚠️  群组创建失败（可能缺少 im:chat 权限）")
        return ""
    print(f"   chat_id: {chat_id} ✅\n")
    return chat_id


def main():
    # 幂等性检查
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE) as f:
            existing = json.load(f)
        required_keys = ["bitable_app_token", "msg_table_id", "sta_table_id", "chat_id"]
        if all(existing.get(k) for k in required_keys):
            print("✅ runtime_config.json 已存在且配置完整，跳过初始化。")
            print(f"   如需重新初始化，请先删除 {CONFIG_FILE}")
            return

    if not AGENTS:
        print("❌ team.json 未配置或为空，请先创建团队配置。")
        sys.exit(1)

    base_token = create_bitable()
    msg_table = create_inbox_table(base_token)
    sta_table = create_status_table(base_token)
    kanban_table = create_kanban_table(base_token)
    ws_tables = create_workspace_tables(base_token)
    chat_id = create_chat_group()

    cfg = {
        "bitable_app_token": base_token,
        "msg_table_id": msg_table,
        "sta_table_id": sta_table,
        "kanban_table_id": kanban_table,
        "workspace_tables": ws_tables,
        "chat_id": chat_id,
    }
    save_runtime_config(cfg)
    print(f"✅ 配置已保存到 {CONFIG_FILE}")
    print("=" * 50)

if __name__ == "__main__":
    main()
