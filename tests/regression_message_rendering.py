#!/usr/bin/env python3
"""Local message rendering regression checks.

This script does not send live Feishu messages. Delivery-path checks monkeypatch
Feishu calls and only inspect the cards that would be sent.
"""
from __future__ import annotations

import sys
import textwrap
import re
import subprocess
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
    "long_lists": "\n".join(f"- 第 {i} 项：保留列表换行和内容" for i in range(120)),
    "long_code_block": "Run:\n```bash\n" + "\n".join(
        f"echo line-{i:03d}" for i in range(180)
    ) + "\n```",
    "runtime_command": (
        "CODEX_AGENT=toolsmith codex --dangerously-bypass-approvals-and-sandbox "
        "--model gpt-5.4 真实任务"
    ),
    "runtime_command_with_config": (
        "CODEX_AGENT=toolsmith codex --dangerously-bypass-approvals-and-sandbox "
        "--model gpt-5.4 -c 'model_reasoning_effort=\"high\"' 真实任务"
    ),
    "command_example": (
        "Codex example:\n"
        "CODEX_AGENT=<agent> codex --dangerously-bypass-approvals-and-sandbox "
        "--model gpt-5.4 -c 'model_reasoning_effort=\"high\"'"
    ),
    "angle_placeholders": (
        "Use <agent> and <model>, but escape <at id='all'></at>."
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
    if name != "command_example":
        assert "CODEX_AGENT=" not in rendered, name
        assert "--dangerously-bypass-approvals-and-sandbox" not in rendered, name
    assert not re.search(r"<(?:at|button|table|row|col|highlight|note)\b", rendered, re.I), name
    assert rendered.count("```") % 2 == 0, name
    if name == "runtime_command_with_config":
        assert "model_reasoning_effort" not in rendered, name
        assert rendered == "真实任务" or "真实任务" in rendered, name
    if name == "command_example":
        assert "CODEX_AGENT=&lt;agent&gt;" in rendered, name
        assert "--dangerously-bypass-approvals-and-sandbox" in rendered, name
        assert "model_reasoning_effort" in rendered, name
    if name == "angle_placeholders":
        assert "&lt;agent&gt;" in rendered and "&lt;model&gt;" in rendered, name
        assert "<agent>" not in rendered and "<model>" not in rendered, name
    if name == "table_fallback" and target != "split":
        assert "| name | value |" not in rendered, name
        assert "name; value" in rendered, name
    if name == "long_message" and target == "card":
        assert "0123456789" in rendered, name
    if name.startswith("chinese_curly_quote"):
        assert "“" in rendered and "”" in rendered, name


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
    assert joined.count("“") >= 40, joined.count("“")
    assert joined.count("”") >= 40, joined.count("”")
    # Additional split cases from main
    split_cases = {
        "long_lists": CASES["long_lists"],
        "long_code_block": CASES["long_code_block"],
        "table_fallback": CASES["table_fallback"] + "\n" + CASES["long_lists"],
    }
    for name, body in split_cases.items():
        chunks = split_feishu_markdown(body, max_chars=420)
        assert len(chunks) > 1, name
        for chunk in chunks:
            assert len(chunk) <= 420, (name, len(chunk))
            assert_invariants(name, "split", chunk)
        joined = "\n".join(chunks)
        if name == "long_lists":
            assert "第 0 项" in joined and "第 119 项" in joined, name
        if name == "long_code_block":
            assert "echo line-000" in joined and "echo line-179" in joined, name


def main():
    check_shell_argv_regression()
    check_split_regression()
    check_delivery_card_splitting()
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


def check_shell_argv_regression():
    broken = (
        "python3 -c \"import sys; print(repr(sys.argv[1:]))\" "
        "$'Codex command:\\nCODEX_AGENT=<agent> codex "
        "--dangerously-bypass-approvals-and-sandbox --model gpt-5.4 "
        "-c 'model_reasoning_effort=\"high\"'\\nNext line'"
    )
    safe = (
        "tmp=$(mktemp); "
        "printf '%s\\n' 'Codex command:' "
        "'CODEX_AGENT=<agent> codex --dangerously-bypass-approvals-and-sandbox --model gpt-5.4 -c '\\''model_reasoning_effort=\"high\"'\\''' "
        "'Next line' > \"$tmp\"; "
        "python3 -c \"import sys, pathlib; print(repr(pathlib.Path(sys.argv[1]).read_text()))\" \"$tmp\"; "
        "rm -f \"$tmp\""
    )
    broken_res = subprocess.run(["bash", "-lc", broken], capture_output=True, text=True)
    safe_res = subprocess.run(["bash", "-lc", safe], capture_output=True, text=True)
    assert broken_res.returncode == 0, broken_res.stderr
    assert safe_res.returncode == 0, safe_res.stderr
    assert "\\\\nNext line" in broken_res.stdout, broken_res.stdout
    assert "\\\\nNext line" not in safe_res.stdout, safe_res.stdout


def check_split_regression():
    split_cases = {
        "long_chinese": CASES["long_chinese"],
        "long_lists": CASES["long_lists"],
        "long_code_block": CASES["long_code_block"],
        "table_fallback": CASES["table_fallback"] + "\n" + CASES["long_lists"],
    }
    for name, body in split_cases.items():
        chunks = split_feishu_markdown(body, max_chars=420)
        assert len(chunks) > 1, name
        for chunk in chunks:
            assert len(chunk) <= 420, (name, len(chunk))
            assert_invariants(name, "split", chunk)
        joined = "\n".join(chunks)
        if name == "long_chinese":
            assert "飞书群聊回复必须完整保留" in joined, name
        if name == "long_lists":
            assert "第 0 项" in joined and "第 119 项" in joined, name
        if name == "long_code_block":
            assert "echo line-000" in joined and "echo line-179" in joined, name


def _card_markdown(card):
    return "\n".join(
        elem.get("content", "")
        for elem in card.get("elements", [])
        if elem.get("tag") == "markdown"
    )


def check_delivery_card_splitting():
    import feishu_msg

    long_body = CASES["long_chinese"] + "\n" + CASES["long_code_block"]
    captured = []

    originals = {
        "CHAT": feishu_msg.CHAT,
        "_lark_im_send": feishu_msg._lark_im_send,
        "ws_log": feishu_msg.ws_log,
        "bitable_insert_message": feishu_msg.bitable_insert_message,
        "_notify_agent_tmux": feishu_msg._notify_agent_tmux,
    }

    def fake_send(chat_id, content=None, markdown=None, image=None, card=None):
        if card:
            captured.append(card)
        return {}

    try:
        feishu_msg.CHAT = lambda: "chat-test"
        feishu_msg._lark_im_send = fake_send
        feishu_msg.ws_log = lambda *args, **kwargs: None
        feishu_msg.bitable_insert_message = lambda *args, **kwargs: "rec-test"
        feishu_msg._notify_agent_tmux = lambda *args, **kwargs: None

        captured.clear()
        feishu_msg.cmd_say("toolsmith", long_body)
        assert len(captured) > 1, "say did not split long group message"
        assert_cards_complete(captured, "say")

        captured.clear()
        feishu_msg.cmd_send("manager", "toolsmith", long_body, "高")
        assert len(captured) > 1, "send did not split group notification"
        assert_cards_complete(captured, "send")

        captured.clear()
        feishu_msg.cmd_direct("manager", "toolsmith", long_body)
        assert len(captured) > 1, "direct did not split group notification"
        assert_cards_complete(captured, "direct")

        captured.clear()
        cards = feishu_msg.build_system_cards(long_body, max_chars=420)
        captured.extend(cards)
        assert len(captured) > 1, "system card did not split"
        assert_cards_complete(captured, "system")
    finally:
        for name, value in originals.items():
            setattr(feishu_msg, name, value)


def assert_cards_complete(cards, label):
    joined = "\n".join(_card_markdown(card) for card in cards)
    assert not any(marker in joined for marker in FORBIDDEN_VISIBLE_MARKERS), label
    assert "飞书群聊回复必须完整保留" in joined, label
    assert "echo line-000" in joined and "echo line-179" in joined, label


if __name__ == "__main__":
    try:
        main()
    except AssertionError as exc:
        print(f"❌ regression failed: {exc}", file=sys.stderr)
        raise
