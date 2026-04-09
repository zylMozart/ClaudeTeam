#!/usr/bin/env python3
"""
飞书通讯脚本 — ClaudeTeam

用法:
  python3 scripts/feishu_msg.py send <收件人> <发件人> "<消息>" [优先级]
  python3 scripts/feishu_msg.py direct <收件人> <发件人> "<消息>"  # 直连，manager 自动抄送
  python3 scripts/feishu_msg.py say <发件人> ["<消息>"] [--image <路径>]  # 发群消息/图片
  python3 scripts/feishu_msg.py inbox <agent名称>
  python3 scripts/feishu_msg.py read <record_id>
  python3 scripts/feishu_msg.py status <agent> <状态> "<任务>" ["<阻塞原因>"]
  python3 scripts/feishu_msg.py log <agent> <类型> "<内容>" ["<关联对象>"]
  python3 scripts/feishu_msg.py workspace <agent>

优先级: 高 | 中（默认）| 低
状态:   进行中 | 已完成 | 阻塞 | 待命
类型:   状态更新 | 任务日志 | 消息发出 | 消息收到 | 产出记录 | 阻塞上报
"""
import sys, os, json, time, requests

sys.path.insert(0, os.path.dirname(__file__))
from config import APP_ID, APP_SECRET, BASE, AGENTS, CONFIG_FILE, PROJECT_ROOT


# ── 运行时配置加载 ─────────────────────────────────────────────

_cfg = None
def cfg():
    global _cfg
    if _cfg is None:
        if os.path.exists(CONFIG_FILE):
            with open(CONFIG_FILE) as f:
                _cfg = json.load(f)
        else:
            print("❌ 未找到 runtime_config.json，请先运行 python3 scripts/setup.py")
            sys.exit(1)
    return _cfg

def BT():  return cfg()["bitable_app_token"]
def MT():  return cfg()["msg_table_id"]
def ST():  return cfg()["sta_table_id"]
def WS(a): return cfg()["workspace_tables"].get(a, "")
def CHAT(): return cfg().get("chat_id", "")

# ── 基础工具 ──────────────────────────────────────────────────

from token_cache import get_token_cached

def get_token():
    return get_token_cached(APP_ID, APP_SECRET, BASE)

def h(token):
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

def now_ms():
    return int(time.time() * 1000)

def txt(v):
    if isinstance(v, list): return v[0].get("text", "") if v else ""
    return str(v) if v else ""

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

def send_card_to_group(token, chat_id, card):
    """向群聊发送消息卡片"""
    return requests.post(
        f"{BASE}/im/v1/messages?receive_id_type=chat_id",
        headers=h(token),
        json={
            "receive_id": chat_id,
            "msg_type": "interactive",
            "content": json.dumps(card, ensure_ascii=False)
        }
    )

# ── 群组发消息 ─────────────────────────────────────────────────

def post_to_group(token, from_agent, to_agent, content, priority="中"):
    """向飞书群组发一条消息卡片"""
    chat_id = CHAT()
    if not chat_id:
        return
    card = build_card(from_agent, to_agent, content, priority)
    send_card_to_group(token, chat_id, card)

# ── 工作空间日志 ───────────────────────────────────────────────

def ws_log(token, agent, log_type, content, ref=""):
    tid = WS(agent)
    if not tid: return
    requests.post(f"{BASE}/bitable/v1/apps/{BT()}/tables/{tid}/records",
                  headers=h(token),
                  json={"fields": {"类型": log_type, "内容": content,
                                   "时间": now_ms(), "关联对象": ref}})

# ── 辅助：上传图片到飞书 ──────────────────────────────────────

def upload_image(token, local_path):
    """上传本地图片到飞书，返回 image_key；文件不存在或上传失败返回 None。"""
    if not os.path.exists(local_path):
        print(f"❌ 图片文件不存在: {local_path}")
        return None
    with open(local_path, "rb") as f:
        r = requests.post(
            f"{BASE}/im/v1/images",
            headers={"Authorization": f"Bearer {get_token()}"},
            files={"image": f},
            data={"image_type": "message"}
        )
    d = r.json()
    if d.get("code") != 0:
        print(f"❌ 图片上传失败: {d.get('msg')}")
        return None
    return d["data"]["image_key"]

# ── 命令：say（直接发群消息，用于回复用户）──────────────────────

def cmd_say(from_agent, message="", image_path=""):
    if not message and not image_path:
        print("❌ 消息内容和图片路径不能同时为空"); sys.exit(1)
    token = get_token()
    chat_id = CHAT()
    if not chat_id:
        print("❌ 群组未配置"); sys.exit(1)

    # 1. 有文字时发消息卡片
    if message:
        card = build_card(from_agent, None, message)
        r = send_card_to_group(token, chat_id, card)
        if r.json().get("code") != 0:
            print(f"❌ 发送失败: {r.json()}"); sys.exit(1)
        ws_log(token, from_agent, "消息发出", f"→ 群聊：{message[:10000]}")
        print(f"✅ 已发送到群聊")

    # 2. 有图片时上传后发图片消息
    if image_path:
        image_key = upload_image(token, image_path)
        if not image_key:
            sys.exit(1)
        r = requests.post(f"{BASE}/im/v1/messages?receive_id_type=chat_id",
                          headers=h(token),
                          json={"receive_id": chat_id, "msg_type": "image",
                                "content": json.dumps({"image_key": image_key})})
        if r.json().get("code") != 0:
            print(f"❌ 图片消息发送失败: {r.json()}"); sys.exit(1)
        ws_log(token, from_agent, "消息发出",
               f"→ 群聊图片：{os.path.basename(image_path)}")
        print(f"✅ 图片已发送到群聊: {os.path.basename(image_path)}")


# ── 辅助：Bitable 写入单条消息 ────────────────────────────────

def bitable_insert_message(token, to, frm, content, priority):
    """向消息表写入一条记录，返回原始 response JSON。"""
    return requests.post(
        f"{BASE}/bitable/v1/apps/{BT()}/tables/{MT()}/records",
        headers=h(token),
        json={"fields": {"收件人": to, "发件人": frm,
                         "消息内容": content, "优先级": priority,
                         "时间": now_ms(), "已读": False}}
    ).json()

# ── 命令：send ────────────────────────────────────────────────

def cmd_send(to_agent, from_agent, message, priority="中", task_id=""):
    # 若关联任务，在消息内容前加 [TASK-XXX] 前缀
    actual_message = f"[{task_id}] {message}" if task_id else message
    token = get_token()
    # ① 核心：写 Bitable 收件箱（必须先完成，拿 rid）
    d = bitable_insert_message(token, to_agent, from_agent, actual_message, priority)
    if d.get("code") != 0:
        print(f"❌ 发送失败: {d}"); sys.exit(1)
    rid = d["data"]["record"]["record_id"]
    # ② 群聊通知 + ws_log 全部并行（Bitable 已拿到 rid，后续均为 fire-and-forget）
    ref_str = f"{rid} | task:{task_id}" if task_id else rid
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=3) as pool:
        pool.submit(post_to_group, token, from_agent, to_agent, actual_message, priority)
        pool.submit(ws_log, token, from_agent, "消息发出",
                    f"→ {to_agent}：{actual_message[:10000]}", ref_str)
        pool.submit(ws_log, token, to_agent, "消息收到",
                    f"← {from_agent}：{actual_message[:10000]}", ref_str)
    print(f"✅ 消息已发送 → {to_agent}  [rid: {rid}]")

# ── 辅助：直连消息群通知 ───────────────────────────────────────

def post_to_group_direct(token, from_agent, to_agent, content, priority="中"):
    """直连消息的群组卡片通知，标题栏加 [直连] 标记。"""
    chat_id = CHAT()
    if not chat_id:
        return
    info = AGENTS.get(from_agent, {"role": "?", "emoji": "🤖", "color": "grey"})
    color = info.get("color", "grey")
    title = f"{info['emoji']} {from_agent} · {info['role']} → @{to_agent} [直连]"
    pri_tag = {"高": "🔴 ", "中": "", "低": "🟢 "}.get(priority, "")
    card = {
        "config": {"wide_screen_mode": True},
        "header": {
            "template": color,
            "title": {"tag": "plain_text", "content": title}
        },
        "elements": [
            {"tag": "markdown", "content": f"{pri_tag}{content}"}
        ]
    }
    send_card_to_group(token, chat_id, card)

# ── 命令：direct ──────────────────────────────────────────────

def cmd_direct(to_agent, from_agent, message):
    """
    直连发消息：写入收件人收件箱，并自动抄送 manager（低优先级）。
    若 to 或 from 任意一方是 manager，跳过抄送步骤。
    """
    token = get_token()

    # ① 发给收件人（优先级默认"中"）
    d = bitable_insert_message(token, to_agent, from_agent, message, "中")
    if d.get("code") != 0:
        print(f"❌ 发送失败: {d}"); sys.exit(1)
    rid = d["data"]["record"]["record_id"]

    # ② 抄送 manager（任意一方是 manager 时跳过）
    cc_rid = None
    if to_agent != "manager" and from_agent != "manager":
        cc_content = f"[抄送] {from_agent}→{to_agent}: {message}"
        d2 = bitable_insert_message(token, "manager", from_agent, cc_content, "低")
        if d2.get("code") == 0:
            cc_rid = d2["data"]["record"]["record_id"]

    # ③ 发飞书群消息（带"[直连]"标记，仅一条）
    post_to_group_direct(token, from_agent, to_agent, message)

    # ④ 双方工作空间日志
    ws_log(token, from_agent, "消息发出", f"→ {to_agent}[直连]：{message[:10000]}", rid)
    ws_log(token, to_agent,   "消息收到", f"← {from_agent}[直连]：{message[:10000]}", rid)

    print(f"✅ 消息已直发 → {to_agent}  [rid: {rid}]")
    if cc_rid:
        print(f"✅ 抄送已发送 → manager     [rid: {cc_rid}]")

# ── 命令：inbox ───────────────────────────────────────────────

def cmd_inbox(agent_name):
    token = get_token()
    r = requests.post(f"{BASE}/bitable/v1/apps/{BT()}/tables/{MT()}/records/search",
                      headers=h(token),
                      json={"filter": {"conjunction": "and", "conditions": [
                                {"field_name": "收件人", "operator": "is", "value": [agent_name]},
                                {"field_name": "已读",   "operator": "is", "value": ["false"]},
                            ]},
                            "sort": [{"field_name": "时间", "desc": False}]})
    records = r.json().get("data", {}).get("items", [])
    if not records:
        print(f"📭 {agent_name} 暂无未读消息")
        return
    print(f"📬 {agent_name} 有 {len(records)} 条未读消息:\n")
    for rec in records:
        f = rec["fields"]
        rid = rec["record_id"]
        t = f.get("时间", 0)
        ts = time.strftime("%m-%d %H:%M", time.localtime(t / 1000)) if isinstance(t, (int, float)) else "?"
        frm = txt(f.get("发件人", "?"))
        pri = txt(f.get("优先级", "?"))
        content = txt(f.get("消息内容", ""))
        print(f"── [{ts}] 来自 {frm} [优先级:{pri}]")
        print(f"   {content}")
        print(f"   标记已读: python3 scripts/feishu_msg.py read {rid}")
        print()

# ── 命令：read ────────────────────────────────────────────────

def cmd_read(record_id):
    token = get_token()
    r = requests.put(f"{BASE}/bitable/v1/apps/{BT()}/tables/{MT()}/records/{record_id}",
                     headers=h(token), json={"fields": {"已读": True}})
    if r.json().get("code") != 0:
        print(f"❌ 标记失败: {r.json()}"); sys.exit(1)
    print(f"✅ 已标记已读: {record_id}")

# ── 命令：status ──────────────────────────────────────────────

def cmd_status(agent_name, status, task, blocker=""):
    token = get_token()
    r = requests.post(f"{BASE}/bitable/v1/apps/{BT()}/tables/{ST()}/records/search",
                      headers=h(token),
                      json={"filter": {"conjunction": "and", "conditions": [
                                {"field_name": "Agent名称", "operator": "is", "value": [agent_name]}]}})
    records = r.json().get("data", {}).get("items", [])
    fields = {"Agent名称": agent_name, "状态": status, "当前任务": task,
              "阻塞原因": blocker, "更新时间": now_ms()}
    if records:
        requests.put(f"{BASE}/bitable/v1/apps/{BT()}/tables/{ST()}/records/{records[0]['record_id']}",
                     headers=h(token), json={"fields": fields})
    else:
        requests.post(f"{BASE}/bitable/v1/apps/{BT()}/tables/{ST()}/records",
                      headers=h(token), json={"fields": fields})
    content = f"状态：{status} | {task}"
    if blocker: content += f" | ⛔ {blocker}"
    ws_log(token, agent_name, "阻塞上报" if status == "阻塞" else "状态更新", content)
    print(f"✅ {agent_name} → {status}: {task}")

# ── 命令：log ─────────────────────────────────────────────────

def cmd_log(agent_name, log_type, content, ref=""):
    token = get_token()
    tid = WS(agent_name)
    if not tid:
        print(f"❌ 找不到 {agent_name} 的工作空间"); sys.exit(1)
    r = requests.post(f"{BASE}/bitable/v1/apps/{BT()}/tables/{tid}/records",
                      headers=h(token),
                      json={"fields": {"类型": log_type, "内容": content,
                                       "时间": now_ms(), "关联对象": ref}})
    if r.json().get("code") != 0:
        print(f"❌ 写入失败: {r.json()}"); sys.exit(1)
    print(f"✅ [{log_type}] 已写入 {agent_name} 工作空间")

# ── 命令：workspace ────────────────────────────────────────────

def cmd_workspace(agent_name):
    token = get_token()
    tid = WS(agent_name)
    if not tid:
        print(f"❌ 找不到 {agent_name} 的工作空间"); sys.exit(1)
    r = requests.post(f"{BASE}/bitable/v1/apps/{BT()}/tables/{tid}/records/search",
                      headers=h(token),
                      json={"page_size": 20, "sort": [{"field_name": "时间", "desc": True}]})
    items = r.json().get("data", {}).get("items", [])
    print(f"📁 {agent_name} 工作空间 (最近 {len(items)} 条):\n")
    for rec in items:
        f = rec["fields"]
        t = f.get("时间", 0)
        ts = time.strftime("%m-%d %H:%M", time.localtime(t / 1000)) if isinstance(t, (int, float)) else "?"
        lt = txt(f.get("类型", "?"))
        c  = txt(f.get("内容", ""))
        ref = txt(f.get("关联对象", ""))
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
        # 用法: say <发件人> ["<消息>"] [--image <路径>]
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
        # 提取可选参数（--task、--file 可出现在任意位置）
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
            # 消息内容从文件读取（避免 # 等特殊字符在 shell/tmux 中被解释）
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
