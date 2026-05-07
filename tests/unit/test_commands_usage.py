"""Tests for `claudeteam usage` — token-spend snapshot."""
from __future__ import annotations

import shutil
import subprocess

from helpers import attr_patch, isolated_env, run_cli
from claudeteam.commands import usage as _usage_mod


def _stub_runner(*, rc: int, output: str):
    """Replace subprocess.run only for ccusage invocations."""
    saved = subprocess.run

    class FakeResult:
        def __init__(self, returncode, stdout, stderr=""):
            self.returncode = returncode
            self.stdout = stdout
            self.stderr = stderr

    def fake(argv, *args, **kwargs):
        if argv[:1] == ["npx"]:
            return FakeResult(rc, output)
        return saved(argv, *args, **kwargs)

    return attr_patch(subprocess, run=fake)


def _stub_npx_present(present: bool):
    saved = shutil.which

    def fake(name, *args, **kwargs):
        if name == "npx":
            return "/usr/bin/npx" if present else None
        return saved(name, *args, **kwargs)

    return attr_patch(shutil, which=fake)


# ── happy path ──────────────────────────────────────────────────


# R173: ccusage-shell-out tests retired — the CC probe is now an
# Anthropic OAuth API call (`_query_cc_usage` hits api.anthropic.com).
# `_query_cc_usage` is covered by the new tests further below + the
# slash card tests in test_feishu_slash.py.


def test_usage_lists_other_clis_with_no_tool_message():
    """R170: codex-cli + kimi-code now have first-class probes (handled
    by their own sections), so the catch-all `other_clis` branch fires
    only for CLIs we genuinely have no upstream tool for — qwen / gemini."""
    team = {"agents": {"a": {"cli": "qwen-code"}, "b": {"cli": "gemini-cli"}}}
    with isolated_env(team=team), _stub_npx_present(False):
        rc, out, _ = run_cli(["usage"])
        assert rc == 0
        assert "qwen-code: no upstream usage tool" in out
        assert "gemini-cli: no upstream usage tool" in out


def test_usage_rejects_unknown_view():
    team = {"agents": {"manager": {"cli": "claude-code"}}}
    with isolated_env(team=team):
        rc, _, err = run_cli(["usage", "--view", "bogus"])
        assert rc == 1
        assert "unknown view" in err


def test_usage_rejects_unexpected_args():
    with isolated_env():
        rc, _, err = run_cli(["usage", "--bogus"])
        assert rc == 1
        assert "unexpected args" in err


def test_usage_help():
    rc, out, _ = run_cli(["usage", "--help"])
    assert rc == 0
    assert "usage: claudeteam usage" in out


# ── --json mode ─────────────────────────────────────────────────


def test_usage_json_includes_claude_code_section_with_metrics():
    """R173: --json record carries `claude_code` as the new shape
    `{ok, metrics: [...]}` from `_query_cc_usage`. ccusage-era fields
    (rc, output, lines) gone — ok=False with `note` is the failure
    surface now. CC probe stubbed because the live API needs OAuth
    creds the test host doesn't provide."""
    import json as _json
    from helpers import attr_patch
    team = {"agents": {
        "manager":      {"cli": "claude-code"},
        "worker_qwen":  {"cli": "qwen-code"},
    }}
    fake_metrics = [{"label": "5-hour window", "used_pct": 30,
                     "remaining_pct": 70, "reset_iso": "2026-05-05T18:00:00Z"}]
    with isolated_env(team=team), \
            attr_patch(_usage_mod,
                       _query_cc_usage=lambda home=None, opener=None: {
                           "ok": True, "metrics": fake_metrics}):
        rc, out, _ = run_cli(["usage", "--json"])
        assert rc == 0
        data = _json.loads(out)
        assert "claude-code" in data["clis"]
        assert "qwen-code" in data["clis"]
        assert data["claude_code"]["ok"] is True
        assert data["claude_code"]["metrics"] == fake_metrics
        qwen_entry = next(r for r in data["other_clis"] if r["cli"] == "qwen-code")
        assert "no upstream usage tool" in qwen_entry["note"]


def test_usage_json_records_cc_failure_without_aborting():
    """When the CC probe fails (token expired, network down), JSON
    still emits with ok=False + `note` so consumers branch on field."""
    import json as _json
    from helpers import attr_patch
    team = {"agents": {"manager": {"cli": "claude-code"}}}
    with isolated_env(team=team), \
            attr_patch(_usage_mod,
                       _query_cc_usage=lambda home=None, opener=None: {
                           "ok": False, "note": "access token 已过期"}):
        rc, out, _ = run_cli(["usage", "--json"])
        assert rc == 0
        data = _json.loads(out)
        assert data["claude_code"]["ok"] is False
        assert "已过期" in data["claude_code"]["note"]


# ── R170: codex + kimi probes ───────────────────────────────────


import contextlib
import tempfile
from pathlib import Path


@contextlib.contextmanager
def _fake_home(*, codex_auth=None, kimi_cred=None):
    """Build a tempdir with .codex/auth.json + .kimi/credentials/kimi-code.json
    populated only when their kwargs are provided. Yields the home Path
    so tests can run without touching the dev's real ~/.codex / ~/.kimi."""
    import json as _json
    with tempfile.TemporaryDirectory() as tmp:
        home = Path(tmp) / "home"
        home.mkdir()
        if codex_auth is not None:
            d = home / ".codex"
            d.mkdir()
            (d / "auth.json").write_text(_json.dumps(codex_auth))
        if kimi_cred is not None:
            d = home / ".kimi" / "credentials"
            d.mkdir(parents=True)
            (d / "kimi-code.json").write_text(_json.dumps(kimi_cred))
        yield home


def _b64url(payload: dict) -> str:
    import base64, json as _json
    raw = _json.dumps(payload).encode()
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _fake_jwt(payload: dict) -> str:
    """Build a minimal JWT (header.payload.signature) — only the
    payload section is decoded by `_decode_jwt_payload`."""
    return f"hdr.{_b64url(payload)}.sig"


def _make_runner(returncode=0, stdout="", stderr=""):
    class _R:
        pass
    _R.returncode = returncode
    _R.stdout = stdout
    _R.stderr = stderr
    def runner(argv):
        return _R
    return runner


def test_codex_query_parses_codex_cli_usage_output():
    """R173: real path is `codex-cli-usage` subprocess. Output looks
    like:
        Plan: ChatGPT Pro
        5h limit  20% resets 4h
        Weekly limit  35% resets 5d
    Each `<label> <pct>% resets <reset>` becomes a metric with the
    used+remaining percent split + reset string surfaced verbatim."""
    import shutil
    from helpers import attr_patch
    output = (
        "Plan: ChatGPT Pro\n"
        "5h limit  20% resets 4h\n"
        "Weekly limit  35% resets 5d\n"
    )
    with attr_patch(shutil, which=lambda t: "/usr/local/bin/codex-cli-usage" if t == "codex-cli-usage" else None):
        result = _usage_mod._query_codex_usage(
            runner=_make_runner(returncode=0, stdout=output))
    assert result["ok"] is True
    assert result["plan"] == "ChatGPT Pro"
    labels = [m["label"] for m in result["metrics"]]
    assert labels == ["5h limit", "Weekly limit"]
    assert result["metrics"][0]["used_pct"] == 20
    assert result["metrics"][0]["remaining_pct"] == 80
    assert result["metrics"][0]["reset"] == "4h"
    assert result["metrics"][1]["used_pct"] == 35


def test_codex_query_returns_failure_when_tool_missing_and_no_auth():
    """If codex-cli-usage isn't on PATH AND ~/.codex/auth.json doesn't
    exist, surface a clear missing-login note. Both prereqs absent →
    user genuinely has nothing logged in for Codex."""
    import shutil
    import tempfile
    from helpers import attr_patch
    with tempfile.TemporaryDirectory() as td:
        with attr_patch(shutil, which=lambda t: None):
            result = _usage_mod._query_codex_usage(
                home=Path(td), runner=_make_runner())
    assert result["ok"] is False
    assert "不存在" in result["note"] or "登录" in result["note"]


def test_codex_query_falls_back_to_auth_json_summary_when_tool_missing():
    """When codex-cli-usage isn't installed but ~/.codex/auth.json IS
    present (real host dev case), fall back to the JWT summary helper
    so the user sees their login status instead of a "未安装" wall."""
    import shutil
    from helpers import attr_patch
    payload = {
        "https://api.openai.com/auth": {
            "chatgpt_plan_type": "pro",
            "chatgpt_subscription_active_until": "2026-05-20T00:00:00+00:00",
        },
    }
    with _fake_home(codex_auth={"tokens": {"id_token": _fake_jwt(payload)}}) \
            as home, attr_patch(shutil, which=lambda t: None):
        result = _usage_mod._query_codex_usage(
            home=home, runner=_make_runner())
    assert result["ok"] is True
    assert "已登录" in result["note"]
    assert "Pro" in result.get("plan", "") or "Pro" in result["note"]


def test_codex_query_returns_failure_on_nonzero_exit():
    """codex-cli-usage rc != 0 → surface first non-npm-noise error line."""
    import shutil
    from helpers import attr_patch
    with attr_patch(shutil, which=lambda t: "/usr/local/bin/codex-cli-usage"):
        result = _usage_mod._query_codex_usage(
            runner=_make_runner(returncode=1, stderr="Error: HTTP 401 unauthorized\n"))
    assert result["ok"] is False
    assert "HTTP 401" in result["note"] or "Error" in result["note"]


def test_kimi_query_parses_weekly_and_window_metrics():
    """Happy path: API returns a `usage` dict + a list of `limits`
    windows; we transform each into a metric row."""
    captured = {}

    class FakeResp:
        def __init__(self, body):
            self._body = body.encode()
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False
        def read(self):
            return self._body

    def fake_opener(req, timeout):
        captured["url"] = req.full_url
        captured["auth"] = req.get_header("Authorization")
        import json as _json
        return FakeResp(_json.dumps({
            "usage": {"limit": 100, "used": 25, "remaining": 75,
                       "resetTime": "2026-05-08T00:00:00Z"},
            "limits": [
                {"window": {"duration": 300, "timeUnit": "MINUTE"},
                 "detail": {"limit": 50, "remaining": 40,
                            "resetTime": "2026-05-04T19:00:00Z"}},
            ],
        }))

    with _fake_home(kimi_cred={"access_token": "tok123"}) as home:
        result = _usage_mod._query_kimi_usage(home=home, opener=fake_opener)
    assert result["ok"] is True
    assert captured["auth"] == "Bearer tok123"
    assert "api.kimi.com" in captured["url"]
    labels = [m["label"] for m in result["metrics"]]
    assert "Weekly limit" in labels
    assert "5h limit" in labels
    weekly = next(m for m in result["metrics"] if m["label"] == "Weekly limit")
    assert weekly["used_pct"] == 25
    assert weekly["remaining_pct"] == 75


def test_kimi_query_failure_when_credential_missing():
    with _fake_home() as home:  # no .kimi
        result = _usage_mod._query_kimi_usage(home=home)
    assert result["ok"] is False
    assert "kimi-code.json" in result["note"]


def test_kimi_query_failure_when_token_blank():
    with _fake_home(kimi_cred={"access_token": ""}) as home:
        result = _usage_mod._query_kimi_usage(home=home)
    assert result["ok"] is False
    assert "access_token" in result["note"]


def test_kimi_query_failure_on_http_error():
    from urllib import error as urllib_error

    def fake_opener(req, timeout):
        raise urllib_error.HTTPError(req.full_url, 401, "Unauthorized",
                                      hdrs=None, fp=None)

    with _fake_home(kimi_cred={"access_token": "tok"}) as home:
        result = _usage_mod._query_kimi_usage(home=home, opener=fake_opener)
    assert result["ok"] is False
    assert "401" in result["note"]


# ── R170: --json end-to-end shape ───────────────────────────────


def test_usage_json_includes_codex_and_kimi_keys_for_those_clis():
    """Mock the probes so the test doesn't reach the real host home."""
    import json as _json
    team = {"agents": {
        "manager":      {"cli": "claude-code"},
        "worker_codex": {"cli": "codex-cli"},
        "worker_kimi":  {"cli": "kimi-code"},
    }}
    with isolated_env(team=team), _stub_npx_present(True), \
            _stub_runner(rc=0, output="Total: 7777"), \
            attr_patch(_usage_mod,
                       _query_codex_usage=lambda home=None, runner=None: {"ok": True, "plan": "Pro", "metrics": []},
                       _query_kimi_usage=lambda home=None, opener=None: {
                           "ok": True, "metrics": [
                               {"label": "Weekly limit", "used": 1,
                                "limit": 10, "used_pct": 10,
                                "remaining_pct": 90, "reset_iso": "x"}]}):
        rc, out, _ = run_cli(["usage", "--json"])
        assert rc == 0
        data = _json.loads(out)
        assert data["codex"]["ok"] is True
        assert data["codex"]["plan"] == "Pro"
        assert data["kimi"]["ok"] is True
        assert data["kimi"]["metrics"][0]["label"] == "Weekly limit"
        # codex-cli + kimi-code must NOT show up as catch-all entries
        other_names = {row["cli"] for row in data["other_clis"]}
        assert "codex-cli" not in other_names
        assert "kimi-code" not in other_names


def test_usage_probes_codex_kimi_when_team_has_no_matching_agent():
    """R170: even when no team agent declares cli=codex-cli/kimi-code,
    `_build_data` opportunistically probes if the host has the cred
    files — so a single-claude-code deployment still surfaces whether
    Codex Pro / Kimi auth is alive."""
    payload = {
        "email": "x@y.z",
        "https://api.openai.com/auth": {"chatgpt_plan_type": "pro"},
    }

    def fake_opener(req, timeout):
        raise OSError("no net in tests")

    # codex-cli-usage missing → fall back to auth.json JWT summary
    # (now that auth.json exists in the fake home, the codex section
    # reports ok=True with the login summary, instead of the old
    # "未安装" failure). Kimi has no upstream tool path — so its
    # opener-failure still surfaces as ok=False.
    import shutil
    from helpers import attr_patch
    with _fake_home(
            codex_auth={"tokens": {"id_token": _fake_jwt(payload)}},
            kimi_cred={"access_token": "tok"}) as home, \
            attr_patch(shutil, which=lambda t: None):
        data = _usage_mod._build_data(
            "daily", "", {"claude-code"}, home=home, opener=fake_opener)
    # Codex section IS produced (because cred file existed) AND succeeds
    # via the auth.json fallback path
    assert data["codex"] is not None
    assert data["codex"]["ok"] is True
    assert "已登录" in data["codex"].get("note", "")
    # Kimi probed too; opener throws so ok=False, section still rendered
    assert data["kimi"] is not None
    assert data["kimi"]["ok"] is False


def test_usage_skips_codex_kimi_when_no_creds_no_matching_agent():
    """Mirror of the test above — without cred files AND without a
    matching team agent, the sections stay null. Avoids drive-by
    probes when there's nothing useful to query."""
    with _fake_home() as home:
        data = _usage_mod._build_data(
            "daily", "", {"claude-code"}, home=home,
            opener=lambda *a, **k: None)
    assert data["codex"] is None
    assert data["kimi"] is None


def test_usage_text_renders_codex_and_kimi_sections():
    team = {"agents": {
        "manager":      {"cli": "claude-code"},
        "worker_codex": {"cli": "codex-cli"},
        "worker_kimi":  {"cli": "kimi-code"},
    }}
    with isolated_env(team=team), _stub_npx_present(True), \
            _stub_runner(rc=0, output="Total: 1"), \
            attr_patch(_usage_mod,
                       _query_codex_usage=lambda home=None, runner=None: {
                           "ok": True, "plan": "Pro",
                           "metrics": [
                               {"label": "5h limit", "used_pct": 20,
                                "remaining_pct": 80, "reset": "4h"}]},
                       _query_kimi_usage=lambda home=None, opener=None: {
                           "ok": True, "metrics": [
                               {"label": "Weekly limit", "used": 5,
                                "limit": 10, "used_pct": 50,
                                "remaining_pct": 50,
                                "reset_iso": "2026-05-08T00:00:00Z"}]}):
        rc, out, _ = run_cli(["usage"])
        assert rc == 0
        # R173: codex header changed from "(chatgpt OAuth)" → "(codex-cli-usage)"
        assert "codex (codex-cli-usage)" in out
        assert "Plan: Pro" in out
        assert "kimi-code (api.kimi.com)" in out
        assert "Weekly limit" in out

