#!/usr/bin/env python3
"""
工作空间文件同步到飞书云文档 — ClaudeTeam

功能描述:
  扫描本地 workspace/ 产出文件 + agents/ 角色和记忆文件，增量同步到飞书 Drive 云文档。
  内容以单个 Markdown 代码块存储，文件变化检测用 sha256 hash，映射关系存 sync_manifest.json。

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
  Python 3.6+，requests，runtime_config.json（先运行 setup.py）
"""
import sys, os, re, json, time, glob, hashlib, requests, atexit, signal
from datetime import datetime, timezone, timedelta

sys.path.insert(0, os.path.dirname(__file__))
from config import BASE, CONFIG_FILE
from feishu_api import get_token, h, api_request

ROOT          = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MANIFEST_FILE = os.path.join(os.path.dirname(__file__), "sync_manifest.json")

SYNC_PATTERNS = [
    "agents/*/workspace/**/*.md",
    "workspace/shared/**/*.md",
    "agents/*/identity.md",
    "agents/*/core_memory.md",
]

# ── 基础工具 ──────────────────────────────────────────────────

def now_str():
    return datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%dT%H:%M:%S+08:00")

def load_cfg():
    if not os.path.exists(CONFIG_FILE):
        print("❌ 未找到 runtime_config.json，请先运行 python3 scripts/setup.py")
        sys.exit(1)
    with open(CONFIG_FILE) as f:
        return json.load(f)

def save_cfg(cfg):
    with open(CONFIG_FILE, "w") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)

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

# ── Markdown → Block 解析 ────────────────────────────────────

LANG_MAP = {
    "python": 21, "py": 21,
    "bash": 2, "sh": 2, "shell": 2,
    "javascript": 13, "js": 13,
    "typescript": 28, "ts": 28,
    "go": 9, "golang": 9,
    "java": 12,
    "rust": 23,
    "sql": 32,
    "json": 29,
    "yaml": 36, "yml": 36,
    "html": 10,
    "markdown": 18, "md": 18,
    "ruby": 22, "rb": 22,
}

def resolve_language(lang_str):
    return LANG_MAP.get(lang_str.lower().strip(), 1)

def make_text_run(text, bold=False, italic=False, inline_code=False, link_url=""):
    style = {}
    if bold:        style["bold"] = True
    if italic:      style["italic"] = True
    if inline_code: style["inline_code"] = True
    if link_url:    style["link"] = {"url": link_url}
    elem = {"text_run": {"content": text}}
    if style:
        elem["text_run"]["text_element_style"] = style
    return elem

def make_text_block(block_type, runs):
    key = {2: "text", 3: "heading1", 4: "heading2", 5: "heading3",
           12: "bullet", 13: "ordered"}[block_type]
    return {"block_type": block_type, key: {"elements": runs, "style": {}}}

def make_code_block(code_text, language=1):
    if len(code_text) > 100000:
        code_text = code_text[:100000] + "\n...[内容已截断]"
    return {
        "block_type": 14,
        "code": {
            "elements": [{"text_run": {"content": code_text}}],
            "language": language,
            "wrap": False,
        }
    }

_TOKEN_RE = re.compile(
    r'(`[^`]+`)'               # 行内代码
    r'|(\*\*[^*]+\*\*)'        # 加粗 **
    r'|(__[^_]+__)'             # 加粗 __
    r'|(\*[^*]+\*)'             # 斜体 *
    r'|(_[^_]+_)'               # 斜体 _
    r'|(\[[^\]]+\]\([^)]+\))'  # 链接
    r'|([^`*_\[]+)'             # 普通文本
)

def parse_inline(text):
    runs = []
    for m in _TOKEN_RE.finditer(text):
        raw = m.group(0)
        if raw.startswith('`') and raw.endswith('`') and len(raw) >= 2:
            runs.append(make_text_run(raw[1:-1], inline_code=True))
        elif raw.startswith('**') or raw.startswith('__'):
            runs.append(make_text_run(raw[2:-2], bold=True))
        elif raw.startswith('*') or raw.startswith('_'):
            runs.append(make_text_run(raw[1:-1], italic=True))
        elif raw.startswith('['):
            lm = re.match(r'\[([^\]]+)\]\(([^)]+)\)', raw)
            if lm:
                runs.append(make_text_run(lm.group(1), link_url=lm.group(2)))
        else:
            if raw:
                runs.append(make_text_run(raw))
    return runs if runs else [make_text_run(text)]

def parse_table_rows(table_rows, R, C):
    """table_rows: list of list of str（已去除分隔行）。返回 (table_block, cell_matrix)。"""
    table_block = {
        "block_type": 22,
        "table": {"property": {"row_size": R, "column_size": C}}
    }
    cell_matrix = []
    for row in table_rows:
        padded = list(row) + [""] * (C - len(row))
        cell_matrix.append(padded[:C])
    return table_block, cell_matrix

def parse_markdown_to_blocks(content):
    """将 Markdown 字符串解析为飞书 docx block 列表。"""
    blocks = []
    lines = content.split("\n")
    i = 0

    while i < len(lines):
        line = lines[i]

        # 四级及以上标题 → Heading3
        if re.match(r'^#{4,} ', line):
            blocks.append(make_text_block(5, parse_inline(re.sub(r'^#{4,} ', '', line))))
            i += 1

        elif line.startswith("### "):
            blocks.append(make_text_block(5, parse_inline(line[4:])))
            i += 1

        elif line.startswith("## "):
            blocks.append(make_text_block(4, parse_inline(line[3:])))
            i += 1

        elif line.startswith("# "):
            blocks.append(make_text_block(3, parse_inline(line[2:])))
            i += 1

        # 代码块（围栏式）
        elif line.startswith("```"):
            lang = line[3:].strip()
            code_lines = []
            i += 1
            while i < len(lines) and not lines[i].startswith("```"):
                code_lines.append(lines[i])
                i += 1
            i += 1  # 跳过结束 ```
            blocks.append(make_code_block("\n".join(code_lines), resolve_language(lang)))

        # 无序列表
        elif re.match(r'^[-*] ', line):
            blocks.append(make_text_block(12, parse_inline(line[2:])))
            i += 1

        # 有序列表
        elif re.match(r'^\d+\. ', line):
            blocks.append(make_text_block(13, parse_inline(re.sub(r'^\d+\. ', '', line))))
            i += 1

        # 表格
        elif (re.match(r'^\|.*\|$', line) and
              i + 1 < len(lines) and re.match(r'^\|[-:| ]+\|$', lines[i + 1])):
            table_rows = []
            while i < len(lines) and re.match(r'^\|.*\|$', lines[i]):
                if re.match(r'^\|[-:| ]+\|$', lines[i]):
                    i += 1
                    continue
                cells = [c.strip() for c in lines[i].strip("|").split("|")]
                table_rows.append(cells)
                i += 1
            if table_rows:
                # 飞书 Table block API（block_type=22）创建时返回 1770001 invalid param，
                # 降级为纯文本代码块，保留 Markdown 表格格式以便阅读。
                sep = "|" + "|".join("---" for _ in table_rows[0]) + "|"
                table_lines = ["| " + " | ".join(table_rows[0]) + " |", sep]
                for row in table_rows[1:]:
                    table_lines.append("| " + " | ".join(row) + " |")
                blocks.append(make_code_block("\n".join(table_lines), 1))

        # 水平分隔线（跳过）
        elif re.match(r'^[-*_]{3,}$', line.strip()):
            i += 1

        elif line.strip() == "":
            i += 1

        else:
            blocks.append(make_text_block(2, parse_inline(line)))
            i += 1

    return blocks

# ── 飞书 Drive 文件夹 ─────────────────────────────────────────

def create_folder(token, name):
    """在飞书 Drive 根目录创建文件夹，返回 folder_token。"""
    r = api_request("POST", f"{BASE}/drive/v1/files/create_folder", token,
                    json={"name": name, "folder_token": ""})
    d = r.json()
    if d.get("code") != 0:
        print(f"❌ 创建文件夹失败: {d}"); sys.exit(1)
    return d["data"]["token"]

# ── 飞书 Docx 文档操作 ────────────────────────────────────────

def _chunks(lst, n):
    for i in range(0, len(lst), n):
        yield lst[i:i + n]

def ensure_doc(token, folder_token, title):
    """创建空飞书 docx 文档，返回 doc_id。"""
    r = api_request("POST", f"{BASE}/docx/v1/documents", token,
                    json={"title": title, "folder_token": folder_token})
    d = r.json()
    if d.get("code") != 0:
        raise RuntimeError(f"创建文档失败: {d}")
    return d["data"]["document"]["document_id"]

def clear_doc_children(token, doc_id):
    """删除 doc 页面 block 下的所有子 block。"""
    r = api_request("GET",
                    f"{BASE}/docx/v1/documents/{doc_id}/blocks/{doc_id}/children",
                    token, params={"page_size": 200})
    items = r.json().get("data", {}).get("items", [])
    if not items:
        return
    r2 = api_request("DELETE",
                     f"{BASE}/docx/v1/documents/{doc_id}/blocks/{doc_id}/children",
                     token,
                     params={"start_index": 0, "end_index": len(items)})
    if r2.status_code >= 300:
        raise RuntimeError(f"清空文档内容失败: HTTP {r2.status_code} {r2.text[:100]}")

def _json(resp):
    """解析响应 JSON，失败时抛出含原始响应的 RuntimeError。"""
    try:
        return resp.json()
    except ValueError as e:
        raise RuntimeError(f"API 响应非 JSON (HTTP {resp.status_code}): {resp.text[:200]}") from e

def upload_children(token, doc_id, parent_block_id, blocks):
    """向指定父 block 批量追加子 block，每批最多 50 个。用 POST（创建），非 PATCH（重排序）。"""
    for batch in _chunks(blocks, 50):
        r = api_request("POST",
                        f"{BASE}/docx/v1/documents/{doc_id}/blocks/{parent_block_id}/children",
                        token,
                        json={"children": batch, "index": -1})
        d = _json(r)
        if d.get("code") != 0:
            raise RuntimeError(f"上传 block 失败: {d}")

def get_table_cell_ids(token, doc_id, table_block_id, R, C):
    """获取表格自动生成的 cell block IDs，返回 R×C 二维列表。"""
    r = api_request("GET",
                    f"{BASE}/docx/v1/documents/{doc_id}/blocks/{table_block_id}/children",
                    token, params={"page_size": R * C + 10})
    items = r.json().get("data", {}).get("items", [])
    block_ids = [item["block_id"] for item in items]
    return [block_ids[r_idx * C:(r_idx + 1) * C] for r_idx in range(R)]

def upload_table(token, doc_id, table_entry):
    """两阶段上传表格 block。"""
    R = table_entry["block"]["table"]["property"]["row_size"]
    C = table_entry["block"]["table"]["property"]["column_size"]
    cells = table_entry["cells"]

    # 阶段1：创建 Table block，获取 table_block_id（POST 创建，非 PATCH 重排序）
    r = api_request("POST",
                    f"{BASE}/docx/v1/documents/{doc_id}/blocks/{doc_id}/children",
                    token,
                    json={"children": [table_entry["block"]], "index": -1})
    d = _json(r)
    if d.get("code") != 0:
        raise RuntimeError(f"创建 Table block 失败: {d}")
    table_block_id = d["data"]["children"][0]["block_id"]

    # 阶段2：获取 cell IDs 并写入内容
    cell_ids = get_table_cell_ids(token, doc_id, table_block_id, R, C)
    for r_idx in range(R):
        for c_idx in range(C):
            if r_idx >= len(cell_ids) or c_idx >= len(cell_ids[r_idx]):
                continue
            cell_id = cell_ids[r_idx][c_idx]
            cell_text = cells[r_idx][c_idx] if c_idx < len(cells[r_idx]) else ""
            upload_children(token, doc_id, cell_id,
                            [make_text_block(2, [make_text_run(cell_text)])])

# ── 单文件同步逻辑 ────────────────────────────────────────────

def sync_one_file(token, rel_path, manifest, verbose=True):
    """
    同步单个文件到飞书 docx（v2：逐 block 上传，支持标题/列表/代码块/表格）。
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

    content = open(abs_path, encoding="utf-8").read()
    folder_token = manifest["folder_token"]
    doc_title = rel_path.replace("/", "_").replace(".md", "")

    try:
        if not entry.get("doc_token"):
            doc_id = ensure_doc(token, folder_token, doc_title)
        else:
            doc_id = entry["doc_token"]
            clear_doc_children(token, doc_id)

        blocks = parse_markdown_to_blocks(content)

        plain_batch = []
        for block in blocks:
            if block.get("type") == "table":
                if plain_batch:
                    upload_children(token, doc_id, doc_id, plain_batch)
                    plain_batch = []
                upload_table(token, doc_id, block)
            else:
                plain_batch.append(block)
        if plain_batch:
            upload_children(token, doc_id, doc_id, plain_batch)

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
    token = get_token()
    cfg = load_cfg()
    manifest = load_manifest()

    if cfg.get("sync_folder_token"):
        print(f"⚠️  同步文件夹已存在: {cfg['sync_folder_token']}，跳过创建")
        return

    folder_token = create_folder(token, folder_name)
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
    token = get_token()
    manifest = load_manifest()
    if not manifest["folder_token"]:
        print("❌ 未配置 folder_token，请先运行: python3 scripts/feishu_sync.py init")
        sys.exit(1)

    files = scan_files()
    print(f"🔍 扫描到 {len(files)} 个待检查文件")
    for rel_path in files:
        sync_one_file(token, rel_path, manifest, verbose=True)
    print("✅ 全量同步完成")

# ── 命令：sync-file ───────────────────────────────────────────

def cmd_sync_file(rel_path):
    token = get_token()
    manifest = load_manifest()
    if not manifest["folder_token"]:
        print("❌ 未配置 folder_token，请先运行: python3 scripts/feishu_sync.py init")
        sys.exit(1)
    sync_one_file(token, rel_path, manifest, verbose=True)

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

    # 显示 manifest 中有记录但不在当前扫描结果里的文件（已删除）
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
            token = get_token()
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
                        sync_one_file(token, rel_path, manifest, verbose=True)
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
