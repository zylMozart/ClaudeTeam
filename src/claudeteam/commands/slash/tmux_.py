"""Handlers for /tmux, /send, /compact, /stop, /clear slash commands."""
from __future__ import annotations

import re
import subprocess
import threading
import time
from pathlib import Path
from typing import Callable

from .context import SlashContext

_TMUX_RE = re.compile(r"^/tmux(?:\s+([A-Za-z0-9_-]+))?(?:\s+(\d+))?\s*$")
MAX_LINES = 2000


def _run(cmd, timeout=5):
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except Exception as e:
        class R:
            returncode = -1
            stdout = ""
            stderr = str(e)
        return R()


def list_windows_local(session: str, run_fn: Callable = _run):
    r = run_fn(["tmux", "list-windows", "-t", session, "-F", "#{window_name}"])
    if r.returncode != 0:
        return []
    return [w.strip() for w in r.stdout.splitlines() if w.strip()]


def containers(run_fn: Callable = _run):
    r = run_fn(["sudo", "-n", "docker", "ps", "--format", "{{.Names}}"])
    if r.returncode != 0:
        return []
    return [n.strip() for n in r.stdout.splitlines() if n.strip().startswith("claudeteam-")]


def container_window_map(cname: str, agent_set: frozenset, run_fn: Callable = _run) -> dict:
    r = run_fn(["sudo", "-n", "docker", "exec", cname, "tmux",
                "list-windows", "-a", "-F", "#{session_name}:#{window_name}"])
    if r.returncode != 0:
        return {}
    out = {}
    for line in r.stdout.splitlines():
        sess, _, win = line.partition(":")
        if win in agent_set and win not in out:
            out[win] = f"{sess}:{win}"
    return out


def resolve_target(agent: str, session: str, agent_set: frozenset, run_fn: Callable = _run):
    if agent in list_windows_local(session, run_fn):
        return {"kind": "host", "session": session, "target": f"{session}:{agent}", "agent": agent}
    for cname in containers(run_fn):
        target = container_window_map(cname, agent_set, run_fn).get(agent)
        if target:
            return {"kind": "container", "container": cname, "target": target, "agent": agent}
    return None


def send_text_host(session: str, agent: str, msg: str, run_fn: Callable = _run, sleep_fn: Callable = time.sleep) -> bool:
    target = f"{session}:{agent}"
    if run_fn(["tmux", "send-keys", "-l", "-t", target, msg]).returncode != 0:
        return False
    sleep_fn(0.2)
    return run_fn(["tmux", "send-keys", "-t", target, "Enter", "C-m"]).returncode == 0


def send_text_container(cname: str, target: str, msg: str, run_fn: Callable = _run, sleep_fn: Callable = time.sleep) -> bool:
    if run_fn(["sudo", "-n", "docker", "exec", cname, "tmux", "send-keys", "-l", "-t", target, msg]).returncode != 0:
        return False
    sleep_fn(0.2)
    return run_fn(["sudo", "-n", "docker", "exec", cname, "tmux", "send-keys", "-t", target, "Enter", "C-m"]).returncode == 0


def send_ctrlc_host(session: str, agent: str, run_fn: Callable = _run) -> bool:
    return run_fn(["tmux", "send-keys", "-t", f"{session}:{agent}", "C-c"]).returncode == 0


def send_ctrlc_container(cname: str, target: str, run_fn: Callable = _run) -> bool:
    return run_fn(["sudo", "-n", "docker", "exec", cname, "tmux", "send-keys", "-t", target, "C-c"]).returncode == 0


def init_msg(agent: str) -> str:
    return (
        f"你是团队的 {agent}。\n\n"
        f"【必读】请读取：agents/{agent}/identity.md — 了解你的角色和通讯规范\n"
        f"【然后立即执行】\n"
        f"1. python3 scripts/feishu_msg.py inbox {agent}    # 查看收件箱\n"
        f"2. python3 scripts/feishu_msg.py status {agent} 进行中 \"初始化完成，待命中\"\n\n"
        f"准备好后，简短汇报：你是谁、当前状态、有无未读消息。"
    )


def clear_host(session: str, agent: str, run_fn: Callable = _run, sleep_fn: Callable = time.sleep) -> bool:
    if not send_text_host(session, agent, "/clear", run_fn, sleep_fn):
        return False
    sleep_fn(2)
    try:
        from claudeteam.runtime.tmux_utils import inject_when_idle
        return bool(inject_when_idle(session, agent, init_msg(agent), wait_secs=15))
    except Exception:
        target = f"{session}:{agent}"
        if run_fn(["tmux", "send-keys", "-l", "-t", target, init_msg(agent)]).returncode != 0:
            return False
        sleep_fn(0.5)
        return run_fn(["tmux", "send-keys", "-t", target, "Enter", "C-m"]).returncode == 0


def clear_container(cname: str, target: str, agent: str, run_fn: Callable = _run, sleep_fn: Callable = time.sleep) -> bool:
    if not send_text_container(cname, target, "/clear", run_fn, sleep_fn):
        return False
    sleep_fn(2)
    if run_fn(["sudo", "-n", "docker", "exec", cname, "tmux", "send-keys", "-l", "-t", target, init_msg(agent)]).returncode != 0:
        return False
    sleep_fn(0.5)
    return run_fn(["sudo", "-n", "docker", "exec", cname, "tmux", "send-keys", "-t", target, "Enter", "C-m"]).returncode == 0


def tmux_command(text: str, agents: list[str], session: str, agent_set: frozenset, run_fn: Callable = _run) -> str | None:
    m = _TMUX_RE.match(text)
    if not m:
        return None
    agent = m.group(1) or (agents[0] if agents else "manager")
    lines = int(m.group(2)) if m.group(2) else 10
    lines = max(1, min(lines, MAX_LINES))
    if agent not in agent_set:
        return f"⚠️ 未知 agent：`{agent}`"
    target = resolve_target(agent, session, agent_set, run_fn)
    if not target:
        return f"⚠️ 未找到 agent `{agent}` 的 tmux 窗口"
    if target["kind"] == "host":
        r = run_fn(["tmux", "capture-pane", "-t", target["target"], "-p", "-S", f"-{lines}"])
        label = target["target"]
    else:
        r = run_fn(["sudo", "-n", "docker", "exec", target["container"], "tmux", "capture-pane", "-t", target["target"], "-p", "-S", f"-{lines}"])
        label = f"{target['container']} {target['target']}"
    if r.returncode != 0:
        return f"⚠️ 读取 tmux `{label}` 失败：{(r.stderr or '').strip()}"
    body = r.stdout.rstrip() or "(窗口为空)"
    return f"=== {label} 最后 {lines} 行 ===\n{body}"


def send_command(text: str, agents: list[str], session: str, agent_set: frozenset,
                 run_fn: Callable = _run, sleep_fn: Callable = time.sleep) -> str | None:
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
    if agent not in agent_set:
        return f"⚠️ 未知 agent：`{agent}`\n白名单：{sorted(agent_set)}"
    target = resolve_target(agent, session, agent_set, run_fn)
    if not target:
        return f"⚠️ 未找到 agent `{agent}` 的 tmux 窗口"
    if target["kind"] == "host":
        ok = send_text_host(session, agent, msg, run_fn, sleep_fn)
        return f"{'✅' if ok else '❌'} /send → {session}:{agent} (本机)\n内容：{msg}"
    ok = send_text_container(target["container"], target["target"], msg, run_fn, sleep_fn)
    return f"{'✅' if ok else '❌'} /send → {target['container']} {target['target']}\n内容：{msg}"


def compact_command(text: str, agents: list[str], session: str, agent_set: frozenset,
                    run_fn: Callable = _run, sleep_fn: Callable = time.sleep) -> str | None:
    m = re.fullmatch(r"/compact(?:\s+(\S+))?\s*", text)
    if not m:
        return None
    agent = (m.group(1) or (agents[0] if agents else "manager")).strip()
    if not re.fullmatch(r"[A-Za-z0-9_-]+", agent):
        return f"⚠️ 非法 agent 名：`{agent}`"
    if agent not in agent_set:
        return f"⚠️ 未知 agent：`{agent}`"
    target = resolve_target(agent, session, agent_set, run_fn)
    if not target:
        return f"⚠️ 未找到 agent `{agent}` 的 tmux 窗口"
    if target["kind"] == "host":
        ok = send_text_host(session, agent, "/compact", run_fn, sleep_fn)
        return f"{'✅' if ok else '❌'} /compact → {session}:{agent} (本机)"
    ok = send_text_container(target["container"], target["target"], "/compact", run_fn, sleep_fn)
    return f"{'✅' if ok else '❌'} /compact → {target['container']} {target['target']}"


def stop_command(text: str, agents: list[str], session: str, agent_set: frozenset, run_fn: Callable = _run) -> str | None:
    if re.fullmatch(r"/stop\s*", text):
        return "用法: /stop <agent>\n例: /stop devops（给 devops 发 Ctrl+C 中断当前动作）"
    m = re.match(r"^/stop\s+(\S+)\s*$", text)
    if not m:
        return None
    agent = m.group(1).strip()
    if not re.fullmatch(r"[A-Za-z0-9_-]+", agent):
        return f"⚠️ 非法 agent 名：`{agent}`"
    if agent not in agent_set:
        return f"⚠️ 未知 agent：`{agent}`\n白名单：{sorted(agent_set)}"
    target = resolve_target(agent, session, agent_set, run_fn)
    if not target:
        return f"⚠️ 未找到 agent `{agent}` 的 tmux 窗口"
    if target["kind"] == "host":
        ok = send_ctrlc_host(session, agent, run_fn)
        return f"{'✅' if ok else '❌'} /stop → {session}:{agent} (本机) · C-c 已送"
    ok = send_ctrlc_container(target["container"], target["target"], run_fn)
    return f"{'✅' if ok else '❌'} /stop → {target['container']} {target['target']} · C-c 已送"


def clear_command(text: str, agents: list[str], session: str, agent_set: frozenset,
                  run_fn: Callable = _run, sleep_fn: Callable = time.sleep) -> str | None:
    if re.fullmatch(r"/clear\s*", text):
        return ("用法: /clear <agent>\n"
                "例: /clear devops（先送 /clear 清上下文，再送 hire_agent init_msg 重新入职）\n"
                "⚠️ 会丢 agent 当前会话记忆，谨慎用")
    m = re.match(r"^/clear\s+(\S+)\s*$", text)
    if not m:
        return None
    agent = m.group(1).strip()
    if not re.fullmatch(r"[A-Za-z0-9_-]+", agent):
        return f"⚠️ 非法 agent 名：`{agent}`"
    if agent not in agent_set:
        return f"⚠️ 未知 agent：`{agent}`\n白名单：{sorted(agent_set)}"
    target = resolve_target(agent, session, agent_set, run_fn)
    if not target:
        return f"⚠️ 未找到 agent `{agent}` 的 tmux 窗口"
    if target["kind"] == "host":
        ok = clear_host(session, agent, run_fn, sleep_fn)
        return f"{'✅' if ok else '❌'} /clear → {session}:{agent} (本机)\n· 已送 /clear + 重新入职 init_msg"
    ok = clear_container(target["container"], target["target"], agent, run_fn, sleep_fn)
    return f"{'✅' if ok else '❌'} /clear → {target['container']} {target['target']}\n· 已送 /clear + 重新入职 init_msg"


def handle_tmux(text: str, ctx: SlashContext) -> str | None:
    m = _TMUX_RE.match(text)
    if not m:
        return None
    agent = m.group(1) or (ctx.team_agents[0] if ctx.team_agents else "manager")
    lines = int(m.group(2)) if m.group(2) else 10
    lines = max(1, min(lines, MAX_LINES))
    if agent not in ctx.agent_set:
        return f"⚠️ 未知 agent：`{agent}`"
    body = ctx.capture_pane(agent).rstrip() or "(窗口为空)"
    return f"=== {ctx.tmux_session}:{agent} 最后 {lines} 行 ===\n{body}"


def handle_send(text: str, ctx: SlashContext) -> str | None:
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
    if agent not in ctx.agent_set:
        return f"⚠️ 未知 agent：`{agent}`\n白名单：{sorted(ctx.agent_set)}"
    ok = ctx.send_to_agent(ctx.tmux_session, agent, msg)
    return f"{'✅' if ok else '❌'} /send → {ctx.tmux_session}:{agent}\n内容：{msg}"


def _schedule_post_compact_reread(session: str, agent: str):
    """After compact completes (~20s), inject identity re-read instruction."""
    def _delayed():
        time.sleep(20)
        msg = (
            f"上下文刚被压缩。请立即重新读取 agents/{agent}/identity.md "
            f"和 agents/{agent}/core_memory.md（如果存在）重建身份认知，然后继续工作。"
        )
        _run(["tmux", "send-keys", "-t", f"{session}:{agent}", msg, "Enter"])
    threading.Thread(target=_delayed, daemon=True).start()


def handle_compact(text: str, ctx: SlashContext) -> str | None:
    m = re.fullmatch(r"/compact(?:\s+(\S+))?\s*", text)
    if not m:
        return None
    agent = (m.group(1) or (ctx.team_agents[0] if ctx.team_agents else "manager")).strip()
    if not re.fullmatch(r"[A-Za-z0-9_-]+", agent):
        return f"⚠️ 非法 agent 名：`{agent}`"
    if agent not in ctx.agent_set:
        return f"⚠️ 未知 agent：`{agent}`"
    ok = ctx.send_to_agent(ctx.tmux_session, agent, "/compact")
    if ok:
        _schedule_post_compact_reread(ctx.tmux_session, agent)
    return f"{'✅' if ok else '❌'} /compact → {ctx.tmux_session}:{agent}"


def handle_stop(text: str, ctx: SlashContext) -> str | None:
    m = re.fullmatch(r"/stop\s+(\S+)\s*", text)
    if not m:
        if re.fullmatch(r"/stop\s*", text):
            return "用法: /stop <agent>"
        return None
    agent = m.group(1)
    if agent not in ctx.agent_set:
        return f"⚠️ 未知 agent：`{agent}`"
    ok = ctx.send_to_agent(ctx.tmux_session, agent, "\x03")
    return f"{'✅' if ok else '❌'} C-c → {ctx.tmux_session}:{agent}"


def handle_clear(text: str, ctx: SlashContext) -> str | None:
    m = re.fullmatch(r"/clear\s+(\S+)\s*", text)
    if not m:
        if re.fullmatch(r"/clear\s*", text):
            return "用法: /clear <agent>"
        return None
    agent = m.group(1)
    if agent not in ctx.agent_set:
        return f"⚠️ 未知 agent：`{agent}`"
    ok = ctx.send_to_agent(ctx.tmux_session, agent, "/clear")
    return f"{'✅' if ok else '❌'} /clear → {ctx.tmux_session}:{agent}"
