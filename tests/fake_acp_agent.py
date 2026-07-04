"""Scriptable fake ACP agent for tests — speaks newline-delimited JSON-RPC
2.0 over stdio, the same wire shape as claude-code-acp / codex-acp.

Spawn it as a real subprocess to exercise `runtime/acp.AcpClient` and
`runtime/acp_host` end-to-end without a real LLM:

    python3 tests/fake_acp_agent.py

Behavior is scripted via env vars so tests stay declarative:

    FAKE_ACP_PROMPT_LOG      append each received prompt text to this file
                             (one line per turn) so tests can assert delivery
    FAKE_ACP_REPLY_PREFIX    agent_message_chunk text prefix (default "echo: ")
    FAKE_ACP_TURN_DELAY_S    seconds to stall inside each turn (default 0);
                             gives session/cancel a window to land
    FAKE_ACP_ASK_PERMISSION  "1" → issue a session/request_permission request
                             mid-turn and echo the chosen optionId
    FAKE_ACP_DIE_AFTER       exit(1) hard after N completed turns (respawn tests)
    FAKE_ACP_LOAD_SESSION    "1" → advertise loadSession capability and accept
                             session/load

Single reader loop + per-prompt worker thread: cancel notifications must be
readable while a turn is in flight.
"""
from __future__ import annotations

import json
import os
import sys
import threading
import time


def _send(obj: dict) -> None:
    sys.stdout.write(json.dumps(obj) + "\n")
    sys.stdout.flush()


def _result(rid, result: dict) -> None:
    _send({"jsonrpc": "2.0", "id": rid, "result": result})


def _notify(method: str, params: dict) -> None:
    _send({"jsonrpc": "2.0", "method": method, "params": params})


class _Turn:
    """One in-flight session/prompt. `cancelled` is flipped by the reader
    thread when session/cancel arrives; the worker polls it."""

    def __init__(self, rid, session_id: str, text: str):
        self.rid = rid
        self.session_id = session_id
        self.text = text
        self.cancelled = threading.Event()
        self.perm_answered = threading.Event()


class FakeAgent:
    def __init__(self):
        self.sessions: set[str] = set()
        self.turns_done = 0
        self.seq = 0
        self.current_turn: _Turn | None = None
        self.pending_permission: dict = {}  # our request id → turn
        self._lock = threading.Lock()

    # ── request handlers ──────────────────────────────────────────

    def handle(self, msg: dict) -> None:
        method = msg.get("method")
        rid = msg.get("id")
        if method is None and rid is not None:
            self._on_response(msg)
            return
        params = msg.get("params") or {}
        if method == "initialize":
            _result(rid, {
                "protocolVersion": 1,
                "agentCapabilities": {
                    "loadSession": os.environ.get("FAKE_ACP_LOAD_SESSION") == "1",
                },
                "authMethods": [],
            })
        elif method == "session/new":
            self.seq += 1
            sid = f"sess-{self.seq}"
            self.sessions.add(sid)
            _result(rid, {"sessionId": sid})
        elif method == "session/load":
            sid = params.get("sessionId", "")
            self.sessions.add(sid)
            _result(rid, {})
        elif method == "session/prompt":
            text = "".join(b.get("text", "") for b in params.get("prompt", [])
                           if b.get("type") == "text")
            turn = _Turn(rid, params.get("sessionId", ""), text)
            with self._lock:
                self.current_turn = turn
            threading.Thread(target=self._run_turn, args=(turn,),
                             daemon=True).start()
        elif method == "session/cancel":
            with self._lock:
                turn = self.current_turn
            if turn is not None:
                turn.cancelled.set()
        elif rid is not None:
            _send({"jsonrpc": "2.0", "id": rid,
                   "error": {"code": -32601, "message": f"no such method: {method}"}})

    def _on_response(self, msg: dict) -> None:
        """Response to a request WE issued (session/request_permission)."""
        turn = self.pending_permission.pop(msg.get("id"), None)
        if turn is None:
            return
        outcome = (msg.get("result") or {}).get("outcome") or {}
        _notify("session/update", {
            "sessionId": turn.session_id,
            "update": {"sessionUpdate": "agent_message_chunk",
                       "content": {"type": "text",
                                   "text": f"[perm:{outcome.get('optionId', outcome.get('outcome', '?'))}]"}},
        })
        turn.perm_answered.set()

    # ── one turn ──────────────────────────────────────────────────

    def _run_turn(self, turn: _Turn) -> None:
        log = os.environ.get("FAKE_ACP_PROMPT_LOG")
        if log:
            with open(log, "a", encoding="utf-8") as fh:
                fh.write(turn.text.replace("\n", "\\n") + "\n")
        if os.environ.get("FAKE_ACP_ASK_PERMISSION") == "1":
            self.seq += 1
            rid = f"perm-{self.seq}"
            self.pending_permission[rid] = turn
            _send({"jsonrpc": "2.0", "id": rid,
                   "method": "session/request_permission",
                   "params": {"sessionId": turn.session_id,
                              "toolCall": {"toolCallId": "tc-1", "title": "rm -rf /fake"},
                              "options": [
                                  {"optionId": "allow", "name": "Allow", "kind": "allow_once"},
                                  {"optionId": "always", "name": "Always", "kind": "allow_always"},
                                  {"optionId": "no", "name": "Reject", "kind": "reject_once"},
                              ]}})
            # Block the turn on the client's answer (a real agent can't
            # proceed with the tool call until permission resolves).
            turn.perm_answered.wait(10)
        delay = float(os.environ.get("FAKE_ACP_TURN_DELAY_S", "0") or 0)
        deadline = time.monotonic() + delay
        while time.monotonic() < deadline:
            if turn.cancelled.wait(0.05):
                break
        if turn.cancelled.is_set():
            _result(turn.rid, {"stopReason": "cancelled"})
        else:
            prefix = os.environ.get("FAKE_ACP_REPLY_PREFIX", "echo: ")
            _notify("session/update", {
                "sessionId": turn.session_id,
                "update": {"sessionUpdate": "agent_message_chunk",
                           "content": {"type": "text",
                                       "text": prefix + turn.text}},
            })
            _result(turn.rid, {"stopReason": "end_turn"})
        with self._lock:
            self.current_turn = None
            self.turns_done += 1
            die_after = int(os.environ.get("FAKE_ACP_DIE_AFTER", "0") or 0)
            if die_after and self.turns_done >= die_after:
                os._exit(1)


def main() -> int:
    agent = FakeAgent()
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except ValueError:
            continue
        agent.handle(msg)
    return 0


if __name__ == "__main__":
    sys.exit(main())
