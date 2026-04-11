#!/usr/bin/env python3
"""
工作空间文件同步到飞书云文档 — ClaudeTeam

功能描述:
  扫描本地 workspace/ 产出文件 + agents/ 角色和记忆文件，增量同步到飞书 Drive 云文档。
  使用 lark-cli docs 命令直传 Markdown，无需自研解析器。
  文件变化检测用 sha256 hash，映射关系存 sync_manifest.json。

同步范围:
  agents/*/workspace/**/*.md  workspace/shared/**/*.md
  agents/*/identity.md        agents/*/core_memory.md
  （agents/*/memory/ 扩展层不同步）

输入输出:
  CLI 子命令:
    init [--folder-name <名称>]  — 在飞书 Drive 创建同步根文件夹，保存 folder_token
    sync                         — 全量扫描，同步新增/变化文件
    sync-file <相对路径>          — 同步单个文件
    status                       — 显示各文件同步状态
    daemon [--interval N]        — 后台守护（默认30秒检查一次）

依赖:
  Python 3.6+, lark-cli (npx @larksuite/cli), runtime_config.json（先运行 setup.py）
"""
import sys, os, json, time, glob, hashlib, subprocess, atexit, signal
from datetime import datetime, timezone, timedelta

sys.path.insert(0, os.path.dirname(__file__))
from config import load_runtime_config, save_runtime_config

ROOT          = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MANIFEST_FILE = os.path.join(os.path.dirname(__file__), "sync_manifest.json")

SYNC_PATTERNS = [
    "agents/*/workspace/**/*.md",
    "workspace/shared/**/*.md",
    "agents/*/identity.md",
    "agents/*/core_memory.md",
]

LARK_CLI = ["npx", "@larksuite/cli"]

# ── 基础工具 ──────────────────────────────────────────────────

def now_str():
    return datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%dT%H:%M:%S+08:00")

load_cfg = load_runtime_config
save_cfg = save_runtime_config

def load_manifest():
    if not os.path.exists(MANIFEST_FILE):
        return {"folder_token": "", "files": {}}
    with open(MANIFEST_FILE) as f:
        return json.load(f)

def save_manifest(manifest):
    tmp = MANIFEST_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(manifest, f, indent=2, ensure_ascii=False)
    os.replace(tmp, MANIFEST_FILE)

# ── 文件扫描与 hash ────────────────────────────────────────────

def scan_files():
    """返回所有待同步的本地文件路径（相对于 ROOT），已排序。"""
    result = []
    for pattern in SYNC_PATTERNS:
        matched = glob.glob(os.path.join(ROOT, pattern))
        result.extend(os.path.relpath(p, ROOT) for p in matched)
    return sorted(result)

def file_hash(abs_path):
    with open(abs_path, "rb") as f:
        return "sha256:" + hashlib.sha256(f.read()).hexdigest()[:16]

# ── lark-cli 文档操作 ────────────────────────────────────────

def lark_create_folder(name):
    """在飞书 Drive 根目录创建文件夹，返回 folder_token。"""
    r = subprocess.run(
        LARK_CLI + ["drive", "files", "create_folder",
                    "--name", name, "--folder_token", "", "--as", "bot"],
        capture_output=True, text=True, timeout=30)
    if r.returncode != 0:
        print(f"❌ 创建文件夹失败: {r.stderr}")
        sys.exit(1)
    d = json.loads(r.stdout)
    return d.get("data", {}).get("token", "")

def lark_create_doc(folder_token, title, markdown_path):
    """用 lark-cli 创建飞书文档（直传 Markdown 文件），返回 doc URL。"""
    r = subprocess.run(
        LARK_CLI + ["docs", "+create",
                    "--folder-token", folder_token,
                    "--title", title,
                    "--markdown", f"@{markdown_path}",
                    "--as", "bot"],
        capture_output=True, text=True, timeout=60)
    if r.returncode != 0:
        raise RuntimeError(f"创建文档失败: {r.stderr.strip()}")
    d = json.loads(r.stdout)
    return d.get("document_id", d.get("url", ""))

def lark_update_doc(doc_url, markdown_path):
    """用 lark-cli 覆盖更新飞书文档内容。"""
    r = subprocess.run(
        LARK_CLI + ["docs", "+update",
                    "--doc", doc_url,
                    "--mode", "overwrite",
                    "--markdown", f"@{markdown_path}",
                    "--as", "bot"],
        capture_output=True, text=True, timeout=60)
    if r.returncode != 0:
        raise RuntimeError(f"更新文档失败: {r.stderr.strip()}")

# ── 单文件同步逻辑 ────────────────────────────────────────────

def sync_one_file(rel_path, manifest, verbose=True):
    """
    同步单个文件到飞书文档（使用 lark-cli docs --markdown）。
    - 文件不存在时标记 deleted=true，不删飞书侧文档。
    - 内容未变化时跳过。
    """
    abs_path = os.path.join(ROOT, rel_path)

    if not os.path.exists(abs_path):
        entry = manifest["files"].get(rel_path, {})
        if entry:
            entry["deleted"] = True
            manifest["files"][rel_path] = entry
            save_manifest(manifest)
        if verbose:
            print(f"  ⚠️  文件已删除，跳过（飞书文档保留）: {rel_path}")
        return

    current_hash = file_hash(abs_path)
    entry = manifest["files"].get(rel_path, {})

    if entry.get("last_hash") == current_hash:
        if verbose:
            print(f"  ─ 未变化，跳过: {rel_path}")
        return

    folder_token = manifest["folder_token"]
    doc_title = rel_path.replace("/", "_").replace(".md", "")

    try:
        if not entry.get("doc_token"):
            doc_id = lark_create_doc(folder_token, doc_title, abs_path)
        else:
            doc_id = entry["doc_token"]
            lark_update_doc(doc_id, abs_path)

        manifest["files"][rel_path] = {
            "doc_token":   doc_id,
            "last_hash":   current_hash,
            "last_synced": now_str(),
        }
        save_manifest(manifest)
        if verbose:
            print(f"  ✅ 已同步: {rel_path}")
    except Exception as e:
        print(f"  ❌ 同步失败: {rel_path} — {e}")

# ── 命令：init ────────────────────────────────────────────────

def cmd_init(folder_name="Agent团队工作空间"):
    cfg = load_cfg()
    manifest = load_manifest()

    if cfg.get("sync_folder_token"):
        print(f"⚠️  同步文件夹已存在: {cfg['sync_folder_token']}，跳过创建")
        return

    folder_token = lark_create_folder(folder_name)
    cfg["sync_folder_token"] = folder_token
    save_cfg(cfg)

    manifest["folder_token"] = folder_token
    save_manifest(manifest)

    print(f"✅ 同步根文件夹已创建: {folder_name}")
    print(f"   folder_token: {folder_token}")
    print(f"   配置已更新: runtime_config.json → sync_folder_token")
    print(f"   manifest 已初始化: scripts/sync_manifest.json")

# ── 命令：sync ────────────────────────────────────────────────

def cmd_sync():
    manifest = load_manifest()
    if not manifest["folder_token"]:
        print("❌ 未配置 folder_token，请先运行: python3 scripts/feishu_sync.py init")
        sys.exit(1)

    files = scan_files()
    print(f"🔍 扫描到 {len(files)} 个待检查文件")
    for rel_path in files:
        sync_one_file(rel_path, manifest, verbose=True)
    print("✅ 全量同步完成")

# ── 命令：sync-file ───────────────────────────────────────────

def cmd_sync_file(rel_path):
    manifest = load_manifest()
    if not manifest["folder_token"]:
        print("❌ 未配置 folder_token，请先运行: python3 scripts/feishu_sync.py init")
        sys.exit(1)
    sync_one_file(rel_path, manifest, verbose=True)

# ── 命令：status ──────────────────────────────────────────────

def cmd_status():
    manifest = load_manifest()
    files = scan_files()
    synced = manifest.get("files", {})

    print(f"📊 同步状态 (folder_token: {manifest['folder_token'] or '未配置'})\n")
    print(f"  {'文件路径':<50} {'状态':<10} {'上次同步'}")
    print(f"  {'-'*50} {'-'*10} {'-'*16}")

    for rel_path in files:
        abs_path = os.path.join(ROOT, rel_path)
        entry = synced.get(rel_path, {})

        if not entry:
            state = "未同步"
            last = "─"
        elif entry.get("deleted"):
            state = "已删除"
            last = entry.get("last_synced", "─")[:16]
        else:
            current_hash = file_hash(abs_path) if os.path.exists(abs_path) else ""
            if current_hash == entry.get("last_hash"):
                state = "已同步"
            else:
                state = "待更新"
            last = entry.get("last_synced", "─")[:16]

        print(f"  {rel_path:<50} {state:<10} {last}")

    extra = set(synced.keys()) - set(files)
    for rel_path in sorted(extra):
        entry = synced[rel_path]
        last = entry.get("last_synced", "─")[:16]
        print(f"  {rel_path:<50} {'(已删除)':<10} {last}")

    total = len(files)
    n_synced  = sum(1 for p in files if synced.get(p) and not synced[p].get("deleted")
                    and file_hash(os.path.join(ROOT, p)) == synced[p].get("last_hash")
                    if os.path.exists(os.path.join(ROOT, p)))
    n_pending = total - n_synced
    print(f"\n  共 {total} 个文件：{n_synced} 已同步，{n_pending} 待同步/更新")

# ── 命令：daemon ──────────────────────────────────────────────

_PID_FILE = os.path.join(os.path.dirname(__file__), ".feishu_sync.pid")

def _acquire_pid_lock():
    if os.path.exists(_PID_FILE):
        try:
            with open(_PID_FILE) as f:
                old_pid = int(f.read().strip())
            os.kill(old_pid, 0)
            print(f"❌ feishu_sync daemon 已在运行 (PID {old_pid})")
            sys.exit(1)
        except (ValueError, OSError):
            pass
    with open(_PID_FILE, "w") as f:
        f.write(str(os.getpid()))
    atexit.register(_cleanup_pid)

def _cleanup_pid():
    try:
        if os.path.exists(_PID_FILE):
            with open(_PID_FILE) as f:
                pid = int(f.read().strip())
            if pid == os.getpid():
                os.remove(_PID_FILE)
    except Exception:
        pass

signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))

def cmd_daemon(interval=30):
    _acquire_pid_lock()
    print(f"🔄 文件同步守护进程启动（每 {interval} 秒检查一次）")
    while True:
        try:
            manifest = load_manifest()
            if not manifest["folder_token"]:
                print("⚠️  folder_token 未配置，跳过本次同步")
            else:
                files = scan_files()
                changed = 0
                for rel_path in files:
                    abs_path = os.path.join(ROOT, rel_path)
                    if not os.path.exists(abs_path):
                        continue
                    entry = manifest["files"].get(rel_path, {})
                    if entry.get("last_hash") != file_hash(abs_path):
                        sync_one_file(rel_path, manifest, verbose=True)
                        changed += 1
                if changed:
                    print(f"[{time.strftime('%H:%M:%S')}] 同步 {changed} 个变化文件")
        except Exception as e:
            print(f"⚠️  守护同步失败: {e}")
        time.sleep(interval)

# ── main ──────────────────────────────────────────────────────

def main():
    args = sys.argv[1:]
    if not args:
        print(__doc__); sys.exit(0)

    cmd = args[0]

    if cmd == "init":
        name = "Agent团队工作空间"
        if "--folder-name" in args:
            idx = args.index("--folder-name")
            if idx + 1 < len(args):
                name = args[idx + 1]
        cmd_init(name)
    elif cmd == "sync":
        cmd_sync()
    elif cmd == "sync-file":
        if len(args) < 2:
            print("用法: sync-file <本地相对路径>"); sys.exit(1)
        cmd_sync_file(args[1])
    elif cmd == "status":
        cmd_status()
    elif cmd == "daemon":
        interval = 30
        if "--interval" in args:
            idx = args.index("--interval")
            if idx + 1 < len(args):
                interval = int(args[idx + 1])
        cmd_daemon(interval)
    else:
        print(f"未知命令: {cmd}"); sys.exit(1)

if __name__ == "__main__":
    main()
