#!/usr/bin/env python3
"""
分层渐进记忆管理 — ClaudeTeam

功能描述:
  三层文件结构管理 Agent 跨会话记忆：
    核心层：agents/<name>/core_memory.md（启动必读，≤50行）
    扩展层：agents/<name>/memory/<topic>.md（按需加载）
    归档层：agents/<name>/memory/archive/YYYYMMDD_<topic>.md（冷存储）

输入输出:
  CLI 子命令:
    init <agent>                            — 初始化记忆目录和核心层骨架
    write <agent> <topic> "<内容>"          — 覆盖写入扩展层文件
    append <agent> <topic> "<内容>"         — 追加到扩展层文件末尾
    read <agent> <topic>                    — 读取扩展层文件
    update-core <agent> "<markdown内容>"    — 覆盖核心层整个文件
    note <agent> "<一条事实>"               — 追加关键事实到核心层
    archive <agent> <topic>                 — 将扩展层文件移入归档层
    index <agent>                           — 显示记忆概览

依赖:
  Python 3.6+，仅用标准库
"""
import sys, os, re, shutil
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

def core_path(agent):
    return os.path.join(ROOT, "agents", agent, "core_memory.md")

def ext_dir(agent):
    return os.path.join(ROOT, "agents", agent, "memory")

def ext_path(agent, topic):
    return os.path.join(ext_dir(agent), f"{topic}.md")

def arc_dir(agent):
    return os.path.join(ROOT, "agents", agent, "memory", "archive")

# ── 基础工具 ──────────────────────────────────────────────────

def now_str():
    return datetime.now().strftime("%Y-%m-%d %H:%M")

def read_file(path):
    with open(path, "r", encoding="utf-8") as f:
        return f.read()

def write_file(path, content):
    """原子写入：先写 .tmp 再 os.replace。"""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(content)
    os.replace(tmp, path)

def update_timestamp(content):
    """将 '> 最后更新：...' 行替换为当前时间。"""
    return re.sub(r"^> 最后更新：.*$", f"> 最后更新：{now_str()}", content, flags=re.MULTILINE)

# ── parse_index（供 Agent 启动时按需加载扩展层）─────────────────

def parse_index(agent):
    """
    从核心层解析扩展记忆路径列表。
    格式：- <相对路径>   # <说明>
    返回：[相对于项目根目录的路径列表]
    """
    path = core_path(agent)
    if not os.path.exists(path):
        return []
    content = read_file(path)
    paths = []
    in_index = False
    for line in content.split("\n"):
        if line.startswith("## 扩展记忆索引"):
            in_index = True
            continue
        if in_index:
            if line.startswith("## "):
                break
            m = re.match(r"^-\s+([\w/.\-]+\.md)", line)
            if m:
                paths.append(m.group(1))
    return paths

# ── 命令：init ────────────────────────────────────────────────

def cmd_init(agent):
    """初始化 Agent 记忆目录，创建核心层骨架（已存在则跳过）。"""
    cp = core_path(agent)
    ed = ext_dir(agent)
    ad = arc_dir(agent)

    os.makedirs(ed, exist_ok=True)
    os.makedirs(ad, exist_ok=True)

    was_existing = os.path.exists(cp)
    if was_existing:
        print(f"⚠️  核心层已存在，跳过创建: agents/{agent}/core_memory.md")
    else:
        skeleton = (
            f"# {agent} 核心记忆\n\n"
            f"> 最后更新：{now_str()}\n\n"
            "## 关键事实\n"
            "- （待填写）\n\n"
            "## 当前状态\n"
            "- （待填写）\n\n"
            "## 扩展记忆索引\n"
            "- （按需添加）\n"
        )
        write_file(cp, skeleton)

    print(f"✅ 已初始化 {agent} 记忆目录")
    print(f"   核心层: agents/{agent}/core_memory.md"
          + ("（已存在，未覆盖）" if was_existing else "（已创建骨架）"))
    print(f"   扩展层: agents/{agent}/memory/（目录已就绪）")
    print(f"   归档层: agents/{agent}/memory/archive/（目录已就绪）")

# ── 命令：write ───────────────────────────────────────────────

def cmd_write(agent, topic, content):
    """覆盖写入扩展层文件，若不存在则创建。"""
    path = ext_path(agent, topic)
    write_file(path, content)
    lines = content.count("\n") + 1
    if lines > 200:
        print(f"⚠️  文件已超200行，建议分割或归档")
    print(f"✅ 已写入: agents/{agent}/memory/{topic}.md ({lines}行)")

# ── 命令：append ──────────────────────────────────────────────

def cmd_append(agent, topic, content):
    """追加内容到扩展层文件末尾，若不存在则创建。"""
    path = ext_path(agent, topic)
    if os.path.exists(path):
        existing = read_file(path)
        # 确保追加前有换行分隔
        sep = "" if existing.endswith("\n") else "\n"
        new_content = existing + sep + content
    else:
        new_content = content

    write_file(path, new_content)
    lines = new_content.count("\n") + 1
    if lines > 200:
        print(f"⚠️  文件已超200行，建议分割或归档")
    print(f"✅ 已追加: agents/{agent}/memory/{topic}.md ({lines}行)")

# ── 命令：read ────────────────────────────────────────────────

def cmd_read(agent, topic):
    """读取并打印扩展层文件内容。"""
    path = ext_path(agent, topic)
    if not os.path.exists(path):
        print(f"❌ 找不到: agents/{agent}/memory/{topic}.md")
        sys.exit(1)
    print(read_file(path))

# ── 命令：update-core ─────────────────────────────────────────

def cmd_update_core(agent, content):
    """覆盖核心层整个文件内容。"""
    cp = core_path(agent)
    if not os.path.exists(cp):
        print(f"❌ 核心层不存在，请先运行: init {agent}")
        sys.exit(1)
    write_file(cp, content)
    lines = content.count("\n") + 1
    if lines > 50:
        print(f"⚠️  核心层已超50行（当前{lines}行），建议将旧内容迁移到扩展层")
    print(f"✅ 核心层已更新: agents/{agent}/core_memory.md ({lines}行)")

# ── 命令：note ────────────────────────────────────────────────

def cmd_note(agent, fact):
    """在核心层"## 关键事实"段末尾插入一条新事实，并刷新时间戳。"""
    cp = core_path(agent)
    if not os.path.exists(cp):
        print(f"❌ 核心层不存在，请先运行: init {agent}")
        sys.exit(1)

    content = read_file(cp)
    marker = "## 关键事实"
    if marker not in content:
        print(f"❌ 核心层缺少 '{marker}' 段落")
        sys.exit(1)

    lines = content.split("\n")
    insert_idx = None
    in_section = False
    for i, line in enumerate(lines):
        if line.strip() == marker:
            in_section = True
            continue
        if in_section and line.startswith("## "):
            insert_idx = i
            break
    if insert_idx is None:
        insert_idx = len(lines)

    # 若段末已有空行，在空行之前插入，保持空行始终在段尾
    if insert_idx > 0 and lines[insert_idx - 1] == "":
        insert_idx -= 1
    lines.insert(insert_idx, f"- {fact}")
    new_content = update_timestamp("\n".join(lines))
    write_file(cp, new_content)
    print(f"✅ 已添加到核心层关键事实: {fact[:60]}")

# ── 命令：archive ─────────────────────────────────────────────

def cmd_archive(agent, topic):
    """将扩展层文件移入归档层，处理同名冲突。"""
    src = ext_path(agent, topic)
    if not os.path.exists(src):
        print(f"❌ 找不到扩展层文件: agents/{agent}/memory/{topic}.md")
        sys.exit(1)

    date = datetime.now().strftime("%Y%m%d")
    dst = os.path.join(arc_dir(agent), f"{date}_{topic}.md")

    # 处理同名冲突
    if os.path.exists(dst):
        n = 2
        while os.path.exists(dst):
            dst = os.path.join(arc_dir(agent), f"{date}_{topic}_{n}.md")
            n += 1

    os.makedirs(arc_dir(agent), exist_ok=True)
    shutil.move(src, dst)

    dst_rel = os.path.relpath(dst, ROOT)
    print(f"✅ 已归档: agents/{agent}/memory/{topic}.md")
    print(f"        → {dst_rel}")
    print(f"⚠️  记得更新核心层索引，移除该路径的引用")

# ── 命令：index ───────────────────────────────────────────────

def cmd_index(agent):
    """显示 Agent 记忆概览：核心层摘要 + 扩展层文件列表 + 归档层文件列表。"""
    cp = core_path(agent)
    ed = ext_dir(agent)
    ad = arc_dir(agent)

    print(f"🧠 {agent} 记忆概览\n")

    # 核心层
    print(f"── 核心层 (agents/{agent}/core_memory.md) ──")
    if not os.path.exists(cp):
        print("  （未初始化，运行 init 创建）\n")
    else:
        content = read_file(cp)
        lines = content.split("\n")

        # 最后更新时间
        ts_match = re.search(r"^> 最后更新：(.+)$", content, re.MULTILINE)
        ts = ts_match.group(1) if ts_match else "未知"

        # 关键事实条数
        facts = [l for l in lines if re.match(r"^\s*-\s+(?!（待填写）)", l)]
        # 只统计"## 关键事实"段内的条目
        fact_count = 0
        in_facts = False
        for line in lines:
            if line.strip() == "## 关键事实":
                in_facts = True; continue
            if in_facts and line.startswith("## "):
                break
            if in_facts and re.match(r"^\s*-\s+(?!\（待填写\）)", line):
                fact_count += 1

        # 扩展索引条数
        index_paths = parse_index(agent)

        print(f"  最后更新: {ts}")
        print(f"  关键事实: {fact_count} 条")
        print(f"  扩展索引: {len(index_paths)} 个文件")
        print()

    # 扩展层
    print(f"── 扩展层 (agents/{agent}/memory/) ──")
    if not os.path.exists(ed):
        print("  （目录不存在）")
    else:
        ext_files = sorted(f for f in os.listdir(ed) if f.endswith(".md"))
        if not ext_files:
            print("  （暂无文件）")
        else:
            for fname in ext_files:
                fpath = os.path.join(ed, fname)
                mtime = datetime.fromtimestamp(os.path.getmtime(fpath)).strftime("%Y-%m-%d %H:%M")
                size_kb = os.path.getsize(fpath) / 1024
                print(f"  {fname:<30} {mtime}  {size_kb:.1f}KB")
    print()

    # 归档层
    print(f"── 归档层 (agents/{agent}/memory/archive/) ──")
    if not os.path.exists(ad):
        print("  （目录不存在）")
    else:
        arc_files = sorted(f for f in os.listdir(ad) if f.endswith(".md"))
        if not arc_files:
            print("  （暂无归档）")
        else:
            for fname in arc_files:
                print(f"  {fname}")

# ── main ──────────────────────────────────────────────────────

def main():
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        sys.exit(0)

    cmd = args[0]

    if cmd == "init":
        if len(args) < 2:
            print("用法: init <agent>"); sys.exit(1)
        cmd_init(args[1])

    elif cmd == "write":
        if len(args) < 4:
            print("用法: write <agent> <topic> \"<内容>\""); sys.exit(1)
        cmd_write(args[1], args[2], args[3])

    elif cmd == "append":
        if len(args) < 4:
            print("用法: append <agent> <topic> \"<内容>\""); sys.exit(1)
        cmd_append(args[1], args[2], args[3])

    elif cmd == "read":
        if len(args) < 3:
            print("用法: read <agent> <topic>"); sys.exit(1)
        cmd_read(args[1], args[2])

    elif cmd == "update-core":
        if len(args) < 3:
            print("用法: update-core <agent> \"<markdown内容>\""); sys.exit(1)
        cmd_update_core(args[1], args[2])

    elif cmd == "note":
        if len(args) < 3:
            print("用法: note <agent> \"<一条事实>\""); sys.exit(1)
        cmd_note(args[1], args[2])

    elif cmd == "archive":
        if len(args) < 3:
            print("用法: archive <agent> <topic>"); sys.exit(1)
        cmd_archive(args[1], args[2])

    elif cmd == "index":
        if len(args) < 2:
            print("用法: index <agent>"); sys.exit(1)
        cmd_index(args[1])

    else:
        print(f"未知命令: {cmd}")
        sys.exit(1)

if __name__ == "__main__":
    main()
