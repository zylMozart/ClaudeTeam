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


def test_run_returns_none_on_invalid_json():
    rec = _Recorder(FakeProc(stdout="not-json"))
    assert lark.call(["x"], run=rec) is None


def test_run_returns_none_on_timeout():
    def fake(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd=["lark"], timeout=90)
    assert lark.call(["x"], run=fake) is None


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
