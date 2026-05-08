#!/usr/bin/env python3
"""
hire_agent.py — /hire skill steps 5 & 6.

Subcommands:
  setup-feishu <agent_name>  Create workspace table in Bitable, update runtime_config.json
  start-tmux   <agent_name>  Create tmux window for the agent, optionally spawn CLI
"""
import sys
import os
import json
import subprocess

# Allow running from project root with PYTHONPATH=src
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from claudeteam.runtime.config import (
    PROJECT_ROOT,
    TEAM_FILE,
    TMUX_SESSION,
    get_lark_cli,
    load_runtime_config,
    save_runtime_config,
    resolve_model_for_agent,
)

LARK_CLI = get_lark_cli()

# Workspace table fields — same as setup.py
WORKSPACE_FIELDS = [
    {"name": "类型", "type": "text"},
    {"name": "内容", "type": "text"},
    {"name": "时间", "type": "date_time"},
    {"name": "关联对象", "type": "text"},
]


# ── helpers (mirrored from setup.py) ────────────────────────────────────────

def _lark(args, label="", timeout=30):
    """Run lark-cli command, return parsed data dict. None on failure."""
    r = subprocess.run(LARK_CLI + args, capture_output=True, text=True, timeout=timeout)
    if r.returncode != 0:
        print(f"   [warn] {label}: {r.stderr.strip()[:200]}")
        return None
    try:
        full = json.loads(r.stdout) if r.stdout.strip() else {}
        return full.get("data", full)
    except json.JSONDecodeError:
        return None


def _extract_table_id(d):
    """Extract table_id from +table-create response (compatible with multiple paths)."""
    if not d:
        return ""
    if isinstance(d.get("table"), dict):
        return d["table"].get("id", d["table"].get("table_id", ""))
    return d.get("table_id", "")


def _load_team():
    """Load team.json."""
    with open(TEAM_FILE) as f:
        return json.load(f)


# ── setup-feishu ────────────────────────────────────────────────────────────

def setup_feishu(agent_name):
    """Create a workspace table in Bitable for the agent and update runtime_config.json."""
    team = _load_team()
    agents = team.get("agents", {})
    if agent_name not in agents:
        print(f"Error: agent '{agent_name}' not found in team.json")
        sys.exit(1)

    role = agents[agent_name].get("role", agent_name)
    table_name = f"{agent_name}({role})工作空间"

    cfg = load_runtime_config()
    base_token = cfg.get("bitable_app_token", "")
    if not base_token:
        print("Error: bitable_app_token not found in runtime_config.json. Run setup.py first.")
        sys.exit(1)

    # Check if workspace table already exists for this agent
    ws_tables = cfg.get("workspace_tables") or {}
    if agent_name in ws_tables and ws_tables[agent_name]:
        print(f"Workspace table already exists for {agent_name}: {ws_tables[agent_name]}")
        print("Skipping creation.")
        return

    # Create workspace table using lark-cli
    print(f"Creating workspace table: {table_name} ...")
    fields_json = json.dumps(WORKSPACE_FIELDS, ensure_ascii=False)
    d = _lark(
        ["base", "+table-create",
         "--base-token", base_token,
         "--name", table_name,
         "--fields", fields_json,
         "--as", "bot"],
        label=f"create workspace table for {agent_name}",
    )
    table_id = _extract_table_id(d)
    if not table_id:
        print(f"Error: failed to create workspace table for {agent_name}")
        sys.exit(1)

    # Update runtime_config.json
    ws_tables[agent_name] = table_id
    cfg["workspace_tables"] = ws_tables
    save_runtime_config(cfg)

    # Also write an initial status row for the new agent in the status table
    sta_table = cfg.get("sta_table_id", "")
    if sta_table:
        payload = json.dumps({
            "fields": ["Agent名称", "角色", "状态", "当前任务"],
            "rows": [[agent_name, role, "待命", "等待启动"]],
        }, ensure_ascii=False)
        _lark(
            ["base", "+record-batch-create",
             "--base-token", base_token,
             "--table-id", sta_table,
             "--json", payload,
             "--as", "bot"],
            label=f"init status row for {agent_name}",
        )

    print(f"OK  workspace table created: {table_id}")
    print(f"    runtime_config.json updated (workspace_tables.{agent_name})")


# ── start-tmux ──────────────────────────────────────────────────────────────

def start_tmux(agent_name):
    """Create a tmux window for the agent and optionally spawn the CLI."""
    team = _load_team()
    session = team.get("session", TMUX_SESSION)
    agents = team.get("agents", {})
    if agent_name not in agents:
        print(f"Error: agent '{agent_name}' not found in team.json")
        sys.exit(1)

    # Verify session exists
    r = subprocess.run(["tmux", "has-session", "-t", session],
                       capture_output=True, text=True)
    if r.returncode != 0:
        print(f"Error: tmux session '{session}' does not exist. Start the team first.")
        sys.exit(1)

    # Check if window already exists
    r = subprocess.run(
        ["tmux", "list-windows", "-t", session, "-F", "#{window_name}"],
        capture_output=True, text=True,
    )
    existing_windows = r.stdout.strip().splitlines() if r.returncode == 0 else []
    if agent_name in existing_windows:
        print(f"Window '{agent_name}' already exists in session '{session}'. Skipping creation.")
        return

    # Create new tmux window
    subprocess.run(
        ["tmux", "new-window", "-t", session, "-n", agent_name, "-c", PROJECT_ROOT],
        check=True,
    )

    # Forward environment variables to the new pane (same list as start-team.sh B4)
    env_vars = [
        "ANTHROPIC_API_KEY", "CLAUDETEAM_LAZY_MODE", "CLAUDETEAM_LAZY_AGENTS",
        "CLAUDETEAM_DEFAULT_MODEL", "CLAUDETEAM_ENABLE_FEISHU_REMOTE",
        "CLAUDETEAM_FEISHU_REMOTE", "CLAUDETEAM_PROBE_TIMEOUT",
        "PYTHONPATH", "PATH",
    ]
    for var in env_vars:
        val = os.environ.get(var)
        if val is not None:
            subprocess.run(
                ["tmux", "set-environment", "-t", session, var, val],
                capture_output=True,
            )

    # Decide lazy vs eager
    lazy_mode = os.environ.get("CLAUDETEAM_LAZY_MODE", "on")
    lazy_agents_default = "worker_cc,worker_codex,worker_kimi,worker_gemini"
    # Use the same "unset vs empty" semantics as tmux_team_bringup.sh:
    # CLAUDETEAM_LAZY_AGENTS not set -> use default; explicitly empty -> disable lazy
    lazy_agents_raw = os.environ.get("CLAUDETEAM_LAZY_AGENTS")
    if lazy_agents_raw is None:
        lazy_agents_str = lazy_agents_default
    else:
        lazy_agents_str = lazy_agents_raw
    lazy_agents = set(a.strip() for a in lazy_agents_str.split(",") if a.strip())

    is_lazy = (lazy_mode == "on" and agent_name in lazy_agents)

    target = f"{session}:{agent_name}"

    if is_lazy:
        # Show lazy-mode banner, don't spawn CLI
        try:
            model = resolve_model_for_agent(agent_name)
        except Exception:
            model = "unknown"
        banner = f"💤 待 wake  (agent={agent_name}, model={model}, lazy-mode)"
        subprocess.run(
            ["tmux", "send-keys", "-t", target,
             f"clear && echo '{banner}' && echo '   router 收到业务消息后会唤醒本窗口'",
             "Enter"],
            check=True,
        )
        print(f"OK  tmux window created: {agent_name} (lazy-mode, waiting for wake)")
    else:
        # Resolve spawn command via cli_adapters
        try:
            model = resolve_model_for_agent(agent_name)
        except Exception as e:
            print(f"Error resolving model for {agent_name}: {e}")
            sys.exit(1)

        r = subprocess.run(
            [sys.executable, "-m", "claudeteam.cli_adapters.resolve",
             agent_name, "spawn_cmd", model],
            capture_output=True, text=True,
            env={**os.environ, "PYTHONPATH": os.path.join(PROJECT_ROOT, "src")},
        )
        if r.returncode != 0:
            print(f"Error resolving spawn command for {agent_name}: {r.stderr.strip()}")
            sys.exit(1)
        spawn_cmd = r.stdout.strip()

        subprocess.run(
            ["tmux", "send-keys", "-t", target, spawn_cmd, "Enter"],
            check=True,
        )
        print(f"OK  tmux window created: {agent_name} (spawned CLI: {spawn_cmd})")


# ── main ────────────────────────────────────────────────────────────────────

def main():
    if len(sys.argv) < 3:
        print("Usage: python3 scripts/hire_agent.py {setup-feishu|start-tmux} <agent_name>")
        sys.exit(2)

    cmd = sys.argv[1]
    agent_name = sys.argv[2]

    if cmd == "setup-feishu":
        setup_feishu(agent_name)
    elif cmd == "start-tmux":
        start_tmux(agent_name)
    else:
        print(f"Unknown subcommand: {cmd}")
        print("Usage: python3 scripts/hire_agent.py {setup-feishu|start-tmux} <agent_name>")
        sys.exit(2)


if __name__ == "__main__":
    main()
