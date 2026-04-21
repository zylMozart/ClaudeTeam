#!/usr/bin/env python3
"""Credential inventory and doctor for ClaudeTeam CLI usage providers.

This module deliberately reports paths and remediation steps only. It never
copies credentials or prints secret file contents.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


PROJECT_ROOT = Path(os.environ.get("CLAUDE_PROJECT_DIR") or
                    Path(__file__).resolve().parent.parent)
HOME = Path(os.environ.get("HOME", str(Path.home()))).expanduser()

STATUS_DISABLED = "disabled"
STATUS_TOOL_MISSING = "tool_missing"
STATUS_CREDENTIAL_MISSING = "credential_missing"
STATUS_AUTH_EXPIRED = "auth_expired"
STATUS_CONTAINER_LOGIN_REQUIRED = "container_login_required"
STATUS_PERMISSION_DENIED = "permission_denied"
STATUS_API_FAILED = "api_failed"
STATUS_OK = "ok"


@dataclass(frozen=True)
class CliCredentialSpec:
    name: str
    title: str
    cli_values: tuple[str, ...]
    tool_commands: tuple[str, ...]
    credential_paths: tuple[Path, ...]
    project_paths: tuple[Path, ...]
    host_paths: tuple[Path, ...]
    login_hint: str
    migrate_hint: str


def _p(path: str) -> Path:
    return Path(path.replace("$HOME", str(HOME))).expanduser()


SPECS = {
    "cc": CliCredentialSpec(
        name="cc",
        title="Claude Code",
        cli_values=("claude-code",),
        tool_commands=("claude",),
        credential_paths=(
            _p("$HOME/.claude/.credentials.json"),
            _p("$HOME/.claude.json"),
        ),
        project_paths=(),
        host_paths=(
            Path("~/.claude/.credentials.json").expanduser(),
            Path("~/.claude.json").expanduser(),
        ),
        login_hint=("Run `claude` on the host once, or set ANTHROPIC_API_KEY "
                    "in .env for Docker deployments."),
        migrate_hint=("docker-compose.yml already bind-mounts "
                      "~/.claude/.credentials.json and ~/.claude.json."),
    ),
    "kimi": CliCredentialSpec(
        name="kimi",
        title="Kimi Code",
        cli_values=("kimi-code",),
        tool_commands=("kimi",),
        credential_paths=(
            _p("$HOME/.kimi/credentials/kimi-code.json"),
            PROJECT_ROOT / ".kimi-credentials" / "credentials" / "kimi-code.json",
        ),
        project_paths=(PROJECT_ROOT / ".kimi-credentials",),
        host_paths=(Path("~/.kimi").expanduser(),),
        login_hint=("Start a kimi-code agent and complete the device-code login, "
                    "or copy host ~/.kimi into ./.kimi-credentials/."),
        migrate_hint=("mkdir -p .kimi-credentials && "
                      "rsync -a ~/.kimi/ .kimi-credentials/"),
    ),
    "codex": CliCredentialSpec(
        name="codex",
        title="Codex CLI",
        cli_values=("codex-cli",),
        tool_commands=("codex", "codex-cli-usage"),
        credential_paths=(
            _p("$HOME/.codex/auth.json"),
            PROJECT_ROOT / ".codex-credentials" / "auth.json",
        ),
        project_paths=(PROJECT_ROOT / ".codex-credentials",),
        host_paths=(Path("~/.codex").expanduser(),),
        login_hint=("Run `codex` and complete device-code login, or copy host "
                    "~/.codex into ./.codex-credentials/."),
        migrate_hint=("mkdir -p .codex-credentials && "
                      "rsync -a ~/.codex/ .codex-credentials/"),
    ),
    "gemini": CliCredentialSpec(
        name="gemini",
        title="Gemini CLI",
        cli_values=("gemini-cli",),
        tool_commands=("gemini", "gemini-cli-usage"),
        credential_paths=(
            _p("$HOME/.gemini/oauth_creds.json"),
            PROJECT_ROOT / ".gemini-credentials" / "oauth_creds.json",
            _p("$HOME/.gemini/google_accounts.json"),
            PROJECT_ROOT / ".gemini-credentials" / "google_accounts.json",
        ),
        project_paths=(PROJECT_ROOT / ".gemini-credentials",),
        host_paths=(Path("~/.gemini").expanduser(),),
        login_hint=("Run `gemini` and complete Google OAuth, or copy host "
                    "~/.gemini into ./.gemini-credentials/."),
        migrate_hint=("mkdir -p .gemini-credentials && "
                      "rsync -a ~/.gemini/ .gemini-credentials/"),
    ),
}


def load_team(project_root: Path = PROJECT_ROOT) -> dict:
    try:
        return json.loads((project_root / "team.json").read_text())
    except Exception:
        return {"agents": {}}


def enabled_cli_values(team: dict | None = None) -> set[str]:
    team = team if team is not None else load_team()
    values = set()
    for info in (team.get("agents") or {}).values():
        if isinstance(info, dict):
            values.add(info.get("cli", "claude-code"))
    return values


def is_enabled(name: str, team: dict | None = None) -> bool:
    spec = SPECS[name]
    enabled = enabled_cli_values(team)
    return any(value in enabled for value in spec.cli_values)


def _existing(paths: Iterable[Path]) -> list[Path]:
    return [p for p in paths if p.exists()]


def _credential_ok(spec: CliCredentialSpec) -> bool:
    if spec.name == "cc" and os.environ.get("ANTHROPIC_API_KEY"):
        return True
    if spec.name == "cc":
        return all(p.is_file() and p.stat().st_size > 0
                   for p in spec.credential_paths)
    return any(p.is_file() and p.stat().st_size > 0 for p in spec.credential_paths)


def inspect_cli(name: str, *, respect_enabled: bool = True,
                check_tools: bool = True,
                team: dict | None = None) -> dict:
    spec = SPECS[name]
    enabled = is_enabled(name, team)
    missing_tools = ([cmd for cmd in spec.tool_commands if shutil.which(cmd) is None]
                     if check_tools else [])
    credential_ok = _credential_ok(spec)

    status = STATUS_OK
    if respect_enabled and not enabled:
        status = STATUS_DISABLED
    elif missing_tools:
        status = STATUS_TOOL_MISSING
    elif not credential_ok:
        status = STATUS_CREDENTIAL_MISSING

    return {
        "name": name,
        "title": spec.title,
        "status": status,
        "enabled": enabled,
        "cli_values": list(spec.cli_values),
        "missing_tools": missing_tools,
        "tools": list(spec.tool_commands),
        "credential_ok": credential_ok,
        "credential_paths": [str(p) for p in spec.credential_paths],
        "existing_credentials": [str(p) for p in _existing(spec.credential_paths)],
        "project_paths": [str(p) for p in spec.project_paths],
        "host_paths": [str(p) for p in spec.host_paths],
        "existing_host_paths": [str(p) for p in _existing(spec.host_paths)],
        "login_hint": spec.login_hint,
        "migrate_hint": spec.migrate_hint,
    }


def classify_failure(output: str) -> str:
    clean = " ".join((output or "").split()).lower()
    if "403" in clean and "401" in clean and "refresh" in clean:
        return STATUS_CONTAINER_LOGIN_REQUIRED
    if any(s in clean for s in (
        "oauth access token expired",
        "token expired",
        "expired token",
        "refresh token",
        "reauth",
        "not logged in",
        "login required",
        "please login",
        "please log in",
        "no credentials",
        "missing credentials",
    )):
        return STATUS_AUTH_EXPIRED
    if "403" in clean or "permission" in clean or "access denied" in clean:
        return STATUS_PERMISSION_DENIED
    return STATUS_API_FAILED


def status_label(status: str) -> str:
    return {
        STATUS_DISABLED: "未启用",
        STATUS_TOOL_MISSING: "缺工具",
        STATUS_CREDENTIAL_MISSING: "未登录/缺凭证",
        STATUS_AUTH_EXPIRED: "登录过期",
        STATUS_CONTAINER_LOGIN_REQUIRED: "需容器内重新登录",
        STATUS_PERMISSION_DENIED: "权限不足",
        STATUS_API_FAILED: "接口失败",
        STATUS_OK: "正常",
    }.get(status, status)


def status_detail(info: dict) -> str:
    status = info["status"]
    if status == STATUS_DISABLED:
        return (f"team.json 未启用 {info['title']} agent "
                f"(cli={','.join(info['cli_values'])})")
    if status == STATUS_TOOL_MISSING:
        return (f"容器/环境缺少命令: {', '.join(info['missing_tools'])}；"
                "请确认镜像构建完成或在 PATH 中安装对应 CLI/usage helper")
    if status == STATUS_CREDENTIAL_MISSING:
        return f"{info['login_hint']} 迁移: {info['migrate_hint']}"
    if status == STATUS_AUTH_EXPIRED:
        return f"登录态已过期；{info['login_hint']}"
    if status == STATUS_CONTAINER_LOGIN_REQUIRED:
        return ("凭证文件存在，但容器内 usage/refresh 被拒绝；请在容器内重新登录该 CLI，"
                "或临时使用宿主机 usage 查询")
    if status == STATUS_PERMISSION_DENIED:
        return "账号权限不足或 usage API 被拒绝；请确认订阅/账号权限"
    if status == STATUS_API_FAILED:
        return "usage API/工具调用失败；请查看 provider 输出"
    return "凭证与工具检查通过"


def doctor(*, respect_enabled: bool = True, check_tools: bool = True,
           names: Iterable[str] = ("cc", "kimi", "codex", "gemini")) -> list[dict]:
    team = load_team()
    return [inspect_cli(name, respect_enabled=respect_enabled,
                        check_tools=check_tools, team=team)
            for name in names]


def _print_text(rows: list[dict]) -> int:
    worst = 0
    for row in rows:
        status = row["status"]
        if status != STATUS_OK:
            worst = 1
        print(f"[{status}] {row['name']}: {status_label(status)}")
        print(f"  {status_detail(row)}")
        if row["existing_credentials"]:
            print("  credential: " + ", ".join(row["existing_credentials"]))
        elif row["existing_host_paths"]:
            print("  host candidate: " + ", ".join(row["existing_host_paths"]))
        if row["project_paths"]:
            print("  project dir: " + ", ".join(row["project_paths"]))
    return worst


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("doctor", "json"))
    parser.add_argument("--include-disabled", action="store_true",
                        help="check credentials even for CLIs not enabled in team.json")
    parser.add_argument("--skip-tools", action="store_true",
                        help="do not check command availability")
    parser.add_argument("--no-fail", action="store_true",
                        help="always exit 0; useful for startup hints")
    parser.add_argument("names", nargs="*")
    args = parser.parse_args()

    names = args.names or ["cc", "kimi", "codex", "gemini"]
    invalid = [name for name in names if name not in SPECS]
    if invalid:
        parser.error("unknown CLI name(s): " + ", ".join(invalid))
    rows = doctor(respect_enabled=not args.include_disabled,
                  check_tools=not args.skip_tools, names=names)
    if args.command == "json":
        print(json.dumps(rows, ensure_ascii=False, indent=2))
        rc = 0 if all(r["status"] == STATUS_OK for r in rows) else 1
    else:
        rc = _print_text(rows)
    return 0 if args.no_fail else rc


if __name__ == "__main__":
    raise SystemExit(main())
