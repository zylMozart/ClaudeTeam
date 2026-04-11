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
from config import AGENTS, PROJECT_ROOT, load_runtime_config

LARK_CLI = ["npx", "@larksuite/cli"]

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

# ── lark-cli 封装 ────────────────────────────────────────────

def _lark_run(args, timeout=30):
    """执行 lark-cli 命令，返回解析后的 JSON（失败返回 None）。"""
    r = subprocess.run(LARK_CLI + args, capture_output=True, text=True, timeout=timeout)
    if r.returncode != 0:
        print(f"  ⚠️ lark-cli 失败: {r.stderr.strip()[:200]}")
        return None
    if not r.stdout.strip():
        return {}
    try:
        return json.loads(r.stdout)
    except json.JSONDecodeError:
        return {"_raw": r.stdout.strip()}


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


def _lark_base_search(base_token, table_id, search_json):
    """搜索 Bitable 记录。"""
    return _lark_run(["base", "+record-search",
                      "--base-token", base_token, "--table-id", table_id,
                      "--json", json.dumps(search_json, ensure_ascii=False),
                      "--as", "bot"])


def _lark_base_update(base_token, table_id, record_ids, patch):
    """批量更新 Bitable 记录。"""
    payload = json.dumps({"record_id_list": record_ids, "patch": patch},
                         ensure_ascii=False)
    return _lark_run(["base", "+record-batch-update",
                      "--base-token", base_token, "--table-id", table_id,
                      "--json", payload, "--as", "bot"])


def _lark_base_list(base_token, table_id, limit=20):
    """列出 Bitable 记录。"""
    return _lark_run(["base", "+record-list",
                      "--base-token", base_token, "--table-id", table_id,
                      "--limit", str(limit), "--as", "bot"])

# ── 消息卡片构建 ──────────────────────────────────────────────

def build_card(from_agent, to_agent, content, priority="中"):
    """构建飞书消息卡片 JSON"""
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
    """向飞书群组发一条消息卡片"""
    chat_id = CHAT()
    if not chat_id:
        return
    card = build_card(from_agent, to_agent, content, priority)
    _lark_im_send(chat_id, card=card)

# ── 工作空间日志 ───────────────────────────────────────────────

def ws_log(agent, log_type, content, ref=""):
    tid = WS(agent)
    if not tid: return
    _lark_base_create(BT(), tid,
                      {"类型": log_type, "内容": content,
                       "时间": now_ms(), "关联对象": ref})

# ── 命令：say（直接发群消息，用于回复用户）──────────────────────

def cmd_say(from_agent, message="", image_path=""):
    if not message and not image_path:
        print("❌ 消息内容和图片路径不能同时为空"); sys.exit(1)
    chat_id = CHAT()
    if not chat_id:
        print("❌ 群组未配置"); sys.exit(1)

    if message:
        card = build_card(from_agent, None, message)
        d = _lark_im_send(chat_id, card=card)
        if d is None:
            print("❌ 发送失败"); sys.exit(1)
        ws_log(from_agent, "消息发出", f"→ 群聊：{message[:10000]}")
        print(f"✅ 已发送到群聊")

    if image_path:
        if not os.path.exists(image_path):
            print(f"❌ 图片文件不存在: {image_path}"); sys.exit(1)
        d = _lark_im_send(chat_id, image=image_path)
        if d is None:
            print("❌ 图片消息发送失败"); sys.exit(1)
        ws_log(from_agent, "消息发出",
               f"→ 群聊图片：{os.path.basename(image_path)}")
        print(f"✅ 图片已发送到群聊: {os.path.basename(image_path)}")

# ── 辅助：Bitable 写入单条消息 ────────────────────────────────

def bitable_insert_message(to, frm, content, priority):
    """向消息表写入一条记录，返回 record_id 或 None。"""
    d = _lark_base_create(BT(), MT(),
                          {"收件人": to, "发件人": frm,
                           "消息内容": content, "优先级": priority,
                           "时间": now_ms(), "已读": False})
    if d is None:
        return None
    # lark-cli batch-create 返回的 records 列表
    records = d.get("data", {}).get("records", [])
    if records:
        return records[0].get("record_id", "")
    # 兼容不同返回格式
    return d.get("record_id", "")

# ── 命令：send ────────────────────────────────────────────────

def cmd_send(to_agent, from_agent, message, priority="中", task_id=""):
    actual_message = f"[{task_id}] {message}" if task_id else message
    rid = bitable_insert_message(to_agent, from_agent, actual_message, priority)
    if not rid:
        print(f"❌ 发送失败"); sys.exit(1)
    ref_str = f"{rid} | task:{task_id}" if task_id else rid
    post_to_group(from_agent, to_agent, actual_message, priority)
    ws_log(from_agent, "消息发出", f"→ {to_agent}：{actual_message[:10000]}", ref_str)
    ws_log(to_agent, "消息收到", f"← {from_agent}：{actual_message[:10000]}", ref_str)
    print(f"✅ 消息已发送 → {to_agent}  [rid: {rid}]")

# ── 命令：direct ──────────────────────────────────────────────

def cmd_direct(to_agent, from_agent, message):
    """直连发消息：写入收件箱，自动抄送 manager。"""
    rid = bitable_insert_message(to_agent, from_agent, message, "中")
    if not rid:
        print(f"❌ 发送失败"); sys.exit(1)

    cc_rid = None
    if to_agent != "manager" and from_agent != "manager":
        cc_content = f"[抄送] {from_agent}→{to_agent}: {message}"
        cc_rid = bitable_insert_message("manager", from_agent, cc_content, "低")

    # 群通知（带 [直连] 标记）
    chat_id = CHAT()
    if chat_id:
        info = AGENTS.get(from_agent, {"role": "?", "emoji": "🤖", "color": "grey"})
        title = f"{info['emoji']} {from_agent} · {info['role']} → @{to_agent} [直连]"
        card = {
            "config": {"wide_screen_mode": True},
            "header": {"template": info.get("color", "grey"),
                       "title": {"tag": "plain_text", "content": title}},
            "elements": [{"tag": "markdown", "content": message}]
        }
        _lark_im_send(chat_id, card=card)

    ws_log(from_agent, "消息发出", f"→ {to_agent}[直连]：{message[:10000]}", rid)
    ws_log(to_agent,   "消息收到", f"← {from_agent}[直连]：{message[:10000]}", rid)

    print(f"✅ 消息已直发 → {to_agent}  [rid: {rid}]")
    if cc_rid:
        print(f"✅ 抄送已发送 → manager     [rid: {cc_rid}]")

# ── 命令：inbox ───────────────────────────────────────────────

def cmd_inbox(agent_name):
    d = _lark_base_search(BT(), MT(), {
        "filter": {"conjunction": "and", "conditions": [
            {"field_name": "收件人", "operator": "is", "value": [agent_name]},
            {"field_name": "已读",   "operator": "is", "value": ["false"]},
        ]},
        "sort": [{"field_name": "时间", "desc": False}]
    })
    records = (d or {}).get("data", {}).get("items", [])
    if not records:
        print(f"📭 {agent_name} 暂无未读消息")
        return
    print(f"📬 {agent_name} 有 {len(records)} 条未读消息:\n")
    for rec in records:
        f = rec["fields"]
        rid = rec["record_id"]
        t = f.get("时间", 0)
        ts = time.strftime("%m-%d %H:%M", time.localtime(t / 1000)) if isinstance(t, (int, float)) else "?"
        frm = extract_text(f.get("发件人", "?"))
        pri = extract_text(f.get("优先级", "?"))
        content = extract_text(f.get("消息内容", ""))
        print(f"── [{ts}] 来自 {frm} [优先级:{pri}]")
        print(f"   {content}")
        print(f"   标记已读: python3 scripts/feishu_msg.py read {rid}")
        print()

# ── 命令：read ────────────────────────────────────────────────

def cmd_read(record_id):
    d = _lark_base_update(BT(), MT(), [record_id], {"已读": True})
    if d is None:
        print(f"❌ 标记失败"); sys.exit(1)
    print(f"✅ 已标记已读: {record_id}")

# ── 命令：status ──────────────────────────────────────────────

def cmd_status(agent_name, status, task, blocker=""):
    # 先搜索是否已有记录
    d = _lark_base_search(BT(), ST(), {
        "filter": {"conjunction": "and", "conditions": [
            {"field_name": "Agent名称", "operator": "is", "value": [agent_name]}
        ]}
    })
    records = (d or {}).get("data", {}).get("items", [])
    fields = {"Agent名称": agent_name, "状态": status, "当前任务": task,
              "阻塞原因": blocker, "更新时间": now_ms()}
    if records:
        _lark_base_update(BT(), ST(), [records[0]["record_id"]], fields)
    else:
        _lark_base_create(BT(), ST(), fields)
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
    if d is None:
        print(f"❌ 写入失败"); sys.exit(1)
    print(f"✅ [{log_type}] 已写入 {agent_name} 工作空间")

# ── 命令：workspace ────────────────────────────────────────────

def cmd_workspace(agent_name):
    tid = WS(agent_name)
    if not tid:
        print(f"❌ 找不到 {agent_name} 的工作空间"); sys.exit(1)
    d = _lark_base_list(BT(), tid, limit=20)
    items = (d or {}).get("data", {}).get("items", [])
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
