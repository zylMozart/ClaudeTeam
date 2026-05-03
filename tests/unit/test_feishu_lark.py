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


def test_run_builds_npx_lark_cli_argv_with_profile():
    rec = _Recorder(FakeProc(stdout='{"ok": true, "data": {"x": 1}}'))
    out = lark.call(["im", "+messages-send"], profile="my-team", run=rec)
    assert out == {"x": 1}
    sent = rec.calls[0]["args"]
    assert sent[:2] == ["npx", "@larksuite/cli"]
    assert "--profile" in sent and "my-team" in sent
    assert sent[-2:] == ["im", "+messages-send"]


def test_run_omits_profile_when_empty():
    rec = _Recorder(FakeProc(stdout='{"data":{}}'))
    lark.call(["foo"], profile="", run=rec)
    sent = rec.calls[0]["args"]
    assert "--profile" not in sent


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
