#!/usr/bin/env python3
"""
一键初始化：创建飞书群组、Bitable、工作空间表，保存配置
底层通过 lark-cli 执行飞书 API 操作（im/base 命令）。
运行：python3 scripts/setup.py
"""
import sys, os, json, time, subprocess

sys.path.insert(0, os.path.dirname(__file__))
from config import AGENTS, CONFIG_FILE, TMUX_SESSION, save_runtime_config, get_lark_cli

LARK_CLI = get_lark_cli()  # 自动带 --profile（如已初始化）


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
              label="创建群组", timeout=60)
    chat_id = (d or {}).get("chat_id", "")
    if not chat_id:
        print("⚠️  群组创建失败（可能缺少 im:chat 权限）")
        return ""
    print(f"   chat_id: {chat_id} ✅")
    # 生成永久邀请链接
    link_data = _lark(["im", "chats", "link",
                        "--params", json.dumps({"chat_id": chat_id}),
                        "--data", '{"validity_period":"permanently"}',
                        "--as", "bot"], label="生成邀请链接")
    share_link = (link_data or {}).get("share_link", "")
    if share_link:
        print(f"   邀请链接: {share_link}")
    else:
        print("   ⚠️ 邀请链接生成失败，可稍后手动生成")
    print()
    return chat_id, share_link


def init_manager_identity():
    """为 manager 创建身份文件和目录结构（与 /hire 对其他 agent 做的一样）。

    这是一个关键步骤：manager 不走 /hire 流程，但同样需要 identity.md
    才能知道如何使用 send 命令给团队成员分发任务。没有 identity.md 的
    manager 只会在群里喊话，其他 agent 收不到任何指令。
    """
    mgr_dir = os.path.join(PROJECT_ROOT, "agents", "manager")
    identity_file = os.path.join(mgr_dir, "identity.md")

    # 幂等：已存在则跳过
    if os.path.exists(identity_file):
        print("👔 Manager 身份文件已存在，跳过")
        return

    print("👔 创建 Manager 身份文件...")

    # 创建目录结构
    for sub in ["memory/archive", "workspace", "tasks"]:
        os.makedirs(os.path.join(mgr_dir, sub), exist_ok=True)

    # 从模板生成 identity.md
    template_file = os.path.join(PROJECT_ROOT, "templates", "manager.identity.md")
    if os.path.exists(template_file):
        with open(template_file) as f:
            content = f.read()
    else:
        # 内置最小 identity（模板文件缺失时的兜底）
        content = """# 我是：manager（主管）

## 角色
团队总指挥。分配任务、协调进度、做最终决策。

## 职责
- 把用户的需求拆分为子任务，分配给合适的团队成员
- 审查下属的产出，批准或要求修改
- 回应用户（老板）在飞书群里的消息

## 通讯规范（必须遵守）
```bash
# 查看收件箱（启动后第一件事）
python3 scripts/feishu_msg.py inbox manager

# 给团队成员发任务（重要！这是分配工作的唯一方式）
python3 scripts/feishu_msg.py send <收件人> manager "<指令>" 高

# 回复群里的用户消息
python3 scripts/feishu_msg.py say manager "<回复内容>"

# 更新自己状态
python3 scripts/feishu_msg.py status manager 进行中 "<当前在做什么>"
```

## 关键规则
1. **收到用户消息后**，用 `send` 命令分发给对应的团队成员
2. **不要只在群里喊话** — 其他 agent 看不到群聊，必须用 `send` 发到收件箱

## 工作流
1. 启动 → 读取本文件 → 查飞书收件箱
2. 收到用户消息 → 用 `send` 分发任务
3. 收到团队汇报 → 用 `say` 回复群里
"""

    # 追加团队成员列表
    team_section = "\n## 团队成员\n"
    for name, info in AGENTS.items():
        if name != "manager":
            team_section += f"- **{name}**：{info.get('role', '成员')}\n"
    if team_section.strip() != "## 团队成员":
        content += team_section

    with open(identity_file, "w") as f:
        f.write(content)

    # 生成 core_memory.md
    from datetime import datetime
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    today = datetime.now().strftime("%Y-%m-%d")
    members = ", ".join(f"{n}({info.get('role','')})"
                        for n, info in AGENTS.items() if n != "manager")
    core_memory = f"""# manager 核心记忆

> 最后更新：{now}

## 关键事实
- 入职时间：{today}
- 角色：主管，负责协调团队
- 团队成员：{members}

## 当前状态
- 团队刚组建，全员待命

## 扩展记忆索引
- （按需添加）
"""
    with open(os.path.join(mgr_dir, "core_memory.md"), "w") as f:
        f.write(core_memory)

    print("   ✅ Manager identity.md + core_memory.md 已创建")


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

    # 检测当前 lark-cli profile 并保存，确保多项目隔离
    lark_profile = None
    try:
        r = subprocess.run(LARK_CLI + ["config", "show", "--format", "json"],
                           capture_output=True, text=True, timeout=10)
        if r.returncode == 0:
            cfg_data = json.loads(r.stdout)
            lark_profile = cfg_data.get("profile") or cfg_data.get("appId", "")
            if lark_profile:
                print(f"🔑 lark-cli profile: {lark_profile}")
    except Exception:
        pass

    base_token = create_bitable()
    time.sleep(2)  # 等待 Bitable 初始化完成，避免后续建表报 OpenAPIAddField limited
    msg_table = create_inbox_table(base_token)
    sta_table = create_status_table(base_token)
    kanban_table = create_kanban_table(base_token)
    ws_tables = create_workspace_tables(base_token)
    chat_id, share_link = create_chat_group()

    # 为 manager 创建身份文件（关键！没有这个 manager 无法正确分发任务）
    init_manager_identity()

    cfg = {
        "lark_profile": lark_profile,
        "bitable_app_token": base_token,
        "msg_table_id": msg_table,
        "sta_table_id": sta_table,
        "kanban_table_id": kanban_table,
        "workspace_tables": ws_tables,
        "chat_id": chat_id,
        "share_link": share_link,
    }
    save_runtime_config(cfg)
    print(f"✅ 配置已保存到 {CONFIG_FILE}")
    if share_link:
        print(f"\n📎 飞书群聊邀请链接（发给用户）:\n   {share_link}")
    print("=" * 50)

if __name__ == "__main__":
    main()
