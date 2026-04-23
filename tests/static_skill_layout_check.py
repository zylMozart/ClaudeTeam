#!/usr/bin/env python3
"""Static checks for ClaudeTeam skill layout.

This test is deliberately no-live: it reads local Markdown files only and does
not import runtime modules, call Feishu, tmux, Docker, or the network.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SKILLS_DIR = ROOT / ".claude" / "skills"
FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---\n", re.DOTALL)


def fail(message: str) -> None:
    print(f"FAIL: {message}")
    raise SystemExit(1)


def parse_frontmatter(path: Path) -> dict[str, str]:
    text = path.read_text(encoding="utf-8")
    match = FRONTMATTER_RE.match(text)
    if not match:
        fail(f"{path.relative_to(ROOT)} missing YAML frontmatter")

    result: dict[str, str] = {}
    for raw in match.group(1).splitlines():
        if ":" not in raw:
            continue
        key, value = raw.split(":", 1)
        result[key.strip()] = value.strip().strip('"').strip("'")
    return result


def check_skill(skill_dir: Path) -> None:
    skill_file = skill_dir / "SKILL.md"
    rel = skill_dir.relative_to(ROOT)
    if not skill_file.exists():
        fail(f"{rel} missing SKILL.md")

    meta = parse_frontmatter(skill_file)
    expected = skill_dir.name
    actual = meta.get("name")
    if actual != expected:
        fail(f"{skill_file.relative_to(ROOT)} name={actual!r}, expected {expected!r}")
    if not meta.get("description"):
        fail(f"{skill_file.relative_to(ROOT)} missing description")

    body = skill_file.read_text(encoding="utf-8")
    if "python3 " in body and "python3 scripts/" not in body:
        fail(f"{skill_file.relative_to(ROOT)} uses python3 outside stable scripts wrappers")
    if any(secret in body.lower() for secret in ("app_secret", "tenant_access_token", "refresh_token")):
        fail(f"{skill_file.relative_to(ROOT)} appears to mention credential material")


def main() -> int:
    if not SKILLS_DIR.exists():
        fail(".claude/skills directory missing")
    if not (SKILLS_DIR / "README.md").exists():
        fail(".claude/skills/README.md missing")

    checked = 0
    for entry in sorted(SKILLS_DIR.iterdir()):
        if not entry.is_dir() or entry.name.startswith("_"):
            continue
        check_skill(entry)
        checked += 1

    if checked == 0:
        fail("no runtime skills checked")
    print(f"OK: {checked} skills checked")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
