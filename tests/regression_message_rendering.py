#!/usr/bin/env python3
"""Pure local message rendering regression prototype.

This script intentionally does not import Feishu/tmux delivery paths and does
not send live messages. It models the proposed rendering rules from
docs/message_rendering_spec.md and verifies snapshot-like invariants.
"""
from __future__ import annotations

import sys
import textwrap
import re
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
for _p in (_ROOT / "scripts", _ROOT / "src", _ROOT):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from claudeteam.messaging.renderer import (
    MessageEnvelope,
    render_envelope,
    render_feishu_markdown,
    render_inbox_text,
    render_tmux_prompt,
    split_feishu_markdown,
)


CASES = {
    "paragraphs": "第一段。\n\nSecond paragraph.",
    "lists": "- item1\n- item2\n  - nested should be reviewed",
    "code_blocks": "Run:\n```bash\necho hello\n```",
    "links": "See [docs](https://www.feishu.cn/content/7gprunv5).",
    "table_fallback": "| name | value |\n| --- | --- |\n| p95 | 430ms |",
    "long_message": "长文本 " + ("0123456789" * 160),
    "long_chinese": "长中文消息：" + ("飞书群聊回复必须完整保留，不能显示内部保护文案。\n" * 120),
    "runtime_command": (
        "CODEX_AGENT=toolsmith codex --dangerously-bypass-approvals-and-sandbox "
        "--model gpt-5.4 真实任务"
    ),
    "multilingual": "中文 English mixed 内容。",
    "emoji": "✅ 状态正常，下一步继续。",
    "feishu_tags": "<at id='all'></at> <button action=\"message\">Run</button>",
    "chinese_curly_quote_short": "好，把5秒抓10次的\u201c前N轮\u201d日志贴这里：开始啊",
    "chinese_curly_quote_long": (
        "老板要我抓 5 秒 10 次的\u201c前 N 轮\u201d日志，下面是完整证据：\n"
        + ("中文长段落，含逗号、句号；还有\u201c左双引号\u201d和\u201d右双引号\u201c"
           "以及\u2018单引号\u2019边界。\n" * 40)
    ),
}

FORBIDDEN_VISIBLE_MARKERS = ("内容过长", "截断", "卡片预览")


def assert_invariants(name: str, target: str, rendered: str):
    assert not any(marker in rendered for marker in FORBIDDEN_VISIBLE_MARKERS), name
    assert "CODEX_AGENT=" not in rendered, name
    assert "--dangerously-bypass-approvals-and-sandbox" not in rendered, name
    assert not re.search(r"<(?:at|button|table|row|col|highlight|note)\b", rendered, re.I), name
    assert rendered.count("```") % 2 == 0, name
    if name == "table_fallback" and target != "split":
        assert "| name | value |" not in rendered, name
        assert "name; value" in rendered, name
    if name == "long_message" and target == "card":
        assert "0123456789" in rendered, name
    if name.startswith("chinese_curly_quote"):
        assert "\u201c" in rendered and "\u201d" in rendered, name


def check_split_regression():
    long_body = CASES["chinese_curly_quote_long"]
    assert len(long_body) >= 1500, len(long_body)
    chunks = split_feishu_markdown(long_body, max_chars=420)
    assert len(chunks) > 1, "long curly-quote body did not split"
    for chunk in chunks:
        assert len(chunk) <= 420, len(chunk)
        assert_invariants("chinese_curly_quote_long", "split", chunk)
    joined = "\n".join(chunks)
    assert "前 N 轮" in joined, "header lost across chunks"
    assert joined.count("\u201c") >= 40, joined.count("\u201c")
    assert joined.count("\u201d") >= 40, joined.count("\u201d")


def main():
    check_split_regression()
    print("message rendering regression snapshots")
    for name, body in CASES.items():
        env = MessageEnvelope(kind="task", title=f"case:{name}", body_plain=body)
        card = render_envelope(env, "feishu_card")
        inbox = render_inbox_text(env.body_plain)
        prompt = render_tmux_prompt(env.kind, env.title, env.body_plain)
        for target, text in [
            ("card", card["markdown"]),
            ("inbox", inbox),
            ("tmux", prompt),
        ]:
            assert_invariants(name, target, text)
            preview = textwrap.shorten(" ".join(text.split()), width=100, placeholder=" ...")
            print(f"- {name}/{target}: {preview}")
    print("✅ message rendering regression passed")


if __name__ == "__main__":
    main()
