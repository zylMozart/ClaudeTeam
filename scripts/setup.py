#!/usr/bin/env python3
"""
一键初始化：创建飞书群组、Bitable、工作空间表，保存配置
底层通过 lark-cli 执行飞书 API 操作（im/base 命令）。
运行：python3 scripts/setup.py
"""
import sys, os, json, time, subprocess, shutil

# pyproject.toml 声明 requires-python = ">=3.10"，但 macOS 默认 Python 3.9 + 老
# pip 在 editable install (`pip install -e .`) 上会失败而无明确版本提示，用户被
# 推到 PYTHONPATH=src 的兜底路径上当成"装好了"，后面 walrus / | 类型注解 / match
# 等 3.10+ 语法一旦命中就崩在远端守护里很难调。在 setup 入口提前拦下，给出明确
# 的升级路径 —— PYTHONPATH=src 仅是开发期 fallback，不是版本兼容靠山。
if sys.version_info < (3, 10):
    sys.stderr.write(
        "❌ ClaudeTeam 需要 Python 3.10+，当前: "
        f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro} "
        f"({sys.executable})\n"
        "   pyproject.toml 声明 requires-python>=3.10，请升级后再跑 setup。\n"
        "   推荐升级路径:\n"
        "     • macOS:   brew install python@3.11   # 然后用 python3.11 scripts/setup.py\n"
        "     • Linux:   apt/yum 装 python3.11，或用 pyenv install 3.11 / uv python install 3.11\n"
        "     • 通用:    pyenv install 3.11.* && pyenv local 3.11.*\n"
        "   注: PYTHONPATH=src 只是开发期 fallback，不能用作 3.9 的版本兼容补丁。\n"
    )
    sys.exit(1)

from claudeteam.runtime.config import (
    AGENTS,
    CONFIG_FILE,
    TMUX_SESSION,
    PROJECT_ROOT,
    get_lark_cli,
    load_runtime_config,
    load_runtime_config_from_path,
    save_runtime_config,
    scan_other_deployments,
)

LARK_CLI = get_lark_cli()  # 自动带 --profile（如已初始化）

TEAM_JSON = os.path.join(PROJECT_ROOT, "team.json")
TEAM_JSON_BACKUP = os.path.join(os.path.dirname(__file__), ".team.json.prev")


# ── 同机多团队部署的 profile 冲突检查 ─────────────────────────────

def _scan_other_deployments(current_root):
    return scan_other_deployments(current_root)


def _current_default_profile():
    """问 lark-cli **真正**的默认 profile 名字 (appId)。失败返回空字符串。

    注意 1: `lark-cli config show` 不支持 --format 参数——它的 stdout 本身
    就是 JSON + 一行尾部文本 (Config file path: ...),截出 JSON 块解析即可。

    注意 2: 这里**必须**用裸的 `npx @larksuite/cli`,不能用 LARK_CLI 常量。
    LARK_CLI 已经被 config.get_lark_cli() 注入了 `--profile <env/runtime>`,
    拿它去查 `config show` 会回显被 override 的 profile,而不是真正的默认。
    用这个返回值再去跟其他部署比较,会把 `lark_profile=null` 的其他部署
    (它们真正用的是宿主机默认 App)归一到当前 override 的 profile 上,
    结果本应零冲突的部署被全部误判。已踩过一次,不要改回去。
    """
    try:
        r = subprocess.run(["npx", "@larksuite/cli", "config", "show"],
                           capture_output=True, text=True, timeout=10)
        if r.returncode != 0:
            return ""
        text = r.stdout
        start = text.find("{")
        end = text.rfind("}")
        if start < 0 or end <= start:
            return ""
        d = json.loads(text[start:end + 1])
        return d.get("profile") or d.get("appId") or ""
    except Exception:
        return ""


def _normalize_profile(p, default_name):
    """把 None / 空串归一到当前默认 profile,方便比较。"""
    if not p:
        return default_name
    return p


def _check_profile_conflict(effective_profile, default_name):
    """扫描冲突并决定是否 abort。

    返回 effective_profile (可能被修正)。冲突且未确认时 sys.exit(1)。
    """
    deployments = _scan_other_deployments(PROJECT_ROOT)
    if not deployments:
        return effective_profile

    norm_self = _normalize_profile(effective_profile, default_name)
    conflicts = [
        d for d in deployments
        if _normalize_profile(d["lark_profile"], default_name) == norm_self
    ]
    if not conflicts:
        print(f"ℹ️  宿主机上还有 {len(deployments)} 个其他 ClaudeTeam 部署,"
              f"但它们使用不同的 lark-cli profile,不会冲突。")
        return effective_profile

    accept = os.environ.get("CLAUDE_TEAM_ACCEPT_SHARED_PROFILE", "").lower() in ("1", "yes", "true")

    print("=" * 70)
    print("⚠️  检测到 lark-cli profile 冲突")
    print(f"   当前准备使用的 profile: {norm_self}")
    print(f"   以下已有部署也在使用同一个 profile (= 同一个 Feishu App):")
    for d in conflicts:
        label = d.get("session") or "?"
        print(f"     • {d['path']}  (session={label})")
    print()
    print("   共享 profile 意味着所有团队共享一个 bot 身份 + 一条事件流。")
    print("   Router 会按 chat_id 过滤跨团队事件,但这依赖 router 代码")
    print("   的正确性,不是真正的身份隔离。")
    print()
    print("   推荐做法 (真正隔离):")
    print(f"     1) npx @larksuite/cli config init --new --name {TMUX_SESSION}")
    print("        扫码为本团队创建一个独立的 Feishu App")
    print(f"     2) LARK_CLI_PROFILE={TMUX_SESSION} python3 scripts/setup.py")
    print()
    print("   继续使用共享 profile (依赖 chat_id 过滤):")
    print("     CLAUDE_TEAM_ACCEPT_SHARED_PROFILE=1 python3 scripts/setup.py")
    print("=" * 70)

    if not accept:
        sys.exit(1)

    print("✅ CLAUDE_TEAM_ACCEPT_SHARED_PROFILE=1 已设置,继续。")
    return effective_profile


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


def _find_table_id_by_name(base_token, table_name):
    """用 +table-list 查找指定名字的表,返回 table_id 或空串。"""
    d = _lark(["base", "+table-list", "--base-token", base_token, "--as", "bot"],
              label=f"查找表 {table_name}")
    if not d:
        return ""
    items = d.get("items") or d.get("tables") or []
    for t in items:
        if t.get("name") == table_name:
            return t.get("table_id") or t.get("id") or ""
    return ""


def _list_existing_field_names(base_token, table_id):
    """用 +field-list 返回表中已存在的字段名集合。"""
    d = _lark(["base", "+field-list", "--base-token", base_token,
               "--table-id", table_id, "--as", "bot"],
              label="查列字段")
    if not d:
        return set()
    items = d.get("items") or d.get("fields") or []
    return {f.get("field_name") or f.get("name") for f in items if f.get("field_name") or f.get("name")}


def _add_field_with_backoff(base_token, table_id, field, attempts=4):
    """逐字段加字段,遇到 OpenAPIAddField limited (800004135) 指数退避重试。

    退避序列: 0s, 8s, 20s, 45s (总计最多等 73s)。再不行就 sys.exit。
    """
    backoffs = [0, 8, 20, 45][:attempts]
    for i, wait in enumerate(backoffs):
        if wait > 0:
            print(f"   ⏳ AddField 限流, 等 {wait}s 后重试字段 {field['name']} ({i+1}/{attempts})")
            time.sleep(wait)
        r = subprocess.run(
            LARK_CLI + ["base", "+field-create", "--base-token", base_token,
                        "--table-id", table_id,
                        "--json", json.dumps(field, ensure_ascii=False), "--as", "bot"],
            capture_output=True, text=True, timeout=30)
        if r.returncode == 0:
            return True
        err = (r.stderr or "") + (r.stdout or "")
        if "800004135" in err or "OpenAPIAddField limited" in err:
            continue  # 限流重试
        print(f"   ⚠️ 字段 {field['name']} 创建失败: {err.strip()[:200]}")
        return False
    print(f"   ⚠️ 字段 {field['name']} 反复限流,放弃")
    return False


def _create_table_with_fields(base_token, table_name, fields, label=""):
    """用 `+table-create --fields` 一次性创建表和字段，返回 table_id。

    P1-6 修复: 原版先建空表再逐字段 sleep(1) 添加,一张 6 字段的表要 6 秒,
    初始化 3 核心表 + N 个 workspace 会堆积到 >30 秒。kanban_sync.py:135 已经
    在用 `--fields` 一次建完的写法,这里迁移过来对齐,消除 sleep 环节。

    P0 自愈 (本地 patch): 一次性 create-with-fields 在新 Bitable 冷启动时
    容易触发 800004135 "OpenAPIAddField limited",此时表已建出来但字段只加了
    一半。原 fallback 会再试一次 +table-create,直接撞上 800010102 名字冲突。
    新路径: 失败后 +table-list 定位已存在的半成品表,+field-list 算出缺的字段,
    对每个缺字段用 _add_field_with_backoff 单独退避重试。
    """
    fields_json = json.dumps(fields, ensure_ascii=False)
    d = _lark(["base", "+table-create", "--base-token", base_token,
               "--name", table_name, "--fields", fields_json, "--as", "bot"],
              label=label or f"创建表 {table_name}")
    tid = _extract_table_id(d)
    if tid:
        # 一次性成功不代表所有字段都加上了 (Feishu 有时静默只加部分)。
        # 仍然跑一次字段补齐,幂等的。
        existing = _list_existing_field_names(base_token, tid)
        missing = [f for f in fields if f["name"] not in existing]
        if missing:
            print(f"   🩹 一次性建表返回 ok 但缺 {len(missing)}/{len(fields)} 个字段, 补齐中")
            for f in missing:
                if not _add_field_with_backoff(base_token, tid, f):
                    print(f"❌ 表 {table_name} ({tid}) 补字段失败,请手动删表重跑",
                          file=sys.stderr)
                    sys.exit(1)
        return tid

    # ── 自愈路径: 一次性建表失败 (通常是 AddField 限流) ──
    print(f"   🩹 一次性建表失败, 尝试自愈: 查找半成品表 + 补字段")
    tid = _find_table_id_by_name(base_token, table_name)
    if not tid:
        # 表根本没建出来 (纯网络 / 权限错误), 回退到老的空表+逐字段
        print(f"   ↩️  未发现半成品表, 回退到空表+逐字段模式")
        d = _lark(["base", "+table-create", "--base-token", base_token,
                   "--name", table_name, "--as", "bot"],
                  label=(label or f"创建表 {table_name}") + " (fallback)")
        tid = _extract_table_id(d)
        if not tid:
            return ""

    existing = _list_existing_field_names(base_token, tid)
    missing = [f for f in fields if f["name"] not in existing]
    print(f"   🩹 表 {tid}: 现存字段 {len(existing)}, 缺 {len(missing)}/{len(fields)}")
    for f in missing:
        if not _add_field_with_backoff(base_token, tid, f):
            print(f"❌ 字段 {f['name']} 创建失败,表 {table_name} "
                  f"({tid}) 处于残缺状态,请手动删除后重跑 setup.py",
                  file=sys.stderr)
            sys.exit(1)
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

BOSS_TODO_TABLE_NAME = "老板代办"
BOSS_TODO_DEDUPE_KEYS = ["来源任务", "标题"]
BOSS_TODO_FIELDS = [
    {"name": "标题", "type": "text"},
    {"name": "状态", "type": "text"},
    {"name": "优先级", "type": "text"},
    {"name": "来源任务", "type": "text"},
    {"name": "来源类型", "type": "text"},
    {"name": "创建人", "type": "text"},
    {"name": "负责人", "type": "text"},
    {"name": "截止时间", "type": "text"},
    {"name": "最新备注", "type": "text"},
    {"name": "关联消息", "type": "text"},
    {"name": "创建时间", "type": "date_time"},
    {"name": "更新时间", "type": "date_time"},
    {"name": "完成时间", "type": "date_time"},
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
        # ADR silent_swallow_remaining (architect 补丁): 和 hire_agent.py 的
        # 状态表初始行写入对称, 失败走 warn 降级不 sys.exit —— 首次 setup 被
        # 限流时状态表空行仍然让 setup 走完, 各 agent 首次 status 会自动补 create。
        d_init = _lark(["base", "+record-batch-create", "--base-token", base_token,
                        "--table-id", tid, "--json", payload, "--as", "bot"],
                       label="写入初始状态")
        if d_init is None:
            print("   ⚠️ 状态表初始行写入失败, 各 agent 首次 status "
                  "会自动补 create", file=sys.stderr)
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


def _existing_boss_todo_cfg(existing_cfg, base_token):
    nested = existing_cfg.get("boss_todo") or {}
    if not isinstance(nested, dict):
        nested = {}
    table_id = nested.get("table_id") or existing_cfg.get("boss_todo_table_id") or ""
    if not table_id:
        return None
    return {
        "base_token": nested.get("base_token") or base_token,
        "table_id": table_id,
        "table_name": nested.get("table_name") or BOSS_TODO_TABLE_NAME,
        "view_link": nested.get("view_link") or existing_cfg.get("boss_todo_link") or "",
        "dedupe_keys": nested.get("dedupe_keys") or existing_cfg.get("boss_todo_dedupe_keys") or BOSS_TODO_DEDUPE_KEYS,
    }


def create_boss_todo_table(base_token, existing_cfg=None):
    """创建或复用老板代办表，返回 runtime_config.boss_todo 配置。"""
    existing_cfg = existing_cfg or {}
    existing = _existing_boss_todo_cfg(existing_cfg, base_token)
    if existing:
        print(f"🧾 老板代办表已配置，复用: {existing['table_id']} ✅\n")
        return existing

    print("🧾 创建/复用老板代办表...")
    tid = _find_table_id_by_name(base_token, BOSS_TODO_TABLE_NAME)
    if tid:
        print(f"   发现已有表: {tid}")
    else:
        tid = _create_table_with_fields(
            base_token, BOSS_TODO_TABLE_NAME, BOSS_TODO_FIELDS, "创建老板代办表")
    if not tid:
        print("❌ 创建老板代办表失败"); sys.exit(1)
    cfg = {
        "base_token": base_token,
        "table_id": tid,
        "table_name": BOSS_TODO_TABLE_NAME,
        "view_link": existing_cfg.get("boss_todo_link", ""),
        "dedupe_keys": existing_cfg.get("boss_todo_dedupe_keys", BOSS_TODO_DEDUPE_KEYS),
    }
    print(f"   table_id: {tid} ✅\n")
    return cfg


def ensure_boss_todo_table():
    """为已有部署补创建/复用老板代办表并写入 runtime_config。"""
    if not os.path.exists(CONFIG_FILE) or os.path.getsize(CONFIG_FILE) == 0:
        print(f"❌ 未找到有效 {CONFIG_FILE}，请先运行 python3 scripts/setup.py")
        sys.exit(1)
    cfg = load_runtime_config()
    base_token = cfg.get("bitable_app_token")
    if not base_token:
        print("❌ runtime_config.json 缺少 bitable_app_token，无法创建老板代办表")
        sys.exit(1)
    _warmup_lark_cli()
    cfg["boss_todo"] = create_boss_todo_table(base_token, cfg)
    save_runtime_config(cfg)
    print(f"✅ 老板代办配置已保存到 {CONFIG_FILE}")


def create_chat_group():
    """创建飞书群组，返回 chat_id。"""
    print("💬 创建飞书群组...")
    d = _lark(["im", "+chat-create",
               "--name", f"🤖 {TMUX_SESSION} 协作团队",
               "--description", "ClaudeTeam 多智能体协作团队",
               "--type", "private",
               "--set-bot-manager", "--as", "bot"],
              label="创建群组", timeout=60)
    # ADR silent_swallow_remaining P1 ④: 不能把 None 折叠成空 chat_id,
    # 后续 runtime_config.json 写入空 chat_id 会让 router 的 chat_id filter
    # 把所有事件都当成跨团队过滤掉,一次性破坏整个团队部署。和 create_bitable
    # 的错误处理风格对齐。
    if d is None:
        print("❌ 创建群组失败 — lark-cli 调用失败,检查 im:chat 权限 / 限流"); sys.exit(1)
    chat_id = d.get("chat_id", "")
    if not chat_id:
        print("❌ 创建群组失败 — 响应缺 chat_id 字段(可能权限不足)"); sys.exit(1)
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


def _check_team_json_consistency(existing_cfg):
    """P0-7 保护: 检测"Phase 2 Step 4 意外覆盖 team.json"的情况。

    背景
    ----
    README 的 CLAUDE 指令(Phase 2 Step 4)会让 Claude "Create team.json with
    only manager",这对全新仓库是对的,但对**已经跑过一次 setup.py** 的仓库
    就是灾难: 原有的 workspace_tables 里还挂着其他 agent (triager/coder/...)
    的飞书表 id,但 team.json 里只剩 manager,agent 列表彻底丢失。

    检测规则
    --------
    如果 runtime_config.json 的 workspace_tables 里出现了 team.json **不认识**
    的 agent 名字,几乎可以 100% 确定 team.json 是被截断的。此时:
      1) 先备份当前 team.json 到 scripts/.team.json.prev
      2) 若备份里原本就有齐全的 agent 列表(= 上一次 setup 成功后保存的),
         直接打印恢复命令并 sys.exit(1),让用户决定
      3) 否则打印警告但允许继续(首次运行场景)
    """
    ws_tables = existing_cfg.get("workspace_tables", {}) or {}
    if not ws_tables:
        return  # 首次运行,没有历史残留,无从检测

    missing_in_team = [a for a in ws_tables.keys() if a not in AGENTS]
    if not missing_in_team:
        return  # 所有残留 agent 都还在 team.json 里,一致

    print("=" * 70)
    print("⚠️  检测到 team.json 与 runtime_config.json 不一致")
    print(f"    runtime_config.json 的 workspace_tables 里有这些 agent:")
    for a in ws_tables.keys():
        mark = "  " if a in AGENTS else "❌"
        print(f"      {mark} {a}")
    print(f"    但 team.json 只列出: {', '.join(AGENTS.keys()) or '(空)'}")
    print()
    print("    最可能的原因: Phase 2 Step 4 的 'Create team.json with only manager'")
    print("    把已有团队的 team.json 截断成只剩 manager。此时继续 setup.py")
    print("    会让 /hire 流程从零开始重建飞书资源,原有表和历史数据全部丢失。")
    print()

    if os.path.exists(TEAM_JSON_BACKUP):
        print(f"    ✅ 找到 setup.py 上一次成功后的备份: {TEAM_JSON_BACKUP}")
        print(f"    恢复命令:")
        print(f"       cp {TEAM_JSON_BACKUP} {TEAM_JSON}")
        print()
        print("    如果你确认 team.json 当前状态就是你想要的(例如你故意裁撤了部分")
        print("    agent),删除备份后重跑: rm " + TEAM_JSON_BACKUP)
    else:
        print("    (没找到 .team.json.prev 备份,本次是首次被检测到。)")

    print("=" * 70)
    sys.exit(1)


def _backup_team_json():
    """setup.py 成功跑完后,把当前 team.json 存一份到 scripts/.team.json.prev。

    下次 setup.py 启动时,如果检测到 team.json 被截断,可以用这个备份恢复。
    备份文件放在 scripts/ 下面,docker-compose 的 bind mount 会自动持久化。
    """
    if not os.path.exists(TEAM_JSON):
        return
    try:
        shutil.copy2(TEAM_JSON, TEAM_JSON_BACKUP)
        print(f"   💾 team.json 已备份到 {TEAM_JSON_BACKUP}")
    except Exception as e:
        print(f"   ⚠️  备份 team.json 失败(非致命): {e}")


def _warmup_lark_cli():
    """预热 npx 缓存,给首次下载 lark-cli 的用户明确进度预期。

    为什么需要 (P1-12): npx 第一次执行 @larksuite/cli 时会从 npm registry
    拉取约 80MB 的包,期间 stdout 几乎没有输出,慢网环境下用户会误以为
    脚本卡死。Docker 镜像里已经 npm install -g 过,这里是 no-op 级别的快
    速路径;host-native 首次使用则会真正触发下载。我们主动打印一条预期
    提示,而不是让下载静默发生在后面 create_bitable() 的第一个 _lark
    调用里。
    """
    print("📦 预热 lark-cli (npx 首次使用会从 npm registry 下载约 80MB 的包,")
    print("   慢网环境可能需要 1–2 分钟,请保持网络稳定)...", flush=True)
    try:
        r = subprocess.run(
            ["npx", "--yes", "@larksuite/cli", "--version"],
            capture_output=True, text=True, timeout=600,
        )
    except FileNotFoundError:
        print("❌ 系统里找不到 npx 命令,请先安装 Node.js (>=22)。")
        sys.exit(1)
    except subprocess.TimeoutExpired:
        print("   ⚠️ warm-up 超过 10 分钟未完成,网络可能有问题。继续执行,")
        print("      后续 _lark 调用若再次触发下载会抛出更具体的错误。")
        return
    if r.returncode == 0:
        version = r.stdout.strip() or "unknown"
        print(f"   ✓ lark-cli ready ({version})")
    else:
        # warm-up 失败只告警,不阻断 —— 后续真正的 _lark 调用会带更清晰的错误上下文。
        err = (r.stderr or "").strip()[:200]
        print(f"   ⚠️ warm-up 返回 {r.returncode}: {err}")
    print()


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "ensure-boss-todo":
        ensure_boss_todo_table()
        return

    # P1-12 首次使用 lark-cli 时 npx 会下载 ~80MB 且完全静默。先 warm-up 一次,
    # 让用户对"这 1-2 分钟的等待是下载, 不是脚本卡死"有明确预期。
    _warmup_lark_cli()

    # 幂等性检查 —— 空文件 / 非 JSON 视为"未初始化",继续跑。
    # 空文件场景很常见: Docker bind mount 要求目标文件存在,容器部署指南让用户
    # `touch scripts/runtime_config.json` 占位,首次 init 时走到这里。
    existing = {}
    if os.path.exists(CONFIG_FILE) and os.path.getsize(CONFIG_FILE) > 0:
        try:
            existing = load_runtime_config_from_path(CONFIG_FILE)
        except json.JSONDecodeError:
            print(f"⚠️  {CONFIG_FILE} 不是合法 JSON,当作未初始化处理")
            existing = {}

    # P0-7: team.json vs runtime_config.json 一致性保护
    # 必须在幂等性 short-circuit 之前跑,否则"配置完整就跳过"会掩盖截断事故。
    _check_team_json_consistency(existing)

    required_keys = ["lark_profile", "bitable_app_token", "msg_table_id", "sta_table_id", "chat_id"]
    if all(existing.get(k) for k in required_keys):
        # lark_profile 环境漂移检测：LARK_CLI_PROFILE 若已设且与存储值不同则告警退出
        env_profile = os.environ.get("LARK_CLI_PROFILE", "").strip()
        stored_profile = existing["lark_profile"]
        if env_profile and env_profile != stored_profile:
            print("❌ lark_profile 环境漂移:")
            print(f"   runtime_config.json 记录: {stored_profile}")
            print(f"   LARK_CLI_PROFILE 环境变量: {env_profile}")
            print("   两者不一致，可能导致事件流串台。")
            print(f"   如确认切换 profile，请先删除 {CONFIG_FILE} 重新 setup。")
            sys.exit(1)
        print("✅ runtime_config.json 已存在且配置完整,跳过初始化。")
        print(f"   如需重新初始化,请先删除 {CONFIG_FILE}")
        # 跳过场景下也刷新一次 team.json 备份(当前 team.json 就是权威版本)
        _backup_team_json()
        return

    if not AGENTS:
        print("❌ team.json 未配置或为空，请先创建团队配置。")
        sys.exit(1)

    # 解析本次 setup 将使用的 lark-cli profile。
    # 优先级: LARK_CLI_PROFILE 环境变量 > lark-cli 当前默认 profile。
    default_name = _current_default_profile()
    env_profile = os.environ.get("LARK_CLI_PROFILE", "").strip()
    lark_profile = env_profile or default_name
    if not lark_profile:
        print("❌ 无法确定 lark-cli profile。请先运行:")
        print("     npx @larksuite/cli config init --new")
        sys.exit(1)
    print(f"🔑 lark-cli profile: {lark_profile}")

    # profile 名 ≠ session 名警告：用户可能用了隐式继承的默认 profile，
    # 未来多团队部署时会串台。显式确认后可跳过。
    if not env_profile and lark_profile != TMUX_SESSION:
        accept_default = os.environ.get("CLAUDE_TEAM_ACCEPT_DEFAULT_PROFILE", "").lower() in ("1", "yes", "true")
        print("=" * 70)
        print(f"⚠️  lark-cli profile 名 ({lark_profile}) 与 team.json session 名 ({TMUX_SESSION}) 不一致")
        print("   当前 profile 是从 lark-cli 默认继承的，不是显式指定的。")
        print("   如果将来在同一台机器部署第二个团队，共享 profile 会导致事件流串台。")
        print()
        print("   推荐做法 (为本团队创建独立 App):")
        print(f"     1) npx @larksuite/cli config init --new --name {TMUX_SESSION}")
        print(f"     2) LARK_CLI_PROFILE={TMUX_SESSION} python3 scripts/setup.py")
        print()
        print("   继续使用当前默认 profile:")
        print("     CLAUDE_TEAM_ACCEPT_DEFAULT_PROFILE=1 python3 scripts/setup.py")
        print("=" * 70)
        if not accept_default:
            sys.exit(1)
        print("✅ CLAUDE_TEAM_ACCEPT_DEFAULT_PROFILE=1 已设置,继续。")

    # 同机多团队部署时,若多个部署共用同一个 profile (= 同一个 Feishu App),
    # 它们会在 WebSocket 层共享事件流,router 必须按 chat_id 过滤才不会串台。
    # 这里做预检查,让用户显式选路。
    lark_profile = _check_profile_conflict(lark_profile, default_name)

    base_token = create_bitable()
    time.sleep(10)  # 等待 Bitable 初始化完成，避免后续建表报 OpenAPIAddField limited (原版 sleep(2) 被观察到不够)
    msg_table = create_inbox_table(base_token)
    sta_table = create_status_table(base_token)
    kanban_table = create_kanban_table(base_token)
    boss_todo = create_boss_todo_table(base_token, existing)
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
        "boss_todo": boss_todo,
        "workspace_tables": ws_tables,
        "chat_id": chat_id,
        "share_link": share_link,
    }
    save_runtime_config(cfg)
    print(f"✅ 配置已保存到 {CONFIG_FILE}")
    # P0-7: 成功写入后立刻备份 team.json,作为下次 setup.py 的恢复依据
    _backup_team_json()
    if share_link:
        print(f"\n📎 飞书群聊邀请链接（发给用户）:\n   {share_link}")
        print("   ⚠️  bot 已在群里, 但老板默认不在。蒙眼 / 远程部署应急路径:")
        print("       host 端 `python3 scripts/feishu_msg.py boss \"<applink>\"` 把上面")
        print("       的链接发给老板,老板点链接入群。详见 docs/OPERATIONS.md")
        print("       \"新群老板入群\" 段。")
    print("=" * 50)

if __name__ == "__main__":
    main()
