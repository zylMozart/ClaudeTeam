"""Feishu lark-cli adapter primitives.

This module only contains low-level remote I/O helpers. Business semantics stay
in scripts/feishu_msg.py during migration.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
from claudeteam.runtime.config import LARK_CLI, get_chat_id as _runtime_get_chat_id

# 服务器侧 /records/search 状态码:
#   800080303 "unsafe_operation_blocked" = 端点在当前品牌(目前仅国际版 Lark)
#   还未放出,再多重试也没用,必须走客户端过滤兜底。
_BITABLE_SEARCH_PATH_BLOCKED_CODE = 800080303


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


def _lark_im_send_with_run(
    run_fn,
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
    return run_fn(args)


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
    return _lark_im_send_with_run(
        _lark_run,
        chat_id,
        content,
        markdown,
        image,
        card,
        reply_to=reply_to,
        reply_in_thread=reply_in_thread,
        msg_type=msg_type,
    )


def _lark_base_create_with_run(run_fn, base_token, table_id, fields_json):
    """向 Bitable 写入一条记录，返回响应 JSON。"""
    payload = json.dumps({"fields": list(fields_json.keys()),
                          "rows": [list(fields_json.values())]},
                         ensure_ascii=False)
    return run_fn(["base", "+record-batch-create",
                   "--base-token", base_token, "--table-id", table_id,
                   "--json", payload, "--as", "bot"])


def _lark_base_create(base_token, table_id, fields_json):
    return _lark_base_create_with_run(_lark_run, base_token, table_id, fields_json)


def get_chat_id() -> str:
    """Return the configured Feishu group chat_id, or empty string if not set."""
    return _runtime_get_chat_id()


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


def _lark_base_update_with_run(run_fn, base_token, table_id, record_ids, patch):
    """批量更新 Bitable 记录。"""
    payload = json.dumps({"record_id_list": record_ids, "patch": patch},
                         ensure_ascii=False)
    return run_fn(["base", "+record-batch-update",
                   "--base-token", base_token, "--table-id", table_id,
                   "--json", payload, "--as", "bot"])


def _lark_base_update(base_token, table_id, record_ids, patch):
    return _lark_base_update_with_run(_lark_run, base_token, table_id, record_ids, patch)


def _lark_base_list_with_run(run_fn, base_token, table_id, limit=20, offset=0):
    """列出 Bitable 记录（支持 offset 翻页）。"""
    args = ["base", "+record-list",
            "--base-token", base_token, "--table-id", table_id,
            "--limit", str(limit), "--as", "bot"]
    if offset:
        args += ["--offset", str(offset)]
    return run_fn(args)


def _lark_base_list(base_token, table_id, limit=20, offset=0):
    return _lark_base_list_with_run(_lark_run, base_token, table_id, limit=limit, offset=offset)
