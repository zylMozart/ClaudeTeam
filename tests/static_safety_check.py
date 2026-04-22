#!/usr/bin/env python3
"""No-pytest safety checks for the ClaudeTeam test harness."""
from __future__ import annotations

import os
import tomllib
from pathlib import Path

from safety import (
    ExternalCallBlocked,
    LiveTestNotConfirmed,
    assert_subprocess_allowed,
    blocked_network_message,
    blocked_subprocess_message,
    require_live_confirmation,
)


ROOT = Path(__file__).resolve().parents[1]


def expect_raises(exc_type, fn, *args, **kwargs):
    try:
        fn(*args, **kwargs)
    except exc_type:
        return
    raise AssertionError(f"expected {exc_type.__name__}: {fn.__name__}")


def check_pyproject_markers():
    data = tomllib.loads((ROOT / "pyproject.toml").read_text())
    markers = "\n".join(data["tool"]["pytest"]["ini_options"]["markers"])
    addopts = data["tool"]["pytest"]["ini_options"]["addopts"]
    assert "unit" in markers
    assert "contract" in markers
    assert "regression" in markers
    assert "integration" in markers
    assert "CLAUDETEAM_LIVE_TESTS=1" in markers
    for marker in ["live_feishu", "tmux", "docker", "manual"]:
        assert marker in markers
    assert "allow_subprocess" in markers and "tmux/docker/lark/feishu_msg" in markers
    assert "not live and not smoke and not live_feishu and not tmux and not docker and not manual" in addopts


def check_subprocess_policy():
    dangerous = [
        ["tmux", "list-sessions"],
        ["docker", "ps"],
        ["npx", "@larksuite/cli", "base", "+record-list"],
        ["python3", "scripts/feishu_msg.py", "inbox", "coder"],
        ["python3", "scripts/feishu_router.py", "--stdin"],
        ["bash", "-lc", "tmux list-sessions"],
    ]
    for cmd in dangerous:
        expect_raises(ExternalCallBlocked, assert_subprocess_allowed, cmd)

    assert_subprocess_allowed(["python3", "-c", "print('local ok')"], marker_args=("python3",))
    expect_raises(
        ExternalCallBlocked,
        assert_subprocess_allowed,
        ["bash", "-lc", "echo ok"],
        marker_args=("python3",),
    )


def check_live_confirmation():
    old = os.environ.get("CLAUDETEAM_LIVE_TESTS")
    try:
        os.environ.pop("CLAUDETEAM_LIVE_TESTS", None)
        expect_raises(LiveTestNotConfirmed, require_live_confirmation)
        os.environ["CLAUDETEAM_LIVE_TESTS"] = "1"
        require_live_confirmation()
    finally:
        if old is None:
            os.environ.pop("CLAUDETEAM_LIVE_TESTS", None)
        else:
            os.environ["CLAUDETEAM_LIVE_TESTS"] = old


def check_failure_messages():
    assert "Blocked external subprocess call" in blocked_subprocess_message(["tmux", "ls"])
    assert "CLAUDETEAM_LIVE_TESTS=1" in blocked_subprocess_message(["tmux", "ls"])
    assert "Blocked external network/socket call" in blocked_network_message("socket.socket")
    assert "CLAUDETEAM_LIVE_TESTS=1" in blocked_network_message("socket.socket")


def check_conftest_static_coverage():
    conftest = (ROOT / "tests" / "conftest.py").read_text()
    for needle in [
        "socket.socket",
        "socket.create_connection",
        "socket.getaddrinfo",
        "urllib.request.urlopen",
        "HTTPConnection",
        "requests.sessions.Session",
        "subprocess.run",
        "subprocess.Popen",
    ]:
        assert needle in conftest, f"missing guard hook in conftest.py: {needle}"


def main():
    check_pyproject_markers()
    check_subprocess_policy()
    check_live_confirmation()
    check_failure_messages()
    check_conftest_static_coverage()
    print("✅ static safety checks passed")


if __name__ == "__main__":
    main()
