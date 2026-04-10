#!/usr/bin/env python3
"""
一键初始化：创建飞书群组、Bitable、工作空间表，保存配置
运行：python3 scripts/setup.py
"""
import sys, os, json, time, requests

sys.path.insert(0, os.path.dirname(__file__))
from config import BASE, AGENTS, CONFIG_FILE, TMUX_SESSION, save_runtime_config
from feishu_api import get_token, h, now_ms


def _api(method, url, token, **kwargs):
    """发送飞书 API 请求并检查返回值。"""
    r = requests.request(method, url, headers=h(token), **kwargs)
    d = r.json()
    return d


def create_bitable(token):
    """创建 Bitable 应用，返回 (app_token, default_table_id, url)。"""
    print("📊 创建 Bitable...")
    d = _api("POST", f"{BASE}/bitable/v1/apps", token,
             json={"name": f"{TMUX_SESSION}-通讯中心"})
    if d.get("code") != 0:
        print(f"❌ 创建 Bitable 失败: {d}")
        sys.exit(1)
    app_token = d["data"]["app"]["app_token"]
    default_table = d["data"]["app"]["default_table_id"]
    url = d["data"]["app"]["url"]
    print(f"   app_token: {app_token}")
    print(f"   URL: {url}\n")
    return app_token, default_table, url


def configure_inbox_table(token, bitable_token, table_id):
    """配置消息收件箱表（重命名默认表字段 + 添加新字段）。"""
    print("📬 配置消息收件箱表...")
    _api("PATCH", f"{BASE}/bitable/v1/apps/{bitable_token}/tables/{table_id}", token,
         json={"name": "消息收件箱"})

    d = _api("GET", f"{BASE}/bitable/v1/apps/{bitable_token}/tables/{table_id}/fields", token)
    fields = d.get("data", {}).get("items", [])
    field_map = {"文本": ("消息内容", 1), "单选": ("优先级", 3), "日期": ("时间", 5)}
    for f_ in fields:
        fid, fname = f_["field_id"], f_["field_name"]
        if fname in field_map:
            new_name, ftype = field_map[fname]
            body = {"field_name": new_name, "type": ftype}
            if fname == "单选":
                body["property"] = {"options": [
                    {"name": "高", "color": 0}, {"name": "中", "color": 1}, {"name": "低", "color": 2}]}
            _api("PUT", f"{BASE}/bitable/v1/apps/{bitable_token}/tables/{table_id}/fields/{fid}",
                 token, json=body)
        elif fname == "附件":
            _api("DELETE", f"{BASE}/bitable/v1/apps/{bitable_token}/tables/{table_id}/fields/{fid}",
                 token)

    for field_def in [
        {"field_name": "收件人", "type": 1},
        {"field_name": "发件人", "type": 1},
        {"field_name": "已读",   "type": 7},
    ]:
        _api("POST", f"{BASE}/bitable/v1/apps/{bitable_token}/tables/{table_id}/fields",
             token, json=field_def)
    print(f"   table_id: {table_id} ✅\n")


def create_status_table(token, bitable_token):
    """创建 Agent 状态表，写入初始状态行，返回 table_id。"""
    print("📋 创建 Agent 状态表...")
    d = _api("POST", f"{BASE}/bitable/v1/apps/{bitable_token}/tables", token,
             json={"table": {"name": "Agent状态", "fields": [
                 {"field_name": "Agent名称", "type": 1},
                 {"field_name": "角色",       "type": 1},
                 {"field_name": "状态",       "type": 3, "property": {"options": [
                     {"name": "进行中", "color": 1}, {"name": "已完成", "color": 2},
                     {"name": "阻塞",   "color": 0}, {"name": "待命",   "color": 4},
                 ]}},
                 {"field_name": "当前任务",   "type": 1},
                 {"field_name": "阻塞原因",   "type": 1},
                 {"field_name": "更新时间",   "type": 5},
             ]}})
    if d.get("code") != 0:
        print(f"❌ 创建状态表失败: {d}")
        sys.exit(1)
    sta_table = d["data"]["table_id"]

    records = [{"fields": {"Agent名称": n, "角色": info["role"], "状态": "待命",
                            "当前任务": "等待启动", "更新时间": now_ms()}}
               for n, info in AGENTS.items()]
    _api("POST", f"{BASE}/bitable/v1/apps/{bitable_token}/tables/{sta_table}/records/batch_create",
         token, json={"records": records})
    print(f"   table_id: {sta_table} ✅\n")
    return sta_table


def create_kanban_table(token, bitable_token):
    """创建项目看板表，返回 table_id（失败返回空字符串）。"""
    print("📊 创建项目看板表...")
    d = _api("POST", f"{BASE}/bitable/v1/apps/{bitable_token}/tables", token,
             json={"table": {"name": "项目看板", "fields": [
                 {"field_name": "任务ID",        "type": 1},
                 {"field_name": "标题",          "type": 1},
                 {"field_name": "状态",          "type": 3, "property": {"options": [
                     {"name": "待处理", "color": 4}, {"name": "进行中", "color": 1},
                     {"name": "已完成", "color": 2}, {"name": "已取消", "color": 5},
                 ]}},
                 {"field_name": "负责人",        "type": 1},
                 {"field_name": "Agent当前状态", "type": 1},
                 {"field_name": "Agent当前任务", "type": 1},
                 {"field_name": "任务更新时间",  "type": 5},
                 {"field_name": "Agent状态更新", "type": 5},
             ]}})
    if d.get("code") != 0:
        print(f"⚠️  创建项目看板表失败（跳过）: {d.get('msg')}")
        return ""
    tid = d["data"]["table_id"]
    print(f"   table_id: {tid} ✅\n")
    return tid


def create_workspace_tables(token, bitable_token):
    """为每个 Agent 创建工作空间表，返回 {agent_name: table_id}。"""
    print("🗂  创建工作空间表...")
    ws_tables = {}
    for agent_name, info in AGENTS.items():
        d = _api("POST", f"{BASE}/bitable/v1/apps/{bitable_token}/tables", token,
                 json={"table": {"name": f"{agent_name}（{info['role']}）工作空间",
                     "fields": [
                         {"field_name": "类型", "type": 3, "property": {"options": [
                             {"name": "状态更新", "color": 1}, {"name": "任务日志", "color": 2},
                             {"name": "消息发出", "color": 3}, {"name": "消息收到", "color": 4},
                             {"name": "产出记录", "color": 0}, {"name": "阻塞上报", "color": 5},
                         ]}},
                         {"field_name": "内容",     "type": 1},
                         {"field_name": "时间",     "type": 5},
                         {"field_name": "关联对象", "type": 1},
                     ]}})
        if d.get("code") != 0:
            print(f"   ⚠️ {agent_name}: 创建失败 — {d.get('msg')}")
            continue
        tid = d["data"]["table_id"]
        ws_tables[agent_name] = tid
        print(f"   {agent_name}: {tid} ✅")
        time.sleep(0.3)
    print()
    return ws_tables


def create_chat_group(token):
    """创建飞书群组，返回 chat_id（失败返回空字符串）。"""
    print("💬 创建飞书群组...")
    d = _api("POST", f"{BASE}/im/v1/chats", token, json={
        "name": f"🤖 {TMUX_SESSION} 协作团队",
        "description": "ClaudeTeam 多智能体协作团队",
        "chat_mode": "group",
        "chat_type": "private",
    })
    if d.get("code") != 0:
        print(f"⚠️  群组创建失败（可能缺少 im:chat 权限）: {d.get('msg')}")
        print(f"   请先在飞书开放平台添加 im:chat 权限后重新运行")
        return ""
    chat_id = d["data"]["chat_id"]
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

    token = get_token()
    print(f"✅ Token 获取成功\n")

    bitable_token, default_table, bitable_url = create_bitable(token)
    configure_inbox_table(token, bitable_token, default_table)
    sta_table = create_status_table(token, bitable_token)
    kanban_table = create_kanban_table(token, bitable_token)
    ws_tables = create_workspace_tables(token, bitable_token)
    chat_id = create_chat_group(token)

    cfg = {
        "bitable_app_token": bitable_token,
        "msg_table_id": default_table,
        "sta_table_id": sta_table,
        "kanban_table_id": kanban_table,
        "workspace_tables": ws_tables,
        "chat_id": chat_id,
    }
    save_runtime_config(cfg)
    print(f"✅ 配置已保存到 {CONFIG_FILE}")
    print()
    print("=" * 50)
    print("📊 Bitable:")
    print(f"   {bitable_url}")
    if chat_id:
        print(f"💬 飞书群组 chat_id: {chat_id}")
    print("=" * 50)

if __name__ == "__main__":
    main()
