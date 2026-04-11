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
        return full.get("data", full)
    except json.JSONDecodeError:
        return None


def _extract_table_id(d):
    """从 +table-create 响应中提取 table_id（兼容多种路径）。"""
    if not d:
        return ""
    if isinstance(d.get("table"), dict):
        return d["table"].get("id", d["table"].get("table_id", ""))
    return d.get("table_id", "")


def _create_table_with_fields(base_token, table_name, fields, label=""):
    """先建空表，再逐个添加字段（每个间隔 1 秒，规避 AddField 限流）。返回 table_id。"""
    d = _lark(["base", "+table-create", "--base-token", base_token,
               "--name", table_name, "--as", "bot"],
              label=label or f"创建表 {table_name}")
    tid = _extract_table_id(d)
    if not tid:
        return ""
    for field in fields:
        time.sleep(1)
        _lark(["base", "+field-create", "--base-token", base_token,
               "--table-id", tid,
               "--json", json.dumps(field, ensure_ascii=False), "--as", "bot"],
              label=f"添加字段 {field['name']}")
    return tid


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


INBOX_FIELDS = [
    {"name": "消息内容", "type": "text"},
    {"name": "收件人", "type": "text"},
    {"name": "发件人", "type": "text"},
    {"name": "优先级", "type": "text"},
    {"name": "已读", "type": "checkbox"},
    {"name": "时间", "type": "date_time"},
]

STATUS_FIELDS = [
    {"name": "Agent名称", "type": "text"},
    {"name": "角色", "type": "text"},
    {"name": "状态", "type": "text"},
    {"name": "当前任务", "type": "text"},
    {"name": "阻塞原因", "type": "text"},
    {"name": "更新时间", "type": "date_time"},
]

KANBAN_FIELDS = [
    {"name": "任务ID", "type": "text"},
    {"name": "标题", "type": "text"},
    {"name": "状态", "type": "text"},
    {"name": "负责人", "type": "text"},
    {"name": "Agent当前状态", "type": "text"},
    {"name": "Agent当前任务", "type": "text"},
    {"name": "任务更新时间", "type": "date_time"},
    {"name": "Agent状态更新", "type": "date_time"},
]

WORKSPACE_FIELDS = [
    {"name": "类型", "type": "text"},
    {"name": "内容", "type": "text"},
    {"name": "时间", "type": "date_time"},
    {"name": "关联对象", "type": "text"},
]


def create_inbox_table(base_token):
    """创建消息收件箱表，返回 table_id。"""
    print("📬 创建消息收件箱表...")
    tid = _create_table_with_fields(base_token, "消息收件箱", INBOX_FIELDS, "创建收件箱表")
    if not tid:
        print("❌ 创建收件箱表失败"); sys.exit(1)
    print(f"   table_id: {tid} ✅\n")
    return tid


def create_status_table(base_token):
    """创建 Agent 状态表，返回 table_id。"""
    print("📋 创建 Agent 状态表...")
    tid = _create_table_with_fields(base_token, "Agent状态", STATUS_FIELDS, "创建状态表")
    if not tid:
        print("❌ 创建状态表失败"); sys.exit(1)

    rows = [[n, info["role"], "待命", "等待启动"] for n, info in AGENTS.items()]
    if rows:
        payload = json.dumps({"fields": ["Agent名称", "角色", "状态", "当前任务"],
                              "rows": rows}, ensure_ascii=False)
        _lark(["base", "+record-batch-create", "--base-token", base_token,
               "--table-id", tid, "--json", payload, "--as", "bot"],
              label="写入初始状态")
    print(f"   table_id: {tid} ✅\n")
    return tid


def create_kanban_table(base_token):
    """创建项目看板表，返回 table_id。"""
    print("📊 创建项目看板表...")
    tid = _create_table_with_fields(base_token, "项目看板", KANBAN_FIELDS, "创建看板表")
    if not tid:
        print("⚠️  创建项目看板表失败（跳过）")
        return ""
    print(f"   table_id: {tid} ✅\n")
    return tid


def create_workspace_tables(base_token):
    """为每个 Agent 创建工作空间表，返回 {agent_name: table_id}。"""
    print("🗂  创建工作空间表...")
    ws_tables = {}
    for agent_name, info in AGENTS.items():
        tid = _create_table_with_fields(
            base_token, f"{agent_name}（{info['role']}）工作空间",
            WORKSPACE_FIELDS, f"创建 {agent_name} 工作空间")
        if not tid:
            print(f"   ⚠️ {agent_name}: 创建失败")
            continue
        ws_tables[agent_name] = tid
        print(f"   {agent_name}: {tid} ✅")
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
    time.sleep(2)  # 等待 Bitable 初始化完成，避免后续建表报 OpenAPIAddField limited
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
