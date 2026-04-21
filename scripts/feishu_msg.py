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
import sys, os, json, time, subprocess

sys.path.insert(0, os.path.dirname(__file__))
from config import AGENTS, PROJECT_ROOT, TMUX_SESSION, load_runtime_config, LARK_CLI
from message_renderer import render_feishu_markdown, render_inbox_text, render_log_text
from tmux_utils import inject_when_idle

# ── 运行时配置加载 ─────────────────────────────────────────────

def cfg():
    return load_runtime_config()

def BT():  return cfg()["bitable_app_token"]
def MT():  return cfg()["msg_table_id"]
def ST():  return cfg()["sta_table_id"]
def WS(a): return cfg()["workspace_tables"].get(a, "")
def CHAT(): return cfg().get("chat_id", "")

def now_ms():
    return int(time.time() * 1000)

def extract_text(v):
    """从 Bitable 字段值中提取文本。"""
    if isinstance(v, list): return v[0].get("text", "") if v else ""
    return str(v) if v else ""


def sanitize_agent_message(text: str) -> str:
    """Remove Codex CLI spawn command fragments accidentally mixed into messages."""
    return render_inbox_text(text)


# ── lark-cli 封装 ────────────────────────────────────────────

def _lark_run(args, timeout=30):
    """执行 lark-cli 命令，返回 data 层 JSON（失败返回 None）。"""
    r = subprocess.run(LARK_CLI + args, capture_output=True, text=True, timeout=timeout)
    if r.returncode != 0:
        print(f"  ⚠️ lark-cli 失败: {r.stderr.strip()[:200]}")
        return None
    if not r.stdout.strip():
        return {}
    try:
        full = json.loads(r.stdout)
        return full.get("data", full)
    except json.JSONDecodeError:
        return None


def _check_lark_result(result, action, *, fatal=True):
    """统一校验 _lark_* 调用返回值（ADR: lark_result_check）。

    参数
    ----
    result : _lark_run / _lark_im_send / _lark_base_* 的返回值
             约定 None = lark-cli 侧失败，其他 (含 {}) = 成功
    action : 人类可读的动作描述，形如 "<动作> <from>→<to>"
             例如 "状态写入 manager→进行中"、"群通知 coder→*"
    fatal  : True  → 失败时打印 ❌ 错误 + sys.exit(1)
             False → 失败时打印 ⚠️ 警告，返回 False

    返回
    ----
    True  : 成功（result is not None）
    False : 失败且 fatal=False

    调用方如需 "已写入 A 但 B 失败" 的 exit 2 等混合语义，应 fatal=False
    拿到返回值后自行 sys.exit(2)。helper 不负责自定义退出码。
    """
    if result is not None:
        return True
    prefix = "❌" if fatal else "⚠️"
    print(f"{prefix} lark-cli 调用失败: {action}", file=sys.stderr)
    if fatal:
        sys.exit(1)
    return False


def _lark_im_send(chat_id, content=None, markdown=None, image=None, card=None):
    """通过 lark-cli 向群组发送消息。"""
    args = ["im", "+messages-send", "--chat-id", chat_id, "--as", "bot"]
    if markdown:
        args += ["--markdown", markdown]
    elif image:
        args += ["--image", image]
    elif card:
        args += ["--content", json.dumps(card, ensure_ascii=False), "--msg-type", "interactive"]
    elif content:
        args += ["--text", content]
    return _lark_run(args)


def _lark_base_create(base_token, table_id, fields_json):
    """向 Bitable 写入一条记录，返回响应 JSON。"""
    payload = json.dumps({"fields": list(fields_json.keys()),
                          "rows": [list(fields_json.values())]},
                         ensure_ascii=False)
    d = _lark_run(["base", "+record-batch-create",
                   "--base-token", base_token, "--table-id", table_id,
                   "--json", payload, "--as", "bot"])
    return d


# 服务器侧 /records/search 状态码:
#   800080303 "unsafe_operation_blocked" = 端点在当前品牌(目前仅国际版 Lark)
#   还未放出,再多重试也没用,必须走客户端过滤兜底。
_BITABLE_SEARCH_PATH_BLOCKED_CODE = 800080303


def _lark_base_search(base_token, table_id, search_json):
    """单次调用 +record-search。返回三元 status:

        ("ok", data_dict)    成功,data_dict 形如 {data, fields, record_id_list}
        ("blocked", None)    服务器返回 800080303 (端点未放出,仅国际版 Lark)
        ("error",   msg)     其他失败,msg 是 stderr 截断后的文本

    刻意**不**走 _lark_run —— 调用方需要区分"端点被平台屏蔽"和"一般失败"
    来决定是否 fallback 到 _lark_base_list + 客户端过滤,而 _lark_run 把
    所有失败都归并成 None,无从辨别。
    """
    args = LARK_CLI + ["base", "+record-search",
                       "--base-token", base_token, "--table-id", table_id,
                       "--json", json.dumps(search_json, ensure_ascii=False),
                       "--as", "bot"]
    r = subprocess.run(args, capture_output=True, text=True, timeout=30)
    if r.returncode == 0:
        try:
            return "ok", json.loads(r.stdout).get("data", {})
        except json.JSONDecodeError:
            return "error", (r.stdout or "")[:200]
    try:
        err = json.loads(r.stderr).get("error") or {}
        if err.get("code") == _BITABLE_SEARCH_PATH_BLOCKED_CODE:
            return "blocked", None
        msg = err.get("message") or r.stderr.strip()
    except (json.JSONDecodeError, AttributeError, TypeError):
        msg = r.stderr.strip()
    return "error", msg[:200]


def _lark_base_update(base_token, table_id, record_ids, patch):
    """批量更新 Bitable 记录。"""
    payload = json.dumps({"record_id_list": record_ids, "patch": patch},
                         ensure_ascii=False)
    return _lark_run(["base", "+record-batch-update",
                      "--base-token", base_token, "--table-id", table_id,
                      "--json", payload, "--as", "bot"])


def _lark_base_list(base_token, table_id, limit=20, offset=0):
    """列出 Bitable 记录（支持 offset 翻页）。"""
    args = ["base", "+record-list",
            "--base-token", base_token, "--table-id", table_id,
            "--limit", str(limit), "--as", "bot"]
    if offset:
        args += ["--offset", str(offset)]
    return _lark_run(args)

# ── 消息卡片构建 ──────────────────────────────────────────────

def build_system_card(content: str, template: str = "grey") -> dict:
    """系统消息卡片（给 slash 命令的文本回显用），不带 sender · role 标签。"""
    content = render_feishu_markdown(content)
    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "template": template,
            "title": {"tag": "plain_text", "content": "🛠️ 系统消息"},
        },
        "elements": [{"tag": "markdown", "content": content}],
    }


def build_card(from_agent, to_agent, content, priority="中"):
    """构建飞书消息卡片 JSON"""
    content = render_feishu_markdown(content)
    info = AGENTS.get(from_agent, {"role": "?", "emoji": "🤖", "color": "grey"})
    emoji = info["emoji"]
    role  = info["role"]
    color = info.get("color", "grey")

    if to_agent and to_agent != "*":
        title = f"{emoji} {from_agent} · {role} → @{to_agent}"
    else:
        title = f"{emoji} {from_agent} · {role}"

    pri_tag = {"高": "🔴 ", "中": "", "低": "🟢 "}.get(priority, "")

    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "template": color,
            "title": {"tag": "plain_text", "content": title}
        },
        "elements": [
            {"tag": "markdown", "content": f"{pri_tag}{content}"}
        ]
    }

# ── 群组发消息 ─────────────────────────────────────────────────

def post_to_group(from_agent, to_agent, content, priority="中"):
    """向飞书群组发一条消息卡片。返回 True 表示发送成功。

    失败非致命 —— 调用方（cmd_send / cmd_direct）根据返回值决定是否升级
    为 exit 2（"收件箱已写但群通知失败"）。
    """
    chat_id = CHAT()
    if not chat_id:
        print("  ⚠️ chat_id 未配置，跳过群通知", file=sys.stderr)
        return False
    card = build_card(from_agent, to_agent, content, priority)
    d = _lark_im_send(chat_id, card=card)
    return _check_lark_result(d, f"群通知 {from_agent}→{to_agent or '*'}", fatal=False)

# ── 工作空间日志 ───────────────────────────────────────────────

def ws_log(agent, log_type, content, ref=""):
    """写工作空间审计日志（非致命：失败仅 stderr 警告，主命令继续）。

    限流期间如果强行退出会让所有命令连锁挂掉；丢几条审计记录的代价
    可接受，丢可观测性不行。
    """
    tid = WS(agent)
    if not tid:
        return
    content = render_log_text(content)
    d = _lark_base_create(BT(), tid,
                          {"类型": log_type, "内容": content,
                           "时间": now_ms(), "关联对象": ref})
    _check_lark_result(d, f"ws_log {agent}/{log_type}", fatal=False)

# ── 命令：say（直接发群消息，用于回复用户）──────────────────────

def cmd_say(from_agent, message="", image_path=""):
    if not message and not image_path:
        print("❌ 消息内容和图片路径不能同时为空"); sys.exit(1)
    chat_id = CHAT()
    if not chat_id:
        print("❌ 群组未配置"); sys.exit(1)

    if message:
        message = sanitize_agent_message(message)
        card = build_card(from_agent, None, message)
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

# ── 命令：send ────────────────────────────────────────────────

def _notify_agent_tmux(to_agent, from_agent, message):
    """向目标 agent 的 tmux 窗口注入收件通知（best-effort）。

    lazy-wake-v2 适配:
      - 目标窗口若还是 💤 占位 (pane 里没有 claude 进程) → 先调 agent_lifecycle.sh wake
      - lifecycle wake 幂等: 已活则立即返回, 所以对非 lazy-mode 也安全
      - wake 失败时退化为老行为 (尝试直接 inject), 不阻塞主发送流程
    """
    try:
        # lazy-wake: 检测 💤 → wake before inject
        import subprocess as _sp
        lifecycle = os.path.join(os.path.dirname(__file__), "lib",
                                 "agent_lifecycle.sh")
        if os.path.exists(lifecycle):
            try:
                _sp.run(["bash", lifecycle, "wake", to_agent],
                        capture_output=True, timeout=25, check=False)
            except Exception:
                pass  # best-effort, 继续走 inject

        notify_text = (
            f"你有来自 {from_agent} 的新消息。"
            f"请执行: python3 scripts/feishu_msg.py inbox {to_agent}"
        )
        inject_when_idle(TMUX_SESSION, to_agent, notify_text, wait_secs=5)
    except Exception:
        pass  # best-effort，不影响消息发送本身


def cmd_send(to_agent, from_agent, message, priority="中", task_id=""):
    message = sanitize_agent_message(message)
    actual_message = f"[{task_id}] {message}" if task_id else message
    rid = bitable_insert_message(to_agent, from_agent, actual_message, priority)
    # 主写入失败 → fatal exit 1。`rid or None` 把空串也归一到 None。
    _check_lark_result(rid or None, f"收件箱写入 {from_agent}→{to_agent}")
    ref_str = f"{rid} | task:{task_id}" if task_id else rid

    group_ok = post_to_group(from_agent, to_agent, actual_message, priority)
    # 主写入已完成（Bitable 有记录），所以无论群通知是否成功都要写 ws_log，
    # 保证审计链路完整。
    ws_log(from_agent, "消息发出", f"→ {to_agent}：{actual_message[:10000]}", ref_str)
    ws_log(to_agent, "消息收到", f"← {from_agent}：{actual_message[:10000]}", ref_str)

    if not group_ok:
        # 收件箱已写 + 群通知失败 → exit 2 让上游（watchdog / 调用脚本）
        # 感知"部分成功"，不重试（重试会复制消息到 Bitable）。
        print(f"⚠️ 收件箱已写但群通知失败 [rid: {rid}]")
        _notify_agent_tmux(to_agent, from_agent, actual_message)
        sys.exit(2)

    print(f"✅ 消息已发送 → {to_agent}  [rid: {rid}]")
    _notify_agent_tmux(to_agent, from_agent, actual_message)

# ── 命令：direct ──────────────────────────────────────────────

def cmd_direct(to_agent, from_agent, message):
    """直连发消息：写入收件箱，自动抄送 manager。"""
    message = sanitize_agent_message(message)
    rid = bitable_insert_message(to_agent, from_agent, message, "中")
    _check_lark_result(rid or None, f"直连写入 {from_agent}→{to_agent}")

    cc_rid = None
    if to_agent != "manager" and from_agent != "manager":
        cc_content = f"[抄送] {from_agent}→{to_agent}: {message}"
        cc_rid = bitable_insert_message("manager", from_agent, cc_content, "低")
        # 抄送失败不致命：主消息已写入，manager 仍可从 to_agent inbox 追查
        if not cc_rid:
            print(f"⚠️ lark-cli 调用失败: 抄送 {from_agent}→manager", file=sys.stderr)

    # 群通知（带 [直连] 标记）非致命，失败走 exit 2
    group_ok = True
    chat_id = CHAT()
    if not chat_id:
        print("  ⚠️ chat_id 未配置，跳过群通知", file=sys.stderr)
        group_ok = False
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
        group_ok = _check_lark_result(
            d, f"直连群通知 {from_agent}→{to_agent}", fatal=False)

    ws_log(from_agent, "消息发出", f"→ {to_agent}[直连]：{message[:10000]}", rid)
    ws_log(to_agent,   "消息收到", f"← {from_agent}[直连]：{message[:10000]}", rid)

    if not group_ok:
        print(f"⚠️ 直连已写但群通知失败 [rid: {rid}]")
        _notify_agent_tmux(to_agent, from_agent, message)
        sys.exit(2)

    print(f"✅ 消息已直发 → {to_agent}  [rid: {rid}]")
    if cc_rid:
        print(f"✅ 抄送已发送 → manager     [rid: {cc_rid}]")
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
    records = _search_records(BT(), MT(), agent_name, ["收件人"])
    # ADR lark_read_error_propagation: records 可能为 None(失败) 或 list(成功)
    _check_lark_result(records, f"inbox 查询 {agent_name}")
    # 到这里 records 一定是 list（可能空），可以安全迭代
    unread = [r for r in records if not r["fields"].get("已读")]
    if not unread:
        print(f"📭 {agent_name} 暂无未读消息")
        return
    print(f"📬 {agent_name} 有 {len(unread)} 条未读消息:\n")
    for rec in unread:
        f = rec["fields"]
        rid = rec["record_id"]
        t = f.get("时间", 0)
        ts = time.strftime("%m-%d %H:%M", time.localtime(t / 1000)) if isinstance(t, (int, float)) else "?"
        frm = extract_text(f.get("发件人", "?"))
        pri = extract_text(f.get("优先级", "?"))
        content = sanitize_agent_message(extract_text(f.get("消息内容", "")))
        print(f"── [{ts}] 来自 {frm} [优先级:{pri}]")
        print(f"   {content}")
        print(f"   标记已读: python3 scripts/feishu_msg.py read {rid}")
        print()

# ── 命令：read ────────────────────────────────────────────────

def cmd_read(record_id):
    d = _lark_base_update(BT(), MT(), [record_id], {"已读": True})
    _check_lark_result(d, f"已读标记 {record_id}")
    print(f"✅ 已标记已读: {record_id}")

# ── 命令：status ──────────────────────────────────────────────

def cmd_status(agent_name, status, task, blocker=""):
    # 先搜索是否已有记录
    records = _search_records(BT(), ST(), agent_name, ["Agent名称"])
    # ADR lark_read_error_propagation: 失败时 fatal exit，避免误走 create 分支
    # 创建重复记录（原 bug：查询失败 → records=[] → 重复 create）。
    _check_lark_result(records, f"状态查询 {agent_name}")
    fields = {"Agent名称": agent_name, "状态": status, "当前任务": task,
              "阻塞原因": blocker, "更新时间": now_ms()}
    if records:
        d = _lark_base_update(BT(), ST(), [records[0]["record_id"]], fields)
        _check_lark_result(d, f"状态写入 {agent_name}→{status}")
    else:
        d = _lark_base_create(BT(), ST(), fields)
        _check_lark_result(d, f"状态新建 {agent_name}→{status}")
    # 到这里要么主写入成功，要么已 sys.exit(1)
    content = f"状态：{status} | {task}"
    if blocker: content += f" | ⛔ {blocker}"
    ws_log(agent_name, "阻塞上报" if status == "阻塞" else "状态更新", content)
    print(f"✅ {agent_name} → {status}: {task}")

# ── 命令：log ─────────────────────────────────────────────────

def cmd_log(agent_name, log_type, content, ref=""):
    tid = WS(agent_name)
    if not tid:
        print(f"❌ 找不到 {agent_name} 的工作空间"); sys.exit(1)
    d = _lark_base_create(BT(), tid,
                          {"类型": log_type, "内容": content,
                           "时间": now_ms(), "关联对象": ref})
    _check_lark_result(d, f"工作空间日志 {agent_name}/{log_type}")
    print(f"✅ [{log_type}] 已写入 {agent_name} 工作空间")

# ── 命令：workspace ────────────────────────────────────────────

def cmd_workspace(agent_name):
    tid = WS(agent_name)
    if not tid:
        print(f"❌ 找不到 {agent_name} 的工作空间"); sys.exit(1)
    d = _lark_base_list(BT(), tid, limit=20)
    # ADR lark_read_error_propagation: 原版 (d or {}).get("items", []) 会把
    # 失败折叠成"最近 0 条"，让人以为工作空间是空的。改为 fatal 校验。
    _check_lark_result(d, f"工作空间查询 {agent_name}")
    items = d.get("items", [])
    print(f"📁 {agent_name} 工作空间 (最近 {len(items)} 条):\n")
    for rec in items:
        f = rec.get("fields", {})
        t = f.get("时间", 0)
        ts = time.strftime("%m-%d %H:%M", time.localtime(t / 1000)) if isinstance(t, (int, float)) else "?"
        lt = extract_text(f.get("类型", "?"))
        c  = extract_text(f.get("内容", ""))
        ref = extract_text(f.get("关联对象", ""))
        print(f"  [{ts}] {lt:8} {c[:10000]}")
        if ref: print(f"           → {ref}")
    bt = BT()
    print(f"\n  飞书链接: https://feishu.cn/base/{bt}?table={tid}")

# ── main ──────────────────────────────────────────────────────

def main():
    args = sys.argv[1:]
    if not args: print(__doc__); sys.exit(0)
    cmd = args[0]
    if cmd == "say":
        say_args = list(args[1:])
        image_path = ""
        if "--image" in say_args:
            idx = say_args.index("--image")
            image_path = say_args[idx + 1] if idx + 1 < len(say_args) else ""
            say_args = [a for i, a in enumerate(say_args) if i != idx and i != idx + 1]
        if len(say_args) < 1:
            print("用法: say <发件人> [\"<消息>\"] [--image <路径>]"); sys.exit(1)
        from_agent = say_args[0]
        message    = say_args[1] if len(say_args) > 1 else ""
        cmd_say(from_agent, message, image_path)
    elif cmd == "send":
        if len(args) < 4: print("用法: send <收件人> <发件人> \"<消息>\" [优先级] [--task <task_id>] [--file <路径>]"); sys.exit(1)
        rest = list(args[1:])
        task_id = ""
        file_path = ""
        for flag in ("--task", "--file"):
            if flag in rest:
                idx = rest.index(flag)
                if idx + 1 < len(rest):
                    val = rest[idx + 1]
                    rest.pop(idx + 1)
                    rest.pop(idx)
                    if flag == "--task": task_id = val
                    else: file_path = val
        to_agent, from_agent = rest[0], rest[1]
        if file_path:
            with open(file_path, encoding="utf-8") as _f:
                message = _f.read().strip()
        else:
            message = rest[2] if len(rest) > 2 else ""
        priority = rest[3] if len(rest) > 3 else "中"
        cmd_send(to_agent, from_agent, message, priority, task_id)
    elif cmd == "direct":
        if len(args) < 4: print("用法: direct <收件人> <发件人> '<消息>'"); sys.exit(1)
        cmd_direct(args[1], args[2], args[3])
    elif cmd == "inbox":
        if len(args) < 2: print("用法: inbox <agent>"); sys.exit(1)
        cmd_inbox(args[1])
    elif cmd == "read":
        if len(args) < 2: print("用法: read <record_id>"); sys.exit(1)
        cmd_read(args[1])
    elif cmd == "status":
        if len(args) < 4: print("用法: status <agent> <状态> \"<任务>\" [\"<阻塞>\"]"); sys.exit(1)
        cmd_status(args[1], args[2], args[3], args[4] if len(args) > 4 else "")
    elif cmd == "log":
        if len(args) < 4: print("用法: log <agent> <类型> \"<内容>\" [\"<ref>\"]"); sys.exit(1)
        cmd_log(args[1], args[2], args[3], args[4] if len(args) > 4 else "")
    elif cmd == "workspace":
        if len(args) < 2: print("用法: workspace <agent>"); sys.exit(1)
        cmd_workspace(args[1])
    else:
        print(f"未知命令: {cmd}"); sys.exit(1)

if __name__ == "__main__":
    main()
