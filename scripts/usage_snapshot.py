#!/usr/bin/env python3
"""Fetch Claude Max /usage data from server side.

Bypasses Cloudflare challenge on claude.ai by calling the mirror
endpoint on api.anthropic.com with the oauth beta header.
See memory/reference_usage_snapshot_endpoint.md for background.
"""
import json
import os
import subprocess
import sys
import time
import urllib.request
import urllib.error
from datetime import datetime, timezone, timedelta
from pathlib import Path

CREDS_PATH = Path.home() / ".claude" / ".credentials.json"
URL = "https://api.anthropic.com/api/oauth/usage"
BETA_HEADER = "oauth-2025-04-20"
BJ_TZ = timezone(timedelta(hours=8))


def _refresh_claude_oauth():
    r = subprocess.run(
        ["claude", "-p", "Return only OK"],
        capture_output=True, text=True, timeout=60
    )
    if r.returncode != 0:
        msg = (r.stderr or r.stdout or "无错误输出").strip()
        sys.exit(f"Claude OAuth token 已过期，自动刷新失败：{msg[:200]}")


def load_token(allow_refresh=True):
    with open(CREDS_PATH) as f:
        data = json.load(f)
    oauth = data["claudeAiOauth"]
    expires_ms = oauth.get("expiresAt", 0)
    now_ms = int(time.time() * 1000)
    if expires_ms and expires_ms < now_ms:
        if not allow_refresh:
            sys.exit(
                f"access token expired at {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(expires_ms/1000))} "
                f"(now {time.strftime('%Y-%m-%d %H:%M:%S')})"
            )
        _refresh_claude_oauth()
        return load_token(allow_refresh=False)
    return oauth["accessToken"]


def fetch(token):
    req = urllib.request.Request(
        URL,
        headers={
            "Authorization": f"Bearer {token}",
            "anthropic-beta": BETA_HEADER,
            "Accept": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read().decode("utf-8"))


def fmt_pct(v):
    return f"{v:.0f}%" if v is not None else "n/a"


def fmt_reset(iso):
    if not iso:
        return "n/a"
    try:
        dt = datetime.fromisoformat(iso)
    except ValueError:
        return iso
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(BJ_TZ).strftime("%Y-%m-%d %H:%M 北京时间")


def render_text(d):
    lines = []
    now_bj = datetime.now(BJ_TZ).strftime("%Y-%m-%d %H:%M:%S 北京时间")
    lines.append(f"📊 Claude Max /usage 快照 (源: api.anthropic.com, {now_bj})")
    fh = d.get("five_hour") or {}
    sd = d.get("seven_day") or {}
    ss = d.get("seven_day_sonnet") or {}
    so = d.get("seven_day_opus") or {}
    lines.append(f"  5-hour window    : {fmt_pct(fh.get('utilization'))}  resets {fmt_reset(fh.get('resets_at'))}")
    lines.append(f"  7-day all models : {fmt_pct(sd.get('utilization'))}  resets {fmt_reset(sd.get('resets_at'))}")
    lines.append(f"  7-day Sonnet     : {fmt_pct(ss.get('utilization'))}  resets {fmt_reset(ss.get('resets_at'))}")
    if so:
        lines.append(f"  7-day Opus       : {fmt_pct(so.get('utilization'))}  resets {fmt_reset(so.get('resets_at'))}")
    eu = d.get("extra_usage") or {}
    if eu.get("is_enabled"):
        used = eu.get("used_credits", 0)
        cap = eu.get("monthly_limit", 0)
        cur = eu.get("currency", "")
        lines.append(f"  Extra usage      : {used:.2f} / {cap} {cur}  ({fmt_pct(eu.get('utilization'))})")
    return "\n".join(lines)


def main():
    want_json = "--json" in sys.argv[1:]
    token = load_token()
    try:
        data = fetch(token)
    except urllib.error.HTTPError as e:
        sys.exit(f"HTTP {e.code}: {e.read().decode('utf-8', errors='replace')[:300]}")
    except urllib.error.URLError as e:
        sys.exit(f"network error: {e}")
    if want_json:
        print(json.dumps(data, indent=2, ensure_ascii=False))
    else:
        print(render_text(data))


if __name__ == "__main__":
    main()
