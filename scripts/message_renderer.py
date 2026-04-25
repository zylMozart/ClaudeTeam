"""Pure message rendering helpers for ClaudeTeam.

The functions in this module must stay side-effect free: no Feishu calls, no
tmux calls, no filesystem writes. Delivery layers import these helpers to render
the same business message for Feishu cards, inbox rows, tmux prompts, and logs.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field


RUNTIME_CMD_RE = re.compile(
    r"^CODEX_AGENT=[A-Za-z0-9_.-]+\s+codex\s+"
    r"--dangerously-bypass-approvals-and-sandbox"
    r"(?:\s+--model\s+\S+)?"
    r"(?:\s+-c\s+(?:'[^']*'|\"[^\"]*\"|\S+))?"
    r"\s*"
)

FEISHU_TAG_RE = re.compile(
    r"</?(?:at|button|table|row|col|highlight|note|record|chart|font)\b[^>]*>",
    re.IGNORECASE,
)

ANGLE_TAG_RE = re.compile(r"<[^>\n]+>")

PIPE_TABLE_RE = re.compile(r"^\s*\|.+\|\s*$")
CODE_FENCE_RE = re.compile(r"^\s*```")

DEFAULT_FEISHU_MARKDOWN_CHUNK_LIMIT = 3500


@dataclass
class MessageEnvelope:
    kind: str
    title: str
    body_plain: str
    priority: str = "中"
    audience: str = "group"
    source: dict = field(default_factory=dict)


def strip_runtime_commands(text: str) -> str:
    lines = []
    for line in str(text or "").splitlines():
        cleaned = RUNTIME_CMD_RE.sub("", line, count=1).strip()
        if cleaned:
            lines.append(cleaned)
    return "\n".join(lines).strip()


def escape_feishu_tags(text: str) -> str:
    text = FEISHU_TAG_RE.sub(
        lambda m: m.group(0).replace("<", "&lt;").replace(">", "&gt;"),
        str(text or ""),
    )
    return ANGLE_TAG_RE.sub(
        lambda m: m.group(0).replace("<", "&lt;").replace(">", "&gt;"),
        text,
    )


def degrade_pipe_tables(text: str) -> str:
    out = []
    in_table = False
    for raw in str(text or "").splitlines():
        line = raw.rstrip()
        if PIPE_TABLE_RE.match(line):
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            if all(set(c) <= {"-", ":"} for c in cells):
                in_table = True
                continue
            prefix = "- " if in_table else "Table: "
            out.append(prefix + "; ".join(cells))
            in_table = True
            continue
        in_table = False
        out.append(line)
    return "\n".join(out)


def balance_code_fences(text: str) -> str:
    text = str(text or "")
    if text.count("```") % 2:
        return text.rstrip() + "\n```"
    return text


def normalize_body(body: str, *, card: bool = False, card_limit: int | None = None) -> str:
    body = strip_runtime_commands(body)
    body = escape_feishu_tags(body)
    body = degrade_pipe_tables(body)
    body = balance_code_fences(body)
    return body.strip()


def render_feishu_markdown(body: str) -> str:
    return normalize_body(body, card=True)


def _split_oversized_line(line: str, limit: int):
    if len(line) <= limit:
        return [line]
    return [line[i:i + limit] for i in range(0, len(line), limit)]


def split_feishu_markdown(
    body: str,
    *,
    max_chars: int = DEFAULT_FEISHU_MARKDOWN_CHUNK_LIMIT,
) -> list[str]:
    """Render and split markdown for Feishu cards without dropping content.

    The renderer must never hide business text behind a preview truncation
    marker. Delivery code can send each returned chunk as a separate card.
    If a split happens inside a fenced code block, each chunk is balanced by
    closing and reopening the fence so Feishu still renders it predictably.
    """
    if max_chars < 64:
        raise ValueError("max_chars must be at least 64")

    text = render_feishu_markdown(body)
    if not text:
        return [""]
    if len(text) <= max_chars:
        return [text]

    chunks: list[str] = []
    current = ""
    in_fence = False

    def flush():
        nonlocal current
        chunk = current.rstrip()
        if in_fence and chunk:
            chunk += "\n```"
        if chunk:
            chunks.append(chunk)
        current = "```\n" if in_fence else ""

    line_limit = max_chars - 8
    for line in text.splitlines(keepends=True):
        for part in _split_oversized_line(line, line_limit):
            close_overhead = 4 if in_fence else 0
            if current and len(current) + len(part) + close_overhead > max_chars:
                flush()
            current += part
            if CODE_FENCE_RE.match(part.strip()):
                in_fence = not in_fence

    if current.strip():
        chunks.append(current.rstrip())
    return chunks


def render_inbox_text(body: str) -> str:
    return normalize_body(body)


def render_log_text(body: str, *, limit: int = 10000) -> str:
    return normalize_body(body)[:limit]


def render_tmux_prompt(kind: str, title: str, body: str, agent: str = "") -> str:
    rendered = normalize_body(body)
    suffix = "请处理后回复。"
    if agent:
        suffix = f"请处理后回复；如需群聊回复，使用: python3 scripts/feishu_msg.py say {agent} \"<你的回复>\""
    return f"【{kind}】{title}\n{rendered}\n\n{suffix}"


def render_envelope(env: MessageEnvelope, target: str):
    if target == "feishu_card":
        return {"title": env.title, "markdown": render_feishu_markdown(env.body_plain)}
    if target == "inbox_text":
        return render_inbox_text(env.body_plain)
    if target == "tmux_prompt":
        return render_tmux_prompt(env.kind, env.title, env.body_plain)
    if target == "log_text":
        return render_log_text(env.body_plain)
    raise ValueError(f"unknown render target: {target}")
