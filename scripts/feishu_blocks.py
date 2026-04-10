#!/usr/bin/env python3
"""
Markdown → 飞书 Block 转换 — ClaudeTeam

提供 Markdown 文本到飞书 docx block 结构的解析功能。
从 feishu_sync.py 和 upload_folded_doc.py 合并而来，统一接口。
"""
import re

# ── 语言映射 ──────────────────────────────────────────────────

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

# ── Block 构建工具 ────────────────────────────────────────────

def make_text_run(text, bold=False, italic=False, inline_code=False, link_url=""):
    """构建飞书 text_run 元素。"""
    style = {}
    if bold:        style["bold"] = True
    if italic:      style["italic"] = True
    if inline_code: style["inline_code"] = True
    if link_url:    style["link"] = {"url": link_url}
    elem = {"text_run": {"content": text}}
    if style:
        elem["text_run"]["text_element_style"] = style
    return elem


def make_text_block(block_type, runs, style=None):
    """构建飞书文本类 block（text/heading/bullet/ordered）。"""
    key = {2: "text", 3: "heading1", 4: "heading2", 5: "heading3",
           6: "heading4", 12: "bullet", 13: "ordered"}[block_type]
    return {"block_type": block_type, key: {"elements": runs, "style": style or {}}}


def make_code_block(code_text, language=1):
    """构建飞书代码 block。"""
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

# ── Inline 解析 ───────────────────────────────────────────────

_TOKEN_RE = re.compile(
    r'(`[^`]+`)'               # 行内代码
    r'|(\*\*[^*]+\*\*)'        # 加粗 **
    r'|(__[^_]+__)'             # 加粗 __
    r'|(\*[^*]+\*)'            # 斜体 *
    r'|(_[^_]+_)'              # 斜体 _
    r'|(\[[^\]]+\]\([^)]+\))'  # 链接
    r'|([^`*_\[]+)'            # 普通文本
)

def parse_inline(text):
    """解析 Markdown 行内格式，返回 text_run 列表。"""
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

# ── Markdown → Block 列表 ────────────────────────────────────

def parse_markdown_to_blocks(content):
    """将 Markdown 字符串解析为飞书 docx block 列表。"""
    blocks = []
    lines = content.split("\n")
    i = 0

    while i < len(lines):
        line = lines[i]

        # 四级及以上标题 -> Heading3
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

        # 表格 -> 降级为代码块
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


def parse_single_line(line, lines, i):
    """解析单行 Markdown，返回 block dict、None（空行/分隔线）、
    或 (block, new_i) 如果消耗了多行（代码块）。"""

    if re.match(r'^#{4,} ', line):
        return make_text_block(6, parse_inline(re.sub(r'^#{4,} ', '', line)))
    if line.startswith("### "):
        return make_text_block(5, parse_inline(line[4:]))
    if line.startswith("## "):
        return make_text_block(4, parse_inline(line[3:]))
    if line.startswith("# "):
        return make_text_block(3, parse_inline(line[2:]))
    if re.match(r'^[-*] ', line):
        return make_text_block(12, parse_inline(line[2:]))
    if re.match(r'^\d+\. ', line):
        return make_text_block(13, parse_inline(re.sub(r'^\d+\. ', '', line)))
    if re.match(r'^[-*_]{3,}$', line.strip()):
        return None
    if line.strip() == "":
        return None

    # 普通文本
    return make_text_block(2, parse_inline(line))
