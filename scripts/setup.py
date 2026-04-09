#!/usr/bin/env python3
"""
一键初始化：创建飞书群组、Bitable、工作空间表，保存配置
运行：python3 scripts/setup.py
"""
import sys, os, json, time, shutil, requests

sys.path.insert(0, os.path.dirname(__file__))
from config import APP_ID, APP_SECRET, BASE, AGENTS, CONFIG_FILE, TMUX_SESSION, PROJECT_ROOT

def get_token():
    r = requests.post(f"{BASE}/auth/v3/app_access_token/internal",
                      json={"app_id": APP_ID, "app_secret": APP_SECRET})
    return r.json()["app_access_token"]

def h(token):
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

def now_ms():
    return int(time.time() * 1000)

def main():
    # 幂等性检查：如果 runtime_config.json 已存在且内容完整，跳过
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
    cfg = {}

    # ── 1. 创建 Bitable ──────────────────────────────────────
    print("📊 创建 Bitable...")
    r = requests.post(f"{BASE}/bitable/v1/apps",
                      headers=h(token), json={"name": f"{TMUX_SESSION}-通讯中心"})
    d = r.json()
    if d.get("code") != 0:
        print(f"❌ 创建 Bitable 失败: {d}")
        sys.exit(1)
    bitable_token = d["data"]["app"]["app_token"]
    default_table = d["data"]["app"]["default_table_id"]
    bitable_url   = d["data"]["app"]["url"]
    cfg["bitable_app_token"] = bitable_token
    print(f"   app_token: {bitable_token}")
    print(f"   URL: {bitable_url}\n")

    # ── 2. 配置消息收件箱表（利用默认表）────────────────────────
    print("📬 配置消息收件箱表...")
    requests.patch(f"{BASE}/bitable/v1/apps/{bitable_token}/tables/{default_table}",
                   headers=h(token), json={"name": "消息收件箱"})
    # 获取并重命名已有字段
    fr = requests.get(f"{BASE}/bitable/v1/apps/{bitable_token}/tables/{default_table}/fields",
                      headers=h(token))
    fields = fr.json().get("data", {}).get("items", [])
    for f_ in fields:
        fid, fname = f_["field_id"], f_["field_name"]
        if fname == "文本":
            requests.put(f"{BASE}/bitable/v1/apps/{bitable_token}/tables/{default_table}/fields/{fid}",
                         headers=h(token), json={"field_name": "消息内容", "type": 1})
        elif fname == "单选":
            requests.put(f"{BASE}/bitable/v1/apps/{bitable_token}/tables/{default_table}/fields/{fid}",
                         headers=h(token), json={"field_name": "优先级", "type": 3,
                             "property": {"options": [{"name": "高", "color": 0}, {"name": "中", "color": 1}, {"name": "低", "color": 2}]}})
        elif fname == "日期":
            requests.put(f"{BASE}/bitable/v1/apps/{bitable_token}/tables/{default_table}/fields/{fid}",
                         headers=h(token), json={"field_name": "时间", "type": 5})
        elif fname == "附件":
            requests.delete(f"{BASE}/bitable/v1/apps/{bitable_token}/tables/{default_table}/fields/{fid}",
                            headers=h(token))
    # 添加收/发件人、已读
    for field_def in [
        {"field_name": "收件人", "type": 1},
        {"field_name": "发件人", "type": 1},
        {"field_name": "已读",   "type": 7},
    ]:
        requests.post(f"{BASE}/bitable/v1/apps/{bitable_token}/tables/{default_table}/fields",
                      headers=h(token), json=field_def)
    cfg["msg_table_id"] = default_table
    print(f"   table_id: {default_table} ✅\n")

    # ── 3. 创建 Agent 状态表 ─────────────────────────────────
    print("📋 创建 Agent 状态表...")
    r = requests.post(f"{BASE}/bitable/v1/apps/{bitable_token}/tables",
                      headers=h(token), json={"table": {"name": "Agent状态", "fields": [
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
    d = r.json()
    sta_table = d["data"]["table_id"]
    cfg["sta_table_id"] = sta_table
    # 写入初始状态行
    records = [{"fields": {"Agent名称": n, "角色": info["role"], "状态": "待命",
                            "当前任务": "等待启动", "更新时间": now_ms()}}
               for n, info in AGENTS.items()]
    requests.post(f"{BASE}/bitable/v1/apps/{bitable_token}/tables/{sta_table}/records/batch_create",
                  headers=h(token), json={"records": records})
    print(f"   table_id: {sta_table} ✅\n")

    # ── 4. 创建项目看板表 ────────────────────────────────────────
    print("📊 创建项目看板表...")
    r = requests.post(f"{BASE}/bitable/v1/apps/{bitable_token}/tables",
        headers=h(token), json={"table": {"name": "项目看板", "fields": [
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
    d = r.json()
    if d.get("code") != 0:
        print(f"⚠️  创建项目看板表失败（跳过）: {d.get('msg')}")
        cfg["kanban_table_id"] = ""
    else:
        cfg["kanban_table_id"] = d["data"]["table_id"]
        print(f"   table_id: {cfg['kanban_table_id']} ✅\n")

    # ── 5. 创建每个 Agent 的工作空间表 ───────────────────────────
    print("🗂  创建工作空间表...")
    ws_tables = {}
    for agent_name, info in AGENTS.items():
        r = requests.post(f"{BASE}/bitable/v1/apps/{bitable_token}/tables",
                          headers=h(token), json={"table": {"name": f"{agent_name}（{info['role']}）工作空间",
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
        tid = r.json()["data"]["table_id"]
        ws_tables[agent_name] = tid
        print(f"   {agent_name}: {tid} ✅")
        time.sleep(0.3)
    cfg["workspace_tables"] = ws_tables
    print()

    # ── 6. 创建飞书群组 ───────────────────────────────────────
    print("💬 创建飞书群组...")
    r = requests.post(f"{BASE}/im/v1/chats",
                      headers=h(token), json={
                          "name": f"🤖 {TMUX_SESSION} 协作团队",
                          "description": "ClaudeTeam 多智能体协作团队",
                          "chat_mode": "group",
                          "chat_type": "private",
                      })
    d = r.json()
    if d.get("code") != 0:
        print(f"⚠️  群组创建失败（可能缺少 im:chat 权限）: {d.get('msg')}")
        print(f"   请先在飞书开放平台添加 im:chat 权限后重新运行")
        cfg["chat_id"] = ""
    else:
        chat_id = d["data"]["chat_id"]
        cfg["chat_id"] = chat_id
        print(f"   chat_id: {chat_id} ✅\n")

    # ── 7. 保存运行时配置 ─────────────────────────────────────
    with open(CONFIG_FILE, "w") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)
    print(f"✅ 配置已保存到 {CONFIG_FILE}")

    # ── 8. 生成 CLAUDE.md（运行时引导文件）──────────────────────
    claude_md = os.path.join(PROJECT_ROOT, "CLAUDE.md")
    if not os.path.exists(claude_md):
        agent_list = ", ".join(AGENTS.keys()) if AGENTS else "manager"
        with open(claude_md, "w") as f:
            f.write(f"""# ClaudeTeam — AI Multi-Agent Team

> 本文件由 setup.py 自动生成，Claude Code 启动时自动读取。

## 快速检查

- `.env` 已配置 ✅
- `team.json` 已配置 ✅
- `scripts/runtime_config.json` 已生成 ✅

## 启动团队

```bash
bash scripts/start-team.sh
```

## 你是 manager

团队已初始化完成。启动后你将成为 manager，请读取 `agents/manager/identity.md` 了解职责。

当前团队成员：{agent_list}

## 通讯规范

参见 `docs/POLICY.md`

## 团队管理

- 招聘：`/hire <角色名> "<描述>"`
- 裁撤：`/fire <角色名>`
""")
        print(f"✅ CLAUDE.md 已生成")

    print()
    print("=" * 50)
    print("📊 Bitable:")
    print(f"   {bitable_url}")
    if cfg.get("chat_id"):
        print(f"💬 飞书群组 chat_id: {cfg['chat_id']}")
    print("=" * 50)

if __name__ == "__main__":
    main()
