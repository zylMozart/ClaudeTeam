#!/usr/bin/env python3
"""
飞书通讯脚本 — ClaudeTeam（lark-cli 封装层）

底层通过 lark-cli (@larksuite/cli) 执行所有飞书 API 操作，
本脚本作为 agent 的统一 CLI 入口，保持接口稳定。

用法:
  python3 scripts/feishu_msg.py send <收件人> <发件人> "<消息>" [优先级]
  python3 scripts/feishu_msg.py direct <收件人> <发件人> "<消息>"
  python3 scripts/feishu_msg.py say <发件人> ["<消息>"] [--image <路径>]
  python3 scripts/feishu_msg.py inbox <agent名称>
  python3 scripts/feishu_msg.py read <record_id>
  python3 scripts/feishu_msg.py status <agent> <状态> "<任务>" ["<阻塞原因>"]
  python3 scripts/feishu_msg.py log <agent> <类型> "<内容>" ["<关联对象>"]
  python3 scripts/feishu_msg.py workspace <agent>

依赖: lark-cli (npm install -g @larksuite/cli)
优先级: 高 | 中（默认）| 低
状态:   进行中 | 已完成 | 阻塞 | 待命
类型:   状态更新 | 任务日志 | 消息发出 | 消息收到 | 产出记录 | 阻塞上报
"""
import sys, os, json, time

_SCRIPT_DIR = os.path.dirname(__file__)
sys.path.insert(0, _SCRIPT_DIR)
_SRC_DIR = os.path.join(os.path.dirname(_SCRIPT_DIR), "src")
if _SRC_DIR not in sys.path:
    sys.path.insert(0, _SRC_DIR)

from claudeteam.integrations.feishu import client as _feishu_client
from claudeteam.messaging import service as _message_service
from claudeteam.commands import feishu_msg as _feishu_commands
from claudeteam.runtime.config import AGENTS, TMUX_SESSION, load_runtime_config
from claudeteam.messaging.renderer import render_inbox_text
from claudeteam.runtime.tmux_utils import inject_when_idle
from claudeteam.storage import local_facts

# ── 运行时配置加载 ─────────────────────────────────────────────

LEGACY_BITABLE_ENV = "CLAUDETEAM_ENABLE_BITABLE_LEGACY"
FEISHU_REMOTE_ENV = "CLAUDETEAM_ENABLE_FEISHU_REMOTE"


def _env_enabled(name):
    return os.environ.get(name, "").strip().lower() in ("1", "true", "yes", "on")


def legacy_bitable_enabled():
    """Bitable is an explicit opt-in legacy adapter, disabled by default."""
    return _env_enabled(LEGACY_BITABLE_ENV)


def feishu_remote_enabled():
    """Live Feishu group sends are explicit opt-in, disabled by default."""
    return _env_enabled(FEISHU_REMOTE_ENV)

def cfg():
    return load_runtime_config()

def _projection_cfg():
    """Return runtime config for optional Bitable projections, or {} if absent."""
    try:
        return cfg()
    except SystemExit:
        return {}
    except Exception as e:
        print(f"⚠️ runtime_config 读取失败，Bitable 投影降级: {e}", file=sys.stderr)
        return {}

def BT():  return cfg()["bitable_app_token"]
def MT():  return cfg()["msg_table_id"]
def ST():  return cfg()["sta_table_id"]
def WS(a): return (_projection_cfg().get("workspace_tables") or {}).get(a, "")
def CHAT(): return _projection_cfg().get("chat_id", "")

def now_ms():
    return int(time.time() * 1000)

def extract_text(v):
    """从 Bitable 字段值中提取文本。"""
    if isinstance(v, list): return v[0].get("text", "") if v else ""
    return str(v) if v else ""


def sanitize_agent_message(text: str) -> str:
    """Compatibility wrapper for messaging.service.sanitize_agent_message."""
    return _message_service.sanitize_agent_message(text)


# ── lark-cli 封装 ────────────────────────────────────────────

def _lark_run(args, timeout=30):
    """Compatibility wrapper for integrations.feishu.client._lark_run."""
    return _feishu_client._lark_run(args, timeout=timeout)


def _check_lark_result(result, action, *, fatal=True):
    """Compatibility wrapper for integrations.feishu.client._check_lark_result."""
    return _feishu_client._check_lark_result(result, action, fatal=fatal)


def _lark_im_send(chat_id, content=None, markdown=None, image=None, card=None):
    """Compatibility wrapper for integrations.feishu.client._lark_im_send."""
    return _feishu_client._lark_im_send_with_run(
        _lark_run,
        chat_id,
        content,
        markdown,
        image,
        card,
    )


def _lark_base_create(base_token, table_id, fields_json):
    """Compatibility wrapper for integrations.feishu.client._lark_base_create."""
    return _feishu_client._lark_base_create_with_run(
        _lark_run,
        base_token,
        table_id,
        fields_json,
    )


_BITABLE_SEARCH_PATH_BLOCKED_CODE = _feishu_client._BITABLE_SEARCH_PATH_BLOCKED_CODE


def _lark_base_search(base_token, table_id, search_json):
    """Compatibility wrapper for integrations.feishu.client._lark_base_search."""
    return _feishu_client._lark_base_search(base_token, table_id, search_json)


def _lark_base_update(base_token, table_id, record_ids, patch):
    """Compatibility wrapper for integrations.feishu.client._lark_base_update."""
    return _feishu_client._lark_base_update_with_run(
        _lark_run,
        base_token,
        table_id,
        record_ids,
        patch,
    )


def _lark_base_list(base_token, table_id, limit=20, offset=0):
    """Compatibility wrapper for integrations.feishu.client._lark_base_list."""
    return _feishu_client._lark_base_list_with_run(
        _lark_run,
        base_token,
        table_id,
        limit=limit,
        offset=offset,
    )

# ── 消息卡片构建 ──────────────────────────────────────────────

def build_system_card(content: str, template: str = "grey") -> dict:
    """Compatibility wrapper for messaging.service.build_system_card."""
    return _message_service.build_system_card(content, template=template)


def build_card(from_agent, to_agent, content, priority="中"):
    """Compatibility wrapper for messaging.service.build_card."""
    return _message_service.build_card(from_agent, to_agent, content, priority=priority)


def build_cards(from_agent, to_agent, content, priority="中", *, max_chars=3500):
    """Compatibility wrapper for messaging.service.build_cards."""
    return _message_service.build_cards(
        from_agent, to_agent, content, priority=priority, max_chars=max_chars,
    )

# ── 群组发消息 ─────────────────────────────────────────────────

def post_to_group(from_agent, to_agent, content, priority="中"):
    """向飞书群组发一条消息卡片。返回 True 表示发送成功。

    Feishu group notification is no longer part of the default core path. It is
    a live remote adapter and must be explicitly enabled.
    """
    if not feishu_remote_enabled():
        print(
            f"ℹ️ Feishu 群通知默认关闭，跳过远端发送 "
            f"(设置 {FEISHU_REMOTE_ENV}=1 才启用 legacy remote)",
            file=sys.stderr,
        )
        return True
    chat_id = CHAT()
    if not chat_id:
        print("  ⚠️ chat_id 未配置，跳过群通知", file=sys.stderr)
        return False
    cards = build_cards(from_agent, to_agent, content, priority)
    ok = True
    for card in cards:
        d = _lark_im_send(chat_id, card=card)
        ok = _check_lark_result(d, f"群通知 {from_agent}→{to_agent or '*'}", fatal=False) and ok
        if not ok:
            break
    return ok

# ── 工作空间日志 ───────────────────────────────────────────────

def ws_log(agent, log_type, content, ref=""):
    """Compatibility wrapper for messaging.service.ws_log."""
    return _message_service.ws_log(agent, log_type, content, ref=ref)

# ── 命令：say（直接发群消息，用于回复用户）──────────────────────

def cmd_say(from_agent, message="", image_path=""):
    if not message and not image_path:
        print("❌ 消息内容和图片路径不能同时为空"); sys.exit(1)
    if not feishu_remote_enabled():
        print(f"❌ Feishu 远端发送默认关闭；设置 {FEISHU_REMOTE_ENV}=1 后才能 say")
        sys.exit(1)
    chat_id = CHAT()
    if not chat_id:
        print("❌ 群组未配置"); sys.exit(1)

    if message:
        message = sanitize_agent_message(message)
        for card in build_cards(from_agent, None, message):
            d = _lark_im_send(chat_id, card=card)
            _check_lark_result(d, f"群聊发言 {from_agent}→*")
        ws_log(from_agent, "消息发出", f"→ 群聊：{message[:10000]}")
        print(f"✅ 已发送到群聊")

    if image_path:
        if not os.path.exists(image_path):
            print(f"❌ 图片文件不存在: {image_path}"); sys.exit(1)
        d = _lark_im_send(chat_id, image=image_path)
        _check_lark_result(d, f"群聊图片 {from_agent}→* ({os.path.basename(image_path)})")
        ws_log(from_agent, "消息发出",
               f"→ 群聊图片：{os.path.basename(image_path)}")
        print(f"✅ 图片已发送到群聊: {os.path.basename(image_path)}")

# ── 辅助：Bitable 写入单条消息 ────────────────────────────────

def bitable_insert_message(to, frm, content, priority):
    """向消息表写入一条记录，返回 record_id 或 None。"""
    content = render_inbox_text(content)
    d = _lark_base_create(BT(), MT(),
                          {"收件人": to, "发件人": frm,
                           "消息内容": content, "优先级": priority,
                           "时间": now_ms(), "已读": False})
    if d is None:
        return None
    # +record-batch-create 返回 record_id_list 或 records
    rid_list = d.get("record_id_list", [])
    if rid_list:
        return rid_list[0]
    records = d.get("records", [])
    if records:
        return records[0].get("record_id", "")
    return d.get("record_id", "")


def _project_message_to_bitable(to, frm, content, priority, local_id):
    """Manual/optional Bitable projection for a locally durable inbox message.

    Core send/direct paths no longer call this by default. Bitable is a
    low-frequency display/audit projection and must not gate message sync.
    """
    if not legacy_bitable_enabled():
        return ""
    pcfg = _projection_cfg()
    if not pcfg.get("bitable_app_token") or not pcfg.get("msg_table_id"):
        print(
            f"⚠️ Bitable 消息投影未配置，本地 inbox 已保存 [local_id: {local_id}]",
            file=sys.stderr,
        )
        return ""
    rid = bitable_insert_message(to, frm, content, priority)
    if rid:
        local_facts.attach_bitable_record(local_id, rid)
        return rid
    print(
        f"⚠️ Bitable 消息投影失败，本地 inbox 已保存 [local_id: {local_id}]",
        file=sys.stderr,
    )
    return ""


def _local_suffix(rid):
    if rid:
        return f"rid: {rid}"
    if legacy_bitable_enabled():
        return "legacy Bitable projection unavailable"
    return "local-only"

# ── 命令：send ────────────────────────────────────────────────

def _notify_agent_tmux(to_agent, from_agent, message):
    """向目标 agent 的 tmux 窗口注入收件通知（best-effort）。

    lazy-wake-v2 适配:
      - 目标窗口若还是 💤 占位 (pane 里没有 claude 进程) → 先调 agent_lifecycle.sh wake
      - lifecycle wake 幂等: 已活则立即返回, 所以对非 lazy-mode 也安全
      - wake 返回后 CLI 进程刚起, UI 未就绪, 直接 inject 会把文字打进 bash →
        调 wait_cli_ui_ready 先等 CLI TUI 就绪 (最长 30s), 就绪后才 inject;
        超时则 enqueue 兜底,绝不回退到往 bash 乱 send-keys
    """
    try:
        import subprocess as _sp
        lifecycle = os.path.join(os.path.dirname(__file__), "lib",
                                 "agent_lifecycle.sh")
        if os.path.exists(lifecycle):
            try:
                _sp.run(["bash", lifecycle, "wake", to_agent],
                        capture_output=True, timeout=25, check=False)
            except Exception:
                pass  # best-effort, 继续走 ready 探测

        notify_text = (
            f"你有来自 {from_agent} 的新消息。"
            f"请执行: python3 scripts/feishu_msg.py inbox {to_agent}"
        )

        ready = False
        try:
            from claudeteam.messaging.router.wake import wait_cli_ui_ready
            from claudeteam.cli_adapters import adapter_for_agent
            from claudeteam.runtime.tmux_utils import capture_pane
            ready = wait_cli_ui_ready(
                to_agent,
                capture_pane_fn=lambda n: capture_pane(TMUX_SESSION, n),
                get_ready_markers=lambda n: adapter_for_agent(n).ready_markers(),
                get_process_name=lambda n: adapter_for_agent(n).process_name(),
                timeout_s=30,
            )
        except Exception:
            ready = False  # 探测失败时宁可入队也不乱 inject

        if ready:
            ok = inject_when_idle(TMUX_SESSION, to_agent, notify_text,
                                  wait_secs=5, force_after_wait=False)
            if ok:
                return True

        try:
            from claudeteam.runtime.queue import enqueue_message
            enqueue_message(
                to_agent, notify_text,
                f"notify_{to_agent}_{int(time.time() * 1000)}",
                is_user_msg=False,
            )
        except Exception:
            pass
        return False
    except Exception:
        return False  # best-effort，不影响本地核心事实写入


def cmd_send(to_agent, from_agent, message, priority="中", task_id=""):
    local_id, actual_message = _message_service.record_local_send(
        to_agent,
        from_agent,
        message,
        priority=priority,
        task_id=task_id,
    )
    rid = ""
    if legacy_bitable_enabled():
        rid = _project_message_to_bitable(
            to_agent, from_agent, actual_message, priority, local_id)
    ref_str = f"{local_id} | legacy_bitable:{rid}" if rid else local_id

    group_ok = True
    if feishu_remote_enabled():
        group_ok = post_to_group(from_agent, to_agent, actual_message, priority)
    ws_log(from_agent, "消息发出", f"→ {to_agent}：{actual_message[:10000]}", ref_str)
    ws_log(to_agent, "消息收到", f"← {from_agent}：{actual_message[:10000]}", ref_str)

    if not group_ok:
        # 收件箱已写 + 群通知失败 → exit 2 让上游（watchdog / 调用脚本）
        # 感知"部分成功"，不重试（重试会复制本地消息）。
        print(f"⚠️ 本地 inbox 已写但 Feishu 群通知失败 [local_id: {local_id}]")
        _notify_agent_tmux(to_agent, from_agent, actual_message)
        sys.exit(2)

    suffix = _local_suffix(rid)
    print(f"✅ 消息已发送 → {to_agent}  [local_id: {local_id}, {suffix}]")
    _notify_agent_tmux(to_agent, from_agent, actual_message)

# ── 命令：direct ──────────────────────────────────────────────

def cmd_direct(to_agent, from_agent, message):
    """直连发消息：写入收件箱，自动抄送 manager。"""
    local_result = _message_service.record_local_direct(to_agent, from_agent, message)
    message = local_result["message"]
    local_id = local_result["local_id"]
    cc_local_id = local_result["cc_local_id"]
    cc_content = local_result["cc_content"]

    rid = ""
    if legacy_bitable_enabled():
        rid = _project_message_to_bitable(to_agent, from_agent, message, "中", local_id)

    cc_rid = None
    if cc_local_id:
        if legacy_bitable_enabled():
            cc_rid = _project_message_to_bitable(
                "manager", from_agent, cc_content, "低", cc_local_id)

    # 群通知（带 [直连] 标记）是显式 opt-in legacy remote。
    chat_id = CHAT() if feishu_remote_enabled() else ""
    if not feishu_remote_enabled():
        pass
    elif not chat_id:
        print("  ⚠️ chat_id 未配置，跳过群通知", file=sys.stderr)
    else:
        info = AGENTS.get(from_agent, {"role": "?", "emoji": "🤖", "color": "grey"})
        title = f"{info['emoji']} {from_agent} · {info['role']} → @{to_agent} [直连]"
        card = {
            "config": {"wide_screen_mode": True},
            "header": {"template": info.get("color", "grey"),
                       "title": {"tag": "plain_text", "content": title}},
            "elements": [{"tag": "markdown", "content": message}]
        }
        d = _lark_im_send(chat_id, card=card)
        if not _check_lark_result(
                d, f"直连群通知 {from_agent}→{to_agent}", fatal=False):
            print(f"⚠️ 本地 inbox 已写但 Feishu 直连通知失败 [local_id: {local_id}]")

    ws_log(from_agent, "消息发出", f"→ {to_agent}[直连]：{message[:10000]}", local_id)
    ws_log(to_agent,   "消息收到", f"← {from_agent}[直连]：{message[:10000]}", local_id)

    suffix = _local_suffix(rid)
    print(f"✅ 消息已直发 → {to_agent}  [local_id: {local_id}, {suffix}]")
    if cc_local_id:
        cc_suffix = _local_suffix(cc_rid)
        print(f"✅ 抄送已写入 → manager     [local_id: {cc_local_id}, {cc_suffix}]")
    _notify_agent_tmux(to_agent, from_agent, message)

# ── 命令：inbox ───────────────────────────────────────────────

# 进程内缓存:首次看到 800080303 后置 True,后续调用直接走 list 兜底,
# 避免每次 inbox/status 都为同一个平台限制多跑一次 RTT。
_bitable_search_blocked = False


def _parse_record_rows(d, keep):
    """解析 +record-search / +record-list 返回的 {data, fields, record_id_list}。

    `keep(fields_dict)` 返回 True 的记录被收集;传 None 等价于全收。
    返回 (results, page_rows) —— page_rows 是本页原始行数(用于翻页推进)。
    """
    rows = d.get("data", [])
    field_names = d.get("fields", [])
    rid_list = d.get("record_id_list", [])
    out = []
    for i, row in enumerate(rows):
        fields = {}
        for j, val in enumerate(row):
            if j < len(field_names):
                fields[field_names[j]] = val
        if keep is not None and not keep(fields):
            continue
        rid = rid_list[i] if i < len(rid_list) else ""
        out.append({"record_id": rid, "fields": fields})
    return out, len(rows)


def _search_records(base_token, table_id, keyword, search_fields):
    """翻页拉取所有匹配 keyword 的记录。

    返回
    ----
    list[{record_id, fields}] — 查询成功，可能为空列表（真的没匹配）
    None                      — 查询失败（lark-cli 侧报错，上游应走 fatal 分支）

    **重要**：调用方在使用返回值之前必须先走 _check_lark_result(result, action)。
    直接对 None 做迭代会抛 TypeError。

    两条路径
    --------
    快路径 (+record-search,服务器侧按 keyword 过滤):
      - 中国版飞书支持,行为等同老实现。
      - 翻页契约 (reviewer 2026-04-13 已用 --help + dry-run 验证):
        offset + limit 请求,响应不返回 has_more,用 len(rows) < PAGE 退出。
      - limit 上限 200,MAX_PAGES=50 硬兜底 10000 条。

    兜底路径 (+record-list + 客户端子串过滤):
      - 国际版 Lark 触发 800080303 "unsafe_operation_blocked" 后切到这条,
        路径见 `_BITABLE_SEARCH_PATH_BLOCKED_CODE`。
      - +record-list 返回 has_more,据此翻页。
      - 客户端过滤语义:keyword 出现在任一 search_fields 的文本值里即命中。
        对当前调用方 (cmd_inbox/cmd_status 查 agent 名) 是精确匹配退化,等价。
    """
    global _bitable_search_blocked

    # ── 快路径: +record-search ─────────────────────────────────
    if not _bitable_search_blocked:
        results = []
        offset = 0
        PAGE = 200
        MAX_PAGES = 50
        hit_block = False
        for page in range(MAX_PAGES):
            status, d = _lark_base_search(base_token, table_id, {
                "keyword": keyword,
                "search_fields": search_fields,
                "offset": offset,
                "limit": PAGE,
            })
            if status == "blocked":
                _bitable_search_blocked = True
                print("ℹ️  Bitable +record-search 被平台屏蔽(800080303),"
                      "切到 +record-list 客户端过滤兜底 (进程内缓存,后续直接走兜底)",
                      file=sys.stderr)
                hit_block = True
                break
            if status == "error":
                if page == 0:
                    print(f"  ⚠️ _search_records 首页失败: table={table_id} "
                          f"keyword={keyword!r}: {d}", file=sys.stderr)
                else:
                    print(f"  ⚠️ _search_records 第 {page+1} 页失败 "
                          f"(已抓 {len(results)} 条,整体视为失败): {d}",
                          file=sys.stderr)
                return None
            parsed, page_rows = _parse_record_rows(d, keep=None)
            results.extend(parsed)
            if page_rows < PAGE:
                break
            offset += page_rows
        if not hit_block:
            return results

    # ── 兜底路径: +record-list + 客户端 substring 过滤 ─────────
    def _match(fields):
        return any(keyword in extract_text(fields.get(f, "")) for f in search_fields)

    results = []
    offset = 0
    PAGE = 200
    MAX_PAGES = 50
    for page in range(MAX_PAGES):
        d = _lark_base_list(base_token, table_id, limit=PAGE, offset=offset)
        if d is None:
            if page == 0:
                print(f"  ⚠️ _search_records (list 兜底) 首页失败: table={table_id}",
                      file=sys.stderr)
            else:
                print(f"  ⚠️ _search_records (list 兜底) 第 {page+1} 页失败 "
                      f"(已抓 {len(results)} 条)", file=sys.stderr)
            return None
        parsed, page_rows = _parse_record_rows(d, keep=_match)
        results.extend(parsed)
        if not d.get("has_more", False) or page_rows == 0:
            break
        offset += page_rows
    return results


def cmd_inbox(agent_name):
    """Compatibility wrapper for messaging.service.cmd_inbox."""
    return _message_service.cmd_inbox(agent_name)

# ── 命令：read ────────────────────────────────────────────────

def cmd_read(record_id):
    if _message_service.mark_local_read(record_id):
        return
    if legacy_bitable_enabled():
        d = _lark_base_update(BT(), MT(), [record_id], {"已读": True})
        _check_lark_result(d, f"已读标记 {record_id}")
        print(f"✅ 已标记 legacy Bitable 已读: {record_id}")
        return
    print(f"❌ 找不到本地消息: {record_id}")
    sys.exit(1)

# ── 命令：status ──────────────────────────────────────────────

def cmd_status(agent_name, status, task, blocker=""):
    _message_service.upsert_local_status(agent_name, status, task, blocker)

    projection_ok = True
    if legacy_bitable_enabled():
        fields = {"Agent名称": agent_name, "状态": status, "当前任务": task,
                  "阻塞原因": blocker, "更新时间": now_ms()}
        projection_ok = False
        pcfg = _projection_cfg()
        bt = pcfg.get("bitable_app_token")
        st = pcfg.get("sta_table_id")
        if not bt or not st:
            print(
                f"⚠️ Bitable 状态投影未配置，本地状态已保存: {agent_name}→{status}",
                file=sys.stderr,
            )
        else:
            records = _search_records(bt, st, agent_name, ["Agent名称"])
            if records is None:
                print(
                    f"⚠️ Bitable 状态投影查询失败，本地状态已保存: {agent_name}→{status}",
                    file=sys.stderr,
                )
            elif records:
                d = _lark_base_update(bt, st, [records[0]["record_id"]], fields)
                projection_ok = _check_lark_result(
                    d, f"状态投影写入 {agent_name}→{status}", fatal=False)
            else:
                d = _lark_base_create(bt, st, fields)
                projection_ok = _check_lark_result(
                    d, f"状态投影新建 {agent_name}→{status}", fatal=False)
    content = f"状态：{status} | {task}"
    if blocker: content += f" | ⛔ {blocker}"
    ws_log(agent_name, "阻塞上报" if status == "阻塞" else "状态更新", content)
    suffix = ""
    if not legacy_bitable_enabled():
        suffix = "（local-only）"
    elif not projection_ok:
        suffix = "（legacy Bitable projection unavailable）"
    print(f"✅ {agent_name} → {status}: {task}{suffix}")

# ── 命令：log ─────────────────────────────────────────────────

def cmd_log(agent_name, log_type, content, ref=""):
    """Compatibility wrapper for messaging.service.cmd_log."""
    return _message_service.cmd_log(agent_name, log_type, content, ref=ref)

# ── 命令：workspace ────────────────────────────────────────────

def cmd_workspace(agent_name):
    """Compatibility wrapper for messaging.service.cmd_workspace."""
    return _message_service.cmd_workspace(agent_name)

# ── main ──────────────────────────────────────────────────────

def main():
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        sys.exit(0)

    def _handle_send(to_agent, from_agent, message, priority="中", task_id="", file_path=""):
        if file_path:
            with open(file_path, encoding="utf-8") as fh:
                message = fh.read().strip()
        return cmd_send(to_agent, from_agent, message, priority, task_id)

    handlers = {
        "say": cmd_say,
        "send": _handle_send,
        "direct": cmd_direct,
        "inbox": cmd_inbox,
        "read": cmd_read,
        "status": cmd_status,
        "log": cmd_log,
        "workspace": cmd_workspace,
    }
    result = _feishu_commands.run(args, handlers=handlers)
    if result.exit_code != 0:
        if result.message:
            print(result.message)
        sys.exit(result.exit_code)

if __name__ == "__main__":
    main()
