"""Handler and provider queries for /usage slash command."""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable
from urllib import request as urllib_request

from .context import BJ_TZ, SlashContext
from .tmux_ import container_window_map, containers, list_windows_local
from claudeteam.messaging.renderer import render_feishu_markdown

try:
    from claudeteam.runtime.cli_credentials import (
        STATUS_API_FAILED,
        STATUS_AUTH_EXPIRED,
        STATUS_CONTAINER_LOGIN_REQUIRED,
        STATUS_OK,
        classify_failure,
        inspect_cli,
        status_detail,
        status_label,
    )
except Exception:  # pragma: no cover - import fallback for isolated tests
    STATUS_API_FAILED = "api_failed"
    STATUS_AUTH_EXPIRED = "auth_expired"
    STATUS_CONTAINER_LOGIN_REQUIRED = "container_login_required"
    STATUS_OK = "ok"

    def classify_failure(_output: str) -> str:
        return STATUS_API_FAILED

    def inspect_cli(_name: str, respect_enabled: bool = False) -> dict:
        return {"status": STATUS_OK}

    def status_detail(_row: dict) -> str:
        return "ok"

    def status_label(status: str) -> str:
        return status

PROJECT_ROOT = Path(os.environ.get("CLAUDE_PROJECT_DIR") or Path(__file__).resolve().parents[4])

_USAGE_LINE_RE = re.compile(
    r"^\s*(?P<label>[^:]+?)\s*:\s*(?P<pct>[\d.]+)%\s+"
    r"(?:\(重置:\s*(?P<reset>.+?)\)|resets\s+(?P<reset_en>.+?))\s*$")
_USAGE_EXTRA_RE = re.compile(
    r"^\s*(?P<label>Extra usage|额外用量)\s*:\s*\$?(?P<used>[\d.]+)\s*/\s*\$?(?P<cap>[\d.]+)\s+"
    r"\((?P<pct>[\d.]+)%\)\s*(?:\[(?P<ccy>\S+)\])?\s*$")
_KIMI_USAGE_RE = re.compile(
    r"(?P<label>[\w ]+?)\s+[━─╸╺╾╼╴╶╌╍]+\s+(?P<left>[\d.]+)%\s+"
    r"(?:left|remaining)\s+\(resets?\s+in\s+(?P<reset>.+?)\)",
    re.I,
)
_KIMI_USAGE_BLOCK_RE = re.compile(
    r"(?P<label>(?:Weekly|Daily|Monthly|5h|5-hour|Hourly)[^\n:]*?(?:limit|usage)[^\n]*?)"
    r"[\s━─╸╺╾╼╴╶╌╍]+(?P<left>[\d.]+)%\s*(?:left|remaining)"
    r".{0,160}?\(?resets?\s+in\s+(?P<reset>[0-9dhm\s]+)\)?",
    re.I | re.S,
)
_KIMI_BUSY_MARKERS = ("⣾", "⣽", "⣻", "⢿", "⡿", "⣟", "⣯", "⣷", "Thinking", "esc to interrupt", "queued", "Queued", "queued input", "Queued input")
_CODEX_USAGE_RE = re.compile(r"(?P<label>[\w ()]+?)\s+(?P<pct>\d+)%\s+resets\s+(?P<reset>\S+)")
_GEMINI_USAGE_RE = re.compile(r"(?P<model>gemini[\w.-]+)\s+(?P<pct>[\d.]+)%\s+used\s+resets\s+(?P<reset>\S+)")

_CLI_HEADINGS = {
    "cc": "Claude Code (Max $100/mo)",
    "kimi": "Kimi ($19/mo Subscription)",
    "codex": "Codex (ChatGPT Plus/Pro)",
    "gemini": "Gemini (Google AI)",
}


def _run(cmd, timeout=5, env=None):
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env=env)
    except Exception as e:
        class R:
            returncode = -1
            stdout = ""
            stderr = str(e)
        return R()


def _pct_color(p: int) -> str:
    if p >= 80:
        return "red"
    if p >= 50:
        return "orange"
    return "green"


def _remaining_pct_color(p: float) -> str:
    if p <= 20:
        return "red"
    if p <= 50:
        return "orange"
    return "green"


def _fmt_pct(value: float) -> str:
    if float(value).is_integer():
        return str(int(value))
    return f"{value:.1f}".rstrip("0").rstrip(".")


def _iso_to_bj(value: str) -> str:
    if not value:
        return "未知"
    try:
        dt = datetime.fromisoformat(value.split(".")[0].replace("Z", "+00:00"))
        return dt.astimezone(BJ_TZ).strftime("%Y-%m-%d %H:%M 北京时间")
    except Exception:
        return value


def _relative_reset_to_bj(reset: str, now_fn: Callable[[], datetime] | None = None) -> str:
    total = timedelta()
    for value, unit in re.findall(r"(\d+)\s*([dhm])", reset):
        n = int(value)
        if unit == "d":
            total += timedelta(days=n)
        elif unit == "h":
            total += timedelta(hours=n)
        elif unit == "m":
            total += timedelta(minutes=n)
    if total == timedelta():
        return reset
    now = (now_fn or (lambda: datetime.now(BJ_TZ)))()
    return (now + total).strftime("%Y-%m-%d %H:%M 北京时间")


def parse_usage_lines(raw_lines: list[str]) -> list[dict]:
    items = []
    for line in raw_lines:
        m = _USAGE_LINE_RE.match(line)
        if m:
            items.append({"type": "quota", "label": m.group("label").strip(), "pct": float(m.group("pct")), "reset": (m.group("reset") or m.group("reset_en") or "").strip()})
            continue
        m2 = _USAGE_EXTRA_RE.match(line)
        if m2:
            items.append({"type": "extra", "label": m2.group("label").strip(), "used": float(m2.group("used")), "cap": float(m2.group("cap")), "pct": float(m2.group("pct")), "ccy": m2.group("ccy") or "USD"})
    return items


def _gemini_usage_env(run_which: Callable[[str], str | None] = shutil.which, home: Path | None = None) -> dict | None:
    if os.environ.get("GEMINI_OAUTH_CLIENT_ID") and os.environ.get("GEMINI_OAUTH_CLIENT_SECRET"):
        return None
    roots = []
    gemini_bin = run_which("gemini")
    if gemini_bin:
        resolved = Path(gemini_bin).resolve()
        roots.extend([resolved.parent.parent / "lib" / "node_modules", resolved.parent.parent / "lib"])
    roots.extend([Path("/usr/local/lib/node_modules"), Path("/usr/lib/node_modules"), (home or Path.home()) / ".nvm" / "versions" / "node"])
    id_re = re.compile(r"(?:var|const)\s+OAUTH_CLIENT_ID\s*=\s*[\"']([^\"']+)[\"']")
    secret_re = re.compile(r"(?:var|const)\s+OAUTH_CLIENT_SECRET\s*=\s*[\"']([^\"']+)[\"']")
    bundle_dirs = []
    for root in roots:
        if not root.exists():
            continue
        if root.name == "node":
            bundle_dirs.extend(root.glob("*/lib/node_modules/@google/gemini-cli/bundle"))
        else:
            bundle_dirs.append(root / "@google" / "gemini-cli" / "bundle")
    for bundle_dir in bundle_dirs:
        if not bundle_dir.is_dir():
            continue
        for bundle_path in sorted(bundle_dir.glob("chunk-*.js")):
            try:
                source = bundle_path.read_text()
            except OSError:
                continue
            if "code_assist/oauth2" not in source:
                continue
            client_id = id_re.search(source)
            client_secret = secret_re.search(source)
            if client_id and client_secret:
                env = os.environ.copy()
                env["GEMINI_OAUTH_CLIENT_ID"] = client_id.group(1)
                env["GEMINI_OAUTH_CLIENT_SECRET"] = client_secret.group(1)
                return env
    return None


def _run_usage_tool(tool: str, timeout=15, run_fn: Callable = _run, which_fn: Callable[[str], str | None] | None = None):
    which_fn = which_fn or shutil.which
    env = _gemini_usage_env(which_fn) if tool == "gemini-cli-usage" else None
    exe = which_fn(tool)
    if exe:
        return run_fn([exe], timeout=timeout, env=env)
    if which_fn("uvx"):
        return run_fn(["uvx", tool], timeout=timeout, env=env)
    return run_fn([tool], timeout=timeout, env=env)


def _team_agents_by_cli(cli_name: str, project_root: Path = PROJECT_ROOT) -> list:
    try:
        data = json.loads((project_root / "team.json").read_text())
        return [name for name, info in (data.get("agents") or {}).items() if info.get("cli") == cli_name]
    except Exception:
        return []


def _tmux_target(agent: str, session: str, agent_set: frozenset, run_fn: Callable = _run):
    if agent in list_windows_local(session, run_fn):
        return f"{session}:{agent}", False
    for cname in containers(run_fn):
        target = container_window_map(cname, agent_set, run_fn).get(agent)
        if target:
            return (cname, target), True
    return None, False


def _capture_tmux_target(tgt, is_ctr: bool, cmd_text: str, wait: int = 4, lines: int = 30,
                         run_fn: Callable = _run, sleep_fn: Callable = time.sleep) -> str:
    if is_ctr:
        cname, target = tgt
        pfx = ["sudo", "-n", "docker", "exec", cname]
        if cmd_text:
            run_fn(pfx + ["tmux", "send-keys", "-l", "-t", target, cmd_text], timeout=5)
            run_fn(pfx + ["tmux", "send-keys", "-t", target, "Enter", "C-m"], timeout=5)
            sleep_fn(wait)
        r = run_fn(pfx + ["tmux", "capture-pane", "-pt", target, "-S", f"-{lines}"], timeout=5)
    else:
        if cmd_text:
            run_fn(["tmux", "send-keys", "-l", "-t", tgt, cmd_text], timeout=3)
            run_fn(["tmux", "send-keys", "-t", tgt, "Enter", "C-m"], timeout=3)
            sleep_fn(wait)
        r = run_fn(["tmux", "capture-pane", "-pt", tgt, "-S", f"-{lines}"], timeout=5)
    return r.stdout if r.returncode == 0 else ""


def _kimi_tmux_candidates(session: str, agent_set: frozenset, run_fn: Callable = _run, project_root: Path = PROJECT_ROOT):
    seen = set()
    candidates = []
    for agent in _team_agents_by_cli("kimi-code", project_root):
        tgt, is_ctr = _tmux_target(agent, session, agent_set, run_fn)
        if tgt is not None:
            key = repr(tgt)
            seen.add(key)
            candidates.append((tgt, is_ctr, f"team agent {agent}"))
    r = run_fn(["tmux", "list-panes", "-a", "-F", "#{session_name}:#{window_name}.#{pane_index}\t#{pane_current_command}"])
    if r.returncode == 0:
        for line in r.stdout.splitlines():
            target, _, command = line.partition("\t")
            if Path(command.strip()).name.lower() in ("kimi", "kimi-cli"):
                target = target.strip()
                key = repr(target)
                if key not in seen:
                    seen.add(key)
                    candidates.append((target, False, f"tmux pane {target}"))
    return candidates


def _kimi_pane_is_busy(raw: str) -> bool:
    tail = "\n".join((raw or "").rstrip().splitlines()[-5:])
    return any(marker in tail for marker in _KIMI_BUSY_MARKERS)


def _add_kimi_metric(seen: dict, label: str, left: float, reset: str) -> None:
    used_pct = 100 - left
    reset_bj = _relative_reset_to_bj(reset)
    label = " ".join(label.strip().split())
    seen[label] = {"label": label, "pct": int(round(used_pct)), "display_pct": int(round(left)), "color": _remaining_pct_color(left), "detail": f"剩余 {_fmt_pct(left)}% · 已用 {_fmt_pct(used_pct)}% · 下次刷新 {reset_bj}"}


def _parse_kimi_usage(raw: str) -> list:
    seen = {}
    for line in raw.splitlines():
        m = _KIMI_USAGE_RE.search(line)
        if m:
            _add_kimi_metric(seen, m.group("label"), float(m.group("left")), m.group("reset"))
    for m in _KIMI_USAGE_BLOCK_RE.finditer(raw):
        _add_kimi_metric(seen, m.group("label"), float(m.group("left")), m.group("reset"))
    return list(seen.values())


def _clean_provider_output(output: str) -> str:
    text = output or ""
    low = text.lower()
    if "403" in low and "401" in low and "refresh" in low:
        return "HTTP 403 from usage API; HTTP 401 during refresh"
    cleaned = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("Traceback") or stripped.startswith('File "') or stripped.startswith("File '"):
            continue
        if re.match(r"^[A-Za-z_][\w.]*Error:", stripped) or "http" in stripped.lower() or "error" in stripped.lower() or "token" in stripped.lower():
            cleaned.append(stripped)
    return " ".join(cleaned) or "provider 未返回可展示错误摘要"


def _usage_status_metric(info: dict, status: str | None = None, extra: str = "") -> list:
    row = dict(info)
    if status:
        row["status"] = status
    detail = status_detail(row)
    if extra:
        clean = " ".join(_clean_provider_output(extra).split())[:140]
        if clean:
            detail = f"{detail}；provider 输出：{clean}"
    icon = {"disabled": "ℹ️", "tool_missing": "❌", "credential_missing": "🔐", "auth_expired": "🔐", "container_login_required": "🔐", "permission_denied": "⛔", "api_failed": "⚠️", "ok": "✅"}.get(row.get("status"), "⚠️")
    return [{"label": "Status", "pct": -1, "detail": f"{icon} {status_label(row.get('status'))} · {detail}"}]


def _usage_preflight(name: str, respect_enabled: bool, inspect_fn: Callable = inspect_cli) -> list | None:
    info = inspect_fn(name, respect_enabled=respect_enabled)
    if info["status"] != STATUS_OK:
        return _usage_status_metric(info)
    return None


def _query_kimi_usage_api(project_root: Path = PROJECT_ROOT, opener=urllib_request.urlopen) -> list | None:
    cred_candidates = [
        Path.home() / ".kimi" / "credentials" / "kimi-code.json",
        project_root / ".kimi-credentials" / "credentials" / "kimi-code.json",
    ]
    token = None
    for path in cred_candidates:
        try:
            token = json.loads(path.read_text()).get("access_token")
            if token:
                break
        except Exception:
            continue
    if not token:
        return None

    req = urllib_request.Request("https://api.kimi.com/coding/v1/usages", headers={"Authorization": f"Bearer {token}"})
    try:
        with opener(req, timeout=10) as resp:
            payload = json.loads(resp.read())
    except Exception:
        return None

    metrics = []
    usage = payload.get("usage", {})
    try:
        limit = int(usage.get("limit", 0))
        used = int(usage.get("used", 0))
        remaining = int(usage.get("remaining", 0))
    except (TypeError, ValueError):
        limit = used = remaining = 0
    if limit > 0:
        used_pct = round(used / limit * 100)
        remaining_pct = max(0, min(100, 100 - used_pct))
        metrics.append({
            "label": "Weekly limit",
            "pct": used_pct,
            "display_pct": remaining_pct,
            "color": _remaining_pct_color(remaining_pct),
            "detail": f"剩余 {remaining_pct}% ({remaining}/{limit}) · 已用 {used_pct}% ({used}/{limit}) · 下次刷新 {_iso_to_bj(usage.get('resetTime', ''))}",
        })

    for item in payload.get("limits", []):
        window = item.get("window", {})
        detail = item.get("detail", {})
        try:
            item_limit = int(detail.get("limit", 0))
            item_remaining = int(detail.get("remaining", 0))
        except (TypeError, ValueError):
            continue
        if item_limit <= 0:
            continue
        item_used = item_limit - item_remaining
        used_pct = round(item_used / item_limit * 100)
        remaining_pct = max(0, min(100, 100 - used_pct))
        duration = int(window.get("duration", 0) or 0)
        unit = window.get("timeUnit", "")
        if "MINUTE" in unit and duration >= 60 and duration % 60 == 0:
            label = f"{duration // 60}h limit"
        elif "MINUTE" in unit:
            label = f"{duration}m limit"
        else:
            label = f"{duration}s limit"
        metrics.append({
            "label": label,
            "pct": used_pct,
            "display_pct": remaining_pct,
            "color": _remaining_pct_color(remaining_pct),
            "detail": f"剩余 {remaining_pct}% ({item_remaining}/{item_limit}) · 已用 {used_pct}% ({item_used}/{item_limit}) · 下次刷新 {_iso_to_bj(detail.get('resetTime', ''))}",
        })
    return metrics or None


def _query_kimi_usage(session: str, agent_set: frozenset, run_fn: Callable = _run, sleep_fn: Callable = time.sleep,
                      inspect_fn: Callable = inspect_cli, project_root: Path = PROJECT_ROOT) -> list:
    api_metrics = _query_kimi_usage_api(project_root)
    if api_metrics:
        return api_metrics
    candidates = _kimi_tmux_candidates(session, agent_set, run_fn, project_root)
    if not candidates:
        info = inspect_fn("kimi", respect_enabled=False)
        return _usage_status_metric(info, STATUS_API_FAILED, "需要启动 kimi-code agent 后才能实时查询；当前 team.json 没有可用 cli=kimi-code pane，tmux 也没有 kimi/kimi-cli pane。非交互 kimi --quiet -p /usage 返回 Unknown slash command。")
    busy_sources = []
    not_ready_sources = []
    for tgt, is_ctr, source in candidates:
        before = _capture_tmux_target(tgt, is_ctr, "", wait=0, lines=18, run_fn=run_fn, sleep_fn=sleep_fn)
        if _kimi_pane_is_busy(before):
            busy_sources.append(source)
            continue
        raw = _capture_tmux_target(tgt, is_ctr, "/usage", wait=5, lines=50, run_fn=run_fn, sleep_fn=sleep_fn)
        metrics = _parse_kimi_usage(raw)
        if metrics:
            return metrics
        if _kimi_pane_is_busy(raw):
            busy_sources.append(source)
        else:
            not_ready_sources.append(source)
    info = inspect_fn("kimi", respect_enabled=False)
    if busy_sources and not not_ready_sources:
        detail = "⚠️ Kimi pane busy · " + "、".join(busy_sources[:4])
        if len(busy_sources) > 4:
            detail += f" 等 {len(busy_sources)} 个 pane"
        detail += " 正忙，已跳过注入 /usage，避免污染输入队列"
        return [{"label": "Status", "pct": -1, "detail": detail}]
    detail = "⚠️ Kimi capture not ready · "
    if not_ready_sources:
        detail += "、".join(not_ready_sources[:4])
        if len(not_ready_sources) > 4:
            detail += f" 等 {len(not_ready_sources)} 个 pane"
        detail += " 未返回可解析 API Usage card"
    if busy_sources:
        detail += "；忙碌 pane 已跳过: " + "、".join(busy_sources[:4])
    detail += "；请稍后重试或确认 kimi-code pane 已空闲"
    if info.get("status") != STATUS_OK:
        detail += f"；preflight: {status_label(info['status'])}"
    return [{"label": "Status", "pct": -1, "detail": detail}]


def _query_codex_usage(run_fn: Callable = _run, which_fn: Callable[[str], str | None] = shutil.which,
                       inspect_fn: Callable = inspect_cli) -> list:
    r = _run_usage_tool("codex-cli-usage", timeout=15, run_fn=run_fn, which_fn=which_fn)
    if r.returncode != 0:
        output = (r.stderr or "") + (r.stdout or "")
        return [{"label": "Codex", "pct": -1, "detail": _usage_status_metric(inspect_fn("codex", respect_enabled=False), classify_failure(output), output)[0]["detail"]}]
    metrics = []
    plan = ""
    for line in (r.stdout or "").splitlines():
        if line.strip().startswith("Plan:"):
            plan = line.split(":", 1)[1].strip()
        m = _CODEX_USAGE_RE.search(line)
        if m:
            pct = int(m.group("pct"))
            remaining = max(0, min(100, 100 - pct))
            metrics.append({"label": m.group("label").strip(), "pct": pct, "display_pct": remaining, "color": _remaining_pct_color(remaining), "detail": f"剩余 {remaining}% · 已用 {pct}% · 下次刷新 {_relative_reset_to_bj(m.group('reset'))}"})
    if plan:
        metrics.insert(0, {"label": "Plan", "pct": -1, "detail": f"✅ {plan}"})
    return metrics or [{"label": "Codex", "pct": -1, "detail": "无数据"}]


def _query_gemini_usage(run_fn: Callable = _run, which_fn: Callable[[str], str | None] = shutil.which,
                        inspect_fn: Callable = inspect_cli) -> list:
    r = _run_usage_tool("gemini-cli-usage", timeout=15, run_fn=run_fn, which_fn=which_fn)
    output = (r.stderr or "") + (r.stdout or "")
    if r.returncode != 0 and "OAuth access token expired" in output:
        run_fn(["gemini", "-p", "Return only OK"], timeout=60)
        r = _run_usage_tool("gemini-cli-usage", timeout=15, run_fn=run_fn, which_fn=which_fn)
    if r.returncode != 0:
        output = (r.stderr or "") + (r.stdout or "")
        return [{"label": "Gemini", "pct": -1, "detail": _usage_status_metric(inspect_fn("gemini", respect_enabled=False), classify_failure(output), output)[0]["detail"]}]
    metrics = []
    auth = ""
    for line in (r.stdout or "").splitlines():
        stripped = line.strip()
        if "Auth:" in stripped:
            auth = stripped.split(":", 1)[1].strip()
        if stripped.startswith("Quota"):
            detail = stripped[len("Quota"):].strip() or "无额度数据"
            if "OAuth access token expired" in detail:
                detail = "OAuth token 已过期，需要重新登录 Gemini CLI：运行 `gemini` 完成登录后重试；或设置 GEMINI_OAUTH_CLIENT_ID/GEMINI_OAUTH_CLIENT_SECRET"
            metrics.append({"label": "Quota", "pct": -1, "detail": f"⚠️ {detail}"})
        m = _GEMINI_USAGE_RE.search(line)
        if m:
            used = float(m.group("pct"))
            remaining = max(0, min(100, 100 - used))
            metrics.append({"label": m.group("model"), "pct": int(round(used)), "display_pct": int(round(remaining)), "color": _remaining_pct_color(remaining), "detail": f"剩余 {_fmt_pct(remaining)}% · 已用 {_fmt_pct(used)}% · 下次刷新 {_relative_reset_to_bj(m.group('reset'))}"})
    if auth:
        auth_prefix = "⚠️" if any(m["label"] == "Quota" for m in metrics) else "✅"
        auth_detail = f"{auth_prefix} {auth}"
        if auth_prefix == "⚠️":
            auth_detail += "（需要刷新登录态）"
        metrics.insert(0, {"label": "Auth", "pct": -1, "detail": auth_detail})
    return metrics or [{"label": "Gemini", "pct": -1, "detail": "无数据"}]


def _cc_usage_metrics(raw: str) -> list:
    metrics = []
    for line in raw.splitlines():
        m = _USAGE_EXTRA_RE.match(line)
        if m:
            ccy = m.group("ccy") or "USD"
            metrics.append({"label": m.group("label"), "pct": int(float(m.group("pct"))), "detail": f"${m.group('used')} / ${m.group('cap')} {ccy}"})
            continue
        m = _USAGE_LINE_RE.match(line)
        if m:
            reset_raw = m.group("reset") or m.group("reset_en") or ""
            try:
                from dateutil.parser import parse as dp
                reset = dp(reset_raw).astimezone(BJ_TZ).strftime("%m-%d %H:%M 北京时间")
            except Exception:
                try:
                    dt = datetime.fromisoformat(reset_raw.split('.')[0].replace('Z', '+00:00'))
                    reset = dt.astimezone(BJ_TZ).strftime("%m-%d %H:%M 北京时间")
                except Exception:
                    reset = reset_raw
            metrics.append({"label": m.group("label").strip(), "pct": int(float(m.group("pct"))), "detail": f"重置 {reset}"})
    return metrics


def _query_cc_usage(project_root: Path = PROJECT_ROOT, run_fn: Callable = _run, inspect_fn: Callable = inspect_cli) -> list:
    snapshot = project_root / "scripts" / "usage_snapshot.py"
    r = run_fn(["python3", str(snapshot)], timeout=30)
    if r.returncode != 0:
        output = (r.stderr or "") + (r.stdout or "")
        return _usage_status_metric(inspect_fn("cc", respect_enabled=False), classify_failure(output), output)
    return _cc_usage_metrics((r.stdout or "").rstrip())


def _query_cli(name: str, *, respect_enabled: bool = False, session: str = "server-manager", agent_set: frozenset = frozenset(),
               run_fn: Callable = _run, sleep_fn: Callable = time.sleep, which_fn: Callable[[str], str | None] = shutil.which,
               inspect_fn: Callable = inspect_cli, project_root: Path = PROJECT_ROOT) -> list:
    try:
        if name == "kimi":
            return _query_kimi_usage(session, agent_set, run_fn, sleep_fn, inspect_fn, project_root)
        if name == "codex":
            return _query_codex_usage(run_fn, which_fn, inspect_fn)
        if name == "gemini":
            return _query_gemini_usage(run_fn, which_fn, inspect_fn)
    except Exception as e:
        return _usage_status_metric(inspect_fn(name, respect_enabled=False), STATUS_API_FAILED, str(e))
    return []


def build_usage_card(sections: list, title_suffix: str) -> dict:
    rows = []
    for sec in sections:
        if sec.get("heading"):
            rows.append({"tag": "markdown", "content": f"**{render_feishu_markdown(sec['heading'])}**"})
        for met in sec.get("metrics", []):
            pct = met["pct"]
            label = render_feishu_markdown(met["label"])
            detail = render_feishu_markdown(met["detail"])
            if pct >= 0:
                display_pct = met.get("display_pct", pct)
                color = met.get("color", _pct_color(pct))
                right = f"<font color='{color}'>**{display_pct}%**</font> · {detail}"
            else:
                right = detail
            rows.append({"tag": "column_set", "flex_mode": "none", "background_style": "default", "columns": [
                {"tag": "column", "width": "weighted", "weight": 2, "elements": [{"tag": "markdown", "content": f"**{label}**"}]},
                {"tag": "column", "width": "weighted", "weight": 3, "elements": [{"tag": "markdown", "content": right}]},
            ]})
        rows.append({"tag": "hr"})
    if rows and rows[-1].get("tag") == "hr":
        rows.pop()
    return {"config": {"wide_screen_mode": True}, "header": {"template": "purple", "title": {"tag": "plain_text", "content": f"📊 /usage · {title_suffix}"}}, "elements": rows or [{"tag": "markdown", "content": "(无数据)"}]}


def usage_command(text: str, *, project_root: Path = PROJECT_ROOT, session: str = "server-manager", agent_set: frozenset = frozenset(),
                  run_fn: Callable = _run, sleep_fn: Callable = time.sleep, which_fn: Callable[[str], str | None] = shutil.which,
                  inspect_fn: Callable = inspect_cli, now_fn: Callable[[], datetime] | None = None) -> dict | str | None:
    m = re.fullmatch(r"/usage(?:\s+(\S+))?\s*", text)
    if not m:
        return None
    target = (m.group(1) or "").lower()
    now = (now_fn or (lambda: datetime.now(BJ_TZ)))().strftime("%Y-%m-%d %H:%M")
    if target in ("", "cc", "claude"):
        targets = ["cc"]
    elif target == "all":
        targets = ["cc", "kimi", "codex", "gemini"]
    elif target in ("kimi", "codex", "gemini"):
        targets = [target]
    else:
        return f"⚠️ 未知 CLI：{target}\n支持：/usage [cc|kimi|codex|gemini|all]"
    sections = []
    text_lines = []
    for provider in targets:
        heading = _CLI_HEADINGS.get(provider, provider)
        text_lines.append(f"[{heading}]")
        if provider == "cc":
            metrics = _query_cc_usage(project_root, run_fn, inspect_fn)
            sections.append({"heading": heading, "metrics": metrics})
            if metrics and all("detail" in m for m in metrics):
                for metric in metrics:
                    pct_s = f"{metric.get('display_pct', metric['pct'])}%" if metric["pct"] >= 0 else "—"
                    text_lines.append(f"  {metric['label']}: {pct_s}  {metric['detail']}")
        else:
            metrics = _query_cli(provider, respect_enabled=False, session=session, agent_set=agent_set, run_fn=run_fn, sleep_fn=sleep_fn, which_fn=which_fn, inspect_fn=inspect_fn, project_root=project_root)
            sections.append({"heading": heading, "metrics": metrics})
            for metric in metrics:
                pct_s = f"{metric.get('display_pct', metric['pct'])}%" if metric["pct"] >= 0 else "—"
                text_lines.append(f"  {metric['label']}: {pct_s}  {metric['detail']}")
    title = "All CLIs" if target == "all" else _CLI_HEADINGS.get(target or "cc", target)
    return {"text": "\n".join(text_lines), "card": build_usage_card(sections, f"{title} · {now}")}


def handle_usage(text: str, ctx: SlashContext) -> dict | None:
    if not re.fullmatch(r"/usage(?:\s+\S+)?\s*", text):
        return None
    if ctx.live_usage:
        command_text = "/usage all" if re.fullmatch(r"/usage\s*", text) else text
        return usage_command(command_text, project_root=ctx.project_root, session=ctx.tmux_session, agent_set=ctx.agent_set, now_fn=ctx.now_bj)
    now_str = ctx.now_bj().strftime("%Y-%m-%d %H:%M 北京时间")
    tools = ["claude-cli-usage", "codex-cli-usage", "gemini-cli-usage", "kimi-cli-usage"]
    sections = []
    for tool in tools:
        raw = ctx.query_usage(tool)
        if raw:
            metrics = []
            for item in parse_usage_lines(raw):
                if item["type"] == "quota":
                    metrics.append({"label": item["label"], "pct": int(item["pct"]), "detail": f"重置 {item.get('reset', '')}"})
                else:
                    metrics.append({"label": item["label"], "pct": int(item["pct"]), "detail": f"${item['used']} / ${item['cap']} {item['ccy']}"})
            sections.append({"heading": tool.replace("-cli-usage", "").capitalize(), "metrics": metrics})
    card = build_usage_card(sections, f"Claude 额度快照 @ {now_str}")
    text_lines = [f"📊 Claude 额度快照 @ {now_str}"]
    for sec in sections:
        for metric in sec.get("metrics", []):
            text_lines.append(f"  {sec['heading']} {metric['label']}: {metric['pct']}%")
    return {"text": "\n".join(text_lines), "card": card}
