#!/usr/bin/env python3
"""
裁员辅助脚本 — ClaudeTeam

功能描述:
  为 /fire skill 提供 tmux 关闭、目录归档、飞书清理的辅助子命令。
  Skill 负责 team.json 修改和流程编排，本脚本负责具体操作。

输入输出:
  CLI 子命令:
    stop-tmux <agent_name>       — 关闭该 Agent 的 tmux 窗口
    archive <agent_name>         — 将 agents/<name>/ 归档到 agents/_archived/
    cleanup-feishu <agent_name>  — 从 runtime_config.json 移除工作空间表配置

依赖:
  Python 3.6+, config.py
"""
import sys, os, json, time, re, shutil, subprocess
from datetime import datetime

sys.path.insert(0, os.path.dirname(__file__))
from config import PROJECT_ROOT, load_runtime_config, save_runtime_config

def validate_name(name):
    if not re.match(r'^[a-z0-9_-]+$', name):
        print(f"❌ 员工名 '{name}' 不合法，只允许小写字母、数字、下划线和连字符")
        sys.exit(1)

# ── 基础工具 ──────────────────────────────────────────────────

def load_team():
    team_file = os.path.join(PROJECT_ROOT, "team.json")
    with open(team_file) as f:
        return json.load(f)

def load_cfg():
    try:
        return load_runtime_config()
    except SystemExit:
        return None

save_cfg = save_runtime_config

# ── 命令：stop-tmux ──────────────────────────────────────────

def cmd_stop_tmux(agent_name):
    """关闭该 Agent 的 tmux 窗口。"""
    validate_name(agent_name)
    team = load_team()
    session = team.get("session", "ClaudeTeam")

    # 检查窗口是否存在
    r = subprocess.run(["tmux", "has-session", "-t", f"{session}:{agent_name}"],
                       capture_output=True, timeout=5)
    if r.returncode != 0:
        print(f"⚠️  tmux 窗口 {agent_name} 不存在，跳过关闭")
        return

    # 发送 Ctrl+C 中断当前进程
    subprocess.run(["tmux", "send-keys", "-t", f"{session}:{agent_name}", "C-c"],
                   capture_output=True)
    time.sleep(1)

    # 发送 /exit 优雅退出 Claude
    subprocess.run(["tmux", "send-keys", "-t", f"{session}:{agent_name}", "/exit", "Enter"],
                   capture_output=True)
    time.sleep(2)

    # kill 窗口确保关闭
    subprocess.run(["tmux", "kill-window", "-t", f"{session}:{agent_name}"],
                   capture_output=True)
    print(f"✅ tmux 窗口 {agent_name} 已关闭")

# ── 命令：archive ─────────────────────────────────────────────

def cmd_archive(agent_name):
    """将 agents/<name>/ 归档到 agents/_archived/<name>_<YYYYMMDD>/。"""
    validate_name(agent_name)
    src = os.path.join(PROJECT_ROOT, "agents", agent_name)
    if not os.path.exists(src):
        print(f"⚠️  agents/{agent_name}/ 不存在，跳过归档")
        return

    archived_dir = os.path.join(PROJECT_ROOT, "agents", "_archived")
    os.makedirs(archived_dir, exist_ok=True)

    date_str = datetime.now().strftime("%Y%m%d")
    dst = os.path.join(archived_dir, f"{agent_name}_{date_str}")

    # 处理同名冲突
    if os.path.exists(dst):
        n = 2
        while os.path.exists(f"{dst}_{n}"):
            n += 1
        dst = f"{dst}_{n}"

    shutil.move(src, dst)

    # 写入裁撤记录
    termination_file = os.path.join(dst, "_termination.md")
    with open(termination_file, "w", encoding="utf-8") as f:
        f.write(f"# 裁撤记录\n\n"
                f"- 员工：{agent_name}\n"
                f"- 裁撤时间：{datetime.now().isoformat()}\n"
                f"- 执行人：manager\n")

    dst_rel = os.path.relpath(dst, PROJECT_ROOT)
    print(f"✅ 已归档: agents/{agent_name}/ → {dst_rel}/")

# ── 命令：cleanup-feishu ─────────────────────────────────────

def cmd_cleanup_feishu(agent_name):
    """从 runtime_config.json 移除该 Agent 的工作空间表配置。"""
    validate_name(agent_name)
    cfg = load_cfg()
    if cfg is None:
        print(f"⚠️  runtime_config.json 不存在，跳过飞书清理")
        return

    ws_tables = cfg.get("workspace_tables", {})
    if agent_name in ws_tables:
        removed_tid = ws_tables.pop(agent_name)
        cfg["workspace_tables"] = ws_tables
        save_cfg(cfg)
        print(f"✅ 已从 runtime_config.json 移除 {agent_name} 工作空间表 ({removed_tid})")
    else:
        print(f"⚠️  {agent_name} 不在 workspace_tables 中，无需清理")

# ── main ──────────────────────────────────────────────────────

def main():
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        sys.exit(0)

    cmd = args[0]

    if cmd == "stop-tmux":
        if len(args) < 2:
            print("用法: stop-tmux <agent_name>"); sys.exit(1)
        cmd_stop_tmux(args[1])

    elif cmd == "archive":
        if len(args) < 2:
            print("用法: archive <agent_name>"); sys.exit(1)
        cmd_archive(args[1])

    elif cmd == "cleanup-feishu":
        if len(args) < 2:
            print("用法: cleanup-feishu <agent_name>"); sys.exit(1)
        cmd_cleanup_feishu(args[1])

    else:
        print(f"未知命令: {cmd}"); sys.exit(1)

if __name__ == "__main__":
    main()
