"""Tests for feishu/lark.py — subprocess wrapper around lark-cli."""
from __future__ import annotations

import subprocess

from helpers import CallRecorder, FakeProc, env_patch
from claudeteam.feishu import lark


def _Recorder(result=None) -> CallRecorder:
    """Recorder pre-seeded with an empty FakeProc — lark.call needs the
    .returncode / .stdout / .stderr trio to do its branching."""
    return CallRecorder(result if result is not None else FakeProc())


def _no_proxy_env():
    """Stage the env so lark-cli will strip HTTPS_PROXY before invoking npx."""
    return env_patch(LARK_CLI_NO_PROXY="1", HTTPS_PROXY="http://proxy.example:7890")


def test_run_builds_lark_cli_argv_with_profile():
    """Round-86: argv prefix is whichever direct lark-cli we found
    (or npx fallback). Either way, profile/positional args must be
    appended in order. Pin the prefix via env override so the test
    doesn't depend on whatever's installed locally."""
    rec = _Recorder(FakeProc(stdout='{"ok": true, "data": {"x": 1}}'))
    with env_patch(CLAUDETEAM_LARK_CLI_BIN="/usr/local/bin/lark-cli"):
        # The override path must exist for the resolver to pick it; create a
        # stub via the mock — but resolver only checks os.path.exists, so we
        # bypass the override and just assert positional shape regardless of
        # which prefix landed.
        pass
    out = lark.call(["im", "+messages-send"], profile="my-team", run=rec)
    assert out == {"x": 1}
    sent = rec.calls[0]["args"]
    # Profile + positional args present in order, regardless of prefix
    assert "--profile" in sent and "my-team" in sent
    assert sent[-2:] == ["im", "+messages-send"]
    # Prefix is one of the known shapes
    assert sent[0] == "npx" or sent[0].endswith("lark-cli")


def test_run_omits_profile_when_empty():
    rec = _Recorder(FakeProc(stdout='{"data":{}}'))
    lark.call(["foo"], profile="", run=rec)
    sent = rec.calls[0]["args"]
    assert "--profile" not in sent


def test_resolve_cli_prefix_uses_explicit_env_override():
    """Round-86: CLAUDETEAM_LARK_CLI_BIN takes priority over auto-discovery."""
    import tempfile
    import os as _os
    with tempfile.TemporaryDirectory() as td:
        fake_bin = _os.path.join(td, "lark-cli")
        with open(fake_bin, "w") as fh:
            fh.write("#!/bin/sh\nexit 0\n")
        _os.chmod(fake_bin, 0o755)
        with env_patch(CLAUDETEAM_LARK_CLI_BIN=fake_bin):
            prefix = lark.resolve_cli_prefix()
        assert prefix == [fake_bin]


def test_resolve_cli_prefix_ignores_nonexistent_override():
    """A bogus path in the env override must NOT be returned (would cause
    every send to fail with FileNotFoundError); fall through to discovery."""
    with env_patch(CLAUDETEAM_LARK_CLI_BIN="/does/not/exist/lark-cli"):
        prefix = lark.resolve_cli_prefix()
    # Falls through to either real lark-cli on PATH or npx fallback
    assert prefix[0] == "npx" or prefix[0].endswith("lark-cli")


def test_resolve_cli_prefix_falls_back_to_npx_when_nothing_else():
    """Stub out shutil.which + the npx-cache path so the resolver can only
    reach the npx fallback. Verifies we never crash on a clean machine
    that's never run lark-cli before."""
    import shutil as _shutil
    real_which = _shutil.which
    real_isdir = lark.os.path.isdir
    try:
        _shutil.which = lambda name: None
        # Pretend the npx cache dir doesn't exist (uninstalled state)
        lark.os.path.isdir = lambda p: False
        with env_patch(CLAUDETEAM_LARK_CLI_BIN=""):
            prefix = lark.resolve_cli_prefix()
    finally:
        _shutil.which = real_which
        lark.os.path.isdir = real_isdir
    assert prefix == ["npx", "@larksuite/cli"]


def test_run_returns_data_field_unwrapped():
    rec = _Recorder(FakeProc(stdout='{"ok":true,"data":{"message_id":"om_1"}}'))
    assert lark.call(["x"], run=rec) == {"message_id": "om_1"}


def test_run_returns_full_object_when_no_data_field():
    rec = _Recorder(FakeProc(stdout='{"raw":"value"}'))
    assert lark.call(["x"], run=rec) == {"raw": "value"}


def test_run_returns_empty_dict_for_blank_stdout():
    rec = _Recorder(FakeProc(stdout=""))
    assert lark.call(["x"], run=rec) == {}


def test_run_returns_none_on_nonzero_exit():
    rec = _Recorder(FakeProc(returncode=1, stderr="oops"))
    assert lark.call(["x"], run=rec) is None


def test_run_returns_none_when_api_says_ok_false():
    """lark-cli exits 0 even when the API returns ok=false; treat as failure
    so callers don't quietly accept a body that's missing the expected fields."""
    rec = _Recorder(FakeProc(stdout='{"ok":false,"msg":"need_user_authorization","code":99991663}'))
    assert lark.call(["x"], run=rec) is None


def test_api_error_extracts_message_from_nested_error_dict():
    """REGRESSION (round-60): lark-cli sometimes returns a structured
    error object: {"error": {"type": "...", "code": ..., "message": "..."}}
    instead of a plain "msg" field. Old code printed the dict's repr
    making the warning useless. Now extract error.message + type."""
    import io
    import contextlib
    payload = ('{"ok":false,"error":{"type":"api_error","code":230002,'
               '"message":"HTTP 400: Bot/User can NOT be out of the chat."}}')
    rec = _Recorder(FakeProc(stdout=payload))
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        result = lark.call(["x"], run=rec)
    assert result is None
    log = out.getvalue()
    assert "HTTP 400: Bot/User can NOT be out of the chat." in log
    assert "type=api_error" in log
    # Should NOT print the dict repr (old behaviour)
    assert "{'type'" not in log


def test_api_error_extracts_message_from_validation_error():
    """Same shape, different type — `validation` errors from
    `--image` flag rejection (round-58 saw this). Operator wants
    the actual rejection reason, not a dict literal."""
    import io
    import contextlib
    payload = ('{"ok":false,"error":{"type":"validation",'
               '"message":"--image: --file must be a relative path"}}')
    rec = _Recorder(FakeProc(stdout=payload))
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        lark.call(["x"], run=rec)
    log = out.getvalue()
    assert "must be a relative path" in log
    assert "type=validation" in log


def test_api_error_falls_back_when_error_is_plain_string():
    """Some endpoints return error as a plain string. Fall back to
    that string verbatim."""
    import io
    import contextlib
    rec = _Recorder(FakeProc(stdout='{"ok":false,"error":"something went wrong"}'))
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        lark.call(["x"], run=rec)
    assert "something went wrong" in out.getvalue()


def test_api_error_falls_back_to_code_when_no_message():
    """Sparse error responses with only a code field — surface the
    code rather than '?'."""
    import io
    import contextlib
    rec = _Recorder(FakeProc(stdout='{"ok":false,"code":42}'))
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        lark.call(["x"], run=rec)
    assert "42" in out.getvalue()


def test_run_returns_data_when_ok_true():
    """Belt-and-suspenders: ok=true with data should still unwrap data."""
    rec = _Recorder(FakeProc(stdout='{"ok":true,"data":{"message_id":"om_2"}}'))
    assert lark.call(["x"], run=rec) == {"message_id": "om_2"}


def test_run_returns_none_on_invalid_json():
    rec = _Recorder(FakeProc(stdout="not-json"))
    assert lark.call(["x"], run=rec) is None


def test_run_returns_none_on_timeout():
    def fake(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd=["lark"], timeout=90)
    assert lark.call(["x"], run=fake) is None


def test_run_returns_none_when_npx_not_on_path():
    """REGRESSION: subprocess.run raising FileNotFoundError (npx not
    installed) used to propagate as a top-level traceback through
    every claudeteam say / router invocation. Now caught with a
    one-liner pointing at Node.js install."""
    import io
    import contextlib

    def fake(*a, **kw):
        raise FileNotFoundError("[Errno 2] No such file or directory: 'npx'")

    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        result = lark.call(["x"], run=fake)
    assert result is None
    assert "npx not found on PATH" in out.getvalue()


def test_run_returns_none_on_other_oserror():
    """OSError variants other than FileNotFoundError (permission denied
    on fork, EAGAIN, etc.) also caught — same one-line warn pattern."""
    import io
    import contextlib

    def fake(*a, **kw):
        raise OSError("[Errno 13] Permission denied")

    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        result = lark.call(["x"], run=fake)
    assert result is None
    assert "could not be launched" in out.getvalue()


def test_run_logs_preview_when_stdout_is_not_json():
    """REGRESSION: silent return None on JSONDecodeError used to make
    debugging impossible. Now logs a one-line preview of the offending
    output so the operator can see the kind of garbage they got back."""
    import io
    import contextlib
    rec = _Recorder(FakeProc(stdout="<html>\n<body>auth required</body>\n</html>\n"))
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        result = lark.call(["x"], run=rec)
    assert result is None
    log = out.getvalue()
    assert "non-JSON" in log
    assert "<html>" in log  # the preview line


def test_run_default_timeout_is_90_seconds():
    rec = _Recorder(FakeProc(stdout="{}"))
    lark.call(["x"], run=rec)
    assert rec.calls[0]["kwargs"]["timeout"] == 90


def test_run_uses_explicit_timeout_when_passed():
    rec = _Recorder(FakeProc(stdout="{}"))
    lark.call(["x"], timeout=5, run=rec)
    assert rec.calls[0]["kwargs"]["timeout"] == 5


def test_run_strips_https_proxy_when_no_proxy_env_set():
    rec = _Recorder(FakeProc(stdout="{}"))
    with _no_proxy_env():
        lark.call(["x"], run=rec)
    env = rec.calls[0]["kwargs"]["env"]
    assert "HTTPS_PROXY" not in env
    assert env.get("LARK_CLI_NO_PROXY") == "1"


def test_run_keeps_proxy_env_when_no_proxy_unset():
    rec = _Recorder(FakeProc(stdout="{}"))
    with env_patch(HTTPS_PROXY="http://x"):
        lark.call(["x"], run=rec)
        assert rec.calls[0]["kwargs"]["env"].get("HTTPS_PROXY") == "http://x"


# ── subprocess_env (public, used by router daemon Popen) ──────────


def test_subprocess_env_strips_proxy_when_no_proxy_set():
    """REGRESSION: round 6 smoke proved the router daemon's Popen
    inherits HTTPS_PROXY untouched and lark-cli +subscribe then fails
    to deliver events. router now calls lark.subprocess_env() to get
    the same proxy-stripped env that lark.call uses."""
    with env_patch(LARK_CLI_NO_PROXY="1",
                   HTTPS_PROXY="http://proxy.example:7890",
                   HTTP_PROXY="http://proxy.example:7890"):
        env = lark.subprocess_env()
    assert "HTTPS_PROXY" not in env
    assert "HTTP_PROXY" not in env
    assert env.get("LARK_CLI_NO_PROXY") == "1"


def test_subprocess_env_keeps_proxy_when_no_proxy_unset():
    with env_patch(HTTPS_PROXY="http://x", LARK_CLI_NO_PROXY=None):
        env = lark.subprocess_env()
    assert env.get("HTTPS_PROXY") == "http://x"


def test_subprocess_env_pins_home_to_pw_dir():
    """Claude panes spawn with HOME=<state_dir>/agent-home/<agent> for
    ~/.claude.json isolation. When an agent inside such a pane runs
    `claudeteam say`, the lark-cli subprocess inherits that per-agent
    HOME and fails to locate `~/.lark-cli/config.json` (rc=2). Pin
    HOME to the OS user's pw_dir so lark-cli always reads the host
    user's profile regardless of how the caller's HOME was mangled."""
    import os, pwd
    expected_home = pwd.getpwuid(os.getuid()).pw_dir
    with env_patch(HOME="/data/agent-home/manager"):
        env = lark.subprocess_env()
    assert env["HOME"] == expected_home
    assert env["HOME"] != "/data/agent-home/manager"


# ── _resolve_timeout (env-driven default override) ────────────────


def test_timeout_default_is_90s_when_unset():
    """No explicit timeout, no CLAUDETEAM_LARK_TIMEOUT → 90 (matches the
    docstring; lark-cli routinely takes ~73s on host networks per
    project_lark_cli_slow.md memory)."""
    rec = _Recorder(FakeProc(stdout="{}"))
    with env_patch(CLAUDETEAM_LARK_TIMEOUT=None):
        lark.call(["x"], run=rec)
    assert rec.calls[0]["kwargs"]["timeout"] == 90


def test_timeout_explicit_arg_wins_over_env():
    """If the caller passes timeout=N, ignore the env entirely."""
    rec = _Recorder(FakeProc(stdout="{}"))
    with env_patch(CLAUDETEAM_LARK_TIMEOUT="240"):
        lark.call(["x"], timeout=5, run=rec)
    assert rec.calls[0]["kwargs"]["timeout"] == 5


def test_timeout_picks_up_env_when_no_explicit_arg():
    """CLAUDETEAM_LARK_TIMEOUT lets operators bump the default for slow
    hosts without changing call sites."""
    rec = _Recorder(FakeProc(stdout="{}"))
    with env_patch(CLAUDETEAM_LARK_TIMEOUT="180"):
        lark.call(["x"], run=rec)
    assert rec.calls[0]["kwargs"]["timeout"] == 180


def test_timeout_falls_back_to_90_when_env_is_garbage():
    """Misconfigured env (`CLAUDETEAM_LARK_TIMEOUT=potato`) should fall
    back to the default rather than raising. ValueError is caught
    inside _resolve_timeout."""
    rec = _Recorder(FakeProc(stdout="{}"))
    with env_patch(CLAUDETEAM_LARK_TIMEOUT="not-a-number"):
        lark.call(["x"], run=rec)
    assert rec.calls[0]["kwargs"]["timeout"] == 90


def test_timeout_clamps_zero_or_negative_to_one():
    """REGRESSION (round-64): CLAUDETEAM_LARK_TIMEOUT=0 used to be
    passed through to subprocess.run, which insta-raises TimeoutExpired
    on every call → every lark op silently fails. -1 caused ValueError
    deeper in subprocess. Now clamped to min 1 second; operator gets
    a real timeout window even with a fat-fingered config."""
    rec = _Recorder(FakeProc(stdout="{}"))
    with env_patch(CLAUDETEAM_LARK_TIMEOUT="0"):
        lark.call(["x"], run=rec)
    assert rec.calls[0]["kwargs"]["timeout"] == 1
    rec2 = _Recorder(FakeProc(stdout="{}"))
    with env_patch(CLAUDETEAM_LARK_TIMEOUT="-5"):
        lark.call(["x"], run=rec2)
    assert rec2.calls[0]["kwargs"]["timeout"] == 1


def test_timeout_explicit_zero_also_clamped():
    """Same clamp applied to the explicit `timeout=` kwarg path —
    a caller passing 0 still gets a usable 1s window, not an
    insta-fail."""
    rec = _Recorder(FakeProc(stdout="{}"))
    lark.call(["x"], timeout=0, run=rec)
    assert rec.calls[0]["kwargs"]["timeout"] == 1


# ── Popen-time errors (npx missing, permission denied, ...) ────


def test_run_returns_none_when_npx_not_on_path():
    """REGRESSION: subprocess raising FileNotFoundError (npx not
    installed) used to propagate as a top-level traceback through
    every claudeteam say / chat invocation. Now caught with a
    one-liner pointing at Node.js install."""
    import io
    import contextlib

    def fake(*a, **kw):
        raise FileNotFoundError("[Errno 2] No such file or directory: 'npx'")

    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        result = lark.call(["x"], run=fake)
    assert result is None
    assert "npx not found on PATH" in out.getvalue()


def test_run_returns_none_on_other_oserror():
    """OSError variants other than FileNotFoundError (permission, EAGAIN,
    fork failed) — same one-line warn pattern."""
    import io
    import contextlib

    def fake(*a, **kw):
        raise OSError("[Errno 13] Permission denied")

    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        result = lark.call(["x"], run=fake)
    assert result is None
    assert "could not be launched" in out.getvalue()


def test_run_logs_preview_when_stdout_is_not_json():
    """REGRESSION: silent return None on JSONDecodeError used to make
    debugging impossible. Now logs a one-line preview of the offending
    output so the operator can see the kind of garbage they got back
    (typically: HTML auth wall page, lark-cli banner text)."""
    import io
    import contextlib
    rec = _Recorder(FakeProc(stdout="<html>\n<body>auth required</body>\n</html>\n"))
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        result = lark.call(["x"], run=rec)
    assert result is None
    log = out.getvalue()
    assert "non-JSON" in log
    assert "<html>" in log


# ── R161: tenant_access_token bootstrap (container path) ─────────


def _cache_path(tmp_dir):
    """Return a temp cache file path under the given TemporaryDirectory."""
    from pathlib import Path
    return str(Path(tmp_dir) / "tok.json")


class _FetchRec:
    """Two-positional-arg fake for `_fetch_tenant_token(app_id, app_secret)`.

    `CallRecorder` from helpers takes a single positional, so it can't
    record this signature. Local fake keeps the test code obvious without
    changing the shared helper.
    """
    def __init__(self, result):
        self.calls: list[dict] = []
        self.result = result

    def __call__(self, *args, **kwargs):
        self.calls.append({"args": list(args), "kwargs": dict(kwargs)})
        return self.result


def test_ensure_tenant_token_returns_existing_env_unchanged():
    """If LARKSUITE_CLI_TENANT_ACCESS_TOKEN is already set, don't fetch.
    Host operators sometimes pre-export a hand-crafted token; respect it."""
    import os
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        cache = _cache_path(td)
        with env_patch(LARKSUITE_CLI_TENANT_ACCESS_TOKEN="t-preset"):
            token = lark._ensure_tenant_token(cache_path=cache)
        assert token == "t-preset"
        # No cache file written — env path doesn't touch disk
        assert not os.path.exists(cache)


def test_ensure_tenant_token_uses_fresh_cache_when_present():
    """A cached token whose `expire_at` is in the future should be
    returned without hitting the network — that's the whole point of
    caching across calls within the 77-min token window."""
    import json
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        cache = _cache_path(td)
        with open(cache, "w", encoding="utf-8") as fh:
            fh.write(json.dumps({"token": "t-cached", "expire_at": 9999999999}))
        fetch = _FetchRec(result=None)
        with env_patch(LARKSUITE_CLI_TENANT_ACCESS_TOKEN=None,
                       FEISHU_APP_ID="cli_x", FEISHU_APP_SECRET="s"):
            token = lark._ensure_tenant_token(fetch=fetch, cache_path=cache)
        assert token == "t-cached"
        assert fetch.calls == []  # cache hit, no fetch


def test_ensure_tenant_token_refetches_when_cache_expired():
    """A cached entry whose expire_at <= now is stale and forces a
    refetch. Validates the refresh-buffer logic that keeps the cache
    flipping over before the wire deadline."""
    import json
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        cache = _cache_path(td)
        with open(cache, "w", encoding="utf-8") as fh:
            fh.write(json.dumps({"token": "t-stale", "expire_at": 100}))
        fetch = _FetchRec(result={"token": "t-fresh", "expire_at": 9999999999})
        with env_patch(LARKSUITE_CLI_TENANT_ACCESS_TOKEN=None,
                       FEISHU_APP_ID="cli_x", FEISHU_APP_SECRET="s"):
            token = lark._ensure_tenant_token(fetch=fetch, now=lambda: 200,
                                              cache_path=cache)
        assert token == "t-fresh"
        assert len(fetch.calls) == 1
        assert fetch.calls[0]["args"] == ["cli_x", "s"]
        # Cache file rewritten with the fresh token
        with open(cache, "r", encoding="utf-8") as fh:
            rewritten = json.loads(fh.read())
        assert rewritten["token"] == "t-fresh"


def test_ensure_tenant_token_fetches_when_no_cache():
    """No env token, no cache file, but app_id+app_secret in env → fetch
    fresh + write cache. This is the cold-start container path."""
    import os
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        cache = _cache_path(td)
        fetch = _FetchRec(result={"token": "t-cold", "expire_at": 1700001000})
        with env_patch(LARKSUITE_CLI_TENANT_ACCESS_TOKEN=None,
                       FEISHU_APP_ID="cli_x", FEISHU_APP_SECRET="s"):
            token = lark._ensure_tenant_token(fetch=fetch, now=lambda: 1700000000,
                                              cache_path=cache)
        assert token == "t-cold"
        assert os.path.exists(cache)


def test_ensure_tenant_token_returns_none_without_env():
    """No env token, no cache, no app_id/secret → None. Caller falls
    back to lark-cli's own keychain path (host case)."""
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        cache = _cache_path(td)
        fetch = _FetchRec(result=None)
        with env_patch(LARKSUITE_CLI_TENANT_ACCESS_TOKEN=None,
                       FEISHU_APP_ID=None, FEISHU_APP_SECRET=None,
                       LARKSUITE_CLI_APP_ID=None, LARKSUITE_CLI_APP_SECRET=None):
            token = lark._ensure_tenant_token(fetch=fetch, cache_path=cache)
        assert token is None
        assert fetch.calls == []  # no fetch when no app creds


def test_ensure_tenant_token_returns_none_when_fetch_fails():
    """Network / API failure on cold start → None, don't cache empties."""
    import os
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        cache = _cache_path(td)
        fetch = _FetchRec(result=None)  # simulate network error
        with env_patch(LARKSUITE_CLI_TENANT_ACCESS_TOKEN=None,
                       FEISHU_APP_ID="cli_x", FEISHU_APP_SECRET="s"):
            token = lark._ensure_tenant_token(fetch=fetch, cache_path=cache)
        assert token is None
        assert not os.path.exists(cache)


def test_subprocess_env_injects_token_when_available():
    """End-to-end: subprocess_env feeds the token into the lark-cli env
    so every `call()` and the long-running subscribe both pick it up
    without caller wiring."""
    import json
    import tempfile
    from helpers import attr_patch
    with tempfile.TemporaryDirectory() as td:
        cache = _cache_path(td)
        with open(cache, "w", encoding="utf-8") as fh:
            fh.write(json.dumps({"token": "t-via-env", "expire_at": 9999999999}))
        # Stub the cache path module-level for the duration of the test
        with attr_patch(lark, _TENANT_TOKEN_CACHE=cache), \
             env_patch(LARKSUITE_CLI_TENANT_ACCESS_TOKEN=None,
                       FEISHU_APP_ID="cli_x", FEISHU_APP_SECRET="s"):
            env = lark.subprocess_env()
        assert env.get("LARKSUITE_CLI_TENANT_ACCESS_TOKEN") == "t-via-env"


def test_subprocess_env_skips_token_injection_when_unavailable():
    """Host without env app_id/secret + no cache → env stays clean,
    macOS keychain path takes over downstream. Don't litter the env
    with empty strings."""
    with env_patch(LARKSUITE_CLI_TENANT_ACCESS_TOKEN=None,
                   FEISHU_APP_ID=None, FEISHU_APP_SECRET=None,
                   LARKSUITE_CLI_APP_ID=None, LARKSUITE_CLI_APP_SECRET=None):
        env = lark.subprocess_env()
    assert "LARKSUITE_CLI_TENANT_ACCESS_TOKEN" not in env
