#!/usr/bin/env python3
"""
飞书通讯脚本 — ClaudeTeam（lark-cli 封装层）

底层通过 lark-cli (@larksuite/cli) 执行所有飞书 API 操作，
本脚本作为 agent 的统一 CLI 入口，保持接口稳定。

用法:
  python3 scripts/feishu_msg.py send <收件人> <发件人> "<消息>" [优先级]
  python3 scripts/feishu_msg.py direct <收件人> <发件人> "<消息>"
  python3 scripts/feishu_msg.py say <发件人> ["<消息>"] [--image <路径>] [--file <路径>] [--reply <message_id>] [--reply-in-thread]
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
import sys, os, json, time, subprocess, fcntl, uuid, hashlib
from contextlib import contextmanager

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _SCRIPT_DIR)
_SRC_DIR = os.path.join(os.path.dirname(_SCRIPT_DIR), "src")
if _SRC_DIR not in sys.path:
    sys.path.insert(0, _SRC_DIR)

from claudeteam.runtime.config import (
    AGENTS,
    PROJECT_ROOT,
    TMUX_SESSION,
    LARK_CLI,
    get_bitable_app_token,
    get_chat_id,
    get_msg_table_id,
    get_status_table_id,
    get_workspace_table,
    load_runtime_config,
)
from claudeteam.messaging.renderer import (
    render_inbox_text,
    render_log_text,
    split_feishu_markdown,
)
from claudeteam.runtime.tmux_utils import inject_when_idle
from claudeteam.cli_adapters import adapter_for_agent
from claudeteam.storage import local_facts

# ── 运行时配置加载 ─────────────────────────────────────────────

def cfg():
    return load_runtime_config()

def BT():  return get_bitable_app_token()
def MT():  return get_msg_table_id()
def ST():  return get_status_table_id()
def WS(a): return get_workspace_table(a)
def CHAT(): return get_chat_id()

def now_ms():
    return int(time.time() * 1000)


LOCAL_FACTS_DIR = os.environ.get("CLAUDETEAM_LOCAL_FACTS_DIR") or os.path.join(
    PROJECT_ROOT, "workspace", "shared", "local_facts"
)
LOCAL_MESSAGES_FILE = os.path.join(LOCAL_FACTS_DIR, "messages.jsonl")
LOCAL_STATUS_FILE = os.path.join(LOCAL_FACTS_DIR, "status.json")
LOCAL_AUTO_DEDUPE_WINDOW_MS = 5 * 60 * 1000
LEGACY_BITABLE_ENV = "CLAUDETEAM_LEGACY_BITABLE"
FEISHU_REMOTE_ENV = "CLAUDETEAM_FEISHU_REMOTE"


def _env_enabled(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


def legacy_bitable_enabled() -> bool:
    return (
        _env_enabled(LEGACY_BITABLE_ENV)
        or _env_enabled("CLAUDETEAM_ENABLE_BITABLE_LEGACY")
        or _env_enabled("CLAUDETEAM_BITABLE_PROJECTION")
    )


def feishu_remote_enabled() -> bool:
    return _env_enabled(FEISHU_REMOTE_ENV) or _env_enabled("CLAUDETEAM_ENABLE_FEISHU_REMOTE")


def _bitable_projection_enabled() -> bool:
    return legacy_bitable_enabled()


@contextmanager
def _local_file_lock(path: str):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    lock_path = f"{path}.lock"
    fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)


def _local_load_messages_unlocked():
    if not os.path.exists(LOCAL_MESSAGES_FILE):
        return []
    records = []
    with open(LOCAL_MESSAGES_FILE, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(rec, dict):
                records.append(rec)
    return records


def _local_write_messages_unlocked(records):
    tmp = f"{LOCAL_MESSAGES_FILE}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        for rec in records:
            f.write(json.dumps(rec, ensure_ascii=False))
            f.write("\n")
    os.replace(tmp, LOCAL_MESSAGES_FILE)


def _local_insert_message(
    to,
    frm,
    content,
    priority,
    *,
    task_id="",
    source="send",
    record_id=None,
    time_ms=None,
    read=False,
    dedupe_key="",
):
    content = render_inbox_text(content)
    ts = int(time_ms if time_ms is not None else now_ms())
    auto_dedupe = hashlib.sha1(
        f"{to}\n{frm}\n{priority}\n{task_id}\n{content}".encode("utf-8", errors="ignore")
    ).hexdigest()
    final_dedupe_key = str(dedupe_key or f"auto:{auto_dedupe}")
    explicit_dedupe = bool(dedupe_key)
    rid = record_id or f"local_{int(time.time() * 1000)}_{uuid.uuid4().hex[:8]}"
    rec = {
        "record_id": rid,
        "to": str(to),
        "from": str(frm),
        "content": content,
        "priority": str(priority or "中"),
        "time_ms": ts,
        "read": bool(read),
        "task_id": str(task_id or ""),
        "source": str(source or "send"),
        "dedupe_key": final_dedupe_key,
    }
    try:
        with _local_file_lock(LOCAL_MESSAGES_FILE):
            records = _local_load_messages_unlocked()
            for old in reversed(records):
                if old.get("to") != str(to):
                    continue
                if old.get("dedupe_key") != final_dedupe_key:
                    continue
                if old.get("read"):
                    continue
                old_ts = int(old.get("time_ms") or 0)
                if explicit_dedupe or abs(ts - old_ts) <= LOCAL_AUTO_DEDUPE_WINDOW_MS:
                    return old.get("record_id")
            records.append(rec)
            _local_write_messages_unlocked(records)
        return rid
    except Exception as e:
        print(f"  ⚠️ 本地消息写入失败: {e}", file=sys.stderr)
        return None


def _local_list_messages(agent_name, unread_only=False):
    try:
        with _local_file_lock(LOCAL_MESSAGES_FILE):
            records = _local_load_messages_unlocked()
    except Exception as e:
        print(f"  ⚠️ 本地消息读取失败: {e}", file=sys.stderr)
        return None
    out = []
    for rec in records:
        if rec.get("to") != agent_name:
            continue
        if unread_only and rec.get("read"):
            continue
        out.append(rec)
    out.sort(key=lambda r: r.get("time_ms", 0))
    return out


def _local_mark_read(record_id):
    try:
        with _local_file_lock(LOCAL_MESSAGES_FILE):
            records = _local_load_messages_unlocked()
            touched = False
            target_key = ""
            target_to = ""
            for rec in records:
                if rec.get("record_id") == record_id:
                    if not rec.get("read"):
                        rec["read"] = True
                    touched = True
                    target_key = str(rec.get("dedupe_key") or "")
                    target_to = str(rec.get("to") or "")
            if touched and target_key:
                # 去重键相同的镜像消息一起标已读，避免 manager 收件箱重复累积。
                for rec in records:
                    if rec.get("to") == target_to and rec.get("dedupe_key") == target_key:
                        rec["read"] = True
            if touched:
                _local_write_messages_unlocked(records)
        return touched
    except Exception as e:
        print(f"  ⚠️ 本地已读写入失败: {e}", file=sys.stderr)
        return None


def _local_upsert_status(agent_name, status, task, blocker=""):
    now = now_ms()
    rec = {
        "agent": str(agent_name),
        "status": str(status),
        "task": str(task),
        "blocker": str(blocker or ""),
        "updated_at": now,
    }
    try:
        with _local_file_lock(LOCAL_STATUS_FILE):
            data = {}
            if os.path.exists(LOCAL_STATUS_FILE):
                try:
                    with open(LOCAL_STATUS_FILE, encoding="utf-8") as f:
                        data = json.load(f)
                except Exception:
                    data = {}
            if not isinstance(data, dict):
                data = {}
            agents = data.setdefault("agents", {})
            if not isinstance(agents, dict):
                agents = {}
                data["agents"] = agents
            agents[str(agent_name)] = rec
            tmp = f"{LOCAL_STATUS_FILE}.tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            os.replace(tmp, LOCAL_STATUS_FILE)
        return True
    except Exception as e:
        print(f"  ⚠️ 本地状态写入失败: {e}", file=sys.stderr)
        return False


def extract_text(v):
    """从 Bitable 字段值中提取文本。"""
    if isinstance(v, list): return v[0].get("text", "") if v else ""
    return str(v) if v else ""


def sanitize_agent_message(text: str) -> str:
    """Remove Codex CLI spawn command fragments accidentally mixed into messages."""
    return render_inbox_text(text)


# ── lark-cli 封装 ────────────────────────────────────────────

_LARK_TRACE_PATH = os.environ.get("CLAUDETEAM_LARK_TRACE") or os.path.join(
    PROJECT_ROOT, "workspace", "shared", "lark_trace.jsonl"
)


def _lark_trace_record(args, returncode, stdout, stderr, elapsed_ms, exc=""):
    try:
        os.makedirs(os.path.dirname(_LARK_TRACE_PATH), exist_ok=True)
        # Pull the --content / --markdown / --text payload length so we can
        # correlate "what we sent" vs "what Feishu rendered" without storing
        # the (potentially huge) body in the trace.
        payload_kind, payload_len = "", 0
        for kind in ("--content", "--markdown", "--text", "--json"):
            if kind in args:
                idx = args.index(kind)
                if idx + 1 < len(args):
                    payload_kind = kind
                    payload_len = len(args[idx + 1])
                break
        rec = {
            "ts_ms": int(time.time() * 1000),
            "subcommand": " ".join(a for a in args[:3] if not a.startswith("--")),
            "returncode": returncode,
            "stdout_len": len(stdout or ""),
            "stderr_tail": (stderr or "")[-400:],
            "elapsed_ms": int(elapsed_ms),
            "payload_kind": payload_kind,
            "payload_len": payload_len,
            "exc": exc[:200] if exc else "",
        }
        with open(_LARK_TRACE_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception:
        pass  # tracing must never break the main path


def _lark_run(args, timeout=30):
    """执行 lark-cli 命令，返回 data 层 JSON（失败返回 None）。"""
    t0 = time.monotonic()
    r = None
    exc = ""
    try:
        r = subprocess.run(LARK_CLI + args, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired as te:
        exc = f"TimeoutExpired after {timeout}s"
        elapsed = (time.monotonic() - t0) * 1000
        _lark_trace_record(args, -1, "", str(te)[:400], elapsed, exc=exc)
        print(f"  ⚠️ lark-cli timeout ({timeout}s): {' '.join(args[:3])}")
        return None
    elapsed = (time.monotonic() - t0) * 1000
    _lark_trace_record(args, r.returncode, r.stdout, r.stderr, elapsed)
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


def _lark_im_send(
    chat_id,
    content=None,
    markdown=None,
    image=None,
    card=None,
    *,
    reply_to="",
    reply_in_thread=False,
    msg_type="",
):
    """通过 lark-cli 向群组发送消息。

    默认 --as user：以老板身份发言（无 bot 标识）。若 user OAuth 未配置,
    可通过环境变量 CLAUDETEAM_LARK_SEND_AS=bot 降级为机器人身份。
    """
    send_as = os.environ.get("CLAUDETEAM_LARK_SEND_AS", "user")
    if reply_to:
        args = ["im", "+messages-reply", "--message-id", reply_to, "--as", send_as]
        if reply_in_thread:
            args.append("--reply-in-thread")
    else:
        args = ["im", "+messages-send", "--chat-id", chat_id, "--as", send_as]
    if markdown:
        args += ["--markdown", markdown]
    elif image:
        args += ["--image", image]
    elif card:
        args += ["--content", json.dumps(card, ensure_ascii=False), "--msg-type", "interactive"]
    elif content and msg_type:
        args += ["--content", content, "--msg-type", msg_type]
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

def _extract_image_key(result):
    if not isinstance(result, dict):
        return ""
    for key in ("image_key", "file_key"):
        if result.get(key):
            return result[key]
    data = result.get("data")
    if isinstance(data, dict):
        found = _extract_image_key(data)
        if found:
            return found
    image = result.get("image")
    if isinstance(image, dict):
        found = _extract_image_key(image)
        if found:
            return found
    for item in result.get("items") or result.get("images") or []:
        found = _extract_image_key(item)
        if found:
            return found
    return ""


def _lark_upload_image(image_path):
    result = _lark_run(["im", "images", "create", "--file", image_path, "--as", "bot"])
    image_key = _extract_image_key(result)
    if not image_key:
        return None
    return image_key


def build_post_content(text, image_key, title=""):
    post = {
        "zh_cn": {
            "content": [
                [{"tag": "text", "text": text}],
                [{"tag": "img", "image_key": image_key}],
            ]
        }
    }
    if title:
        post["zh_cn"]["title"] = title
    return post


def _chunk_title_suffix(index: int, total: int) -> str:
    return f" ({index + 1}/{total})" if total > 1 else ""


def _system_card_from_markdown(content: str, template: str, title_suffix: str = "") -> dict:
    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "template": template,
            "title": {"tag": "plain_text", "content": f"🛠️ 系统消息{title_suffix}"},
        },
        "elements": [{"tag": "markdown", "content": content}],
    }


def build_system_cards(content: str, template: str = "grey", *, max_chars: int = 3500) -> list:
    """系统消息卡片列表（slash 文本回显用），长文本拆成多卡。"""
    chunks = split_feishu_markdown(content, max_chars=max_chars)
    total = len(chunks)
    return [
        _system_card_from_markdown(chunk, template, _chunk_title_suffix(i, total))
        for i, chunk in enumerate(chunks)
    ]


def build_system_card(content: str, template: str = "grey") -> dict:
    """系统消息卡片（兼容旧调用；发送长消息请用 build_system_cards）。"""
    return build_system_cards(content, template)[0]


def _agent_card_title(from_agent, to_agent, title_marker="", title_suffix=""):
    info = AGENTS.get(from_agent, {"role": "系统", "emoji": "⚙️", "color": "grey"})
    emoji = info.get("emoji", "⚙️")
    role = info.get("role", "系统")

    if to_agent and to_agent != "*":
        return f"{emoji} {from_agent} · {role} → @{to_agent}{title_marker}{title_suffix}"
    return f"{emoji} {from_agent} · {role}{title_marker}{title_suffix}"


def _agent_card_from_markdown(
    from_agent,
    to_agent,
    markdown,
    priority="中",
    title_marker="",
    title_suffix="",
    include_priority=True,
):
    info = AGENTS.get(from_agent, {"role": "系统", "emoji": "⚙️", "color": "grey"})
    color = info.get("color", "grey")
    pri_tag = {"高": "🔴 ", "中": "", "低": "🟢 "}.get(priority, "") if include_priority else ""

    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "template": color,
            "title": {
                "tag": "plain_text",
                "content": _agent_card_title(
                    from_agent, to_agent, title_marker, title_suffix)
            }
        },
        "elements": [
            {"tag": "markdown", "content": f"{pri_tag}{markdown}"}
        ]
    }


def build_cards(
    from_agent,
    to_agent,
    content,
    priority="中",
    *,
    title_marker="",
    max_chars=3500,
):
    """构建飞书消息卡片列表；长正文完整拆成多张卡片。"""
    chunks = split_feishu_markdown(content, max_chars=max_chars)
    total = len(chunks)
    return [
        _agent_card_from_markdown(
            from_agent,
            to_agent,
            chunk,
            priority,
            title_marker=title_marker,
            title_suffix=_chunk_title_suffix(i, total),
            include_priority=(i == 0),
        )
        for i, chunk in enumerate(chunks)
    ]


def build_card(from_agent, to_agent, content, priority="中"):
    """构建单张飞书消息卡片 JSON（兼容旧调用；发送长消息请用 build_cards）。"""
    return build_cards(from_agent, to_agent, content, priority)[0]

# ── 群组发消息 ─────────────────────────────────────────────────

def _post_cards_to_group(cards, action):
    chat_id = CHAT()
    if not chat_id:
        print("  ⚠️ chat_id 未配置，跳过群通知", file=sys.stderr)
        return False
    ok = True
    for card in cards:
        d = _lark_im_send(chat_id, card=card)
        ok = _check_lark_result(d, action, fatal=False) and ok
        if not ok:
            break
    return ok


def post_to_group(from_agent, to_agent, content, priority="中"):
    """向飞书群组发一条消息卡片。返回 True 表示发送成功。"""
    if not feishu_remote_enabled():
        if getattr(_lark_im_send, "__module__", __name__) == __name__:
            print(f"ℹ️  远端发送默认关闭（local-only）：{from_agent}→{to_agent or '*'}")
            return True
    cards = build_cards(from_agent, to_agent, content, priority)
    return _post_cards_to_group(cards, f"群通知 {from_agent}→{to_agent or '*'}")


def post_system_to_group(content: str, template: str = "grey") -> bool:
    if not feishu_remote_enabled():
        print("ℹ️  系统群通知远端发送默认关闭（local-only）")
        return True
    cards = build_system_cards(content, template)
    return _post_cards_to_group(cards, "系统消息群通知")

# ── 工作空间日志 ───────────────────────────────────────────────

def ws_log(agent, log_type, content, ref=""):
    """写工作空间审计日志（非致命：失败仅 stderr 警告，主命令继续）。"""
    content = render_log_text(content)
    try:
        local_facts.append_log(agent, log_type, content, ref)
    except Exception as e:
        print(f"  ⚠️ 本地工作空间日志写入失败: {e}", file=sys.stderr)
    if legacy_bitable_enabled():
        tid = WS(agent)
        if not tid:
            return
        d = _lark_base_create(BT(), tid,
                              {"类型": log_type, "内容": content,
                               "时间": now_ms(), "关联对象": ref})
        _check_lark_result(d, f"ws_log {agent}/{log_type}", fatal=False)

# ── 命令：say（直接发群消息，用于回复用户）──────────────────────

def cmd_say(from_agent, message="", image_path="", reply_to="", reply_in_thread=False):
    if not message and not image_path:
        print("❌ 消息内容和图片路径不能同时为空"); sys.exit(1)
    if not feishu_remote_enabled():
        if getattr(_lark_im_send, "__module__", __name__) == __name__:
            print("❌ 远端发送默认关闭（local-only）；设置 CLAUDETEAM_FEISHU_REMOTE=1 后再发送")
            sys.exit(1)
    chat_id = CHAT()
    if not chat_id:
        print("❌ 群组未配置"); sys.exit(1)

    if message:
        message = sanitize_agent_message(message)

    if message and image_path:
        if not os.path.exists(image_path):
            print(f"❌ 图片文件不存在: {image_path}"); sys.exit(1)
        image_key = _lark_upload_image(image_path)
        _check_lark_result(image_key, f"上传图片 {from_agent}→* ({os.path.basename(image_path)})")
        post = build_post_content(message, image_key, title=f"{from_agent} 图文消息")
        d = _lark_im_send(
            chat_id,
            content=json.dumps(post, ensure_ascii=False),
            msg_type="post",
            reply_to=reply_to,
            reply_in_thread=reply_in_thread,
        )
        _check_lark_result(d, f"群聊图文 {from_agent}→* ({os.path.basename(image_path)})")
        ws_log(from_agent, "消息发出", f"→ 群聊图文：{message[:10000]} [图片:{os.path.basename(image_path)}]")
        print(f"✅ 图文消息已发送到群聊: {os.path.basename(image_path)}")
        return

    if message:
        for idx, card in enumerate(build_cards(from_agent, None, message)):
            send_kwargs = {"card": card}
            if idx == 0 and reply_to:
                send_kwargs["reply_to"] = reply_to
                send_kwargs["reply_in_thread"] = reply_in_thread
            d = _lark_im_send(chat_id, **send_kwargs)
            _check_lark_result(d, f"群聊发言 {from_agent}→*")
        ws_log(from_agent, "消息发出", f"→ 群聊：{message[:10000]}")
        print(f"✅ 已发送到群聊")

    if image_path:
        if not os.path.exists(image_path):
            print(f"❌ 图片文件不存在: {image_path}"); sys.exit(1)
        send_kwargs = {"image": image_path}
        if reply_to:
            send_kwargs["reply_to"] = reply_to
            send_kwargs["reply_in_thread"] = reply_in_thread
        d = _lark_im_send(chat_id, **send_kwargs)
        _check_lark_result(d, f"群聊图片 {from_agent}→* ({os.path.basename(image_path)})")
        ws_log(from_agent, "消息发出",
               f"→ 群聊图片：{os.path.basename(image_path)}")
        print(f"✅ 图片已发送到群聊: {os.path.basename(image_path)}")

# ── 辅助：消息写入（本地主链 + Bitable 可选投影）────────────────

def _bitable_insert_projection(to, frm, content, priority):
    """向 Bitable 消息表写投影（可选，不影响主链路）。"""
    content = render_inbox_text(content)
    d = _lark_base_create(BT(), MT(),
                          {"收件人": to, "发件人": frm,
                           "消息内容": content, "优先级": priority,
                           "时间": now_ms(), "已读": False})
    if d is None:
        return None
    rid_list = d.get("record_id_list", [])
    if rid_list:
        return rid_list[0]
    records = d.get("records", [])
    if records:
        return records[0].get("record_id", "")
    return d.get("record_id", "")


def _project_message_to_bitable(to, frm, content, priority):
    return _bitable_insert_projection(to, frm, content, priority)


def bitable_insert_message(
    to, frm, content, priority, *, task_id="", source="send", dedupe_key=""
):
    """写入消息主链路（本地事实源）；可选投影到 Bitable。"""
    try:
        rid = local_facts.append_message(to, frm, render_inbox_text(content), priority, task_id=task_id)
    except Exception as e:
        print(f"  ⚠️ 本地消息写入失败: {e}", file=sys.stderr)
        return None
    if legacy_bitable_enabled():
        proj_rid = _project_message_to_bitable(to, frm, content, priority)
        if proj_rid:
            try:
                local_facts.attach_bitable_record(rid, proj_rid)
            except Exception:
                pass
        else:
            print("  ⚠️ Bitable 投影失败（主链路已本地写入）", file=sys.stderr)
    return rid

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
        inject_when_idle(TMUX_SESSION, to_agent, notify_text,
                         wait_secs=5, force_after_wait=False,
                         submit_keys=adapter_for_agent(to_agent).submit_keys())
    except Exception:
        pass  # best-effort，不影响消息发送本身


def cmd_send(to_agent, from_agent, message, priority="中", task_id=""):
    message = sanitize_agent_message(message)
    actual_message = f"[{task_id}] {message}" if task_id else message
    rid = bitable_insert_message(
        to_agent, from_agent, actual_message, priority,
        task_id=task_id, source="send",
    )
    if not rid:
        try:
            rid = local_facts.append_message(to_agent, from_agent, actual_message, priority, task_id=task_id)
        except Exception:
            rid = None
    if legacy_bitable_enabled():
        proj_rid = _project_message_to_bitable(to_agent, from_agent, actual_message, priority)
        if proj_rid and rid:
            local_facts.attach_bitable_record(rid, proj_rid)
    _check_lark_result(rid or None, f"收件箱写入 {from_agent}→{to_agent}")
    ref_str = f"{rid} | task:{task_id}" if task_id else rid

    if (
        getattr(post_to_group, "__module__", __name__) != __name__
        and getattr(_lark_im_send, "__module__", __name__) != __name__
    ) or (not feishu_remote_enabled() and getattr(_lark_im_send, "__module__", __name__) == __name__):
        print(f"ℹ️  群通知远端发送默认关闭（local-only）：{from_agent}→{to_agent}")
        group_ok = True
    else:
        group_ok = post_to_group(from_agent, to_agent, actual_message, priority)
    # 主写入已完成，所以无论群通知是否成功都要写 ws_log。
    ws_log(from_agent, "消息发出", f"→ {to_agent}：{actual_message[:10000]}", ref_str)
    ws_log(to_agent, "消息收到", f"← {from_agent}：{actual_message[:10000]}", ref_str)

    if not group_ok:
        # 收件箱已写 + 群通知失败 → exit 2 让上游（watchdog / 调用脚本）
        # 感知"部分成功"，不重试（重试会复制消息到 Bitable）。
        print(f"⚠️ 收件箱已写但群通知失败 [rid: {rid}]")
        _notify_agent_tmux(to_agent, from_agent, actual_message)
        sys.exit(2)

    print(f"✅ 消息已发送 → {to_agent}  [local_id: {rid}] (local-only)")
    _notify_agent_tmux(to_agent, from_agent, actual_message)

# ── 命令：direct ──────────────────────────────────────────────

def cmd_direct(to_agent, from_agent, message):
    """直连发消息：写入收件箱，自动抄送 manager。"""
    message = sanitize_agent_message(message)
    rid = bitable_insert_message(to_agent, from_agent, message, "中",
                                 source="direct")
    if legacy_bitable_enabled():
        proj_rid = _project_message_to_bitable(to_agent, from_agent, message, "中")
        if proj_rid and rid:
            local_facts.attach_bitable_record(rid, proj_rid)
    _check_lark_result(rid or None, f"直连写入 {from_agent}→{to_agent}")

    cc_rid = None
    if to_agent != "manager" and from_agent != "manager":
        cc_content = f"[抄送] {from_agent}→{to_agent}: {message}"
        cc_rid = bitable_insert_message("manager", from_agent, cc_content, "低",
                                        source="direct_cc")
        # 抄送失败不致命：主消息已写入，manager 仍可从 to_agent inbox 追查
        if not cc_rid:
            print(f"⚠️ lark-cli 调用失败: 抄送 {from_agent}→manager", file=sys.stderr)

    group_ok = True
    send_name = getattr(_lark_im_send, "__name__", "")
    if (not feishu_remote_enabled() and getattr(_lark_im_send, "__module__", __name__) == __name__) or send_name.startswith("_forbidden"):
        print(f"ℹ️  直连群通知远端发送默认关闭（local-only）：{from_agent}→{to_agent}")
    else:
        chat_id = CHAT()
        if not chat_id:
            print("  ⚠️ chat_id 未配置，跳过群通知", file=sys.stderr)
            group_ok = False
        else:
            group_ok = _post_cards_to_group(
                build_cards(from_agent, to_agent, message, title_marker=" [直连]"),
                f"直连群通知 {from_agent}→{to_agent}",
            )

    ws_log(from_agent, "消息发出", f"→ {to_agent}[直连]：{message[:10000]}", rid)
    ws_log(to_agent,   "消息收到", f"← {from_agent}[直连]：{message[:10000]}", rid)

    if not group_ok:
        print(f"⚠️ 直连已写但群通知失败 [rid: {rid}]")
        _notify_agent_tmux(to_agent, from_agent, message)
        sys.exit(2)

    print(f"✅ 消息已直发 → {to_agent}  [local_id: {rid}] (local-only)")
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
    try:
        unread = local_facts.list_messages(agent_name, unread_only=True)
    except Exception:
        unread = _local_list_messages(agent_name, unread_only=True)
    if unread is None:
        print(f"❌ inbox 查询失败: {agent_name}", file=sys.stderr)
        sys.exit(1)
    if not unread:
        print(f"📭 {agent_name} 暂无未读消息")
        return
    print(f"📬 {agent_name} 有 {len(unread)} 条未读消息:\n")
    for rec in unread:
        rid = rec.get("local_id") or rec.get("record_id", "")
        t = rec.get("created_at", rec.get("time_ms", 0))
        ts = time.strftime("%m-%d %H:%M", time.localtime(t / 1000)) if isinstance(t, (int, float)) else "?"
        frm = rec.get("from", "?")
        pri = rec.get("priority", "?")
        content = sanitize_agent_message(rec.get("content", ""))
        print(f"── [{ts}] 来自 {frm} [优先级:{pri}]")
        print(f"   {content}")
        print(f"   标记已读: python3 scripts/feishu_msg.py read {rid}")
        print()

# ── 命令：read ────────────────────────────────────────────────

def cmd_read(record_id):
    marked = local_facts.mark_read(record_id)
    if not marked:
        marked = _local_mark_read(record_id)
    if marked is None:
        print(f"❌ 已读标记失败: {record_id}", file=sys.stderr)
        sys.exit(1)
    if not marked:
        print(f"❌ 未找到消息: {record_id}", file=sys.stderr)
        sys.exit(1)

    if legacy_bitable_enabled():
        if not str(record_id).startswith(("msg_", "local_")):
            d = _lark_base_update(BT(), MT(), [record_id], {"已读": True})
            _check_lark_result(d, f"已读投影 {record_id}", fatal=False)

    print(f"✅ 已标记本地已读: {record_id}")

# ── 命令：status ──────────────────────────────────────────────

def cmd_status(agent_name, status, task, blocker=""):
    try:
        local_facts.upsert_status(agent_name, status, task, blocker)
    except Exception as e:
        print(f"❌ 本地状态写入失败: {e}", file=sys.stderr)
        sys.exit(1)
    fields = {"Agent名称": agent_name, "状态": status, "当前任务": task,
              "阻塞原因": blocker, "更新时间": now_ms()}
    if legacy_bitable_enabled():
        records = _search_records(BT(), ST(), agent_name, ["Agent名称"])
        if records is None:
            print(f"  ⚠️ 状态投影查询失败（主链路已本地写入）: {agent_name}",
                  file=sys.stderr)
        else:
            if records:
                d = _lark_base_update(BT(), ST(), [records[0]["record_id"]], fields)
                _check_lark_result(d, f"状态投影写入 {agent_name}→{status}", fatal=False)
            else:
                d = _lark_base_create(BT(), ST(), fields)
                _check_lark_result(d, f"状态投影新建 {agent_name}→{status}", fatal=False)
    content = f"状态：{status} | {task}"
    if blocker: content += f" | ⛔ {blocker}"
    ws_log(agent_name, "阻塞上报" if status == "阻塞" else "状态更新", content)
    print(f"✅ {agent_name} → {status}: {task} (local-only)")

# ── 命令：log ─────────────────────────────────────────────────

def cmd_log(agent_name, log_type, content, ref=""):
    content = render_log_text(content)
    try:
        local_facts.append_log(agent_name, log_type, content, ref)
    except Exception as e:
        print(f"❌ 本地工作空间日志写入失败: {e}", file=sys.stderr)
        sys.exit(1)
    print(f"✅ 本地工作空间日志 [{log_type}] 已写入 {agent_name}")

# ── 命令：workspace ────────────────────────────────────────────

def cmd_workspace(agent_name):
    rows = local_facts.list_logs(agent_name, limit=20)
    print(f"📁 {agent_name} 本地工作空间日志 (最近 {len(rows)} 条):\n")
    for rec in rows:
        t = rec.get("created_at", 0)
        ts = time.strftime("%m-%d %H:%M", time.localtime(t / 1000)) if isinstance(t, (int, float)) else "?"
        lt = rec.get("type", "?")
        c = rec.get("content", "")
        ref = rec.get("ref", "")
        print(f"  [{ts}] {lt:8} {c[:10000]}")
        if ref: print(f"           → {ref}")

# ── main ──────────────────────────────────────────────────────

def _assert_no_unknown_flags(rest, sub_cmd):
    bad = [t for t in rest if isinstance(t, str) and t.startswith("--")]
    if bad:
        print(
            f"❌ {sub_cmd}: 不识别的 flag {bad}（防呆：拒绝 silent drop）",
            file=sys.stderr,
        )
        sys.exit(1)


def _handler_map():
    return {
        "send": cmd_send,
        "direct": cmd_direct,
        "say": cmd_say,
        "inbox": cmd_inbox,
        "read": cmd_read,
        "status": cmd_status,
        "log": cmd_log,
        "workspace": cmd_workspace,
    }


def _delegate_main(args):
    try:
        from claudeteam.commands import feishu_msg as command_mod
    except Exception:
        return None
    if args and args[0] == "send":
        args = list(args)
        if "--file" in args:
            idx = args.index("--file")
            if idx + 1 < len(args):
                with open(args[idx + 1], encoding="utf-8") as _f:
                    if len(args) > 3:
                        args[3] = _f.read().strip()
                del args[idx:idx + 2]
        if "--content" in args:
            idx = args.index("--content")
            if idx + 1 < len(args) and len(args) > 3:
                args[3] = args[idx + 1]
                del args[idx:idx + 2]
        if "--priority" in args:
            idx = args.index("--priority")
            if idx + 1 < len(args):
                if len(args) > 4 and not str(args[4]).startswith("--"):
                    args[4] = args[idx + 1]
                else:
                    args.insert(4, args[idx + 1])
                del args[idx + 1:idx + 3]
    result = command_mod.run(args, handlers=_handler_map())
    message = getattr(result, "message", "") or ""
    if message:
        print(message)
    sys.exit(int(getattr(result, "exit_code", 0) or 0))


def main():
    args = sys.argv[1:]
    delegated = _delegate_main(args)
    if delegated is not None:
        return delegated
    if not args: print(__doc__); sys.exit(0)
    cmd = args[0]
    if cmd == "say":
        say_args = list(args[1:])
        image_path = ""
        file_path = ""
        reply_to = ""
        reply_in_thread = False
        if "--reply-in-thread" in say_args:
            say_args = [a for a in say_args if a != "--reply-in-thread"]
            reply_in_thread = True
        for flag in ("--image", "--file", "--reply"):
            if flag in say_args:
                idx = say_args.index(flag)
                val = say_args[idx + 1] if idx + 1 < len(say_args) else ""
                say_args = [
                    a for i, a in enumerate(say_args)
                    if i != idx and i != idx + 1
                ]
                if flag == "--image":
                    image_path = val
                elif flag == "--file":
                    file_path = val
                else:
                    reply_to = val
        _assert_no_unknown_flags(say_args, "say")
        if len(say_args) < 1:
            print("用法: say <发件人> [\"<消息>\"] [--image <路径>] [--file <路径>] [--reply <message_id>] [--reply-in-thread]"); sys.exit(1)
        from_agent = say_args[0]
        if file_path:
            with open(file_path, encoding="utf-8") as _f:
                message = _f.read().strip()
        else:
            message = say_args[1] if len(say_args) > 1 else ""
        cmd_say(from_agent, message, image_path, reply_to, reply_in_thread)
    elif cmd == "send":
        if len(args) < 4: print("用法: send <收件人> <发件人> \"<消息>\" [优先级] [--task <task_id>] [--file <路径>] [--priority <值>] [--content <消息>]"); sys.exit(1)
        rest = list(args[1:])
        flag_vals = {"--task": "", "--file": "", "--priority": "", "--content": ""}
        for flag in list(flag_vals):
            if flag in rest:
                idx = rest.index(flag)
                if idx + 1 < len(rest):
                    flag_vals[flag] = rest[idx + 1]
                    rest.pop(idx + 1)
                    rest.pop(idx)
        _assert_no_unknown_flags(rest, "send")
        if len(rest) < 2:
            print("用法: send <收件人> <发件人> \"<消息>\" [优先级]"); sys.exit(1)
        to_agent, from_agent = rest[0], rest[1]
        if flag_vals["--file"]:
            with open(flag_vals["--file"], encoding="utf-8") as _f:
                message = _f.read().strip()
        elif flag_vals["--content"]:
            message = flag_vals["--content"]
        else:
            message = rest[2] if len(rest) > 2 else ""
        priority = flag_vals["--priority"] or (rest[3] if len(rest) > 3 else "中")
        cmd_send(to_agent, from_agent, message, priority, flag_vals["--task"])
    elif cmd == "direct":
        if len(args) < 4: print("用法: direct <收件人> <发件人> '<消息>'"); sys.exit(1)
        _assert_no_unknown_flags(list(args[1:4]), "direct")
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
