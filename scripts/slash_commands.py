"""Shared slash-command dispatcher — 零 LLM 旁路。

两处入口共用：
  1) scripts/feishu_router.py handle_event — 群聊 /xxx 消息不走 LLM、不进 manager
  2) .claude/hooks/*_intercept.py — Claude Code 本机输入

约定：
  dispatch(text) -> (matched: bool, reply: str | None)
  matched=True 时 reply 是要回显给用户的文本；matched=False 时交给正常路径。
  任何 side effect（tmux send-keys / subprocess）都在这里执行。
"""
import json
import os
import re
import subprocess
import time
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from pathlib import Path

PROJECT_ROOT = Path(os.environ.get("CLAUDE_PROJECT_DIR") or
                    Path(__file__).resolve().parent.parent)
BJ_TZ = timezone(timedelta(hours=8))

def _load_agent_windows():
    """从 team.json 动态读取 agent 列表,不再硬编码。"""
    try:
        tj = json.loads((PROJECT_ROOT / "team.json").read_text())
        return list(tj.get("agents", {}).keys())
    except Exception:
        return ["manager"]

AGENT_WINDOWS = _load_agent_windows()
AGENT_SET = set(AGENT_WINDOWS)


def _host_session() -> str:
    try:
        tj = json.loads((PROJECT_ROOT / "team.json").read_text())
        return tj.get("session", "server-manager")
    except Exception:
        return "server-manager"


def _run(cmd, timeout=5):
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except Exception as e:
        class R: returncode = -1; stdout = ""; stderr = str(e)
        return R()


def _containers():
    r = _run(["sudo", "-n", "docker", "ps", "--format", "{{.Names}}"])
    if r.returncode != 0:
        return []
    return [n.strip() for n in r.stdout.splitlines()
            if n.strip().startswith("claudeteam-")]


def _list_windows_local(session: str):
    r = _run(["tmux", "list-windows", "-t", session, "-F", "#{window_name}"])
    if r.returncode != 0:
        return []
    return [w.strip() for w in r.stdout.splitlines() if w.strip()]


def _container_window_map(cname: str) -> dict:
    r = _run(["sudo", "-n", "docker", "exec", cname, "tmux",
              "list-windows", "-a", "-F", "#{session_name}:#{window_name}"])
    if r.returncode != 0:
        return {}
    m = {}
    for line in r.stdout.splitlines():
        sess, _, win = line.partition(":")
        if win in AGENT_SET and win not in m:
            m[win] = f"{sess}:{win}"
    return m


def _send_local(session: str, agent: str, msg: str) -> bool:
    t = f"{session}:{agent}"
    if _run(["tmux", "send-keys", "-t", t, msg]).returncode != 0:
        return False
    return _run(["tmux", "send-keys", "-t", t, "Enter"]).returncode == 0


def _send_container(cname: str, target: str, msg: str) -> bool:
    if _run(["sudo", "-n", "docker", "exec", cname, "tmux",
             "send-keys", "-t", target, msg]).returncode != 0:
        return False
    return _run(["sudo", "-n", "docker", "exec", cname, "tmux",
                 "send-keys", "-t", target, "Enter"]).returncode == 0


# ── /help ──────────────────────────────────────────────────────
_HELP_TEXT = """🆘 ClaudeTeam 自定义斜杠命令（零 LLM，router/hook 直拦）

/help                    → 本帮助
/team                    → 所有员工实时 tmux 状态（卡片）
/usage                   → Claude Max 周额度 + Extra usage 快照（卡片）
/health                  → 主机 + 容器 + 员工资源占用（卡片）
/tmux [agent] [lines]    → capture-pane 窗口（默认 manager/10 行）
/send <agent> <msg>      → 直接注入消息到 agent 窗口
/compact [agent]         → 群聊无参=压缩 manager；带参压缩指定 agent
/stop <agent>            → 送 C-c 到 agent 窗口（中断当前动作）
/clear <agent>           → 送 /clear + 重新入职 init_msg（相当于 rehire）
"""


def _cmd_help(text: str):
    return _HELP_TEXT if re.fullmatch(r"/help\s*", text) else None


# ── /usage ─────────────────────────────────────────────────────
_USAGE_LINE_RE = re.compile(
    r"^\s*(?P<label>[^:]+?)\s*:\s*(?P<pct>[\d.]+)%\s+"
    r"(?:\(重置:\s*(?P<reset>.+?)\)|resets\s+(?P<reset_en>.+?))\s*$")
_USAGE_EXTRA_RE = re.compile(
    r"^\s*(?P<label>Extra usage|额外用量)\s*:\s*\$?(?P<used>[\d.]+)\s*/\s*\$?(?P<cap>[\d.]+)\s+"
    r"\((?P<pct>[\d.]+)%\)\s*(?:\[(?P<ccy>\S+)\])?\s*$")


def _pct_color(p: int) -> str:
    if p >= 80:
        return "red"
    if p >= 50:
        return "orange"
    return "green"


def _find_cred(*candidates) -> Path | None:
    """返回第一个存在的凭证文件路径，全不存在返回 None。"""
    for p in candidates:
        if p.is_file():
            return p
    return None


# ── 各 CLI tmux 用量查询 ──────────────────────────────────────────

# Kimi /usage 输出:  Weekly limit  ━━━━  99% left  (resets in 4d 39m)
_KIMI_USAGE_RE = re.compile(
    r"(?P<label>[\w ]+?)\s+[━─]+\s+(?P<left>\d+)%\s+left\s+"
    r"\(resets\s+in\s+(?P<reset>.+?)\)")


def _tmux_target(agent: str):
    """返回 (tmux_target_str, is_container) 或 (None, False)。"""
    session = _host_session()
    if agent in _list_windows_local(session):
        return f"{session}:{agent}", False
    for cname in _containers():
        wmap = _container_window_map(cname)
        if agent in wmap:
            return (cname, wmap[agent]), True
    return None, False


def _tmux_send_and_capture(agent: str, cmd_text: str, wait: int = 4,
                           lines: int = 30) -> str:
    """向 agent pane 发命令并返回 capture 文本。"""
    tgt, is_ctr = _tmux_target(agent)
    if tgt is None:
        return ""
    if is_ctr:
        cname, target = tgt
        pfx = ["sudo", "-n", "docker", "exec", cname]
        if cmd_text:
            _run(pfx + ["tmux", "send-keys", "-t", target, cmd_text, "Enter"], timeout=5)
            time.sleep(wait)
        r = _run(pfx + ["tmux", "capture-pane", "-pt", target,
                         "-S", f"-{lines}"], timeout=5)
    else:
        if cmd_text:
            _run(["tmux", "send-keys", "-t", tgt, cmd_text, "Enter"], timeout=3)
            time.sleep(wait)
        r = _run(["tmux", "capture-pane", "-pt", tgt, "-S", f"-{lines}"], timeout=5)
    return r.stdout if r.returncode == 0 else ""


def _query_kimi_usage() -> list:
    """向 Kimi pane 发 /usage，解析用量。dedup 同名 label 只取最后一次。"""
    raw = _tmux_send_and_capture("kimi_agent", "/usage", wait=5)
    seen = {}  # label → metric (后出的覆盖先出的，避免重复 capture)
    for line in raw.splitlines():
        m = _KIMI_USAGE_RE.search(line)
        if m:
            left = int(m.group("left"))
            used_pct = 100 - left
            label = m.group("label").strip()
            seen[label] = {
                "label": label,
                "pct": used_pct,
                "detail": f"{left}% left · 重置 {m.group('reset')}",
            }
    return list(seen.values())


# Codex: 无 usage 命令；从 pane banner 读 model + plan（不发命令，不干扰 session）
_CODEX_MODEL_RE = re.compile(r"(\S+)\s+default\s+·")
_CODEX_FREE_RE = re.compile(r"included in your plan for free")


_CODEX_USAGE_RE = re.compile(
    r"(?P<label>[\w ()]+?)\s+(?P<pct>\d+)%\s+resets\s+(?P<reset>\S+)")


def _query_codex_usage() -> list:
    """用 codex-cli-usage 工具查 Codex 额度（百分比 + 重置时间）。"""
    r = _run(["codex-cli-usage"], timeout=15)
    if r.returncode != 0:
        # fallback: 工具没装或失败
        return [{"label": "Codex", "pct": -1,
                 "detail": "codex-cli-usage 不可用 · [点击查看 →](https://chatgpt.com/codex/cloud/settings/analytics#usage)"}]
    metrics = []
    plan = ""
    for line in (r.stdout or "").splitlines():
        if line.strip().startswith("Plan:"):
            plan = line.split(":", 1)[1].strip()
        m = _CODEX_USAGE_RE.search(line)
        if m:
            pct = int(m.group("pct"))
            metrics.append({
                "label": m.group("label").strip(),
                "pct": pct,
                "detail": f"已用 {pct}% · 重置 {m.group('reset')}",
            })
    if plan:
        metrics.insert(0, {"label": "Plan", "pct": -1, "detail": f"✅ {plan}"})
    return metrics or [{"label": "Codex", "pct": -1, "detail": "无数据"}]


# Gemini: /stats 命令获取 session 级用量 + banner 读 plan
_GEMINI_PLAN_RE = re.compile(r"Plan:\s+(.+?)(?:\s+/upgrade)?\s*$")
_GEMINI_MODEL_RE = re.compile(r"Auto \((.+?)\)")
_GEMINI_TIER_RE = re.compile(r"Tier:\s+(.+)")
_GEMINI_AUTH_RE = re.compile(r"Auth Method:\s+(.+)")
_GEMINI_REQS_RE = re.compile(r"Tool Calls:\s+(\d+)")


_GEMINI_USAGE_RE = re.compile(
    r"(?P<model>gemini[\w.-]+)\s+(?P<pct>[\d.]+)%\s+used\s+resets\s+(?P<reset>\S+)")


def _query_gemini_usage() -> list:
    """用 gemini-cli-usage 工具查 Gemini 各 model 额度。"""
    r = _run(["gemini-cli-usage"], timeout=15)
    if r.returncode != 0:
        # fallback
        return [{"label": "Gemini", "pct": -1,
                 "detail": "gemini-cli-usage 不可用 · [点击查看 →](https://aistudio.google.com/apikey)"}]
    metrics = []
    auth = ""
    for line in (r.stdout or "").splitlines():
        if "Auth:" in line:
            auth = line.split(":", 1)[1].strip()
        m = _GEMINI_USAGE_RE.search(line)
        if m:
            pct = int(float(m.group("pct")))
            metrics.append({
                "label": m.group("model"),
                "pct": pct,
                "detail": f"已用 {m.group('pct')}% · 重置 {m.group('reset')}",
            })
    if auth:
        metrics.insert(0, {"label": "Auth", "pct": -1, "detail": f"✅ {auth}"})
    return metrics or [{"label": "Gemini", "pct": -1, "detail": "无数据"}]
    return result


_CLI_QUERY_MAP = {
    "kimi":   _query_kimi_usage,
    "codex":  _query_codex_usage,
    "gemini": _query_gemini_usage,
}

_CLI_HEADINGS = {
    "cc":     "Claude Code (Max $100/mo)",
    "kimi":   "Kimi ($19/mo Subscription)",
    "codex":  "Codex (ChatGPT Plus/Pro)",
    "gemini": "Gemini (Google AI)",
}


def _query_cli(name: str) -> list:
    """统一入口：返回 [{"label","pct","detail"}, ...]。pct=-1 表示无百分比。

    如果 agent 在 lazy-wake（CLI 没跑），尝试用 credential 文件信息代替实时查询。
    """
    fn = _CLI_QUERY_MAP.get(name)
    if not fn:
        return []
    agent = f"{name}_agent"
    tgt, _ = _tmux_target(agent)
    if tgt is not None:
        try:
            result = fn()
            if result:
                return result
        except Exception as e:
            pass
    # Agent 没跑或查询失败 → 从 credential 文件读静态信息
    home = Path(os.environ.get("HOME", "/home/claudeteam"))
    cli_info = {
        "kimi": {
            "cred_paths": [home / ".kimi" / "config.toml", PROJECT_ROOT / ".kimi-credentials" / "config.toml"],
            "plan": "Subscription $19/mo",
            "detail_fn": lambda: "5h rolling quota · 7d 刷新",
        },
        "codex": {
            "cred_paths": [home / ".codex" / "auth.json", PROJECT_ROOT / ".codex-credentials" / "auth.json"],
            "plan": "ChatGPT Plus/Pro",
            "detail_fn": lambda: "30-150 msg/5h (Plus) · 300-1500 msg/5h (Pro)",
        },
        "gemini": {
            "cred_paths": [home / ".gemini" / "oauth_creds.json", PROJECT_ROOT / ".gemini-credentials" / "oauth_creds.json"],
            "plan": "Google AI",
            "detail_fn": lambda: "60 req/min · 1000 req/day (free)",
        },
    }
    info = cli_info.get(name)
    if not info:
        return [{"label": name.title(), "pct": -1, "detail": "未知 CLI"}]
    logged_in = any(p.is_file() for p in info["cred_paths"])
    if logged_in:
        return [
            {"label": f"{name.title()} Plan", "pct": -1, "detail": f"✅ {info['plan']}"},
            {"label": "额度说明", "pct": -1, "detail": info["detail_fn"]()},
            {"label": "状态", "pct": -1, "detail": "agent 未运行 · 启动后可查实时额度"},
        ]
    return [{"label": name.title(), "pct": -1, "detail": "❌ 未登录"}]


# ── CC 额度解析 ───────────────────────────────────────────────────

def _cc_usage_metrics(raw: str) -> list:
    """把 usage_snapshot.py 输出解析成 metrics。"""
    metrics = []
    for line in raw.splitlines():
        m = _USAGE_EXTRA_RE.match(line)
        if m:
            ccy = m.group("ccy") or "USD"
            metrics.append({
                "label": m.group("label"),
                "pct": int(float(m.group("pct"))),
                "detail": f"${m.group('used')} / ${m.group('cap')} {ccy}",
            })
            continue
        m = _USAGE_LINE_RE.match(line)
        if m:
            reset_raw = m.group("reset") or m.group("reset_en") or ""
            # 把 ISO 时间转换为北京时间显示
            try:
                from dateutil.parser import parse as _dp
                dt = _dp(reset_raw).astimezone(BJ_TZ)
                reset = dt.strftime("%m-%d %H:%M 北京时间")
            except Exception:
                # dateutil 不可用或解析失败 → 尝试简单处理
                try:
                    dt = datetime.fromisoformat(reset_raw.split('.')[0].replace('Z', '+00:00'))
                    dt_bj = dt.astimezone(BJ_TZ)
                    reset = dt_bj.strftime("%m-%d %H:%M 北京时间")
                except Exception:
                    reset = reset_raw
            metrics.append({
                "label": m.group("label").strip(),
                "pct": int(float(m.group("pct"))),
                "detail": f"重置 {reset}",
            })
    return metrics


# ── 卡片构建 ──────────────────────────────────────────────────────

def _build_usage_card(sections: list, title_suffix: str) -> dict:
    """构建多段式 usage 卡片。

    sections: [{"heading": str, "metrics": [{"label","pct","detail"},...]}]
    """
    rows = []
    for sec in sections:
        if sec.get("heading"):
            rows.append({"tag": "markdown",
                         "content": f"**{sec['heading']}**"})
        for met in sec.get("metrics", []):
            pct = met["pct"]
            if pct >= 0:
                color = _pct_color(pct)
                right = (f"<font color='{color}'>**{pct}%**</font>"
                         f" · {met['detail']}")
            else:
                right = met["detail"]
            rows.append({
                "tag": "column_set",
                "flex_mode": "none",
                "background_style": "default",
                "columns": [
                    {"tag": "column", "width": "weighted", "weight": 2,
                     "elements": [{"tag": "markdown",
                                   "content": f"**{met['label']}**"}]},
                    {"tag": "column", "width": "weighted", "weight": 3,
                     "elements": [{"tag": "markdown", "content": right}]},
                ],
            })
        rows.append({"tag": "hr"})

    if rows and rows[-1].get("tag") == "hr":
        rows.pop()

    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "template": "purple",
            "title": {"tag": "plain_text",
                      "content": f"📊 /usage · {title_suffix}"},
        },
        "elements": rows or [{"tag": "markdown", "content": "(无数据)"}],
    }


def _cmd_usage(text: str):
    m = re.fullmatch(r"/usage(?:\s+(\S+))?\s*", text)
    if not m:
        return None
    target = (m.group(1) or "").lower()  # "", "all", "kimi", "codex", "gemini", "cc"

    now = datetime.now(BJ_TZ).strftime("%Y-%m-%d %H:%M")
    sections = []
    text_lines = []

    # 决定要查哪些 CLI
    if target in ("", "cc", "claude"):
        targets = ["cc"]
    elif target == "all":
        targets = ["cc", "kimi", "codex", "gemini"]
    elif target in _CLI_QUERY_MAP:
        targets = [target]
    else:
        return f"⚠️ 未知 CLI：{target}\n支持：/usage [cc|kimi|codex|gemini|all]"

    for t in targets:
        heading = _CLI_HEADINGS.get(t, t)
        if t == "cc":
            snapshot = PROJECT_ROOT / "scripts" / "usage_snapshot.py"
            r = _run(["python3", str(snapshot)], timeout=30)
            if r.returncode != 0:
                sections.append({"heading": heading,
                                 "metrics": [{"label": "CC", "pct": -1,
                                              "detail": f"查询失败 (exit {r.returncode})"}]})
                continue
            raw = (r.stdout or "").rstrip()
            cc_metrics = _cc_usage_metrics(raw)
            sections.append({"heading": heading, "metrics": cc_metrics})
            text_lines.append(raw)
        else:
            metrics = _query_cli(t)
            sections.append({"heading": heading, "metrics": metrics})
            for met in metrics:
                pct_s = f"{met['pct']}%" if met["pct"] >= 0 else "—"
                text_lines.append(f"  {met['label']}: {pct_s}  {met['detail']}")

    title = "All CLIs" if target == "all" else _CLI_HEADINGS.get(target or "cc", target)
    return {"text": "\n".join(text_lines),
            "card": _build_usage_card(sections, f"{title} · {now}")}


# ── /tmux ──────────────────────────────────────────────────────
_TMUX_RE = re.compile(r"^/tmux(?:\s+([A-Za-z0-9_-]+))?(?:\s+(\d+))?\s*$")


def _cmd_tmux(text: str):
    m = _TMUX_RE.match(text)
    if not m:
        return None
    agent = m.group(1) or (AGENT_WINDOWS[0] if AGENT_WINDOWS else "manager")
    lines = int(m.group(2)) if m.group(2) else 10
    lines = max(1, min(lines, 2000))
    if agent not in AGENT_SET:
        return f"⚠️ 未知 agent：`{agent}`"
    session = _host_session()
    r = _run(["tmux", "capture-pane", "-t", f"{session}:{agent}",
              "-p", "-S", f"-{lines}"])
    if r.returncode != 0:
        return f"⚠️ 读取 tmux `{session}:{agent}` 失败：{(r.stderr or '').strip()}"
    body = r.stdout.rstrip() or "(窗口为空)"
    return f"=== {session}:{agent} 最后 {lines} 行 ===\n{body}"


# ── /send ──────────────────────────────────────────────────────
def _cmd_send(text: str):
    if re.fullmatch(r"/send\s*", text):
        return "用法: /send <agent> <message>\n例: /send devops 马上停"
    m = re.match(r"^/send\s+(\S+)\s+(.+)$", text, re.DOTALL)
    if not m:
        if re.match(r"^/send\s+\S+\s*$", text):
            return "用法: /send <agent> <message>\n例: /send devops 马上停\n（缺少消息内容）"
        return None
    agent = m.group(1).strip()
    msg = m.group(2).strip()
    if not re.fullmatch(r"[A-Za-z0-9_-]+", agent):
        return f"⚠️ 非法 agent 名：`{agent}`"
    if agent not in AGENT_SET:
        return f"⚠️ 未知 agent：`{agent}`\n白名单：{sorted(AGENT_SET)}"

    session = _host_session()
    if agent in _list_windows_local(session):
        ok = _send_local(session, agent, msg)
        return f"{'✅' if ok else '❌'} /send → {session}:{agent} (本机)\n内容：{msg}"
    for cname in _containers():
        target = _container_window_map(cname).get(agent)
        if target:
            ok = _send_container(cname, target, msg)
            return f"{'✅' if ok else '❌'} /send → {cname} {target}\n内容：{msg}"
    return f"⚠️ 未找到 agent `{agent}` 的 tmux 窗口"


# ── /compact [agent] ───────────────────────────────────────────
# 注意: hook 端(manager 本机输入) 的 compact_intercept.py 只匹配带参 /compact,
# 无参 /compact 从终端敲会 fall through 给 Claude Code 原生自压缩。
# 这里(router 端/群聊) 则对无参 /compact 默认为 /compact manager,否则走大模型。
def _cmd_compact(text: str):
    m = re.fullmatch(r"/compact(?:\s+(\S+))?\s*", text)
    if not m:
        return None
    agent = (m.group(1) or (AGENT_WINDOWS[0] if AGENT_WINDOWS else "manager")).strip()
    if not re.fullmatch(r"[A-Za-z0-9_-]+", agent):
        return f"⚠️ 非法 agent 名：`{agent}`"
    if agent not in AGENT_SET:
        return f"⚠️ 未知 agent：`{agent}`"
    session = _host_session()
    if agent in _list_windows_local(session):
        ok = _send_local(session, agent, "/compact")
        return f"{'✅' if ok else '❌'} /compact → {session}:{agent} (本机)"
    for cname in _containers():
        target = _container_window_map(cname).get(agent)
        if target:
            ok = _send_container(cname, target, "/compact")
            return f"{'✅' if ok else '❌'} /compact → {cname} {target}"
    return f"⚠️ 未找到 agent `{agent}` 的 tmux 窗口"


# ── /team ──────────────────────────────────────────────────────
def _parse_state(buf: str):
    if not buf:
        return ("❔", "无窗口")
    low = buf.lower()
    tail_lines = [l for l in buf.splitlines() if l.strip()]
    tail = tail_lines[-1] if tail_lines else ""
    if re.search(r"root@[0-9a-f]+:[^#]*#\s*$", tail):
        return ("🛑", "Claude Code 未运行（bash）")
    if "hit your limit" in low:
        return ("⛔", "quota 超限")
    if "do you want to proceed" in low or re.search(r"❯\s*\d\.", buf):
        return ("⚠️", "等权限")
    if "compacting conversation" in low or "compacting…" in low:
        return ("🗜️", "压缩中")
    if "esc to interrupt" in low:
        m = re.search(r"\((\d+m\s*\d+s|\d+s)(?:\s*·[^)]*)?\)", buf)
        return ("🔄", f"工作中 {m.group(1) if m else ''}".strip())
    if "manifesting" in low:
        return ("🔄", "思考中")
    if re.search(r"⏵⏵\s*bypass permissions", buf) or "new task?" in low:
        return ("💤", "idle")
    return ("❓", tail.strip()[:40])


def _build_team_card(sections: list, tally: dict, now: str) -> dict:
    """sections: [(label, [(agent, emoji, brief), ...])]；3 列栅格。"""
    elements = []
    for idx, (label, rows) in enumerate(sections):
        if idx > 0:
            elements.append({"tag": "hr"})
        elements.append({
            "tag": "div",
            "text": {"tag": "lark_md", "content": f"**{label}**"},
        })
        # 3 列 / 行
        i = 0
        while i < len(rows):
            chunk = rows[i:i + 3]
            cols = []
            for agent, emoji, brief in chunk:
                cell = f"{emoji} **{agent}**\n<font color='grey'>{brief or '-'}</font>"
                cols.append({"tag": "column", "width": "weighted", "weight": 1,
                             "elements": [{"tag": "markdown", "content": cell}]})
            while len(cols) < 3:
                cols.append({"tag": "column", "width": "weighted", "weight": 1,
                             "elements": [{"tag": "markdown", "content": " "}]})
            elements.append({
                "tag": "column_set", "flex_mode": "none",
                "background_style": "default", "columns": cols,
            })
            i += 3

    total = sum(tally.values())
    summary = " / ".join(f"{k} {v}" for k, v in tally.items() if v)
    elements.append({"tag": "hr"})
    elements.append({
        "tag": "div",
        "text": {"tag": "lark_md",
                 "content": f"**汇总**：{total} agents · {summary}"},
    })

    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "template": "blue",
            "title": {"tag": "plain_text",
                      "content": f"👥 /team · {now} 北京时间"},
        },
        "elements": elements,
    }


def _cmd_team(text: str):
    if not re.fullmatch(r"/team\s*", text):
        return None
    now = datetime.now(BJ_TZ).strftime("%H:%M")
    sections = []
    text_lines = [f"👥 /team — 员工实时状态 ({now} 北京时间)\n"]
    tally = defaultdict(int)

    host = _host_session()
    rows = []
    text_lines.append(f"[本机 {host}]")
    host_windows = _list_windows_local(host)
    for agent in AGENT_WINDOWS:
        if agent not in host_windows:
            continue
        r = _run(["tmux", "capture-pane", "-t", f"{host}:{agent}", "-p"])
        buf = r.stdout if r.returncode == 0 else ""
        emoji, brief = _parse_state(buf)
        rows.append((agent, emoji, brief))
        text_lines.append(f"  {emoji} {agent:<10} {brief}")
        tally[emoji] += 1
    if rows:
        sections.append((f"本机 {host}", rows))

    for cname in _containers():
        short = cname.replace("claudeteam-", "").replace("-team-1", "")
        wmap = _container_window_map(cname)
        rows = []
        text_lines.append(f"\n[容器 {short}]")
        for agent in AGENT_WINDOWS:
            target = wmap.get(agent)
            if not target:
                continue
            r = _run(["sudo", "-n", "docker", "exec", cname, "tmux",
                      "capture-pane", "-t", target, "-p"])
            buf = r.stdout if r.returncode == 0 else ""
            emoji, brief = _parse_state(buf)
            rows.append((agent, emoji, brief))
            text_lines.append(f"  {emoji} {agent:<10} {brief}")
            tally[emoji] += 1
        if rows:
            sections.append((f"容器 {short}", rows))

    total = sum(tally.values())
    summary = " / ".join(f"{k} {v}" for k, v in tally.items() if v)
    text_lines.append(f"\n汇总：{total} agents · {summary}")

    return {
        "text": "\n".join(text_lines),
        "card": _build_team_card(sections, tally, now),
    }


# ── /health ────────────────────────────────────────────────────
_SIZE_UNIT = {
    "": 1, "K": 1024, "M": 1024**2, "G": 1024**3, "T": 1024**4,
    "KI": 1024, "MI": 1024**2, "GI": 1024**3, "TI": 1024**4,
}


def _parse_size(s: str) -> int:
    m = re.match(r"([\d.]+)\s*([KMGT]i?)?B?\s*", s or "")
    if not m:
        return 0
    return int(float(m.group(1)) * _SIZE_UNIT.get((m.group(2) or "").upper(), 1))


def _fmt_mem(b: int) -> str:
    if b >= 1024**3:
        return f"{b/1024**3:.2f} GB"
    if b >= 1024**2:
        return f"{b/1024**2:.0f} MB"
    if b >= 1024:
        return f"{b/1024:.0f} KB"
    return f"{b} B"


def _load_color(pct: int) -> str:
    if pct >= 80:
        return "red"
    if pct >= 50:
        return "orange"
    return "green"


def _host_cpu():
    r = _run(["uptime"])
    if r.returncode != 0:
        return None
    m = re.search(r"load average:\s*([\d.]+),\s*([\d.]+),\s*([\d.]+)", r.stdout)
    if not m:
        return None
    l1, l5, l15 = (float(m.group(i)) for i in (1, 2, 3))
    n = _run(["nproc"])
    try:
        ncores = int((n.stdout or "").strip() or "1")
    except ValueError:
        ncores = 1
    pct = int(round(l1 / max(ncores, 1) * 100))
    return {"load": (l1, l5, l15), "cores": ncores, "pct": pct}


def _host_mem():
    r = _run(["free", "-b"])
    if r.returncode != 0:
        return None
    mem = swap = None
    for line in r.stdout.splitlines():
        parts = line.split()
        if not parts:
            continue
        if parts[0] == "Mem:" and len(parts) >= 7:
            mem = {"total": int(parts[1]), "used": int(parts[2]),
                   "available": int(parts[6])}
        elif parts[0] == "Swap:" and len(parts) >= 3:
            swap = {"total": int(parts[1]), "used": int(parts[2])}
    if not mem:
        return None
    mem["pct"] = int(round(mem["used"] / max(mem["total"], 1) * 100))
    mem["swap"] = swap or {"total": 0, "used": 0}
    return mem


def _host_disk():
    r = _run(["df", "-B1", "-x", "tmpfs", "-x", "devtmpfs", "-x", "overlay"])
    if r.returncode != 0:
        return None
    worst = None
    for line in r.stdout.splitlines()[1:]:
        parts = line.split()
        if len(parts) < 6:
            continue
        try:
            total = int(parts[1])
            used = int(parts[2])
            pct = int(parts[4].rstrip("%"))
        except ValueError:
            continue
        mount = parts[5]
        if worst is None or pct > worst["pct"]:
            worst = {"mount": mount, "used": used, "total": total, "pct": pct}
    return worst


def _docker_stats():
    r = _run(["sudo", "-n", "docker", "stats", "--no-stream",
              "--format", "{{json .}}"], timeout=15)
    if r.returncode != 0:
        return []
    status_r = _run(["sudo", "-n", "docker", "ps",
                     "--format", "{{.Names}}\t{{.Status}}"])
    status_map = {}
    for line in status_r.stdout.splitlines():
        name, _, status = line.partition("\t")
        if name.startswith("claudeteam-"):
            status_map[name] = status
    out = []
    for line in r.stdout.splitlines():
        try:
            d = json.loads(line)
        except json.JSONDecodeError:
            continue
        name = d.get("Name", "")
        if not name.startswith("claudeteam-"):
            continue
        try:
            cpu = float(d.get("CPUPerc", "0").rstrip("%"))
            mem_pct = float(d.get("MemPerc", "0").rstrip("%"))
            mu = d.get("MemUsage", "")
            used_str = mu.split("/")[0].strip() if "/" in mu else mu.strip()
            mem_used = _parse_size(used_str)
        except Exception:
            cpu = 0.0
            mem_pct = 0.0
            mem_used = 0
        out.append({
            "name": name,
            "short": name.replace("claudeteam-", "").replace("-team-1", ""),
            "cpu_pct": cpu,
            "mem_pct": mem_pct,
            "mem_used": mem_used,
            "status": status_map.get(name, ""),
        })
    return out


def _parse_ps_tree(text: str):
    procs = {}
    children = defaultdict(list)
    for line in (text or "").splitlines()[1:]:
        parts = line.split(None, 3)
        if len(parts) < 4:
            continue
        try:
            pid = int(parts[0]); ppid = int(parts[1])
            pcpu = float(parts[2]); rss_kb = int(parts[3])
        except ValueError:
            continue
        procs[pid] = (ppid, pcpu, rss_kb)
        children[ppid].append(pid)
    return procs, children


def _subtree_usage(root_pid: int, procs: dict, children: dict):
    cpu = 0.0
    rss_kb = 0
    seen = set()
    stack = [root_pid]
    while stack:
        pid = stack.pop()
        if pid in seen or pid not in procs:
            continue
        seen.add(pid)
        _, c, r = procs[pid]
        cpu += c
        rss_kb += r
        stack.extend(children.get(pid, []))
    return cpu, rss_kb * 1024


def _collect_panes_local(session_filter: str | None = None):
    r = _run(["tmux", "list-panes", "-a",
              "-F", "#{session_name}:#{window_name} #{pane_pid}"])
    if r.returncode != 0:
        return {}
    panes = {}
    for line in r.stdout.splitlines():
        parts = line.split()
        if len(parts) < 2:
            continue
        sess, _, win = parts[0].partition(":")
        if win in AGENT_SET and win not in panes:
            try:
                panes[win] = int(parts[1])
            except ValueError:
                continue
    return panes


def _host_agent_usage():
    panes = _collect_panes_local()
    if not panes:
        return []
    ps = _run(["ps", "-eo", "pid,ppid,pcpu,rss"])
    procs, children = _parse_ps_tree(ps.stdout)
    host = _host_session()
    return [{"agent": a, "location": host,
             "cpu": (u := _subtree_usage(pid, procs, children))[0],
             "mem": u[1]}
            for a, pid in panes.items()]


def _container_agent_usage(cname: str, short: str):
    r = _run(["sudo", "-n", "docker", "exec", cname, "tmux",
              "list-panes", "-a",
              "-F", "#{session_name}:#{window_name} #{pane_pid}"])
    if r.returncode != 0:
        return []
    panes = {}
    for line in r.stdout.splitlines():
        parts = line.split()
        if len(parts) < 2:
            continue
        sess, _, win = parts[0].partition(":")
        if win in AGENT_SET and win not in panes:
            try:
                panes[win] = int(parts[1])
            except ValueError:
                continue
    if not panes:
        return []
    ps = _run(["sudo", "-n", "docker", "exec", cname,
               "ps", "-eo", "pid,ppid,pcpu,rss"])
    procs, children = _parse_ps_tree(ps.stdout)
    return [{"agent": a, "location": short,
             "cpu": (u := _subtree_usage(pid, procs, children))[0],
             "mem": u[1]}
            for a, pid in panes.items()]


def _collect_agents(containers: list):
    agents = _host_agent_usage()
    for c in containers:
        agents.extend(_container_agent_usage(c["name"], c["short"]))
    agents.sort(key=lambda a: a["cpu"], reverse=True)
    return agents


def _collect_alarms(host_mem, host_disk, containers):
    a = []
    if host_mem and host_mem["pct"] >= 90:
        a.append(f"主机内存 **{host_mem['pct']}%**（used {_fmt_mem(host_mem['used'])}）")
    if host_disk and host_disk["pct"] >= 80:
        a.append(f"磁盘 `{host_disk['mount']}` **{host_disk['pct']}%**")
    for c in containers:
        if c["mem_pct"] >= 90:
            a.append(f"容器 `{c['short']}` 内存 **{c['mem_pct']:.1f}%**")
        if c["cpu_pct"] >= 80:
            a.append(f"容器 `{c['short']}` CPU **{c['cpu_pct']:.1f}%**")
    dm = _run(["dmesg", "-T"], timeout=3)
    if dm.returncode == 0 and dm.stdout:
        oom = [l for l in dm.stdout.splitlines()[-500:]
               if re.search(r"out of memory|killed process", l, re.I)]
        if oom:
            a.append(f"内核 OOM/killed 记录 {len(oom)} 条（tail 3）：\n  " +
                     "\n  ".join(oom[-3:]))
    return a


def _collect_server_load():
    cpu = _host_cpu()
    mem = _host_mem()
    disk = _host_disk()
    containers = _docker_stats()
    agents = _collect_agents(containers)
    alarms = _collect_alarms(mem, disk, containers)
    return {"host": {"cpu": cpu, "mem": mem, "disk": disk},
            "containers": containers, "agents": agents, "alarms": alarms}


def _hostname() -> str:
    r = _run(["hostname"])
    return (r.stdout or "localhost").strip()


def _emoji_for_agent_cpu(cpu: float) -> str:
    if cpu >= 80:
        return "🔥"
    if cpu >= 30:
        return "🔄"
    if cpu >= 5:
        return "⚙️"
    return "💤"


def _col_cell(content: str) -> dict:
    return {"tag": "column", "width": "weighted", "weight": 1,
            "elements": [{"tag": "markdown", "content": content}]}


def _col_set_3(cells: list) -> dict:
    cols = [_col_cell(c) for c in cells]
    while len(cols) < 3:
        cols.append(_col_cell(" "))
    return {"tag": "column_set", "flex_mode": "none",
            "background_style": "default", "columns": cols}


def _build_server_load_card(data: dict, now: str) -> dict:
    host = data["host"]
    cpu = host["cpu"]; mem = host["mem"]; disk = host["disk"]
    containers = data["containers"]
    agents = data["agents"]
    alarms = data["alarms"]

    elements = []

    cpu_cell = ("**CPU**\n<font color='grey'>无数据</font>" if not cpu else
                f"**CPU**\n"
                f"<font color='{_load_color(cpu['pct'])}'>"
                f"**{cpu['load'][0]:.2f} / {cpu['cores']} 核 ({cpu['pct']}%)**</font>\n"
                f"<font color='grey'>5m {cpu['load'][1]:.2f} · 15m {cpu['load'][2]:.2f}</font>")
    mem_cell = ("**内存**\n<font color='grey'>无数据</font>" if not mem else
                f"**内存**\n"
                f"<font color='{_load_color(mem['pct'])}'>"
                f"**{_fmt_mem(mem['used'])} / {_fmt_mem(mem['total'])} ({mem['pct']}%)**</font>\n"
                f"<font color='grey'>可用 {_fmt_mem(mem['available'])} · "
                f"Swap {_fmt_mem(mem['swap']['used'])}/{_fmt_mem(mem['swap']['total'])}</font>")
    disk_cell = ("**磁盘**\n<font color='grey'>无数据</font>" if not disk else
                 f"**磁盘** `{disk['mount']}`\n"
                 f"<font color='{_load_color(disk['pct'])}'>"
                 f"**{_fmt_mem(disk['used'])} / {_fmt_mem(disk['total'])} ({disk['pct']}%)**</font>")
    elements.append({"tag": "div", "text": {"tag": "lark_md", "content": "**🖥️ 主机总览**"}})
    elements.append(_col_set_3([cpu_cell, mem_cell, disk_cell]))
    elements.append({"tag": "hr"})

    if containers:
        running = sum(1 for c in containers if c["status"])
        total_cpu = sum(c["cpu_pct"] for c in containers)
        total_mem = sum(c["mem_used"] for c in containers)
        peak = max(containers, key=lambda c: c["mem_pct"])
        name_preview = " · ".join(c["short"] for c in containers[:3])
        if len(containers) > 3:
            name_preview += " …"
        count_cell = (f"**容器数**\n**{running} / {len(containers)}** 运行中\n"
                      f"<font color='grey'>{name_preview}</font>")
        cpu_sum_cell = (f"**容器 CPU 合计**\n"
                        f"<font color='{_load_color(int(total_cpu))}'>"
                        f"**{total_cpu:.1f}%**</font>\n"
                        f"<font color='grey'>跨 {len(containers)} 容器加总</font>")
        mem_sum_cell = (f"**容器内存合计**\n**{_fmt_mem(total_mem)}**\n"
                        f"<font color='{_load_color(int(peak['mem_pct']))}'>"
                        f"峰值 `{peak['short']}` {peak['mem_pct']:.1f}%</font>")
        elements.append({"tag": "div", "text": {"tag": "lark_md",
                                                "content": "**📦 团队容器总量**"}})
        elements.append(_col_set_3([count_cell, cpu_sum_cell, mem_sum_cell]))
        elements.append({"tag": "hr"})

    if agents:
        total = len(agents)
        topn = agents[:9]
        elements.append({"tag": "div", "text": {"tag": "lark_md",
            "content": f"**👤 员工细分 Top {len(topn)} / 共 {total}**（按 CPU 降序）"}})
        for i in range(0, len(topn), 3):
            row = topn[i:i + 3]
            cells = []
            for a in row:
                cells.append(
                    f"{_emoji_for_agent_cpu(a['cpu'])} **{a['agent']}**\n"
                    f"CPU `{a['cpu']:.1f}%` · Mem `{_fmt_mem(a['mem'])}`\n"
                    f"<font color='grey'>{a['location']}</font>"
                )
            elements.append(_col_set_3(cells))
        if total > 9:
            elements.append({"tag": "div", "text": {"tag": "lark_md",
                "content": f"<font color='grey'>… 另有 {total - 9} 个员工未显示</font>"}})
        elements.append({"tag": "hr"})

    if alarms:
        body = "\n".join(f"- <font color='red'>⚠️ {a}</font>" for a in alarms)
        elements.append({"tag": "div", "text": {"tag": "lark_md",
            "content": f"**🚨 异常告警**\n{body}"}})
        elements.append({"tag": "hr"})

    elements.append({"tag": "note", "elements": [{"tag": "plain_text",
        "content": f"采集 {now} 北京时间 · 数据源 uptime/free/df/docker stats/ps"}]})

    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "template": "purple",
            "title": {"tag": "plain_text",
                      "content": f"🖥️ 服务器负载 · {_hostname()} · {now}"},
        },
        "elements": elements,
    }


def _build_server_load_text(data: dict, now: str) -> str:
    lines = [f"🖥️ /health — {_hostname()} ({now} 北京时间)\n"]
    host = data["host"]
    if host["cpu"]:
        c = host["cpu"]
        lines.append(f"CPU: load {c['load'][0]:.2f}/{c['cores']} 核 "
                     f"({c['pct']}%) · 5m {c['load'][1]:.2f} · 15m {c['load'][2]:.2f}")
    if host["mem"]:
        m = host["mem"]
        lines.append(f"内存: {_fmt_mem(m['used'])}/{_fmt_mem(m['total'])} "
                     f"({m['pct']}%) · 可用 {_fmt_mem(m['available'])}")
    if host["disk"]:
        d = host["disk"]
        lines.append(f"磁盘 {d['mount']}: {_fmt_mem(d['used'])}/{_fmt_mem(d['total'])} "
                     f"({d['pct']}%)")
    if data["containers"]:
        c_cnt = len(data["containers"])
        c_cpu = sum(c["cpu_pct"] for c in data["containers"])
        c_mem = sum(c["mem_used"] for c in data["containers"])
        lines.append(f"\n容器 {c_cnt}: CPU 合 {c_cpu:.1f}% · 内存合 {_fmt_mem(c_mem)}")
    if data["agents"]:
        lines.append(f"\n员工 Top 9 / 共 {len(data['agents'])}:")
        for a in data["agents"][:9]:
            lines.append(f"  {a['agent']:<10} CPU {a['cpu']:5.1f}% · "
                         f"Mem {_fmt_mem(a['mem']):>9} · {a['location']}")
    if data["alarms"]:
        lines.append("\n⚠️ 告警:")
        for al in data["alarms"]:
            lines.append(f"  - {al}")
    return "\n".join(lines)


def _cmd_health(text: str):
    if not re.fullmatch(r"/health\s*", text):
        return None
    data = _collect_server_load()
    now = datetime.now(BJ_TZ).strftime("%Y-%m-%d %H:%M")
    return {"text": _build_server_load_text(data, now),
            "card": _build_server_load_card(data, now)}


# ── /stop <agent> ──────────────────────────────────────────────
def _ctrlc_local(session: str, agent: str) -> bool:
    return _run(["tmux", "send-keys", "-t", f"{session}:{agent}",
                 "C-c"]).returncode == 0


def _ctrlc_container(cname: str, target: str) -> bool:
    return _run(["sudo", "-n", "docker", "exec", cname, "tmux",
                 "send-keys", "-t", target, "C-c"]).returncode == 0


def _cmd_stop(text: str):
    if re.fullmatch(r"/stop\s*", text):
        return "用法: /stop <agent>\n例: /stop devops（给 devops 发 Ctrl+C 中断当前动作）"
    m = re.match(r"^/stop\s+(\S+)\s*$", text)
    if not m:
        return None
    agent = m.group(1).strip()
    if not re.fullmatch(r"[A-Za-z0-9_-]+", agent):
        return f"⚠️ 非法 agent 名：`{agent}`"
    if agent not in AGENT_SET:
        return f"⚠️ 未知 agent：`{agent}`\n白名单：{sorted(AGENT_SET)}"

    session = _host_session()
    if agent in _list_windows_local(session):
        ok = _ctrlc_local(session, agent)
        return f"{'✅' if ok else '❌'} /stop → {session}:{agent} (本机) · C-c 已送"
    for cname in _containers():
        target = _container_window_map(cname).get(agent)
        if target:
            ok = _ctrlc_container(cname, target)
            return f"{'✅' if ok else '❌'} /stop → {cname} {target} · C-c 已送"
    return f"⚠️ 未找到 agent `{agent}` 的 tmux 窗口"


# ── /clear <agent> ─────────────────────────────────────────────
# 对齐 hire_agent.py:268-275 的入职 init_msg
def _init_msg(agent: str) -> str:
    return (
        f"你是团队的 {agent}。\n\n"
        f"【必读】请读取：agents/{agent}/identity.md — 了解你的角色和通讯规范\n"
        f"【然后立即执行】\n"
        f"1. python3 scripts/feishu_msg.py inbox {agent}    # 查看收件箱\n"
        f"2. python3 scripts/feishu_msg.py status {agent} 进行中 \"初始化完成，待命中\"\n\n"
        f"准备好后，简短汇报：你是谁、当前状态、有无未读消息。"
    )


def _clear_local(session: str, agent: str) -> bool:
    if not _send_local(session, agent, "/clear"):
        return False
    time.sleep(2)
    try:
        import sys as _sys
        scripts_dir = str(Path(__file__).resolve().parent)
        if scripts_dir not in _sys.path:
            _sys.path.insert(0, scripts_dir)
        from tmux_utils import inject_when_idle
        return inject_when_idle(session, agent, _init_msg(agent), wait_secs=15)
    except Exception:
        # 降级：直接 send-keys -l 字面模式 + Enter
        t = f"{session}:{agent}"
        if _run(["tmux", "send-keys", "-l", "-t", t,
                 _init_msg(agent)]).returncode != 0:
            return False
        time.sleep(0.5)
        return _run(["tmux", "send-keys", "-t", t, "Enter"]).returncode == 0


def _clear_container(cname: str, target: str, agent: str) -> bool:
    if not _send_container(cname, target, "/clear"):
        return False
    time.sleep(2)
    # 容器里没法跨容器 import tmux_utils；用 send-keys -l 字面模式直发
    msg = _init_msg(agent)
    if _run(["sudo", "-n", "docker", "exec", cname, "tmux",
             "send-keys", "-l", "-t", target, msg]).returncode != 0:
        return False
    time.sleep(0.5)
    return _run(["sudo", "-n", "docker", "exec", cname, "tmux",
                 "send-keys", "-t", target, "Enter"]).returncode == 0


def _cmd_clear(text: str):
    if re.fullmatch(r"/clear\s*", text):
        # 无参：提示用法；hook 端不拦无参（Claude Code 内置 /clear 给 manager 自己用）
        return ("用法: /clear <agent>\n"
                "例: /clear devops（先送 /clear 清上下文，再送 hire_agent init_msg 重新入职）\n"
                "⚠️ 会丢 agent 当前会话记忆，谨慎用")
    m = re.match(r"^/clear\s+(\S+)\s*$", text)
    if not m:
        return None
    agent = m.group(1).strip()
    if not re.fullmatch(r"[A-Za-z0-9_-]+", agent):
        return f"⚠️ 非法 agent 名：`{agent}`"
    if agent not in AGENT_SET:
        return f"⚠️ 未知 agent：`{agent}`\n白名单：{sorted(AGENT_SET)}"

    session = _host_session()
    if agent in _list_windows_local(session):
        ok = _clear_local(session, agent)
        return (f"{'✅' if ok else '❌'} /clear → {session}:{agent} (本机)\n"
                f"· 已送 /clear + 重新入职 init_msg")
    for cname in _containers():
        target = _container_window_map(cname).get(agent)
        if target:
            ok = _clear_container(cname, target, agent)
            return (f"{'✅' if ok else '❌'} /clear → {cname} {target}\n"
                    f"· 已送 /clear + 重新入职 init_msg")
    return f"⚠️ 未找到 agent `{agent}` 的 tmux 窗口"


# ── dispatch ───────────────────────────────────────────────────
_HANDLERS = [
    _cmd_help, _cmd_team, _cmd_usage, _cmd_health, _cmd_tmux,
    _cmd_send, _cmd_compact, _cmd_stop, _cmd_clear,
]


def dispatch(text: str):
    """返回 (matched, reply)。matched=True 时 reply 可能是：
      - str: 纯文本回显
      - dict {"text": str, "card": dict}: 带卡片的回显，text 用于不支持卡片的渠道。
    """
    if not text:
        return (False, None)
    stripped = text.strip()
    if not stripped.startswith("/"):
        return (False, None)
    for h in _HANDLERS:
        try:
            r = h(stripped)
        except Exception as e:
            return (True, f"⚠️ slash command 执行异常：{e}")
        if r is not None:
            return (True, r)
    return (False, None)
