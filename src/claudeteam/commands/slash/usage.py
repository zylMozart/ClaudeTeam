"""Handler for /usage slash command."""
from __future__ import annotations

import re
from .context import SlashContext

_USAGE_LINE_RE = re.compile(
    r"^\s*(?P<label>[^:]+?)\s*:\s*(?P<pct>[\d.]+)%\s+"
    r"(?:\(重置:\s*(?P<reset>.+?)\)|resets\s+(?P<reset_en>.+?))\s*$")
_USAGE_EXTRA_RE = re.compile(
    r"^\s*(?P<label>Extra usage|额外用量)\s*:\s*\$?(?P<used>[\d.]+)\s*/\s*\$?(?P<cap>[\d.]+)\s+"
    r"\((?P<pct>[\d.]+)%\)\s*(?:\[(?P<ccy>\S+)\])?\s*$")


def _pct_color(p: int) -> str:
    if p >= 80:
        return "red"
    if p >= 50:
        return "orange"
    return "green"


def _remaining_pct_color(p: int) -> str:
    if p <= 20:
        return "red"
    if p <= 50:
        return "orange"
    return "green"


def _fmt_pct(value: float) -> str:
    if float(value).is_integer():
        return str(int(value))
    return f"{value:.1f}".rstrip("0").rstrip(".")


def parse_usage_lines(raw_lines: list[str]) -> list[dict]:
    """Parse raw usage tool output lines into structured dicts."""
    items = []
    for line in raw_lines:
        m = _USAGE_LINE_RE.match(line)
        if m:
            items.append({
                "type": "quota",
                "label": m.group("label").strip(),
                "pct": float(m.group("pct")),
                "reset": (m.group("reset") or m.group("reset_en") or "").strip(),
            })
            continue
        m2 = _USAGE_EXTRA_RE.match(line)
        if m2:
            items.append({
                "type": "extra",
                "label": m2.group("label").strip(),
                "used": float(m2.group("used")),
                "cap": float(m2.group("cap")),
                "pct": float(m2.group("pct")),
                "ccy": m2.group("ccy") or "USD",
            })
    return items


def build_usage_card(sections: list[dict], title_suffix: str = "") -> dict:
    """Build a Feishu card from parsed usage sections."""
    elements = [
        {"tag": "markdown", "content": f"📊 **Claude 额度快照**{title_suffix}"}
    ]
    for sec in sections:
        label = sec.get("tool", "")
        items = sec.get("items", [])
        if not items:
            elements.append({
                "tag": "markdown",
                "content": f"**{label}**: _(无数据)_"
            })
            continue
        lines = [f"**{label}**"]
        for it in items:
            if it["type"] == "quota":
                pct = it["pct"]
                color = _pct_color(int(pct))
                rem = 100 - pct
                rem_color = _remaining_pct_color(int(rem))
                lines.append(
                    f"  {it['label']}: <font color='{color}'>{_fmt_pct(pct)}%</font> 已用 "
                    f"(<font color='{rem_color}'>{_fmt_pct(rem)}%</font> 剩余)"
                    + (f" 重置: {it['reset']}" if it.get("reset") else "")
                )
            else:
                pct = it["pct"]
                color = _pct_color(int(pct))
                lines.append(
                    f"  {it['label']}: <font color='{color}'>"
                    f"${_fmt_pct(it['used'])}/{_fmt_pct(it['cap'])}</font> ({_fmt_pct(pct)}%)"
                )
        elements.append({"tag": "markdown", "content": "\n".join(lines)})
    return {"schema": "2.0", "body": {"elements": elements}}


def handle_usage(text: str, ctx: SlashContext) -> dict | None:
    if not re.fullmatch(r"/usage\s*", text):
        return None
    now_str = ctx.now_bj().strftime("%Y-%m-%d %H:%M 北京时间")
    tools = ["claude-cli-usage", "codex-cli-usage", "gemini-cli-usage", "kimi-cli-usage"]
    sections = []
    for tool in tools:
        raw = ctx.query_usage(tool)
        if raw:
            sections.append({"tool": tool.replace("-cli-usage", "").capitalize(),
                              "items": parse_usage_lines(raw)})
    card = build_usage_card(sections, f" @ {now_str}")
    text_lines = [f"📊 Claude 额度快照 @ {now_str}"]
    for sec in sections:
        for it in sec.get("items", []):
            if it["type"] == "quota":
                text_lines.append(f"  {sec['tool']} {it['label']}: {_fmt_pct(it['pct'])}%")
    return {"text": "\n".join(text_lines), "card": card}
