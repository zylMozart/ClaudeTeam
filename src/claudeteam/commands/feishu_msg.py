"""Pure argv parser for scripts/feishu_msg.py command entrypoints."""
from __future__ import annotations

from dataclasses import dataclass, field
import inspect
from typing import Any, Callable, Mapping


USAGE_TEXT = """飞书通讯脚本 — ClaudeTeam（lark-cli 封装层）

用法:
  python3 scripts/feishu_msg.py send <收件人> <发件人> "<消息>" [优先级]
  python3 scripts/feishu_msg.py direct <收件人> <发件人> "<消息>"
  python3 scripts/feishu_msg.py say <发件人> ["<消息>"] [--image <路径>]
  python3 scripts/feishu_msg.py inbox <agent名称>
  python3 scripts/feishu_msg.py read <record_id>
  python3 scripts/feishu_msg.py status <agent> <状态> "<任务>" ["<阻塞原因>"]
  python3 scripts/feishu_msg.py log <agent> <类型> "<内容>" ["<关联对象>"]
  python3 scripts/feishu_msg.py workspace <agent>

依赖: lark-cli (npm install -g @larksuite/cli)
优先级: 高 | 中（默认）| 低
状态:   进行中 | 已完成 | 阻塞 | 待命
类型:   状态更新 | 任务日志 | 消息发出 | 消息收到 | 产出记录 | 阻塞上报
"""

SAY_USAGE = '用法: say <发件人> ["<消息>"] [--image <路径>]'
SEND_USAGE = '用法: send <收件人> <发件人> "<消息>" [优先级] [--task <task_id>] [--file <路径>]'
DIRECT_USAGE = "用法: direct <收件人> <发件人> '<消息>'"
INBOX_USAGE = "用法: inbox <agent>"
READ_USAGE = "用法: read <record_id>"
STATUS_USAGE = '用法: status <agent> <状态> "<任务>" ["<阻塞>"]'
LOG_USAGE = '用法: log <agent> <类型> "<内容>" ["<ref>"]'
WORKSPACE_USAGE = "用法: workspace <agent>"


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
    "send": ("to_agent", "from_agent", "message", "priority", "task_id", "file_path"),
    "direct": ("to_agent", "from_agent", "message"),
    "say": ("from_agent", "message", "image_path"),
    "inbox": ("agent_name",),
    "read": ("record_id",),
    "status": ("agent_name", "status", "task", "blocker"),
    "log": ("agent_name", "log_type", "content", "ref"),
    "workspace": ("agent_name",),
}


def usage_text() -> str:
    return USAGE_TEXT


def _usage_error(command: str, text: str) -> ParsedCommand:
    return ParsedCommand(command=command, message=text, exit_code=1)


def parse_argv(argv) -> ParsedCommand:
    args = list(argv or [])
    if not args:
        return ParsedCommand(command="help", message=usage_text(), exit_code=0)

    cmd = args[0]
    if cmd == "say":
        say_args = list(args[1:])
        image_path = ""
        if "--image" in say_args:
            idx = say_args.index("--image")
            image_path = say_args[idx + 1] if idx + 1 < len(say_args) else ""
            say_args = [a for i, a in enumerate(say_args) if i != idx and i != idx + 1]
        if len(say_args) < 1:
            return _usage_error("say", SAY_USAGE)
        return ParsedCommand(
            command="say",
            params={
                "from_agent": say_args[0],
                "message": say_args[1] if len(say_args) > 1 else "",
                "image_path": image_path,
            },
        )

    if cmd == "send":
        if len(args) < 4:
            return _usage_error("send", SEND_USAGE)
        rest = list(args[1:])
        task_id = ""
        file_path = ""
        for flag in ("--task", "--file"):
            if flag in rest:
                idx = rest.index(flag)
                if idx + 1 < len(rest):
                    val = rest[idx + 1]
                    rest.pop(idx + 1)
                    rest.pop(idx)
                    if flag == "--task":
                        task_id = val
                    else:
                        file_path = val
        return ParsedCommand(
            command="send",
            params={
                "to_agent": rest[0] if len(rest) > 0 else "",
                "from_agent": rest[1] if len(rest) > 1 else "",
                "message": rest[2] if len(rest) > 2 else "",
                "priority": rest[3] if len(rest) > 3 else "中",
                "task_id": task_id,
                "file_path": file_path,
            },
        )

    if cmd == "direct":
        if len(args) < 4:
            return _usage_error("direct", DIRECT_USAGE)
        return ParsedCommand(
            command="direct",
            params={
                "to_agent": args[1],
                "from_agent": args[2],
                "message": args[3],
            },
        )

    if cmd == "inbox":
        if len(args) < 2:
            return _usage_error("inbox", INBOX_USAGE)
        return ParsedCommand(command="inbox", params={"agent_name": args[1]})

    if cmd == "read":
        if len(args) < 2:
            return _usage_error("read", READ_USAGE)
        return ParsedCommand(command="read", params={"record_id": args[1]})

    if cmd == "status":
        if len(args) < 4:
            return _usage_error("status", STATUS_USAGE)
        return ParsedCommand(
            command="status",
            params={
                "agent_name": args[1],
                "status": args[2],
                "task": args[3],
                "blocker": args[4] if len(args) > 4 else "",
            },
        )

    if cmd == "log":
        if len(args) < 4:
            return _usage_error("log", LOG_USAGE)
        return ParsedCommand(
            command="log",
            params={
                "agent_name": args[1],
                "log_type": args[2],
                "content": args[3],
                "ref": args[4] if len(args) > 4 else "",
            },
        )

    if cmd == "workspace":
        if len(args) < 2:
            return _usage_error("workspace", WORKSPACE_USAGE)
        return ParsedCommand(command="workspace", params={"agent_name": args[1]})

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
