"""`claudeteam usage` — token / credit consumption snapshot.

Each supported CLI has its own data source:
  - claude-code → `npx ccusage <view>` (community ccusage CLI;
    reads `~/.claude/projects` logs)
  - codex       → shell out to `codex-cli-usage` (Python tool,
    installed in container via `uv tool install`) for real % consumed
    per limit window (5h / Weekly / etc).
  - kimi-code   → `https://api.kimi.com/coding/v1/usages` with the
    bearer token from `~/.kimi/credentials/kimi-code.json`. Returns
    weekly + 5h sliding-window quotas.
  - other CLIs (codex-cli legacy alias, gemini, qwen) → no upstream
    tool; report "unsupported" and skip.

Pure shell-out / direct HTTP wrapper, no caching. With `--json`,
dump a machine-readable record so `slash._handle_usage` and
dashboards can ingest the same numbers without re-parsing.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any, Callable
from urllib import error as urllib_error
from urllib import request as urllib_request

from claudeteam.runtime import config
from claudeteam.util import (
    error_exit, maybe_print_help, pop_bool_flag, pop_flag, print_json,
    reject_extra_args,
)


USAGE = ("usage: claudeteam usage [--view daily|monthly|session|blocks] "
         "[--days N] [--json]")

# ccusage's documented views — validated against argv for clearer errors
_VIEWS = ("daily", "monthly", "session", "blocks")

_KIMI_USAGE_URL = "https://api.kimi.com/coding/v1/usages"

# Claude Max OAuth usage endpoint. Hits api.anthropic.com (bypasses
# Cloudflare on claude.ai) with the OAuth beta header. Returns
# JSON: five_hour / seven_day / seven_day_sonnet / seven_day_opus /
# extra_usage; each block has utilization (0-100) + resets_at (ISO).
_CC_USAGE_URL = "https://api.anthropic.com/api/oauth/usage"
_CC_USAGE_BETA = "oauth-2025-04-20"


def _read_claude_oauth(home: Path | None = None,
                       *, keychain_runner: Callable | None = None) -> dict:
    """Resolve the freshest Claude OAuth payload available.

    On macOS, the keychain (`security find-generic-password -s 'Claude
    Code-credentials'`) is the source of truth — claude refreshes there
    on every token rotation. The home file at `~/.claude/.credentials.json`
    only updates occasionally and is often hours behind, so a host that's
    "logged in" still saw `/usage` say "access token 已过期" because we
    keyed off the file's stale `expiresAt` (caught 2026-05-07 host smoke).

    Resolution order:
      1. macOS keychain (when `security` works) — always live.
      2. `~/.claude/.credentials.json` (fallback for Linux + macOS hosts
         where keychain access is denied, e.g. tests with subprocess
         stubs).

    Returns `{"ok": True, "oauth": {...}}` on success or
    `{"ok": False, "note": "..."}` with a user-facing reason."""
    import platform
    # Production path uses host keychain (`home is None`). Tests pass an
    # explicit home= to fence the call into a tmpdir; treat that as
    # opt-out of the host keychain too so a test machine's real OAuth
    # state can't bleed into assertions. Callers that genuinely want
    # to exercise the keychain path with home= can pass keychain_runner.
    if keychain_runner is None and home is None and platform.system() == "Darwin":
        def keychain_runner():
            import subprocess
            return subprocess.run(
                ["security", "find-generic-password",
                 "-s", "Claude Code-credentials", "-w"],
                capture_output=True, text=True, timeout=5,
            )
    if keychain_runner is not None:
        try:
            out = keychain_runner()
            if out is not None and getattr(out, "returncode", 1) == 0 \
                    and out.stdout.strip():
                return {"ok": True,
                        "oauth": json.loads(out.stdout)["claudeAiOauth"]}
        except (OSError, ValueError, KeyError):
            pass  # fall through to file path
    cred_path = (home or Path.home()) / ".claude" / ".credentials.json"
    try:
        return {"ok": True,
                "oauth": json.loads(cred_path.read_text())["claudeAiOauth"]}
    except FileNotFoundError:
        return {"ok": False,
                "note": f"{cred_path} 不存在；运行 claude /login"}
    except (OSError, ValueError, KeyError) as e:
        return {"ok": False, "note": f"读取 {cred_path} 失败: {e}"}


def _query_cc_usage(home: Path | None = None,
                    *, opener: Callable = None,
                    keychain_runner: Callable | None = None) -> dict:
    """Hit Claude Max's `/api/oauth/usage` for real per-window
    utilization (5h / 7d / Sonnet / Opus / Extra) — boss flagged the
    earlier `npx ccusage Total: $X` dump as just cumulative cost,
    NOT actual quota usage. Mirrors main's `scripts/usage_snapshot.py`.

    Returns `{ok, metrics: [{label, used_pct, remaining_pct, reset_iso,
    extra: {used,cap,ccy} optional}]}` on success or `{ok: false, note}`
    on failure. Token resolved via `_read_claude_oauth` (keychain
    preferred over the often-stale home file)."""
    if opener is None:
        opener = _opener_default
    resolved = _read_claude_oauth(home, keychain_runner=keychain_runner)
    if not resolved["ok"]:
        return resolved
    oauth = resolved["oauth"]
    token = oauth.get("accessToken", "")
    expires_ms = oauth.get("expiresAt", 0)
    import time as _time
    if expires_ms and expires_ms < int(_time.time() * 1000):
        return {"ok": False,
                "note": f"access token 已过期 ({_time.strftime('%Y-%m-%d %H:%M', _time.localtime(expires_ms/1000))})；"
                        f"运行 `claude` 触发刷新"}
    if not token:
        return {"ok": False, "note": "credentials 缺少 accessToken"}

    req = urllib_request.Request(_CC_USAGE_URL, headers={
        "Authorization": f"Bearer {token}",
        "anthropic-beta": _CC_USAGE_BETA,
        "Accept": "application/json",
    })
    try:
        with opener(req, timeout=15) as resp:
            data = json.loads(resp.read())
    except urllib_error.HTTPError as e:
        return {"ok": False, "note": f"Claude usage HTTP {e.code}: {e.reason}"}
    except (urllib_error.URLError, OSError, ValueError) as e:
        return {"ok": False, "note": f"Claude usage 请求失败: {e}"}

    metrics: list[dict] = []
    for label, key in (
        ("5-hour window", "five_hour"),
        ("7-day all models", "seven_day"),
        ("7-day Sonnet", "seven_day_sonnet"),
        ("7-day Opus", "seven_day_opus"),
    ):
        block = data.get(key) or {}
        util = block.get("utilization")
        if util is None:
            continue
        used_pct = int(round(float(util)))
        metrics.append({
            "label": label,
            "used_pct": used_pct,
            "remaining_pct": max(0, min(100, 100 - used_pct)),
            "reset_iso": block.get("resets_at", ""),
        })
    extra = data.get("extra_usage") or {}
    if extra.get("is_enabled"):
        used = float(extra.get("used_credits", 0) or 0)
        cap = float(extra.get("monthly_limit", 0) or 0)
        ccy = extra.get("currency", "USD")
        util = extra.get("utilization")
        used_pct = int(round(float(util))) if util is not None else 0
        metrics.append({
            "label": "Extra usage",
            "used_pct": used_pct,
            "remaining_pct": max(0, min(100, 100 - used_pct)),
            "reset_iso": "",
            "extra": {"used": used, "cap": cap, "ccy": ccy},
        })
    if not metrics:
        return {"ok": False, "note": "Claude usage API 没返回可解析窗口"}
    return {"ok": True, "metrics": metrics}


# Back-compat: keep _run_ccusage as a no-op that signals deprecation,
# in case anyone still imports it. Real CC usage now goes via
# `_query_cc_usage` above.
def _run_ccusage(view: str, days: str = "",
                 *, runner: Callable | None = None) -> tuple[int, str]:
    """DEPRECATED. ccusage only returns cumulative cost ('Total: $X'),
    which is wrong data for quota planning. Real quota % comes from
    `_query_cc_usage` (Anthropic OAuth API). Kept so older callers
    get a clear deprecation note rather than a NameError."""
    return 1, "(ccusage replaced by _query_cc_usage / api.anthropic.com)"


_CODEX_USAGE_RE = re.compile(
    r"(?P<label>[\w \-()]+?)\s+(?P<pct>\d+(?:\.\d+)?)%\s+resets\s+(?P<reset>\S+)",
    re.IGNORECASE,
)


def _codex_login_summary(home: Path | None = None) -> dict:
    """Best-effort login status from ~/.codex/auth.json (no `codex-cli-usage`
    needed). Decodes the id_token JWT (payload only — we don't verify
    the signature, just surface display fields) for plan type and
    subscription active_until. Used as the fallback path so a host with
    a healthy Codex login still gets a useful /usage row instead of a
    "未安装" wall.

    Returns the same shape `_query_codex_usage` does on success
    (`{ok, plan, metrics}`), with metrics empty so the renderer falls
    back to printing the plan + active_until status line."""
    auth_path = (home or Path.home()) / ".codex" / "auth.json"
    try:
        auth = json.loads(auth_path.read_text())
    except FileNotFoundError:
        return {"ok": False, "note": f"{auth_path} 不存在；运行 `codex` 完成登录"}
    except (OSError, ValueError):
        return {"ok": False, "note": f"{auth_path} 读取失败"}
    id_token = (auth.get("tokens") or {}).get("id_token", "")
    plan = auth.get("auth_mode") or "ChatGPT"
    active_until = ""
    if id_token and id_token.count(".") == 2:
        try:
            import base64
            payload_b64 = id_token.split(".")[1]
            # JWT base64 may need padding to a multiple of 4
            payload_b64 += "=" * (-len(payload_b64) % 4)
            payload = json.loads(base64.urlsafe_b64decode(payload_b64))
            chatgpt = payload.get("https://api.openai.com/auth", {}) or {}
            plan = (chatgpt.get("chatgpt_plan_type")
                    or plan).title()
            active_until = chatgpt.get("chatgpt_subscription_active_until", "")
        except (ValueError, TypeError, KeyError):
            pass  # JWT not parseable → just show auth_mode
    note = f"已登录 · 计划 {plan}"
    if active_until:
        note += f" · 续费至 {active_until[:10]}"
    note += " · 装 `codex-cli-usage` 看 % 额度"
    return {"ok": True, "plan": plan, "metrics": [], "note": note}


def _query_codex_usage(home: Path | None = None,
                       *, runner: Callable | None = None) -> dict:
    """Shell out to `codex-cli-usage` for real % consumed.

    The `codex-cli-usage` Python tool (installed via `uv tool
    install`, symlinked to /usr/local/bin in our container) queries
    OpenAI's actual quota endpoints and prints lines like:
        Plan: ChatGPT Pro
        5h limit  20% resets 4h
        Weekly limit  35% resets 5d

    Each `<label> <pct>% resets <reset>` line becomes a metric with
    used_pct + remaining_pct so the /usage card renders the same
    traffic-light layout as Claude Code / Kimi.

    Returns `{ok, plan, metrics: [{label, used_pct, remaining_pct,
    reset}]}` on success, `{ok: false, note}` on failure.
    """
    if runner is None:
        runner = lambda argv: subprocess.run(
            argv, capture_output=True, text=True, timeout=15)
    if shutil.which("codex-cli-usage") is None:
        # Fallback: at least confirm login status from ~/.codex/auth.json.
        # The id_token JWT carries chatgpt_plan_type +
        # chatgpt_subscription_active_until — show those instead of a
        # bare "未安装" message so the user can tell their Codex login
        # is healthy without installing an extra tool.
        fallback = _codex_login_summary(home)
        if fallback["ok"]:
            return fallback
        return {"ok": False,
                "note": "codex-cli-usage 未安装；运行 `uv tool install codex-cli-usage` 看额度（已登录但显示不出 %）"}
    try:
        r = runner(["codex-cli-usage"])
    except subprocess.TimeoutExpired:
        return {"ok": False, "note": "codex-cli-usage 超时（15s）"}
    except OSError as e:
        return {"ok": False, "note": f"codex-cli-usage exec 失败: {e}"}
    if r.returncode != 0:
        out = (r.stderr or "") + (r.stdout or "")
        # Prefer the last meaningful exception line (e.g.
        # `urllib.error.HTTPError: HTTP Error 403: Forbidden`) over
        # the first noisy "Traceback ..." or "  File ..." frame.
        # Walk lines bottom-up; pick the first that looks like an
        # actual error — has ":" but isn't a `File "..."` frame and
        # isn't the bare "Traceback (most recent call last):" header.
        err_line = ""
        for ln in reversed(out.splitlines()):
            s = ln.strip()
            if (s and ":" in s
                    and not s.startswith("File ")
                    and not s.startswith("^")
                    and "most recent call last" not in s):
                err_line = s
                break
        if not err_line:
            err_line = next(
                (ln.strip() for ln in out.splitlines() if ln.strip()),
                "未知错误")
        # 403 from OpenAI's usage endpoint is a known container-side
        # auth issue (main's docs: codex tokens get IP-pinned). Add
        # a hint so boss knows to `docker compose exec ... codex login`.
        if "403" in err_line:
            err_line += " · 容器内 codex 可能需重新 login"
        return {"ok": False, "note": f"codex-cli-usage: {err_line[:160]}"}

    plan = ""
    metrics: list[dict] = []
    for line in (r.stdout or "").splitlines():
        s = line.strip()
        if s.startswith("Plan:"):
            plan = s.split(":", 1)[1].strip()
            continue
        m = _CODEX_USAGE_RE.search(s)
        if m:
            used_pct = int(round(float(m.group("pct"))))
            metrics.append({
                "label": m.group("label").strip(),
                "used_pct": used_pct,
                "remaining_pct": max(0, min(100, 100 - used_pct)),
                "reset": m.group("reset"),
            })
    return {
        "ok": True,
        "plan": plan or "unknown",
        "metrics": metrics,
    }


def _opener_default(req, timeout):  # pragma: no cover - thin wrapper
    return urllib_request.urlopen(req, timeout=timeout)


def _query_kimi_usage(home: Path | None = None,
                      *, opener: Callable = _opener_default) -> dict:
    """Hit Kimi's coding API for the current quota window. Bearer
    token lives in `~/.kimi/credentials/kimi-code.json`. Returns
    `{ok, metrics: [{label, used_pct, remaining_pct, used, limit, reset_iso}]}`
    or `{ok: false, note}` describing why we couldn't query."""
    cred_path = (home or Path.home()) / ".kimi" / "credentials" / "kimi-code.json"
    try:
        token = json.loads(cred_path.read_text()).get("access_token", "")
    except FileNotFoundError:
        return {"ok": False, "note": f"{cred_path} 不存在；运行 `kimi` 完成登录"}
    except (OSError, ValueError) as e:
        return {"ok": False, "note": f"读取 {cred_path} 失败：{e}"}
    if not token:
        return {"ok": False, "note": "credentials/kimi-code.json 缺少 access_token"}
    req = urllib_request.Request(
        _KIMI_USAGE_URL, headers={"Authorization": f"Bearer {token}"})
    try:
        with opener(req, timeout=10) as resp:
            payload = json.loads(resp.read())
    except urllib_error.HTTPError as e:
        return {"ok": False, "note": f"Kimi API HTTP {e.code}：{e.reason}"}
    except (urllib_error.URLError, OSError, ValueError) as e:
        return {"ok": False, "note": f"Kimi API 请求失败：{e}"}

    metrics: list[dict] = []
    usage = payload.get("usage", {}) or {}
    try:
        limit = int(usage.get("limit", 0))
        used = int(usage.get("used", 0))
    except (TypeError, ValueError):
        limit = used = 0
    if limit > 0:
        used_pct = round(used / limit * 100)
        metrics.append({
            "label": "Weekly limit",
            "used": used,
            "limit": limit,
            "used_pct": used_pct,
            "remaining_pct": max(0, 100 - used_pct),
            "reset_iso": usage.get("resetTime", ""),
        })
    for item in payload.get("limits", []) or []:
        window = item.get("window", {}) or {}
        detail = item.get("detail", {}) or {}
        try:
            i_limit = int(detail.get("limit", 0))
            i_remaining = int(detail.get("remaining", 0))
        except (TypeError, ValueError):
            continue
        if i_limit <= 0:
            continue
        i_used = i_limit - i_remaining
        used_pct = round(i_used / i_limit * 100)
        duration = int(window.get("duration", 0) or 0)
        unit = window.get("timeUnit", "")
        if "MINUTE" in unit and duration >= 60 and duration % 60 == 0:
            label = f"{duration // 60}h limit"
        elif "MINUTE" in unit:
            label = f"{duration}m limit"
        else:
            label = f"{duration}s window"
        metrics.append({
            "label": label,
            "used": i_used,
            "limit": i_limit,
            "used_pct": used_pct,
            "remaining_pct": max(0, 100 - used_pct),
            "reset_iso": detail.get("resetTime", ""),
        })
    if not metrics:
        return {"ok": False, "note": "Kimi API 返回数据无可解析配额"}
    return {"ok": True, "metrics": metrics}


_NO_TOOL = "no upstream usage tool — track via the provider dashboard"
_UNKNOWN = "unknown — no usage adapter"
_KNOWN_NO_TOOL = ("codex-cli", "kimi-cli", "qwen-code", "qwen-cli", "gemini-cli")


def _note_for(cli: str) -> str:
    return _NO_TOOL if cli in _KNOWN_NO_TOOL else _UNKNOWN


def _build_data(view: str, days: str, clis: set[str],
                *, home: Path | None = None,
                opener: Callable = _opener_default) -> dict:
    """Run each CLI's usage probe and return a structured record.
    Used by both the text renderer (formatted lines) and the --json
    renderer (slash._handle_usage card).

    Codex + Kimi sections render whenever EITHER
    (a) a team agent declares `cli: codex-cli|kimi-code|kimi-cli`, OR
    (b) the corresponding host cred file is present
    (~/.codex/auth.json or ~/.kimi/credentials/kimi-code.json).
    The cred-file fallback lets `/usage` surface "is my Codex Pro
    seat still valid" even before a worker_codex pane is wired up."""
    data: dict[str, Any] = {
        "view": view,
        "days": days or None,
        "clis": sorted(clis),
        "claude_code": None,
        "codex": None,
        "kimi": None,
        "other_clis": [],
    }
    if "claude-code" in clis:
        # Real per-window utilization via Anthropic OAuth API
        # (api.anthropic.com/api/oauth/usage). Replaces the older
        # `npx ccusage <view>` shell-out which only returned
        # cumulative cost, not actual quota %.
        data["claude_code"] = _query_cc_usage(home, opener=opener)
    home_dir = home or Path.home()
    if ("codex-cli" in clis or "codex" in clis
            or (home_dir / ".codex" / "auth.json").exists()):
        data["codex"] = _query_codex_usage(home)
    if ("kimi-code" in clis or "kimi-cli" in clis
            or (home_dir / ".kimi" / "credentials" / "kimi-code.json").exists()):
        data["kimi"] = _query_kimi_usage(home, opener=opener)
    handled = {"claude-code", "codex-cli", "codex", "kimi-code", "kimi-cli"}
    for cli in sorted(clis):
        if cli in handled:
            continue
        data["other_clis"].append({"cli": cli, "note": _note_for(cli)})
    return data


def _emit_text(data: dict) -> None:
    print(f"━━ usage ({data['view']}) ━━")
    cc = data.get("claude_code")
    if cc is not None:
        print("\nclaude-code (api.anthropic.com /usage):")
        if not cc["ok"]:
            print(f"  ⚠️  {cc.get('note', 'unknown error')}")
        else:
            for m in cc.get("metrics", []):
                extra = m.get("extra")
                if extra:
                    print(f"  {m['label']}: 已用 {m['used_pct']}% · "
                          f"${extra['used']:.2f} / ${extra['cap']} {extra['ccy']}")
                else:
                    print(f"  {m['label']}: 已用 {m['used_pct']}% · "
                          f"剩余 {m['remaining_pct']}% · 重置 {m.get('reset_iso', '')}")
    cx = data.get("codex")
    if cx is not None:
        print("\ncodex (codex-cli-usage):")
        if not cx["ok"]:
            print(f"  ⚠️  {cx['note']}")
        else:
            print(f"  Plan: {cx['plan']}")
            for m in cx.get("metrics", []):
                print(f"  {m['label']}: 已用 {m['used_pct']}% · "
                      f"剩余 {m['remaining_pct']}% · 重置 {m.get('reset', '')}")
    km = data.get("kimi")
    if km is not None:
        print("\nkimi-code (api.kimi.com):")
        if not km["ok"]:
            print(f"  ⚠️  {km['note']}")
        else:
            for m in km["metrics"]:
                print(f"  {m['label']}: 已用 {m['used_pct']}% "
                      f"({m['used']}/{m['limit']}) · 剩余 {m['remaining_pct']}% "
                      f"· 重置 {m['reset_iso']}")
    if data["other_clis"]:
        print("\nother CLIs:")
        for row in data["other_clis"]:
            print(f"  {row['cli']}: {row['note']}")


def _emit_json(data: dict) -> None:
    print_json(data)


def main(argv: list[str]) -> int:
    rest = list(argv)
    if maybe_print_help(rest, USAGE):
        return 0

    as_json = pop_bool_flag(rest, "--json")
    view = pop_flag(rest, "--view") or "daily"
    days = pop_flag(rest, "--days") or ""
    if (rc := reject_extra_args(rest, USAGE)) is not None:
        return rc
    if view not in _VIEWS:
        return error_exit(f"❌ unknown view: {view} (valid: {' / '.join(_VIEWS)})")

    try:
        agents = config.load_team().get("agents", {})
        clis = {a.get("cli", "claude-code") for a in agents.values()}
    except Exception:
        clis = set()

    data = _build_data(view, days, clis)
    if as_json:
        _emit_json(data)
    else:
        _emit_text(data)
    return 0
