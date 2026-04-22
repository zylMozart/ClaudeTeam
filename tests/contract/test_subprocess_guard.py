from __future__ import annotations

import os
import socket
import subprocess
import urllib.request

import pytest

from safety import (
    ExternalCallBlocked,
    LiveTestNotConfirmed,
    assert_subprocess_allowed,
    require_live_confirmation,
)


@pytest.mark.contract
def test_default_pytest_run_blocks_unpatched_subprocess_calls():
    with pytest.raises(AssertionError, match="Blocked external subprocess call"):
        subprocess.run(["tmux", "list-sessions"], capture_output=True, text=True)


@pytest.mark.contract
def test_default_pytest_run_blocks_python_native_socket_calls():
    with pytest.raises(AssertionError, match="Blocked external network/socket call"):
        socket.create_connection(("example.com", 443), timeout=1)


@pytest.mark.contract
def test_default_pytest_run_blocks_python_native_urlopen_calls():
    with pytest.raises(AssertionError, match="Blocked external network/socket call"):
        urllib.request.urlopen("https://example.com", timeout=1)


@pytest.mark.contract
def test_allow_subprocess_still_blocks_tmux_docker_lark_and_feishu_msg():
    blocked = [
        ["tmux", "list-sessions"],
        ["docker", "ps"],
        ["npx", "@larksuite/cli", "base", "+record-list"],
        ["python3", "scripts/feishu_msg.py", "inbox", "coder"],
        ["bash", "-lc", "tmux list-sessions"],
    ]
    for cmd in blocked:
        with pytest.raises(ExternalCallBlocked, match="Blocked dangerous external command"):
            assert_subprocess_allowed(cmd)


@pytest.mark.contract
def test_allow_subprocess_accepts_explicit_local_allowlist():
    assert_subprocess_allowed(["python3", "-c", "print('ok')"], marker_args=("python3",))
    with pytest.raises(ExternalCallBlocked, match="outside allow_subprocess local allowlist"):
        assert_subprocess_allowed(["bash", "-lc", "echo ok"], marker_args=("python3",))


@pytest.mark.contract
def test_live_marker_requires_environment_confirmation(monkeypatch):
    monkeypatch.delenv("CLAUDETEAM_LIVE_TESTS", raising=False)
    with pytest.raises(LiveTestNotConfirmed, match="CLAUDETEAM_LIVE_TESTS=1"):
        require_live_confirmation()
    monkeypatch.setenv("CLAUDETEAM_LIVE_TESTS", "1")
    require_live_confirmation()
