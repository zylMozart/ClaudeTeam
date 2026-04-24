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
)


CASES = {
    "paragraphs": "第一段。\n\nSecond paragraph.",
    "lists": "- item1\n- item2\n  - nested should be reviewed",
    "code_blocks": "Run:\n```bash\necho hello\n```",
    "links": "See [docs](https://www.feishu.cn/content/7gprunv5).",
    "table_fallback": "| name | value |\n| --- | --- |\n| p95 | 430ms |",
    "long_message": "长文本 " + ("0123456789" * 160),
    "runtime_command": (
        "CODEX_AGENT=toolsmith codex --dangerously-bypass-approvals-and-sandbox "
        "--model gpt-5.4 真实任务"
    ),
    "multilingual": "中文 English mixed 内容。",
    "emoji": "✅ 状态正常，下一步继续。",
    "feishu_tags": "<at id='all'></at> <button action=\"message\">Run</button>",
}


def assert_invariants(name: str, target: str, rendered: str):
    assert "CODEX_AGENT=" not in rendered, name
    assert "--dangerously-bypass-approvals-and-sandbox" not in rendered, name
    assert not re.search(r"<(?:at|button|table|row|col|highlight|note)\b", rendered, re.I), name
    assert rendered.count("```") % 2 == 0, name
    if name == "table_fallback":
        assert "| name | value |" not in rendered, name
        assert "name; value" in rendered, name
    if name == "long_message" and target == "card":
        assert "已截断" in rendered, name


def main():
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
