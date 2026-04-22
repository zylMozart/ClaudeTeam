"""Shared pytest safety fixtures for ClaudeTeam.

Default unit/contract/regression tests must not call real Feishu, tmux, docker,
network/socket, or any other external boundary. Tests that need those boundaries
must opt into live/smoke execution and set CLAUDETEAM_LIVE_TESTS=1.
"""
from __future__ import annotations

import http.client
import os
import socket
import subprocess
import sys
import urllib.request
from pathlib import Path

import pytest

from safety import (
    ExternalCallBlocked,
    LIVE_ENV_VAR,
    assert_subprocess_allowed,
    blocked_network_message,
    blocked_subprocess_message,
    require_live_confirmation,
)

LIVE_LIKE_MARKERS = ("live", "smoke", "live_feishu", "tmux", "docker", "manual")


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = PROJECT_ROOT / "scripts"

if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))


def _format_cmd(args, kwargs) -> str:
    cmd = args[0] if args else kwargs.get("args", "<missing command>")
    return cmd


@pytest.fixture(autouse=True)
def enforce_no_live_boundaries(monkeypatch, request):
    """Block live boundaries in default tests.

    This is intentionally broad. Most ClaudeTeam live boundaries eventually go
    through subprocess: lark-cli for Feishu, tmux, docker, shell bridges, and
    daemon launchers. Python-native network paths go through socket/urllib or
    requests. Unit tests should replace those call sites with fakes.
    """
    if any(request.node.get_closest_marker(name) for name in LIVE_LIKE_MARKERS):
        require_live_confirmation()
        return

    allow_subprocess = request.node.get_closest_marker("allow_subprocess")

    original_run = subprocess.run
    original_popen = subprocess.Popen
    original_call = subprocess.call
    original_check_call = subprocess.check_call
    original_check_output = subprocess.check_output

    def guarded_subprocess(original):
        def wrapper(*args, **kwargs):
            cmd = _format_cmd(args, kwargs)
            if allow_subprocess:
                assert_subprocess_allowed(cmd, marker_args=allow_subprocess.args)
                return original(*args, **kwargs)
            raise ExternalCallBlocked(blocked_subprocess_message(cmd))
        return wrapper

    monkeypatch.setattr(subprocess, "run", guarded_subprocess(original_run))
    monkeypatch.setattr(subprocess, "Popen", guarded_subprocess(original_popen))
    monkeypatch.setattr(subprocess, "call", guarded_subprocess(original_call))
    monkeypatch.setattr(subprocess, "check_call", guarded_subprocess(original_check_call))
    monkeypatch.setattr(subprocess, "check_output", guarded_subprocess(original_check_output))

    def blocked_socket(*args, **kwargs):
        raise ExternalCallBlocked(blocked_network_message("socket.socket"))

    def blocked_create_connection(*args, **kwargs):
        raise ExternalCallBlocked(blocked_network_message("socket.create_connection"))

    def blocked_getaddrinfo(*args, **kwargs):
        raise ExternalCallBlocked(blocked_network_message("socket.getaddrinfo"))

    def blocked_urlopen(*args, **kwargs):
        raise ExternalCallBlocked(blocked_network_message("urllib.request.urlopen"))

    def blocked_http_connect(self, *args, **kwargs):
        raise ExternalCallBlocked(blocked_network_message("http.client.HTTPConnection.connect"))

    monkeypatch.setattr(socket, "socket", blocked_socket)
    monkeypatch.setattr(socket, "create_connection", blocked_create_connection)
    monkeypatch.setattr(socket, "getaddrinfo", blocked_getaddrinfo)
    monkeypatch.setattr(urllib.request, "urlopen", blocked_urlopen)
    monkeypatch.setattr(http.client.HTTPConnection, "connect", blocked_http_connect)
    monkeypatch.setattr(http.client.HTTPSConnection, "connect", blocked_http_connect)

    try:
        import requests
    except ImportError:
        requests = None
    if requests is not None:
        def blocked_requests(self, method, url, *args, **kwargs):
            raise ExternalCallBlocked(blocked_network_message(f"requests {method} {url}"))

        monkeypatch.setattr(requests.sessions.Session, "request", blocked_requests)


def pytest_collection_modifyitems(config, items):
    """Require an env confirmation before any selected live/smoke test runs."""
    if os.environ.get(LIVE_ENV_VAR) == "1":
        return
    for item in items:
        if any(item.get_closest_marker(name) for name in LIVE_LIKE_MARKERS):
            item.add_marker(pytest.mark.skip(
                reason=f"live-like tests require {LIVE_ENV_VAR}=1"
            ))


@pytest.fixture
def assert_external_subprocess_blocked():
    def _assert(callable_obj, *args, **kwargs):
        with pytest.raises(ExternalCallBlocked, match="Blocked external subprocess call"):
            callable_obj(*args, **kwargs)
    return _assert


@pytest.fixture
def assert_external_network_blocked():
    def _assert(callable_obj, *args, **kwargs):
        with pytest.raises(ExternalCallBlocked, match="Blocked external network/socket call"):
            callable_obj(*args, **kwargs)
    return _assert


@pytest.fixture
def temp_chdir(tmp_path, monkeypatch):
    """Run a test from an isolated directory when cwd-sensitive code is involved."""
    old_cwd = Path.cwd()
    monkeypatch.chdir(tmp_path)
    try:
        yield tmp_path
    finally:
        os.chdir(old_cwd)
