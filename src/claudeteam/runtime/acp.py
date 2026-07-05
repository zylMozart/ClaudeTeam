"""Minimal ACP (Agent Client Protocol) client — JSON-RPC 2.0 over the
stdio of an agent subprocess (claude-code-acp / codex-acp / a fake).

Stdlib-only, matching the repo's zero-dependency rule. Newline-delimited
JSON framing (one message per line), the wire shape the Zed-family
adapters speak.

The client side of the protocol we use:

    initialize                → handshake, discover loadSession capability
    session/new | session/load → one persistent session per agent
    session/prompt            → one turn; blocks the CALLING thread until
                                the agent returns {stopReason}; streamed
                                session/update notifications hit
                                `on_update` as they arrive
    session/cancel            → notification; the in-flight prompt then
                                resolves with stopReason="cancelled"

Agent-initiated requests (session/request_permission) are answered by
`permission_handler` — default policy auto-selects the strongest allow
option, matching today's `--dangerously-skip-permissions` posture. The
handler is injectable so a future Feishu-card approval flow can slot in
without touching this module.

Threading: one reader thread owns stdout; `request()` callers park on a
per-request Event. `cancel()` / `notify()` may be called from any thread
(writes are lock-serialised).
"""
from __future__ import annotations

import json
import subprocess
import threading
from typing import Callable


PROTOCOL_VERSION = 1


class AcpError(RuntimeError):
    """JSON-RPC error response, agent death, or request timeout."""


def default_permission_handler(params: dict) -> dict:
    """Auto-approve: prefer allow_always > allow_once; reject only when the
    agent offers no allow option at all."""
    options = params.get("options") or []
    best = None
    for opt in options:
        kind = opt.get("kind", "")
        if kind == "allow_always":
            best = opt
            break
        if kind == "allow_once" and best is None:
            best = opt
    if best is None:
        return {"outcome": {"outcome": "cancelled"}}
    return {"outcome": {"outcome": "selected", "optionId": best.get("optionId")}}


class AcpClient:
    def __init__(self, argv: list[str], *,
                 cwd: str | None = None,
                 env: dict | None = None,
                 on_update: Callable[[dict], None] | None = None,
                 permission_handler: Callable[[dict], dict] | None = None,
                 popen: Callable | None = None):
        self.argv = argv
        self.cwd = cwd
        self.env = env
        self.on_update = on_update
        self.permission_handler = permission_handler or default_permission_handler
        self._popen = popen or subprocess.Popen
        self.proc: subprocess.Popen | None = None
        self._next_id = 0
        self._id_lock = threading.Lock()
        self._write_lock = threading.Lock()
        self._pending: dict = {}          # id → {"event": Event, "result": ..., "error": ...}
        self._pending_lock = threading.Lock()
        self._reader: threading.Thread | None = None
        self.agent_capabilities: dict = {}

    # ── lifecycle ─────────────────────────────────────────────────

    def start(self) -> None:
        self.proc = self._popen(
            self.argv, cwd=self.cwd, env=self.env,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, text=True, bufsize=1,
        )
        self._reader = threading.Thread(target=self._read_loop, daemon=True)
        self._reader.start()

    def alive(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def stop(self, *, timeout_s: float = 3.0) -> None:
        if self.proc is None:
            return
        try:
            self.proc.terminate()
            self.proc.wait(timeout=timeout_s)
        except (OSError, subprocess.TimeoutExpired):
            try:
                self.proc.kill()
            except OSError:
                pass
        self._fail_all_pending("agent process stopped")

    # ── protocol surface ──────────────────────────────────────────

    def initialize(self, *, timeout_s: float = 30.0) -> dict:
        result = self.request("initialize", {
            "protocolVersion": PROTOCOL_VERSION,
            "clientCapabilities": {
                "fs": {"readTextFile": False, "writeTextFile": False},
                "terminal": False,
            },
        }, timeout_s=timeout_s)
        self.agent_capabilities = result.get("agentCapabilities") or {}
        return result

    def supports_load_session(self) -> bool:
        return bool(self.agent_capabilities.get("loadSession"))

    def new_session(self, cwd: str, *, timeout_s: float = 60.0) -> str:
        result = self.request("session/new",
                              {"cwd": cwd, "mcpServers": []},
                              timeout_s=timeout_s)
        sid = result.get("sessionId", "")
        if not sid:
            raise AcpError("session/new returned no sessionId")
        return sid

    def load_session(self, session_id: str, cwd: str, *,
                     timeout_s: float = 60.0) -> None:
        self.request("session/load",
                     {"sessionId": session_id, "cwd": cwd, "mcpServers": []},
                     timeout_s=timeout_s)

    def prompt(self, session_id: str, text: str, *,
               timeout_s: float = 1800.0) -> str:
        """One turn. Returns the stopReason. Long default timeout — a real
        coding turn can legitimately run many minutes; the host's cancel
        path (session/cancel via another thread) is the interactive
        interrupt, not this timeout."""
        result = self.request("session/prompt", {
            "sessionId": session_id,
            "prompt": [{"type": "text", "text": text}],
        }, timeout_s=timeout_s)
        return result.get("stopReason", "")

    def cancel(self, session_id: str) -> None:
        """Fire the session/cancel notification (never blocks on a reply —
        the in-flight prompt resolves with stopReason=cancelled)."""
        self.notify("session/cancel", {"sessionId": session_id})

    # ── JSON-RPC plumbing ─────────────────────────────────────────

    def request(self, method: str, params: dict, *, timeout_s: float) -> dict:
        if not self.alive():
            raise AcpError(f"{method}: agent process not running")
        with self._id_lock:
            self._next_id += 1
            rid = self._next_id
        slot = {"event": threading.Event(), "result": None, "error": None}
        with self._pending_lock:
            self._pending[rid] = slot
        self._write({"jsonrpc": "2.0", "id": rid, "method": method,
                     "params": params})
        if not slot["event"].wait(timeout_s):
            with self._pending_lock:
                self._pending.pop(rid, None)
            raise AcpError(f"{method}: no response within {timeout_s}s")
        if slot["error"] is not None:
            err = slot["error"]
            raise AcpError(f"{method}: {err.get('message', err)}")
        return slot["result"] or {}

    def notify(self, method: str, params: dict) -> None:
        self._write({"jsonrpc": "2.0", "method": method, "params": params})

    def _write(self, obj: dict) -> None:
        proc = self.proc
        if proc is None or proc.stdin is None:
            raise AcpError("agent stdin closed")
        line = json.dumps(obj, ensure_ascii=False)
        with self._write_lock:
            try:
                proc.stdin.write(line + "\n")
                proc.stdin.flush()
            except (OSError, ValueError) as e:
                raise AcpError(f"write to agent failed: {e}")

    def _read_loop(self) -> None:
        proc = self.proc
        if proc is None or proc.stdout is None:
            return
        for line in proc.stdout:
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except ValueError:
                continue  # adapter debug noise on stdout — ignore
            try:
                self._dispatch(msg)
            except Exception:
                # The reader is the only thread that can resolve pending
                # requests — if a dispatch error (e.g. replying to an agent
                # request while its stdin is closing) killed it here, a
                # worker blocked in prompt() would hang the full turn
                # timeout instead of failing fast at stdout-EOF below.
                continue
        self._fail_all_pending("agent stdout closed (process died?)")

    def _dispatch(self, msg: dict) -> None:
        method = msg.get("method")
        if method is None:
            # response to one of our requests
            with self._pending_lock:
                slot = self._pending.pop(msg.get("id"), None)
            if slot is not None:
                slot["result"] = msg.get("result")
                slot["error"] = msg.get("error")
                slot["event"].set()
            return
        if msg.get("id") is not None:
            # agent-initiated request (permission etc.)
            self._handle_agent_request(msg)
            return
        # notification
        if method == "session/update" and self.on_update is not None:
            try:
                self.on_update(msg.get("params") or {})
            except Exception:
                pass  # observer must never kill the reader loop

    def _handle_agent_request(self, msg: dict) -> None:
        method = msg.get("method")
        rid = msg.get("id")
        if method == "session/request_permission":
            try:
                result = self.permission_handler(msg.get("params") or {})
            except Exception:
                result = {"outcome": {"outcome": "cancelled"}}
            try:
                self._write({"jsonrpc": "2.0", "id": rid, "result": result})
            except AcpError:
                pass  # agent dying mid-request; EOF will fail pending turns
            return
        # fs/terminal methods we declined in clientCapabilities, or unknowns
        try:
            self._write({"jsonrpc": "2.0", "id": rid,
                         "error": {"code": -32601,
                                   "message": f"client does not support {method}"}})
        except AcpError:
            pass

    def _fail_all_pending(self, reason: str) -> None:
        with self._pending_lock:
            slots = list(self._pending.values())
            self._pending.clear()
        for slot in slots:
            slot["error"] = {"message": reason}
            slot["event"].set()
