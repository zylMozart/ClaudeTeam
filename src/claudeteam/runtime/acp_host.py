"""AcpHost — the service that owns every ACP agent's subprocess.

Lives inside the router daemon (`claudeteam router` starts one on boot).
Producers anywhere on the host enqueue prompt rows via `store/acp_queue`;
this service is the single consumer:

    per ACP agent, one worker thread:
        recover_stuck() once            ← re-arm rows a dead host left in-flight
        loop: claim prompt row → ensure subprocess + session →
              session/prompt (streamed updates → transcript + heartbeat) →
              settle(done|failed, stopReason)  ← the ACK
    plus one host-level control thread:
        scan every agent's queue for cancel/stop rows each tick and act
        immediately (a cancel must land WHILE the worker is blocked in
        its turn — that's the whole point)

State it maintains per agent (under `$STATE_DIR/acp/<agent>/`):
    queue.json      the delivery state machine (store/acp_queue)
    session.json    ACP sessionId — reused via session/load when the
                    adapter supports it, so a router restart keeps context
    transcript.log  streamed agent output; the tmux viewer pane tails this
    agent.pid       subprocess pid — lets a fresh host reap a stale
                    adapter process orphaned by a SIGKILL'd predecessor

Status side-effects (this is T2's heartbeat landing):
    turn start → upsert_status(进行中, <prompt preview>)
    turn end   → upsert_status(待命, stopReason)
    every streamed update → touch_heartbeat(agent)
"""
from __future__ import annotations

import json
import os
import signal
import threading
import time
from pathlib import Path
from typing import Callable

from claudeteam.agents import adapter_for_agent
from claudeteam.runtime import config, paths
from claudeteam.runtime.acp import AcpClient, AcpError
from claudeteam.store import acp_queue, local_facts
from claudeteam.util import now_ms, read_json, write_json


def _session_file(agent: str) -> Path:
    return paths.acp_agent_dir(agent) / "session.json"


def transcript_file(agent: str) -> Path:
    return paths.acp_agent_dir(agent) / "transcript.log"


def _pid_file(agent: str) -> Path:
    return paths.acp_agent_dir(agent) / "agent.pid"


def _append_transcript(agent: str, text: str) -> None:
    path = transcript_file(agent)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(text)
    except OSError:
        pass  # observability is best-effort, never blocks a turn


def _reap_stale_agent(agent: str, expected_argv0: str) -> None:
    """SIGTERM a previous host's adapter process if its pid file still
    points at a live process running the expected binary. Defends against
    a SIGKILL'd router leaving claude-code-acp orphans that would race the
    fresh one for the same session."""
    from claudeteam.runtime import pidlock, watchdog
    pid = pidlock.read_pid(_pid_file(agent))
    if pid is None or not pidlock.pid_alive(pid):
        return
    if expected_argv0 not in watchdog._read_cmdline(pid):
        return  # pid reused by an unrelated process — leave it alone
    try:
        os.kill(pid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        pass


def _queue_stat(agent: str):
    """(st_mtime_ns, st_size) of the agent's queue file, or None. The
    pollers remember the last value and skip flock+parse while unchanged —
    an idle team then costs os.stat instead of lock traffic that contends
    with producers."""
    try:
        st = os.stat(acp_queue.queue_path(agent))
        return (st.st_mtime_ns, st.st_size)
    except OSError:
        return None


class AgentWorker:
    """One ACP agent: subprocess lifecycle + queue consumption."""

    def __init__(self, agent: str, *,
                 popen: Callable | None = None,
                 adapter=None,
                 log: Callable = print):
        self.agent = agent
        self.log = log
        self._popen = popen
        self._adapter = adapter          # injectable for tests
        self.client: AcpClient | None = None
        self.session_id: str = ""
        self.stop_event = threading.Event()
        self.thread: threading.Thread | None = None
        self._turn_lock = threading.Lock()   # guards client/session swap vs cancel

    # ── spawn env (mirrors lifecycle.build_spawn_command, dict form) ──

    def _build_env(self, adapter, model: str) -> dict:
        from claudeteam.runtime import agent_auth
        from claudeteam.runtime.lifecycle import _PROPAGATED_ENV
        from claudeteam.util import env_str
        env = dict(os.environ)
        env["CLAUDETEAM_STATE_DIR"] = str(paths.state_dir())
        for var in _PROPAGATED_ENV:
            val = env_str(var)
            if val:
                env[var] = val
        res = agent_auth.resolve(self.agent, adapter)
        for k in res.blank_env:
            env.pop(k, None)
        env.update(res.set_env)
        env.update(adapter.acp_env(self.agent, model))
        return env

    # ── subprocess + session ──────────────────────────────────────

    def _on_update(self, params: dict) -> None:
        update = params.get("update") or {}
        kind = update.get("sessionUpdate", "")
        if kind in ("agent_message_chunk", "agent_thought_chunk"):
            text = (update.get("content") or {}).get("text", "")
            if text:
                _append_transcript(self.agent, text)
        elif kind == "tool_call":
            title = update.get("title") or update.get("toolCallId", "")
            _append_transcript(self.agent, f"\n[tool] {title}\n")
        local_facts.touch_heartbeat(self.agent)

    def ensure_session(self) -> bool:
        """Subprocess up + one live session. Returns False when the agent
        can't come up (missing adapter binary etc.) — caller backs off."""
        if self.client is not None and self.client.alive() and self.session_id:
            return True
        adapter = self._adapter or adapter_for_agent(self.agent)
        model = config.agent_model(self.agent)
        argv = adapter.acp_argv(self.agent, model)
        if not argv:
            self.log(f"  ⚠️ {self.agent}: cli has no ACP adapter but runner=acp")
            return False
        _reap_stale_agent(self.agent, argv[0])
        # per-agent HOME must exist before the adapter boots (claude seeds
        # credentials there — reuse the tmux path's provisioning).
        if self._adapter is None:  # skip host-dotfile seeding under test fakes
            try:
                from claudeteam.runtime.lifecycle import _ensure_agent_home
                _ensure_agent_home(self.agent, config.agent_cli(self.agent))
            except Exception:
                pass
        client = AcpClient(argv, cwd=str(Path.cwd()),
                           env=self._build_env(adapter, model),
                           on_update=self._on_update,
                           popen=self._popen)
        try:
            client.start()
        except (OSError, ValueError) as e:
            self.log(f"  ⚠️ {self.agent}: ACP adapter spawn failed: {e} "
                     f"(is `{argv[0]}` installed? npm i -g)")
            return False
        try:
            client.initialize(timeout_s=60)
        except AcpError as e:
            self.log(f"  ⚠️ {self.agent}: ACP initialize failed: {e}")
            client.stop()
            return False
        if client.proc is not None:
            try:
                _pid_file(self.agent).parent.mkdir(parents=True, exist_ok=True)
                _pid_file(self.agent).write_text(str(client.proc.pid))
            except OSError:
                pass

        cwd = str(Path.cwd())
        saved = read_json(_session_file(self.agent), {})
        sid = ""
        if saved.get("session_id") and client.supports_load_session():
            try:
                client.load_session(saved["session_id"], cwd)
                sid = saved["session_id"]
                self.log(f"  ♻️  {self.agent}: resumed ACP session {sid[:12]}…")
            except AcpError:
                sid = ""  # stale session — fall through to a fresh one
        fresh = not sid
        if fresh:
            try:
                sid = client.new_session(cwd)
            except AcpError as e:
                self.log(f"  ⚠️ {self.agent}: session/new failed: {e}")
                client.stop()
                return False
        # Identity turn BEFORE the session is persisted or published: if it
        # fails, this session must not survive — a saved-but-identity-less
        # session would be resumed forever via session/load (fresh=False)
        # and the agent would permanently not know the `claudeteam say`
        # protocol. Fail the whole ensure_session instead; the row requeues
        # and the next attempt starts clean.
        if fresh and not self._identity_turn(client, sid):
            client.stop()
            return False
        with self._turn_lock:
            self.client = client
            self.session_id = sid
        write_json(_session_file(self.agent),
                   {"session_id": sid, "cwd": cwd, "updated_at": now_ms()})
        return True

    def _identity_turn(self, client: AcpClient, sid: str) -> bool:
        """Turn 0 on every FRESH session: feed the identity init prompt so
        the agent knows who it is before real messages arrive. (A loaded
        session already carries its identity in-context.) Returns False on
        failure — the caller must then discard the session."""
        from claudeteam.agents import identity
        from claudeteam.runtime import tunables
        _append_transcript(self.agent, f"\n===== identity init {_ts()} =====\n")
        try:
            client.prompt(sid, identity.init_prompt(self.agent),
                          timeout_s=float(tunables.tunable(
                              "acp.init_timeout_s", 600.0)))
            return True
        except AcpError as e:
            self.log(f"  ⚠️ {self.agent}: identity init turn failed: {e}")
            return False

    # ── the consume loop ──────────────────────────────────────────

    def run(self) -> None:
        rearmed = acp_queue.recover_stuck(self.agent)
        if rearmed:
            self.log(f"  ♻️  {self.agent}: re-armed {len(rearmed)} in-flight "
                     f"row(s) from a previous host")
        from claudeteam.runtime import tunables
        poll_s = float(tunables.tunable("acp.queue_poll_s", 0.5))
        backoff_s = float(tunables.tunable("acp.spawn_backoff_s", 15.0))
        last_stat = object()   # sentinel ≠ any real stat → first pass parses
        while not self.stop_event.is_set():
            if is_paused() or local_facts.is_retired(self.agent):
                self._teardown_client()
                self.stop_event.wait(poll_s * 4)
                continue
            # Fast path: queue file unchanged since the last empty claim →
            # skip the flock+parse entirely.
            cur_stat = _queue_stat(self.agent)
            if cur_stat == last_stat:
                self.stop_event.wait(poll_s)
                continue
            row = acp_queue.claim_next(self.agent)
            if row is None:
                last_stat = cur_stat
                self.stop_event.wait(poll_s)
                continue
            last_stat = object()  # we mutate the file below — force re-check
            # A claimed row must ALWAYS be settled or re-armed — an
            # unexpected exception killing this thread would otherwise
            # strand it PROMPTING and wedge the agent until router restart.
            try:
                if not self.ensure_session():
                    self._park_or_requeue(row, "agent could not start")
                    self.stop_event.wait(backoff_s)
                    continue
                self._run_turn(row)
            except Exception as e:
                self.log(f"  ⚠️ {self.agent}: worker error: {e}")
                self._teardown_client()
                self._park_or_requeue(row, f"worker error: {e}")
                self.stop_event.wait(backoff_s)
        self._teardown_client()

    def _park_or_requeue(self, row: dict, reason: str) -> None:
        """Re-arm a claimed row, or after MAX_ATTEMPTS park it FAILED with a
        visible 阻塞 status — an unstartable agent (missing adapter binary,
        bad auth) must surface to /team instead of ping-ponging its rows
        pending↔prompting forever with a green delivery receipt."""
        if int(row.get("attempts", 0)) >= acp_queue.MAX_ATTEMPTS:
            acp_queue.settle(self.agent, row["qid"], acp_queue.FAILED,
                             error=reason)
            local_facts.append_log(self.agent, "acp_turn",
                                   f"failed: {reason}", ref=row["qid"])
            local_facts.upsert_status(self.agent, "阻塞", reason[:80],
                                      blocker=reason[:120])
        else:
            acp_queue.requeue(self.agent, row["qid"], error=reason)

    def _run_turn(self, row: dict) -> None:
        # Snapshot under the lock — the control thread's stop/recycle can
        # _teardown_client() between ensure_session() and here; a torn read
        # would crash the worker on client=None.
        with self._turn_lock:
            client, sid = self.client, self.session_id
        if client is None or not sid:
            acp_queue.requeue(self.agent, row["qid"],
                              error="client torn down before turn")
            return
        preview = row["text"].strip().splitlines()[0][:60] if row["text"].strip() else "(empty)"
        local_facts.upsert_status(self.agent, "进行中", preview)
        _append_transcript(
            self.agent,
            f"\n===== turn {_ts()} · from {row.get('sender') or 'user'} "
            f"· {row['qid']} =====\n")
        from claudeteam.runtime import tunables
        try:
            stop = client.prompt(sid, row["text"],
                                 timeout_s=float(tunables.tunable(
                                     "acp.turn_timeout_s", 1800.0)))
        except AcpError as e:
            self._teardown_client()  # process likely dead — respawn next round
            if int(row.get("attempts", 0)) >= acp_queue.MAX_ATTEMPTS:
                acp_queue.settle(self.agent, row["qid"], acp_queue.FAILED,
                                 error=str(e))
                local_facts.append_log(self.agent, "acp_turn",
                                       f"failed: {e}", ref=row["qid"])
                local_facts.upsert_status(self.agent, "阻塞",
                                          f"ACP turn failed: {e}",
                                          blocker=str(e)[:120])
            else:
                acp_queue.requeue(self.agent, row["qid"], error=str(e))
                self.log(f"  ⚠️ {self.agent}: turn error ({e}); row re-armed")
            return
        acp_queue.settle(self.agent, row["qid"], acp_queue.DONE,
                         stop_reason=stop)
        local_facts.append_log(self.agent, "acp_turn",
                               f"stopReason={stop}", ref=row["qid"])
        local_facts.touch_heartbeat(self.agent)
        local_facts.upsert_status(self.agent, "待命", f"turn done ({stop})")

    # ── control ops (called from the host control thread) ─────────

    def handle_control(self, row: dict) -> None:
        kind = row.get("kind")
        with self._turn_lock:
            client, sid = self.client, self.session_id
        if kind == "cancel":
            if client is not None and client.alive() and sid:
                try:
                    client.cancel(sid)
                    self.log(f"  🛑 {self.agent}: session/cancel sent")
                except AcpError as e:
                    self.log(f"  ⚠️ {self.agent}: cancel failed: {e}")
        elif kind == "stop":
            self._teardown_client()
            self.log(f"  ⏹  {self.agent}: ACP subprocess stopped")

    def _teardown_client(self) -> None:
        with self._turn_lock:
            client, self.client, self.session_id = self.client, None, ""
        if client is not None:
            client.stop()

    # ── thread management ─────────────────────────────────────────

    def start(self) -> None:
        self.thread = threading.Thread(target=self.run, daemon=True,
                                       name=f"acp-{self.agent}")
        self.thread.start()

    def stop(self, *, timeout_s: float = 5.0) -> None:
        self.stop_event.set()
        if self.thread is not None:
            self.thread.join(timeout=timeout_s)
        self._teardown_client()


def _ts() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def _paused_file() -> Path:
    return paths.state_dir() / "acp" / "paused"


def is_paused() -> bool:
    """True while /shutdown has the ACP fleet dormant. Workers see this and
    tear down instead of consuming; sessions stay on disk so /restart
    resumes context via session/load."""
    return _paused_file().exists()


def pause_all() -> bool:
    """`/shutdown` for ACP agents: without this, killing the tmux session
    only removes the viewer panes — the real agents live in the router and
    would keep consuming queues while the operator believes the team is
    offline."""
    try:
        _paused_file().parent.mkdir(parents=True, exist_ok=True)
        _paused_file().touch()
        return True
    except OSError:
        return False


def resume_all() -> bool:
    try:
        _paused_file().unlink(missing_ok=True)
        return True
    except OSError:
        return False


def request_cancel(agent: str) -> bool:
    """Interrupt `agent`'s in-flight turn: durable cancel row, consumed by
    the host control thread which fires session/cancel. The client-side op
    behind `/stop` for ACP agents."""
    try:
        acp_queue.enqueue(agent, "", kind="cancel")
        return True
    except OSError:
        return False


def recycle(agent: str) -> bool:
    """Tear down `agent`'s ACP subprocess AND invalidate its saved session,
    so the next message opens a fresh session (new roster config takes
    effect + identity turn re-runs). The shared client-side op behind
    `restart`, `fire`, and `/clear` for ACP agents."""
    try:
        acp_queue.enqueue(agent, "", kind="stop")
        _session_file(agent).unlink(missing_ok=True)
        return True
    except OSError:
        return False


def probe(agent: str) -> str:
    """Disk-readable state probe for an ACP agent — same state constants as
    `pane_probe` so /team and `claudeteam team` render both runners with one
    glyph map. No screen scraping: the queue + pid file ARE the state.

      busy  — a PROMPT turn is in flight or queued, with a live subprocess
      dead  — prompt work exists (in flight or queued) but the subprocess
              is down (router died / SIGKILL'd mid-turn)
      idle  — no prompt work; subprocess up or will spawn on demand

    Control rows (cancel/stop) are NOT work — an out-of-router `restart`
    leaves a pending stop row behind, and counting it flagged perfectly
    healthy idle agents DEAD. One queue read serves both facts.
    """
    from claudeteam.runtime import pane_probe, pidlock
    all_rows = acp_queue.rows(agent)
    prompts = [r for r in all_rows if r.get("kind") == "prompt"]
    inflight = any(r.get("state") == acp_queue.PROMPTING for r in prompts)
    pending = any(r.get("state") == acp_queue.PENDING for r in prompts)
    if not inflight and not pending:
        return pane_probe.IDLE
    pid = pidlock.read_pid(_pid_file(agent))
    alive = pid is not None and pidlock.pid_alive(pid)
    return pane_probe.BUSY if alive else pane_probe.DEAD


def _acp_roster() -> list[str]:
    return [a for a in config.agent_names() if config.agent_runner(a) == "acp"]


class AcpHost:
    """All ACP workers + the control-scan thread. One per router daemon.

    The worker set follows the LIVE roster (runtime/CLAUDE.md's no-config-
    caching rule): `claudeteam hire` or a toml `runner` flip while the
    router runs gets a worker within one roster-refresh tick — producers
    already resolve `agent_runner()` live, so a boot-frozen consumer set
    meant hired-later agents queued messages nobody ever claimed."""

    def __init__(self, agents: list[str] | None = None, *,
                 popen: Callable | None = None, log: Callable = print):
        self._pinned_agents = agents      # tests pin a fixed set; prod follows toml
        self._popen = popen
        self.workers: dict[str, AgentWorker] = {}
        self.log = log
        self._stop = threading.Event()
        self._control_thread: threading.Thread | None = None

    def _sync_workers(self) -> None:
        want = set(self._pinned_agents if self._pinned_agents is not None
                   else _acp_roster())
        for agent in sorted(want - set(self.workers)):
            self.log(f"🔌 AcpHost: worker up for {agent}")
            w = AgentWorker(agent, popen=self._popen, log=self.log)
            self.workers[agent] = w
            w.start()
        for agent in sorted(set(self.workers) - want):
            self.log(f"🔌 AcpHost: worker down for {agent} (left roster)")
            self.workers.pop(agent).stop()

    def start(self) -> None:
        self._sync_workers()
        if self.workers:
            self.log(f"🔌 AcpHost: {len(self.workers)} ACP agent(s): "
                     f"{', '.join(sorted(self.workers))}")
        # Control thread runs even with zero workers at boot — it is also
        # the roster watcher, so an agent hired later still gets a worker.
        self._control_thread = threading.Thread(
            target=self._control_loop, daemon=True, name="acp-control")
        self._control_thread.start()

    def _control_loop(self) -> None:
        from claudeteam.runtime import tunables
        tick = float(tunables.tunable("acp.control_poll_s", 0.5))
        refresh_s = float(tunables.tunable("acp.roster_refresh_s", 5.0))
        last_sync = 0.0
        stats: dict = {}   # agent → last seen queue stat (skip-unchanged)
        while not self._stop.wait(tick):
            now = time.monotonic()
            if now - last_sync >= refresh_s:
                last_sync = now
                try:
                    self._sync_workers()
                except Exception as e:
                    self.log(f"  ⚠️ AcpHost roster sync error: {e}")
            for agent, worker in list(self.workers.items()):
                cur = _queue_stat(agent)
                if cur == stats.get(agent, object()):
                    continue
                try:
                    rows = acp_queue.take_control_rows(agent)
                except OSError:
                    continue
                if not rows:
                    stats[agent] = cur   # settled view — skip until it moves
                else:
                    stats.pop(agent, None)  # we consumed → file changed again
                for row in rows:
                    worker.handle_control(row)

    def stop(self) -> None:
        self._stop.set()
        for w in self.workers.values():
            w.stop()
        if self._control_thread is not None:
            self._control_thread.join(timeout=2)
