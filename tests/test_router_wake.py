#!/usr/bin/env python3
"""No-live tests for router wake process-tree detection."""
from __future__ import annotations

import builtins
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from claudeteam.messaging.router import wake


class Patch:
    def __init__(self, obj, **items):
        self.obj = obj
        self.items = items
        self.old = {}

    def __enter__(self):
        for key, value in self.items.items():
            self.old[key] = getattr(self.obj, key)
            setattr(self.obj, key, value)

    def __exit__(self, exc_type, exc, tb):
        for key, value in self.old.items():
            setattr(self.obj, key, value)


class FakeFile:
    def __init__(self, text):
        self.text = text

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self):
        return self.text


def test_cli_pids_in_pane_finds_grandchild_cli_process():
    files = {
        "/proc/101/status": "Name:\tsh\nPPid:\t100\n",
        "/proc/101/comm": "sh\n",
        "/proc/202/status": "Name:\tclaude\nPPid:\t101\n",
        "/proc/202/comm": "claude\n",
        "/proc/303/status": "Name:\tother\nPPid:\t1\n",
        "/proc/303/comm": "claude\n",
    }

    def fake_open(path, *args, **kwargs):
        if path not in files:
            raise OSError(path)
        return FakeFile(files[path])

    with Patch(wake, _pane_bash_pid=lambda agent, session: 100):
        with Patch(wake.glob, glob=lambda pattern: ["/proc/101", "/proc/202", "/proc/303"]):
            with Patch(builtins, open=fake_open):
                result = wake.cli_pids_in_pane("manager", "sess", get_process_name=lambda _: "claude")

    assert result == [202]


def test_parse_ppid_handles_missing_or_invalid_status():
    assert wake._parse_ppid("Name:\tx\nPPid:\t42\n") == 42
    assert wake._parse_ppid("Name:\tx\n") is None
    assert wake._parse_ppid("PPid:\tnope\n") is None


def main() -> int:
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    passed = failed = 0
    for fn in tests:
        try:
            fn()
            print(f"  ok {fn.__name__}")
            passed += 1
        except Exception as exc:
            print(f"  fail {fn.__name__}: {exc}")
            failed += 1
    print(f"\nrouter wake tests: {passed}/{passed + failed} passed")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
