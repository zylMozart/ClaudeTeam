"""Pure argv parser and injected dispatcher for scripts/kanban_sync.py."""
from __future__ import annotations

from dataclasses import dataclass, field
import inspect
from typing import Any, Callable, Mapping


USAGE_TEXT = """项目看板同步 — ClaudeTeam

用法:
  python3 scripts/kanban_sync.py init
  python3 scripts/kanban_sync.py sync
  python3 scripts/kanban_sync.py daemon [--interval N]
  python3 scripts/kanban_sync.py help

说明:
  --interval N   后台同步间隔（秒），默认 60
"""

DAEMON_USAGE = "用法: daemon [--interval N]"


@dataclass(frozen=True)
class ParsedCommand:
    command: str
    params: dict[str, Any] = field(default_factory=dict)
    message: str = ""
    exit_code: int = 0


@dataclass(frozen=True)
class DispatchResult:
    command: str
    exit_code: int
    message: str = ""
    handled: bool = False
    value: Any = None


COMMAND_PARAM_KEYS: dict[str, tuple[str, ...]] = {
    "init": tuple(),
    "sync": tuple(),
    "daemon": ("interval",),
}


def usage_text() -> str:
    return USAGE_TEXT


def _usage_error(command: str, text: str) -> ParsedCommand:
    return ParsedCommand(command=command, message=text, exit_code=1)


def _parse_interval(value: str) -> int | None:
    try:
        interval = int(value)
    except (TypeError, ValueError):
        return None
    if interval <= 0:
        return None
    return interval


def parse_argv(argv) -> ParsedCommand:
    args = list(argv or [])
    if not args:
        return ParsedCommand(command="help", message=usage_text(), exit_code=0)

    cmd = args[0]
    if cmd in ("help", "-h", "--help"):
        return ParsedCommand(command="help", message=usage_text(), exit_code=0)

    if cmd == "init":
        return ParsedCommand(command="init")

    if cmd == "sync":
        return ParsedCommand(command="sync")

    if cmd == "daemon":
        daemon_args = args[1:]
        if not daemon_args:
            return ParsedCommand(command="daemon", params={"interval": 60})
        if len(daemon_args) != 2 or daemon_args[0] != "--interval":
            return _usage_error("daemon", DAEMON_USAGE)
        interval = _parse_interval(daemon_args[1])
        if interval is None:
            return _usage_error("daemon", DAEMON_USAGE)
        return ParsedCommand(command="daemon", params={"interval": interval})

    return ParsedCommand(
        command="unknown",
        message=f"未知命令: {cmd}",
        exit_code=1,
        params={"raw_command": cmd},
    )


def _resolve_handler(handlers: Mapping[str, Callable[..., Any]] | object | None, command: str):
    if handlers is None:
        return None
    if isinstance(handlers, Mapping):
        return handlers.get(command)
    return getattr(handlers, command, None)


def _bound_kwargs(handler: Callable[..., Any], keys: tuple[str, ...], params: dict[str, Any]) -> dict[str, Any]:
    kwargs = {key: params.get(key) for key in keys if key in params}
    signature = inspect.signature(handler)
    if any(p.kind == inspect.Parameter.VAR_KEYWORD for p in signature.parameters.values()):
        return kwargs
    accepted = {
        name for name, p in signature.parameters.items()
        if p.kind in (inspect.Parameter.POSITIONAL_OR_KEYWORD, inspect.Parameter.KEYWORD_ONLY)
    }
    return {key: value for key, value in kwargs.items() if key in accepted}


def dispatch(
    parsed: ParsedCommand,
    handlers: Mapping[str, Callable[..., Any]] | object | None = None,
) -> DispatchResult:
    command = parsed.command

    if command == "help":
        return DispatchResult(command=command, exit_code=parsed.exit_code, message=parsed.message, handled=False)
    if command == "unknown":
        return DispatchResult(command=command, exit_code=parsed.exit_code, message=parsed.message, handled=False)
    if parsed.exit_code != 0:
        return DispatchResult(command=command, exit_code=parsed.exit_code, message=parsed.message, handled=False)

    handler = _resolve_handler(handlers, command)
    if handler is None:
        return DispatchResult(
            command=command,
            exit_code=2,
            message=f"缺少命令处理器: {command}",
            handled=False,
        )
    if not callable(handler):
        return DispatchResult(
            command=command,
            exit_code=2,
            message=f"命令处理器不可调用: {command}",
            handled=False,
        )

    keys = COMMAND_PARAM_KEYS.get(command, tuple(parsed.params.keys()))
    kwargs = _bound_kwargs(handler, keys, parsed.params)
    result = handler(**kwargs)
    return DispatchResult(command=command, exit_code=0, handled=True, value=result)


def run(
    argv,
    handlers: Mapping[str, Callable[..., Any]] | object | None = None,
) -> DispatchResult:
    return dispatch(parse_argv(argv), handlers=handlers)
