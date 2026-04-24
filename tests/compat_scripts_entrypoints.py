#!/usr/bin/env python3
"""No-live compatibility checks for script entrypoints/wrappers.

Focus: legacy script paths and CLI contracts that must survive thin-wrapper
migration.
"""
from __future__ import annotations

import contextlib
import builtins
import importlib
import io
import os
import subprocess
import sys
import tempfile
import textwrap
from types import SimpleNamespace
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
SRC = ROOT / "src"
for path in (SCRIPTS, SRC, ROOT):
    if path.exists() and str(path) not in sys.path:
        sys.path.insert(0, str(path))

RESOLVE_SCRIPT = ROOT / "src" / "claudeteam" / "cli_adapters" / "resolve.py"
FEISHU_MSG_SCRIPT = ROOT / "scripts" / "feishu_msg.py"
WATCHDOG_SCRIPT = ROOT / "scripts" / "watchdog.py"
MODULE_WRAPPERS = ()


def run_python(script: Path, *args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["python3", str(script), *args],
        cwd=str(ROOT),
        text=True,
        capture_output=True,
        env=env,
        check=False,
    )


def run_python_code(code: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["python3", "-c", code],
        cwd=str(ROOT),
        text=True,
        capture_output=True,
        env=env,
        check=False,
    )


def make_team_env(cli_name: str = "claude-code") -> tuple[dict[str, str], tempfile.TemporaryDirectory]:
    tmp = tempfile.TemporaryDirectory()
    team_file = Path(tmp.name) / "team.json"
    team_file.write_text(
        (
            "{"
            '"session":"compat","agents":{"manager":{"cli":"%s"},"toolsmith":{"cli":"%s"}}' % (cli_name, cli_name)
            + "}\n"
        ),
        encoding="utf-8",
    )
    env = os.environ.copy()
    env["CLAUDETEAM_TEAM_FILE"] = str(team_file)
    return env, tmp


def _parsed_field_view(value):
    if isinstance(value, dict):
        return dict(value)
    out = {}
    if hasattr(value, "_asdict"):
        out.update(value._asdict())
    if hasattr(value, "__dict__"):
        out.update(vars(value))
    return out


def _flatten_text(value) -> str:
    if value is None:
        return ""
    if isinstance(value, (str, int, float, bool)):
        return str(value)
    if isinstance(value, dict):
        parts = []
        for key, item in value.items():
            parts.append(str(key))
            parts.append(_flatten_text(item))
        return " ".join(parts)
    if isinstance(value, (list, tuple, set)):
        return " ".join(_flatten_text(item) for item in value)
    if hasattr(value, "_asdict"):
        return _flatten_text(value._asdict())
    if hasattr(value, "__dict__"):
        return _flatten_text(vars(value))
    return repr(value)


def _extract_command_name(parsed) -> str | None:
    view = _parsed_field_view(parsed)
    for key in ("command", "cmd", "name", "action", "subcommand"):
        value = view.get(key)
        if isinstance(value, str) and value:
            return value
    for key in ("args", "payload", "options", "data"):
        nested = view.get(key)
        nested_view = _parsed_field_view(nested)
        for command_key in ("command", "cmd", "name", "action", "subcommand"):
            value = nested_view.get(command_key)
            if isinstance(value, str) and value:
                return value
    return None


def _assert_parse_error_or_usage(parse_argv, argv: list[str]) -> None:
    try:
        parsed = parse_argv(list(argv))
    except SystemExit as exc:
        code = exc.code if isinstance(exc.code, int) else 1
        assert code != 0, f"unexpected success exit for argv={argv!r}"
        return
    except Exception as exc:
        assert isinstance(exc, (ValueError, RuntimeError, TypeError)), (
            f"unexpected exception type for argv={argv!r}: {type(exc).__name__}: {exc}"
        )
        return

    text = _flatten_text(parsed)
    markers = ("usage", "用法", "error", "未知命令", "invalid", "empty")
    assert any(marker in text.lower() for marker in markers), (
        f"argv={argv!r} should expose usage/error semantics, got: {parsed!r}"
    )


def test_feishu_msg_entrypoint_import_and_usage_contracts() -> None:
    import_code = """
import sys
from pathlib import Path
root = Path('.').resolve()
for p in (root / 'scripts', root / 'src', root):
    s = str(p)
    if s not in sys.path:
        sys.path.insert(0, s)
import feishu_msg
assert callable(feishu_msg.main)
print('OK:feishu_msg import')
""".strip()
    imported = run_python_code(import_code)
    assert imported.returncode == 0, imported
    assert "OK:feishu_msg import" in imported.stdout
    assert "Traceback" not in imported.stderr

    root_help = run_python(FEISHU_MSG_SCRIPT)
    assert root_help.returncode == 0, root_help
    assert "飞书通讯脚本" in root_help.stdout

    unknown = run_python(FEISHU_MSG_SCRIPT, "__compat_unknown__")
    assert unknown.returncode == 1, unknown
    assert "未知命令" in unknown.stdout

    usage_cases = (
        (("send",), "用法: send"),
        (("direct",), "用法: direct"),
        (("say",), "用法: say"),
        (("inbox",), "用法: inbox"),
        (("read",), "用法: read"),
        (("status",), "用法: status"),
        (("log",), "用法: log"),
        (("workspace",), "用法: workspace"),
    )
    for argv, usage_text in usage_cases:
        result = run_python(FEISHU_MSG_SCRIPT, *argv)
        assert result.returncode == 1, (argv, result)
        assert usage_text in (result.stdout + result.stderr), (argv, result.stdout, result.stderr)


def test_feishu_msg_main_delegate_compat_contract() -> None:
    feishu_msg = importlib.import_module("feishu_msg")

    required_symbols = (
        "cmd_send",
        "cmd_direct",
        "cmd_say",
        "cmd_inbox",
        "cmd_read",
        "cmd_status",
        "cmd_log",
        "cmd_workspace",
        "_lark_run",
        "_lark_im_send",
        "_lark_base_create",
        "_lark_base_search",
        "_lark_base_update",
        "_lark_base_list",
        "post_to_group",
        "_project_message_to_bitable",
        "_notify_agent_tmux",
        "_search_records",
    )
    for name in required_symbols:
        assert hasattr(feishu_msg, name), f"feishu_msg missing public symbol {name}"
        assert callable(getattr(feishu_msg, name)), f"feishu_msg symbol not callable: {name}"

    def _exit_code(exc: BaseException) -> int:
        if isinstance(exc, SystemExit):
            if isinstance(exc.code, int):
                return exc.code
            return 0 if exc.code is None else 1
        return 1

    def _run_main(argv: list[str]) -> tuple[int, str, str]:
        old_argv = list(sys.argv)
        out = io.StringIO()
        err = io.StringIO()
        try:
            sys.argv = [str(FEISHU_MSG_SCRIPT), *argv]
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                try:
                    feishu_msg.main()
                    code = 0
                except SystemExit as exc:
                    code = _exit_code(exc)
            return code, out.getvalue(), err.getvalue()
        finally:
            sys.argv = old_argv

    code, out, err = _run_main([])
    assert code == 0, (code, out, err)
    assert ("飞书通讯脚本" in out) or ("用法" in out), out

    code, out, err = _run_main(["__unknown_cmd__"])
    assert code == 1, (code, out, err)
    assert "未知命令" in (out + err), (out, err)

    usage_cases = (
        (["send"], "用法: send"),
        (["direct"], "用法: direct"),
        (["say"], "用法: say"),
        (["inbox"], "用法: inbox"),
        (["read"], "用法: read"),
        (["status"], "用法: status"),
        (["log"], "用法: log"),
        (["workspace"], "用法: workspace"),
    )
    for argv, usage_text in usage_cases:
        code, out, err = _run_main(argv)
        assert code == 1, (argv, code, out, err)
        assert usage_text in (out + err), (argv, out, err)

    cmd_names = (
        "cmd_send",
        "cmd_direct",
        "cmd_say",
        "cmd_inbox",
        "cmd_read",
        "cmd_status",
        "cmd_log",
        "cmd_workspace",
    )
    forbidden_names = (
        "_lark_run",
        "_lark_im_send",
        "_lark_base_create",
        "_lark_base_search",
        "_lark_base_update",
        "_lark_base_list",
        "post_to_group",
        "_project_message_to_bitable",
        "_notify_agent_tmux",
        "_search_records",
    )
    cmd_signature_fields = {
        "cmd_send": ("to_agent", "from_agent", "message", "priority", "task_id"),
        "cmd_direct": ("to_agent", "from_agent", "message"),
        "cmd_say": ("from_agent", "message", "image_path"),
        "cmd_inbox": ("agent_name",),
        "cmd_read": ("record_id",),
        "cmd_status": ("agent_name", "status", "task", "blocker"),
        "cmd_log": ("agent_name", "log_type", "content", "ref"),
        "cmd_workspace": ("agent_name",),
    }

    cmd_calls = []
    delegated_calls = []
    run_calls = []
    forbidden_calls = []
    old_symbols = {}
    old_run = None
    old_run_aliases = {}
    command_mod = None

    def _capture_cmd(name):
        def _capture(*args, **kwargs):
            cmd_calls.append((name, args, kwargs))
            return 0

        return _capture

    def _forbidden(name):
        def _fail(*args, **kwargs):
            forbidden_calls.append((name, args, kwargs))
            raise AssertionError(f"unexpected remote/legacy helper call: {name}")

        return _fail

    def _extract_command_from_parsed(parsed):
        if parsed is None:
            return "", {}
        if isinstance(parsed, dict):
            return str(parsed.get("command", "") or ""), dict(parsed.get("params", {}) or {})
        command = str(getattr(parsed, "command", "") or "")
        params = getattr(parsed, "params", {})
        return command, dict(params or {})

    def _fake_run(*args, **kwargs):
        run_calls.append((args, kwargs))
        handlers = kwargs.get("handlers")
        if handlers is None:
            for item in args:
                if isinstance(item, dict) and ("send" in item or "direct" in item):
                    handlers = item
                    break

        argv = kwargs.get("argv")
        parsed = kwargs.get("parsed")

        if parsed is None:
            for item in args:
                if hasattr(item, "command") and hasattr(item, "params"):
                    parsed = item
                    break

        if argv is None:
            for item in args:
                if isinstance(item, (list, tuple)):
                    argv = list(item)
                    break

        if parsed is None and command_mod is not None:
            parse_argv = getattr(command_mod, "parse_argv", None)
            if callable(parse_argv):
                parsed = parse_argv(list(argv or []))

        command_name, params = _extract_command_from_parsed(parsed)
        if not command_name:
            return SimpleNamespace(exit_code=0, message="", command="", params={}, handled=False, value=None)
        if command_name in ("help", "unknown"):
            return SimpleNamespace(
                exit_code=int(getattr(parsed, "exit_code", 0) or 0),
                message=str(getattr(parsed, "message", "") or ""),
                command=command_name,
                params=dict(params or {}),
            )
        if not isinstance(handlers, dict):
            return SimpleNamespace(
                exit_code=2,
                message="缺少 handlers",
                command=command_name,
                params=dict(params or {}),
            )
        if command_name not in handlers:
            return SimpleNamespace(
                exit_code=2,
                message=f"缺少命令处理器: {command_name}",
                command=command_name,
                params=dict(params or {}),
            )
        clean_params = dict(params or {})
        delegated_calls.append((command_name, clean_params))
        handler = handlers[command_name]
        handler_value = handler(**clean_params) if isinstance(clean_params, dict) else handler()
        return SimpleNamespace(
            exit_code=0,
            message="",
            command=command_name,
            params=clean_params,
            value=handler_value,
        )

    try:
        for name in cmd_names:
            old_symbols[name] = getattr(feishu_msg, name)
            setattr(feishu_msg, name, _capture_cmd(name))
        for name in forbidden_names:
            old_symbols[name] = getattr(feishu_msg, name)
            setattr(feishu_msg, name, _forbidden(name))

        try:
            command_mod = importlib.import_module("claudeteam.commands.feishu_msg")
        except ModuleNotFoundError:
            command_mod = None

        if command_mod is not None and callable(getattr(command_mod, "run", None)):
            old_run = command_mod.run
            command_mod.run = _fake_run
            for attr in dir(feishu_msg):
                try:
                    value = getattr(feishu_msg, attr)
                except Exception:
                    continue
                if value is old_run:
                    old_run_aliases[attr] = value
                    setattr(feishu_msg, attr, _fake_run)

        def _consume_call(expected_command: str):
            if cmd_calls:
                func_name, args, kwargs = cmd_calls.pop(0)
                assert func_name == f"cmd_{expected_command}", (expected_command, func_name, args, kwargs)
                fields = cmd_signature_fields[func_name]
                params = {}
                for idx, field in enumerate(fields):
                    if idx < len(args):
                        params[field] = args[idx]
                params.update(kwargs)
                return params

            assert delegated_calls, f"no command call captured for {expected_command}"
            command_name, params = delegated_calls.pop(0)
            assert command_name == expected_command, (expected_command, command_name, params)
            return params

        with tempfile.TemporaryDirectory() as tmp:
            file_path = Path(tmp) / "send_payload.txt"
            file_path.write_text("来自文件的send内容", encoding="utf-8")

            success_cases = (
                (
                    "send",
                    ["send", "toolsmith", "manager", "普通send", "高", "--task", "TASK-SEND"],
                    {
                        "to_agent": "toolsmith",
                        "from_agent": "manager",
                        "message": "普通send",
                        "priority": "高",
                        "task_id": "TASK-SEND",
                    },
                ),
                (
                    "send",
                    [
                        "send",
                        "toolsmith",
                        "manager",
                        "WILL_BE_REPLACED",
                        "低",
                        "--task",
                        "TASK-FILE",
                        "--file",
                        str(file_path),
                    ],
                    {
                        "to_agent": "toolsmith",
                        "from_agent": "manager",
                        "message": "来自文件的send内容",
                        "priority": "低",
                        "task_id": "TASK-FILE",
                    },
                ),
                (
                    "direct",
                    ["direct", "toolsmith", "coder", "direct测试"],
                    {
                        "to_agent": "toolsmith",
                        "from_agent": "coder",
                        "message": "direct测试",
                    },
                ),
                (
                    "say",
                    ["say", "manager", "say测试", "--image", "/tmp/say-image.png"],
                    {
                        "from_agent": "manager",
                        "message": "say测试",
                        "image_path": "/tmp/say-image.png",
                    },
                ),
                (
                    "inbox",
                    ["inbox", "toolsmith"],
                    {"agent_name": "toolsmith"},
                ),
                (
                    "read",
                    ["read", "msg_compat_1"],
                    {"record_id": "msg_compat_1"},
                ),
                (
                    "status",
                    ["status", "toolsmith", "进行中", "状态任务", ""],
                    {
                        "agent_name": "toolsmith",
                        "status": "进行中",
                        "task": "状态任务",
                        "blocker": "",
                    },
                ),
                (
                    "log",
                    ["log", "toolsmith", "任务日志", "日志内容", "REF-C3C"],
                    {
                        "agent_name": "toolsmith",
                        "log_type": "任务日志",
                        "content": "日志内容",
                        "ref": "REF-C3C",
                    },
                ),
                (
                    "workspace",
                    ["workspace", "toolsmith"],
                    {"agent_name": "toolsmith"},
                ),
            )

            for command_name, argv, expected in success_cases:
                cmd_calls.clear()
                delegated_calls.clear()
                code, out, err = _run_main(list(argv))
                assert code == 0, (command_name, argv, code, out, err)
                params = _consume_call(command_name)
                for key, value in expected.items():
                    assert params.get(key) == value, (
                        command_name,
                        key,
                        value,
                        params,
                    )

        assert not forbidden_calls, f"unexpected remote/local side-effect call: {forbidden_calls!r}"
        if old_run is not None:
            assert run_calls, "commands.run exists but main delegation was never observed"
    finally:
        for name, value in old_symbols.items():
            setattr(feishu_msg, name, value)
        if command_mod is not None and old_run is not None:
            command_mod.run = old_run
        for attr, value in old_run_aliases.items():
            setattr(feishu_msg, attr, value)


def test_feishu_msg_command_parser_contract_when_present() -> None:
    parser_file = ROOT / "src" / "claudeteam" / "commands" / "feishu_msg.py"
    if not parser_file.exists():
        return

    parser_mod = importlib.import_module("claudeteam.commands.feishu_msg")
    assert hasattr(parser_mod, "parse_argv"), "commands.feishu_msg missing parse_argv"
    assert hasattr(parser_mod, "ParsedCommand"), "commands.feishu_msg missing ParsedCommand"

    parse_argv = parser_mod.parse_argv
    parsed_type = parser_mod.ParsedCommand
    assert callable(parse_argv), "parse_argv not callable"

    samples = (
        ("send", ["send", "toolsmith", "manager", "解析消息", "高", "--task", "TASK-PARSE"], ("toolsmith", "manager", "解析消息", "高", "TASK-PARSE")),
        ("direct", ["direct", "toolsmith", "coder", "直发解析"], ("toolsmith", "coder", "直发解析")),
        ("say", ["say", "manager", "系统回显", "--image", "/tmp/fake.png"], ("manager", "系统回显", "/tmp/fake.png")),
        ("inbox", ["inbox", "toolsmith"], ("toolsmith",)),
        ("read", ["read", "msg_123"], ("msg_123",)),
        ("status", ["status", "toolsmith", "进行中", "解析任务", ""], ("toolsmith", "进行中", "解析任务")),
        ("log", ["log", "toolsmith", "任务日志", "解析内容", "REF-1"], ("toolsmith", "任务日志", "解析内容", "REF-1")),
        ("workspace", ["workspace", "toolsmith"], ("toolsmith",)),
    )

    for expected_command, argv, required_tokens in samples:
        parsed = parse_argv(list(argv))
        assert isinstance(parsed, parsed_type), f"parse_argv did not return ParsedCommand for {argv!r}"
        command_name = _extract_command_name(parsed)
        assert command_name == expected_command, (
            f"expected command {expected_command!r}, got {command_name!r} for argv={argv!r}"
        )
        text = _flatten_text(parsed)
        for token in required_tokens:
            assert token in text, f"missing token {token!r} in parsed result for argv={argv!r}: {parsed!r}"

    _assert_parse_error_or_usage(parse_argv, [])
    _assert_parse_error_or_usage(parse_argv, ["unknown-cmd"])


def test_feishu_msg_command_dispatch_contract_when_present() -> None:
    parser_file = ROOT / "src" / "claudeteam" / "commands" / "feishu_msg.py"
    if not parser_file.exists():
        return

    dispatch_smoke = textwrap.dedent(
        """
        import builtins
        import importlib
        import sys
        from pathlib import Path

        root = Path(".").resolve()
        for p in (root / "src", root):
            sp = str(p)
            if sp not in sys.path:
                sys.path.insert(0, sp)

        blocked = ("feishu_msg", "scripts.feishu_msg", "local_facts", "tmux_utils")
        orig_import = builtins.__import__

        def guarded_import(name, globals=None, locals=None, fromlist=(), level=0):
            if name in blocked or any(name.startswith(prefix + ".") for prefix in blocked):
                raise AssertionError(f"forbidden import in dispatch gate: {name}")
            return orig_import(name, globals, locals, fromlist, level)

        builtins.__import__ = guarded_import
        try:
            mod = importlib.import_module("claudeteam.commands.feishu_msg")
        finally:
            builtins.__import__ = orig_import

        for name in blocked:
            assert name not in sys.modules, f"dispatch gate imported forbidden module: {name}"

        parse_argv = getattr(mod, "parse_argv", None)
        ParsedCommand = getattr(mod, "ParsedCommand", None)
        dispatch = getattr(mod, "dispatch", None)
        run = getattr(mod, "run", None)
        assert callable(parse_argv), "commands.feishu_msg missing parse_argv"
        assert ParsedCommand is not None, "commands.feishu_msg missing ParsedCommand"

        if not callable(dispatch) and not callable(run):
            print("SKIP: commands.feishu_msg dispatch/run not present yet")
            raise SystemExit(0)

        def _flatten(value):
            if value is None:
                return ""
            if isinstance(value, (str, int, float, bool)):
                return str(value)
            if isinstance(value, dict):
                parts = []
                for k, v in value.items():
                    parts.append(str(k))
                    parts.append(_flatten(v))
                return " ".join(parts)
            if isinstance(value, (list, tuple, set)):
                return " ".join(_flatten(v) for v in value)
            if hasattr(value, "_asdict"):
                return _flatten(value._asdict())
            if hasattr(value, "__dict__"):
                return _flatten(vars(value))
            return repr(value)

        def _invoke_dispatch(fn, parsed, handlers):
            attempts = (
                lambda: fn(parsed, handlers),
                lambda: fn(parsed=parsed, handlers=handlers),
                lambda: fn(parsed, handlers=handlers),
                lambda: fn(command=parsed, handlers=handlers),
                lambda: fn(parsed),
            )
            last_err = None
            for attempt in attempts:
                try:
                    return attempt()
                except TypeError as exc:
                    last_err = exc
            raise AssertionError(f"cannot invoke dispatch with parsed+handlers: {last_err}")

        def _invoke_run(fn, argv, parsed, handlers):
            io_loader = lambda p: f"FILE:{p}"
            attempts = (
                lambda: fn(argv, handlers),
                lambda: fn(argv=argv, handlers=handlers),
                lambda: fn(argv, handlers=handlers),
                lambda: fn(parsed, handlers),
                lambda: fn(parsed=parsed, handlers=handlers),
                lambda: fn(argv, handlers, io_loader),
                lambda: fn(argv=argv, handlers=handlers, io_loader=io_loader),
                lambda: fn(argv=argv, handlers=handlers, read_file=io_loader),
                lambda: fn(argv=argv, handlers=handlers, file_loader=io_loader),
            )
            last_err = None
            for attempt in attempts:
                try:
                    return attempt()
                except TypeError as exc:
                    last_err = exc
            raise AssertionError(f"cannot invoke run with argv+handlers: {last_err}")

        def _assert_help_semantics(out):
            text = _flatten(out)
            if text:
                lower = text.lower()
                assert (
                    "用法" in text
                    or "usage" in lower
                    or "help" in lower
                    or "飞书通讯脚本" in text
                ), f"help/empty argv missing usage semantics: {out!r}"
                return

            payload = {}
            if hasattr(out, "_asdict"):
                payload = out._asdict()
            elif hasattr(out, "__dict__"):
                payload = vars(out)
            if payload:
                code = payload.get("exit_code")
                assert code in (0, None), f"help/empty argv non-zero exit semantics: {out!r}"

        def _assert_error_semantics(action):
            try:
                out = action()
            except SystemExit as exc:
                code = exc.code if isinstance(exc.code, int) else 1
                assert code != 0, f"unexpected success exit: {exc!r}"
                return
            except Exception as exc:
                assert isinstance(
                    exc, (KeyError, ValueError, RuntimeError, AssertionError, NotImplementedError)
                ), f"unexpected exception type: {type(exc).__name__}: {exc}"
                return
            text = _flatten(out).lower()
            markers = (
                "error",
                "unknown",
                "未知",
                "missing",
                "handler",
                "not found",
                "unsupported",
                "用法",
                "缺失",
                "缺少",
                "处理器",
                "exit_code",
            )
            assert any(marker in text for marker in markers), f"missing error semantics: {out!r}"

        calls = []

        def _mk_handler(name):
            def _handler(*args, **kwargs):
                calls.append((name, args, kwargs))
                return {"handler": name, "args": list(args), "kwargs": kwargs}

            return _handler

        handlers = {
            name: _mk_handler(name)
            for name in ("send", "direct", "say", "inbox", "read", "status", "log", "workspace")
        }

        samples = (
            ("send", ["send", "toolsmith", "manager", "分派消息", "高", "--task", "TASK-DISPATCH"], ("toolsmith", "manager", "分派消息", "高", "TASK-DISPATCH")),
            ("direct", ["direct", "toolsmith", "coder", "分派直发"], ("toolsmith", "coder", "分派直发")),
            ("say", ["say", "manager", "分派回显", "--image", "/tmp/dispatch.png"], ("manager", "分派回显", "/tmp/dispatch.png")),
            ("inbox", ["inbox", "toolsmith"], ("toolsmith",)),
            ("read", ["read", "msg_dispatch_1"], ("msg_dispatch_1",)),
            ("status", ["status", "toolsmith", "进行中", "分派状态", ""], ("toolsmith", "进行中", "分派状态")),
            ("log", ["log", "toolsmith", "任务日志", "分派内容", "REF-DISPATCH"], ("toolsmith", "任务日志", "分派内容", "REF-DISPATCH")),
            ("workspace", ["workspace", "toolsmith"], ("toolsmith",)),
        )

        for command_name, argv, tokens in samples:
            parsed = parse_argv(list(argv))
            assert isinstance(parsed, ParsedCommand), f"parse_argv did not return ParsedCommand for {argv!r}"

            if callable(dispatch):
                calls.clear()
                _invoke_dispatch(dispatch, parsed, handlers)
                assert len(calls) == 1, f"dispatch should call exactly one handler for {argv!r}: {calls!r}"
                assert calls[-1][0] == command_name, (
                    f"dispatch routed to wrong handler for {argv!r}: {calls[-1][0]!r}"
                )
                dispatch_text = _flatten({"args": calls[-1][1], "kwargs": calls[-1][2]})
                for token in tokens:
                    assert token in dispatch_text, (
                        f"dispatch missing token {token!r} for {argv!r}: {calls[-1]!r}"
                    )

            if callable(run):
                calls.clear()
                _invoke_run(run, list(argv), parsed, handlers)
                assert len(calls) == 1, f"run should call exactly one handler for {argv!r}: {calls!r}"
                assert calls[-1][0] == command_name, (
                    f"run routed to wrong handler for {argv!r}: {calls[-1][0]!r}"
                )
                run_text = _flatten({"args": calls[-1][1], "kwargs": calls[-1][2]})
                for token in tokens:
                    assert token in run_text, f"run missing token {token!r} for {argv!r}: {calls[-1]!r}"

        parsed_help = parse_argv([])
        if callable(dispatch):
            calls.clear()
            help_out = _invoke_dispatch(dispatch, parsed_help, handlers)
            assert not calls, f"dispatch should not call handlers for help/empty argv: {calls!r}"
            _assert_help_semantics(help_out)
            _assert_error_semantics(lambda: _invoke_dispatch(dispatch, parse_argv(["unknown-cmd"]), handlers))
            missing_handlers = {k: v for k, v in handlers.items() if k != "send"}
            _assert_error_semantics(
                lambda: _invoke_dispatch(
                    dispatch,
                    parse_argv(["send", "toolsmith", "manager", "缺失handler", "中"]),
                    missing_handlers,
                )
            )

        if callable(run):
            calls.clear()
            help_out = _invoke_run(run, [], parsed_help, handlers)
            assert not calls, f"run should not call handlers for help/empty argv: {calls!r}"
            _assert_help_semantics(help_out)
            _assert_error_semantics(lambda: _invoke_run(run, ["unknown-cmd"], parse_argv(["unknown-cmd"]), handlers))
            missing_handlers = {k: v for k, v in handlers.items() if k != "send"}
            _assert_error_semantics(
                lambda: _invoke_run(
                    run,
                    ["send", "toolsmith", "manager", "缺失handler", "中"],
                    parse_argv(["send", "toolsmith", "manager", "缺失handler", "中"]),
                    missing_handlers,
                )
            )

        for name in blocked:
            assert name not in sys.modules, f"dispatch/run path imported forbidden module: {name}"
        print("OK: dispatch/run contract")
        """
    ).strip()

    result = run_python_code(dispatch_smoke)
    assert result.returncode == 0, (
        f"dispatch/run gate failed: rc={result.returncode}, stdout={result.stdout!r}, stderr={result.stderr!r}"
    )


def test_kanban_sync_command_parser_dispatch_contract_when_present() -> None:
    command_file = ROOT / "src" / "claudeteam" / "commands" / "kanban_sync.py"
    if not command_file.exists():
        return

    import builtins

    blocked_imports = ("kanban_sync", "scripts.kanban_sync")
    orig_import = builtins.__import__

    sys.modules.pop("claudeteam.commands.kanban_sync", None)

    def guarded_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name in blocked_imports or any(name.startswith(prefix + ".") for prefix in blocked_imports):
            raise AssertionError(f"forbidden import in kanban command gate: {name}")
        return orig_import(name, globals, locals, fromlist, level)

    builtins.__import__ = guarded_import
    try:
        mod = importlib.import_module("claudeteam.commands.kanban_sync")
    finally:
        builtins.__import__ = orig_import

    for name in blocked_imports:
        assert name not in sys.modules, f"kanban command gate imported forbidden module: {name}"

    assert hasattr(mod, "parse_argv"), "commands.kanban_sync missing parse_argv"
    assert hasattr(mod, "ParsedCommand"), "commands.kanban_sync missing ParsedCommand"
    parse_argv = mod.parse_argv
    parsed_type = mod.ParsedCommand
    assert callable(parse_argv), "commands.kanban_sync parse_argv not callable"

    def _assert_help_like(argv):
        try:
            parsed = parse_argv(list(argv))
        except SystemExit as exc:
            code = exc.code if isinstance(exc.code, int) else 1
            assert code in (0, 1), (argv, code)
            return
        text = _flatten_text(parsed)
        command_name = _extract_command_name(parsed) or ""
        assert (
            command_name in ("help", "usage", "")
            or "用法" in text
            or "usage" in text.lower()
            or "help" in text.lower()
        ), f"argv={argv!r} missing help/usage semantics: {parsed!r}"

    _assert_help_like([])
    _assert_help_like(["help"])

    valid_cases = (
        ("init", ["init"], ("init",)),
        ("sync", ["sync"], ("sync",)),
        ("daemon", ["daemon"], ("daemon",)),
        ("daemon", ["daemon", "--interval", "5"], ("daemon", "5")),
    )
    for expected_command, argv, tokens in valid_cases:
        parsed = parse_argv(list(argv))
        assert isinstance(parsed, parsed_type), f"parse_argv did not return ParsedCommand for {argv!r}"
        command_name = _extract_command_name(parsed)
        assert command_name == expected_command, (
            f"expected command {expected_command!r}, got {command_name!r} for argv={argv!r}"
        )
        text = _flatten_text(parsed)
        for token in tokens:
            assert str(token) in text, f"missing token {token!r} for argv={argv!r}: {parsed!r}"

    _assert_parse_error_or_usage(parse_argv, ["unknown-cmd"])
    _assert_parse_error_or_usage(parse_argv, ["daemon", "--interval"])
    _assert_parse_error_or_usage(parse_argv, ["daemon", "--interval", "abc"])

    dispatch = getattr(mod, "dispatch", None)
    run = getattr(mod, "run", None)
    if not callable(dispatch) and not callable(run):
        return

    orig_open = builtins.open
    orig_subprocess_run = subprocess.run
    orig_os_kill = os.kill
    side_effect_calls = []

    def guarded_open(file, mode="r", *args, **kwargs):
        m = mode or "r"
        if any(flag in m for flag in ("w", "a", "x", "+")):
            side_effect_calls.append(("open-write", str(file), m))
            raise AssertionError(f"kanban commands gate forbids file writes: {file} ({m})")
        return orig_open(file, mode, *args, **kwargs)

    def guarded_subprocess_run(*args, **kwargs):
        side_effect_calls.append(("subprocess.run", args, kwargs))
        raise AssertionError("kanban commands gate forbids subprocess.run")

    def guarded_os_kill(*args, **kwargs):
        side_effect_calls.append(("os.kill", args, kwargs))
        raise AssertionError("kanban commands gate forbids os.kill")

    def _invoke_dispatch(fn, parsed, handlers):
        attempts = (
            lambda: fn(parsed, handlers),
            lambda: fn(parsed=parsed, handlers=handlers),
            lambda: fn(parsed, handlers=handlers),
            lambda: fn(command=parsed, handlers=handlers),
            lambda: fn(parsed),
        )
        last_err = None
        for attempt in attempts:
            try:
                return attempt()
            except TypeError as exc:
                last_err = exc
        raise AssertionError(f"cannot invoke kanban dispatch with parsed+handlers: {last_err}")

    def _invoke_run(fn, argv, parsed, handlers):
        attempts = (
            lambda: fn(argv, handlers),
            lambda: fn(argv=argv, handlers=handlers),
            lambda: fn(argv, handlers=handlers),
            lambda: fn(parsed, handlers),
            lambda: fn(parsed=parsed, handlers=handlers),
        )
        last_err = None
        for attempt in attempts:
            try:
                return attempt()
            except TypeError as exc:
                last_err = exc
        raise AssertionError(f"cannot invoke kanban run with argv+handlers: {last_err}")

    def _assert_help_semantics(out):
        text = _flatten_text(out)
        if text:
            lower = text.lower()
            assert (
                "用法" in text
                or "usage" in lower
                or "help" in lower
            ), f"help/empty argv missing usage semantics: {out!r}"
            return
        payload = {}
        if hasattr(out, "_asdict"):
            payload = out._asdict()
        elif hasattr(out, "__dict__"):
            payload = vars(out)
        if payload:
            code = payload.get("exit_code")
            assert code in (0, None), f"help/empty argv non-zero exit semantics: {out!r}"

    def _assert_error_semantics(action):
        try:
            out = action()
        except SystemExit as exc:
            code = exc.code if isinstance(exc.code, int) else 1
            assert code != 0, f"unexpected success exit: {exc!r}"
            return
        except Exception as exc:
            assert isinstance(
                exc, (KeyError, ValueError, RuntimeError, AssertionError, NotImplementedError)
            ), f"unexpected exception type: {type(exc).__name__}: {exc}"
            return
        text = _flatten_text(out).lower()
        markers = (
            "error",
            "unknown",
            "未知",
            "missing",
            "handler",
            "not found",
            "unsupported",
            "用法",
            "缺失",
            "缺少",
            "处理器",
            "exit_code",
            "interval",
        )
        assert any(marker in text for marker in markers), f"missing error semantics: {out!r}"

    calls = []

    def _mk_handler(name):
        def _handler(*args, **kwargs):
            calls.append((name, args, kwargs))
            return {"handler": name, "args": list(args), "kwargs": kwargs}

        return _handler

    handlers = {
        "init": _mk_handler("init"),
        "sync": _mk_handler("sync"),
        "daemon": _mk_handler("daemon"),
    }

    try:
        builtins.open = guarded_open
        subprocess.run = guarded_subprocess_run
        os.kill = guarded_os_kill

        dispatch_cases = (
            ("init", ["init"], ()),
            ("sync", ["sync"], ()),
            ("daemon", ["daemon"], ()),
            ("daemon", ["daemon", "--interval", "5"], ("5",)),
        )
        for command_name, argv, must_tokens in dispatch_cases:
            parsed = parse_argv(list(argv))
            assert isinstance(parsed, parsed_type), f"parse_argv did not return ParsedCommand for {argv!r}"

            if callable(dispatch):
                calls.clear()
                _invoke_dispatch(dispatch, parsed, handlers)
                assert len(calls) == 1, f"dispatch should call exactly one handler for {argv!r}: {calls!r}"
                assert calls[-1][0] == command_name, (
                    f"dispatch routed to wrong handler for {argv!r}: {calls[-1][0]!r}"
                )
                dispatch_text = _flatten_text({"args": calls[-1][1], "kwargs": calls[-1][2]})
                for token in must_tokens:
                    assert token in dispatch_text, (
                        f"dispatch missing token {token!r} for {argv!r}: {calls[-1]!r}"
                    )

            if callable(run):
                calls.clear()
                _invoke_run(run, list(argv), parsed, handlers)
                assert len(calls) == 1, f"run should call exactly one handler for {argv!r}: {calls!r}"
                assert calls[-1][0] == command_name, (
                    f"run routed to wrong handler for {argv!r}: {calls[-1][0]!r}"
                )
                run_text = _flatten_text({"args": calls[-1][1], "kwargs": calls[-1][2]})
                for token in must_tokens:
                    assert token in run_text, f"run missing token {token!r} for {argv!r}: {calls[-1]!r}"

        parsed_help = parse_argv([])
        if callable(dispatch):
            calls.clear()
            help_out = _invoke_dispatch(dispatch, parsed_help, handlers)
            assert not calls, f"dispatch should not call handlers for help/empty argv: {calls!r}"
            _assert_help_semantics(help_out)
            _assert_error_semantics(lambda: _invoke_dispatch(dispatch, parse_argv(["unknown-cmd"]), handlers))
            _assert_error_semantics(lambda: _invoke_dispatch(dispatch, parse_argv(["sync"]), {"init": handlers["init"]}))

        if callable(run):
            calls.clear()
            help_out = _invoke_run(run, [], parsed_help, handlers)
            assert not calls, f"run should not call handlers for help/empty argv: {calls!r}"
            _assert_help_semantics(help_out)
            _assert_error_semantics(lambda: _invoke_run(run, ["unknown-cmd"], parse_argv(["unknown-cmd"]), handlers))
            _assert_error_semantics(
                lambda: _invoke_run(
                    run,
                    ["sync"],
                    parse_argv(["sync"]),
                    {"init": handlers["init"]},
                )
            )

        assert not side_effect_calls, f"unexpected side-effect call in kanban command gate: {side_effect_calls!r}"
    finally:
        builtins.open = orig_open
        subprocess.run = orig_subprocess_run
        os.kill = orig_os_kill

    for name in blocked_imports:
        assert name not in sys.modules, f"kanban command path imported forbidden module: {name}"


def test_kanban_sync_main_delegate_compat_contract() -> None:
    kanban_sync = importlib.import_module("kanban_sync")

    required_symbols = (
        "cmd_init",
        "cmd_sync",
        "cmd_daemon",
        "do_sync",
        "_lark",
        "load_tasks",
        "load_cfg",
        "save_cfg",
    )
    for name in required_symbols:
        assert hasattr(kanban_sync, name), f"kanban_sync missing public symbol {name}"
        assert callable(getattr(kanban_sync, name)), f"kanban_sync symbol not callable: {name}"

    def _exit_code(exc: BaseException) -> int:
        if isinstance(exc, SystemExit):
            if isinstance(exc.code, int):
                return exc.code
            return 0 if exc.code is None else 1
        return 1

    def _run_main(argv: list[str]) -> tuple[int, str, str]:
        old_argv = list(sys.argv)
        out = io.StringIO()
        err = io.StringIO()
        try:
            sys.argv = [str(ROOT / "scripts" / "kanban_sync.py"), *argv]
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                try:
                    kanban_sync.main()
                    code = 0
                except SystemExit as exc:
                    code = _exit_code(exc)
                except Exception:
                    code = 1
                    raise
            return code, out.getvalue(), err.getvalue()
        except Exception as exc:
            return 1, out.getvalue(), err.getvalue() + f"{type(exc).__name__}: {exc}"
        finally:
            sys.argv = old_argv

    code, out, err = _run_main([])
    assert code == 0, (code, out, err)
    assert ("项目看板同步" in out) or ("用法" in out), out

    code, out, err = _run_main(["__unknown_cmd__"])
    assert code != 0, (code, out, err)
    assert "未知命令" in (out + err), (out, err)

    code, out, err = _run_main(["daemon", "--interval", "abc"])
    assert code != 0, (code, out, err)
    err_text = out + err
    assert any(marker in err_text for marker in ("interval", "invalid", "ValueError", "用法")), err_text

    cmd_calls = []
    run_calls = []
    forbidden_calls = []
    old_symbols = {}
    old_run = None
    old_run_aliases = {}
    command_mod = None

    def _capture_cmd(name):
        def _capture(*args, **kwargs):
            cmd_calls.append((name, args, kwargs))
            return 0

        return _capture

    def _forbidden(name):
        def _fail(*args, **kwargs):
            forbidden_calls.append((name, args, kwargs))
            raise AssertionError(f"unexpected side-effect helper call: {name}")

        return _fail

    def _extract_command_from_parsed(parsed):
        if parsed is None:
            return "", {}
        if isinstance(parsed, dict):
            return str(parsed.get("command", "") or ""), dict(parsed.get("params", {}) or {})
        command = str(getattr(parsed, "command", "") or "")
        params = getattr(parsed, "params", {})
        return command, dict(params or {})

    def _fake_run(*args, **kwargs):
        run_calls.append((args, kwargs))

        handlers = kwargs.get("handlers")
        if handlers is None:
            for item in args:
                if isinstance(item, dict) and any(k in item for k in ("init", "sync", "daemon")):
                    handlers = item
                    break

        argv = kwargs.get("argv")
        parsed = kwargs.get("parsed")
        if parsed is None:
            for item in args:
                if hasattr(item, "command") and hasattr(item, "params"):
                    parsed = item
                    break
        if argv is None:
            for item in args:
                if isinstance(item, (list, tuple)):
                    argv = list(item)
                    break

        if parsed is None and command_mod is not None:
            parse_argv = getattr(command_mod, "parse_argv", None)
            if callable(parse_argv):
                parsed = parse_argv(list(argv or []))

        command_name, params = _extract_command_from_parsed(parsed)
        if not command_name:
            return SimpleNamespace(exit_code=0, message="", command="", params={})

        parsed_exit = int(getattr(parsed, "exit_code", 0) or 0)
        parsed_msg = str(getattr(parsed, "message", "") or "")
        if command_name in ("help", "unknown") or parsed_exit != 0:
            return SimpleNamespace(
                exit_code=parsed_exit,
                message=parsed_msg,
                command=command_name,
                params=dict(params or {}),
                handled=False,
                value=None,
            )

        if not isinstance(handlers, dict):
            return SimpleNamespace(
                exit_code=2,
                message="缺少 handlers",
                command=command_name,
                params=dict(params or {}),
                handled=False,
                value=None,
            )
        if command_name not in handlers:
            return SimpleNamespace(
                exit_code=2,
                message=f"缺少命令处理器: {command_name}",
                command=command_name,
                params=dict(params or {}),
                handled=False,
                value=None,
            )

        clean_params = dict(params or {})
        handler = handlers[command_name]
        value = handler(**clean_params)
        return SimpleNamespace(
            exit_code=0,
            message="",
            command=command_name,
            params=clean_params,
            handled=True,
            value=value,
        )

    orig_subprocess_run = subprocess.run
    orig_os_kill = os.kill
    orig_kanban_subprocess_run = kanban_sync.subprocess.run
    orig_kanban_os_kill = kanban_sync.os.kill

    try:
        for name in ("cmd_init", "cmd_sync", "cmd_daemon"):
            old_symbols[name] = getattr(kanban_sync, name)
            setattr(kanban_sync, name, _capture_cmd(name))

        for name in ("_lark", "do_sync", "load_tasks", "_acquire_pid_lock"):
            old_symbols[name] = getattr(kanban_sync, name)
            setattr(kanban_sync, name, _forbidden(name))

        try:
            command_mod = importlib.import_module("claudeteam.commands.kanban_sync")
        except ModuleNotFoundError:
            command_mod = None

        if command_mod is not None and callable(getattr(command_mod, "run", None)):
            old_run = command_mod.run
            command_mod.run = _fake_run
            for attr in dir(kanban_sync):
                try:
                    value = getattr(kanban_sync, attr)
                except Exception:
                    continue
                if value is old_run:
                    old_run_aliases[attr] = value
                    setattr(kanban_sync, attr, _fake_run)

        def _forbidden_subprocess(*args, **kwargs):
            forbidden_calls.append(("subprocess.run", args, kwargs))
            raise AssertionError("unexpected subprocess.run call")

        def _forbidden_kill(*args, **kwargs):
            forbidden_calls.append(("os.kill", args, kwargs))
            raise AssertionError("unexpected os.kill call")

        subprocess.run = _forbidden_subprocess
        os.kill = _forbidden_kill
        kanban_sync.subprocess.run = _forbidden_subprocess
        kanban_sync.os.kill = _forbidden_kill

        cases = (
            ("cmd_init", ["init"], None),
            ("cmd_sync", ["sync"], None),
            ("cmd_daemon", ["daemon"], 60),
            ("cmd_daemon", ["daemon", "--interval", "5"], 5),
        )
        for expected_func, argv, expected_interval in cases:
            cmd_calls.clear()
            code, out, err = _run_main(list(argv))
            assert code == 0, (argv, code, out, err)
            assert cmd_calls, f"no command call captured for argv={argv!r}"
            name, args, kwargs = cmd_calls[-1]
            assert name == expected_func, (argv, expected_func, name, args, kwargs)
            if expected_func == "cmd_daemon":
                interval = kwargs.get("interval")
                if interval is None and args:
                    interval = args[0]
                assert interval == expected_interval, (argv, expected_interval, interval, args, kwargs)
            else:
                assert not args and not kwargs, (argv, args, kwargs)

        assert not forbidden_calls, f"unexpected side-effect call: {forbidden_calls!r}"
        if old_run is not None:
            assert run_calls, "commands.kanban_sync.run exists but main delegation was never observed"
    finally:
        for name, value in old_symbols.items():
            setattr(kanban_sync, name, value)
        subprocess.run = orig_subprocess_run
        os.kill = orig_os_kill
        kanban_sync.subprocess.run = orig_kanban_subprocess_run
        kanban_sync.os.kill = orig_kanban_os_kill
        if command_mod is not None and old_run is not None:
            command_mod.run = old_run
        for attr, value in old_run_aliases.items():
            setattr(kanban_sync, attr, value)


def test_kanban_sync_entrypoint_delegate_contract_basic_branches() -> None:
    kanban_sync = importlib.import_module("kanban_sync")

    run_calls = []
    handler_calls = []
    forbidden_calls = []

    def _exit_code(exc: BaseException) -> int:
        if isinstance(exc, SystemExit):
            if isinstance(exc.code, int):
                return exc.code
            return 0 if exc.code is None else 1
        return 1

    def _run_main(argv: list[str]) -> tuple[int, str, str]:
        old_argv = list(sys.argv)
        out = io.StringIO()
        err = io.StringIO()
        try:
            sys.argv = [str(ROOT / "scripts" / "kanban_sync.py"), *argv]
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                try:
                    kanban_sync.main()
                    code = 0
                except SystemExit as exc:
                    code = _exit_code(exc)
            return code, out.getvalue(), err.getvalue()
        finally:
            sys.argv = old_argv

    def _capture(name):
        def _f(*args, **kwargs):
            handler_calls.append((name, args, kwargs))
            return 0

        return _f

    def _fake_run(argv, handlers=None):
        args = list(argv or [])
        handlers = handlers or {}
        run_calls.append((args, tuple(sorted(handlers.keys()))))
        assert set(handlers.keys()) == {"init", "sync", "daemon"}, handlers

        cmd = args[0] if args else ""
        if cmd in ("help", "-h", "--help"):
            return SimpleNamespace(exit_code=0, message="USAGE_HELP", handled=False)
        if cmd == "__unknown_cmd__":
            return SimpleNamespace(exit_code=1, message="未知命令: __unknown_cmd__", handled=False)
        if cmd == "init":
            handlers["init"]()
            return SimpleNamespace(exit_code=0, message="", handled=True)
        if cmd == "sync":
            handlers["sync"]()
            return SimpleNamespace(exit_code=0, message="", handled=True)
        if cmd == "daemon":
            interval = 60
            if len(args) >= 3 and args[1] == "--interval":
                interval = int(args[2])
            handlers["daemon"](interval=interval)
            return SimpleNamespace(exit_code=0, message="", handled=True)
        return SimpleNamespace(exit_code=2, message=f"unexpected argv: {args!r}", handled=False)

    def _forbidden_subprocess(*args, **kwargs):
        forbidden_calls.append(("subprocess.run", args, kwargs))
        raise AssertionError("kanban delegate gate forbids subprocess.run")

    def _forbidden_kill(*args, **kwargs):
        forbidden_calls.append(("os.kill", args, kwargs))
        raise AssertionError("kanban delegate gate forbids os.kill")

    old_run = kanban_sync._kanban_commands.run
    old_cmd_init = kanban_sync.cmd_init
    old_cmd_sync = kanban_sync.cmd_sync
    old_cmd_daemon = kanban_sync.cmd_daemon
    orig_subprocess_run = subprocess.run
    orig_os_kill = os.kill
    orig_kanban_subprocess_run = kanban_sync.subprocess.run
    orig_kanban_os_kill = kanban_sync.os.kill
    try:
        kanban_sync._kanban_commands.run = _fake_run
        kanban_sync.cmd_init = _capture("cmd_init")
        kanban_sync.cmd_sync = _capture("cmd_sync")
        kanban_sync.cmd_daemon = _capture("cmd_daemon")
        subprocess.run = _forbidden_subprocess
        os.kill = _forbidden_kill
        kanban_sync.subprocess.run = _forbidden_subprocess
        kanban_sync.os.kill = _forbidden_kill

        code, out, err = _run_main([])
        assert code == 0, (code, out, err)
        assert ("项目看板同步" in out) or ("用法" in out), out
        assert not run_calls, run_calls

        code, out, err = _run_main(["help"])
        assert code == 0, (code, out, err)
        assert "USAGE_HELP" in (out + err), (out, err)

        code, out, err = _run_main(["__unknown_cmd__"])
        assert code == 1, (code, out, err)
        assert "未知命令" in (out + err), (out, err)

        code, out, err = _run_main(["daemon", "--interval", "5"])
        assert code == 0, (code, out, err)

        code, out, err = _run_main(["init"])
        assert code == 0, (code, out, err)

        code, out, err = _run_main(["sync"])
        assert code == 0, (code, out, err)

        assert not forbidden_calls, f"unexpected side effects: {forbidden_calls!r}"
        assert [argv for argv, _ in run_calls] == [
            ["help"],
            ["__unknown_cmd__"],
            ["daemon", "--interval", "5"],
            ["init"],
            ["sync"],
        ], run_calls

        assert [name for name, _, _ in handler_calls] == [
            "cmd_daemon",
            "cmd_init",
            "cmd_sync",
        ], handler_calls
        assert handler_calls[0][2].get("interval") == 5, handler_calls
    finally:
        kanban_sync._kanban_commands.run = old_run
        kanban_sync.cmd_init = old_cmd_init
        kanban_sync.cmd_sync = old_cmd_sync
        kanban_sync.cmd_daemon = old_cmd_daemon
        subprocess.run = orig_subprocess_run
        os.kill = orig_os_kill
        kanban_sync.subprocess.run = orig_kanban_subprocess_run
        kanban_sync.os.kill = orig_kanban_os_kill


def test_kanban_daemon_helper_import_and_contract_gate_when_present() -> None:
    helper_file = ROOT / "src" / "claudeteam" / "commands" / "kanban_daemon.py"
    if not helper_file.exists():
        return

    blocked_imports = ("kanban_sync", "scripts.kanban_sync")
    side_effect_calls = []
    orig_import = builtins.__import__
    orig_subprocess_run = subprocess.run
    orig_subprocess_popen = subprocess.Popen
    orig_os_kill = os.kill

    sys.modules.pop("claudeteam.commands.kanban_daemon", None)
    for name in blocked_imports:
        sys.modules.pop(name, None)

    def _guarded_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name in blocked_imports or any(name.startswith(prefix + ".") for prefix in blocked_imports):
            raise AssertionError(f"kanban_daemon helper gate forbids import: {name}")
        return orig_import(name, globals, locals, fromlist, level)

    def _forbidden_subprocess(*args, **kwargs):
        side_effect_calls.append(("subprocess.run", args, kwargs))
        raise AssertionError("kanban_daemon helper import gate forbids subprocess.run")

    def _forbidden_popen(*args, **kwargs):
        side_effect_calls.append(("subprocess.Popen", args, kwargs))
        raise AssertionError("kanban_daemon helper import gate forbids subprocess.Popen")

    def _forbidden_kill(*args, **kwargs):
        side_effect_calls.append(("os.kill", args, kwargs))
        raise AssertionError("kanban_daemon helper import gate forbids os.kill")

    builtins.__import__ = _guarded_import
    subprocess.run = _forbidden_subprocess
    subprocess.Popen = _forbidden_popen
    os.kill = _forbidden_kill
    try:
        helper = importlib.import_module("claudeteam.commands.kanban_daemon")
    finally:
        builtins.__import__ = orig_import
        subprocess.run = orig_subprocess_run
        subprocess.Popen = orig_subprocess_popen
        os.kill = orig_os_kill

    for name in blocked_imports:
        assert name not in sys.modules, f"kanban_daemon helper imported forbidden module: {name}"
    assert not side_effect_calls, f"kanban_daemon helper import side-effects: {side_effect_calls!r}"

    for name in (
        "parse_pid_text",
        "is_expected_cmdline",
        "is_live_pid_probe",
        "pid_file_is_live",
    ):
        assert hasattr(helper, name), f"kanban_daemon missing {name}"
        assert callable(getattr(helper, name)), f"kanban_daemon {name} not callable"

    # missing file
    missing_calls = []

    def _missing_exists(path):
        missing_calls.append(("exists", path))
        return False

    def _unexpected_read_text(_path):
        raise AssertionError("read_text should not be called when pid file is missing")

    def _unexpected_pid_alive(_pid):
        raise AssertionError("pid_is_alive should not be called when pid file is missing")

    def _unexpected_cmdline(_pid):
        raise AssertionError("read_cmdline should not be called when pid file is missing")

    assert helper.pid_file_is_live(
        "/tmp/missing.pid",
        path_exists=_missing_exists,
        read_text=_unexpected_read_text,
        pid_is_alive=_unexpected_pid_alive,
        read_cmdline=_unexpected_cmdline,
    ) is False
    assert missing_calls == [("exists", "/tmp/missing.pid")], missing_calls

    # bad pid text
    bad_pid_calls = []

    def _bad_exists(_path):
        bad_pid_calls.append("exists")
        return True

    def _bad_read_text(_path):
        bad_pid_calls.append("read_text")
        return "not-a-number"

    def _bad_pid_alive(_pid):
        bad_pid_calls.append("pid_is_alive")
        raise AssertionError("pid_is_alive should not be called for bad pid")

    def _bad_cmdline(_pid):
        bad_pid_calls.append("read_cmdline")
        raise AssertionError("read_cmdline should not be called for bad pid")

    assert helper.pid_file_is_live(
        "/tmp/bad.pid",
        path_exists=_bad_exists,
        read_text=_bad_read_text,
        pid_is_alive=_bad_pid_alive,
        read_cmdline=_bad_cmdline,
    ) is False
    assert bad_pid_calls == ["exists", "read_text"], bad_pid_calls

    # pid reuse: alive pid but cmdline is unrelated process
    reuse_calls = []

    def _reuse_exists(_path):
        reuse_calls.append("exists")
        return True

    def _reuse_read_text(_path):
        reuse_calls.append("read_text")
        return "1234"

    def _reuse_pid_alive(pid):
        reuse_calls.append(("pid_is_alive", pid))
        return True

    def _reuse_cmdline(pid):
        reuse_calls.append(("read_cmdline", pid))
        return "python unrelated_worker.py"

    assert helper.pid_file_is_live(
        "/tmp/reuse.pid",
        path_exists=_reuse_exists,
        read_text=_reuse_read_text,
        pid_is_alive=_reuse_pid_alive,
        read_cmdline=_reuse_cmdline,
    ) is False
    assert reuse_calls == [
        "exists",
        "read_text",
        ("pid_is_alive", 1234),
        ("read_cmdline", 1234),
    ], reuse_calls

    # bad pid (stale pid file): kill/probe fails
    stale_calls = []

    def _stale_exists(_path):
        stale_calls.append("exists")
        return True

    def _stale_read_text(_path):
        stale_calls.append("read_text")
        return "5678"

    def _stale_pid_alive(pid):
        stale_calls.append(("pid_is_alive", pid))
        raise OSError("no such pid")

    def _stale_cmdline(_pid):
        stale_calls.append("read_cmdline")
        raise AssertionError("read_cmdline should not be called when pid probe fails")

    assert helper.pid_file_is_live(
        "/tmp/stale.pid",
        path_exists=_stale_exists,
        read_text=_stale_read_text,
        pid_is_alive=_stale_pid_alive,
        read_cmdline=_stale_cmdline,
    ) is False
    assert stale_calls == [
        "exists",
        "read_text",
        ("pid_is_alive", 5678),
    ], stale_calls

    # live
    live_calls = []

    def _live_exists(_path):
        live_calls.append("exists")
        return True

    def _live_read_text(_path):
        live_calls.append("read_text")
        return "4321"

    def _live_pid_alive(pid):
        live_calls.append(("pid_is_alive", pid))
        return True

    def _live_cmdline(pid):
        live_calls.append(("read_cmdline", pid))
        return "python3 scripts/kanban_sync.py daemon"

    assert helper.pid_file_is_live(
        "/tmp/live.pid",
        path_exists=_live_exists,
        read_text=_live_read_text,
        pid_is_alive=_live_pid_alive,
        read_cmdline=_live_cmdline,
    ) is True
    assert live_calls == [
        "exists",
        "read_text",
        ("pid_is_alive", 4321),
        ("read_cmdline", 4321),
    ], live_calls


def test_kanban_sync_daemon_pid_wrapper_gate() -> None:
    kanban_sync = importlib.import_module("kanban_sync")
    script_text = (ROOT / "scripts" / "kanban_sync.py").read_text(encoding="utf-8")

    # Static wrapper contract: script layer still delegates liveness decision to src helper.
    assert "from claudeteam.commands import kanban_daemon as _kanban_daemon" in script_text
    assert "return _kanban_daemon.pid_file_is_live(" in script_text
    assert "def cmd_daemon(interval=60):" in script_text
    assert "_acquire_pid_lock()" in script_text
    assert "time.sleep(interval)" in script_text

    # Runtime wrapper contract: _pid_file_is_live_kanban forwards to helper with injected fns.
    helper_calls = []
    old_helper = kanban_sync._kanban_daemon.pid_file_is_live
    try:
        def _fake_pid_file_is_live(path, *, path_exists, read_text, pid_is_alive, read_cmdline, expected_fragment):
            helper_calls.append(
                {
                    "path": path,
                    "path_exists": callable(path_exists),
                    "read_text": callable(read_text),
                    "pid_is_alive": callable(pid_is_alive),
                    "read_cmdline": callable(read_cmdline),
                    "expected_fragment": expected_fragment,
                }
            )
            return False

        kanban_sync._kanban_daemon.pid_file_is_live = _fake_pid_file_is_live
        assert kanban_sync._pid_file_is_live_kanban("/tmp/kanban.pid") is False
    finally:
        kanban_sync._kanban_daemon.pid_file_is_live = old_helper

    assert helper_calls == [
        {
            "path": "/tmp/kanban.pid",
            "path_exists": True,
            "read_text": True,
            "pid_is_alive": True,
            "read_cmdline": True,
            "expected_fragment": "kanban_sync.py",
        }
    ], helper_calls

    # _acquire_pid_lock must still use _pid_file_is_live_kanban checks before writing pid lock.
    old_pid_file = kanban_sync._PID_FILE
    old_legacy_pid_file = kanban_sync._LEGACY_PID_FILE
    old_is_live = kanban_sync._pid_file_is_live_kanban
    old_register = kanban_sync.atexit.register
    try:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            pid_file = tmp_path / "kanban_sync.pid"
            legacy_pid_file = tmp_path / ".kanban_sync.pid"
            legacy_pid_file.write_text("8899", encoding="utf-8")

            call_paths = []

            def _fake_is_live(path):
                call_paths.append(path)
                return path == str(legacy_pid_file)

            registered = []

            def _fake_register(fn):
                registered.append(fn)
                return fn

            kanban_sync._PID_FILE = str(pid_file)
            kanban_sync._LEGACY_PID_FILE = str(legacy_pid_file)
            kanban_sync._pid_file_is_live_kanban = _fake_is_live
            kanban_sync.atexit.register = _fake_register

            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                try:
                    kanban_sync._acquire_pid_lock()
                    assert False, "_acquire_pid_lock should exit when legacy pid is still live"
                except SystemExit as exc:
                    code = exc.code if isinstance(exc.code, int) else 1
                    assert code == 1, code

            assert call_paths == [str(pid_file), str(legacy_pid_file)], call_paths
            assert "kanban_sync daemon 已在运行 (PID 8899)" in out.getvalue(), out.getvalue()
            assert not pid_file.exists(), "pid lock file should not be created when live daemon already exists"
            assert not registered, registered

            call_paths.clear()

            def _always_not_live(path):
                call_paths.append(path)
                return False

            kanban_sync._pid_file_is_live_kanban = _always_not_live
            kanban_sync._acquire_pid_lock()
            assert call_paths == [str(pid_file), str(legacy_pid_file)], call_paths
            assert pid_file.exists(), "pid lock file should be created when no live daemon exists"
            assert pid_file.read_text(encoding="utf-8").strip() == str(os.getpid())
            assert registered and registered[-1] is kanban_sync._cleanup_pid, registered
    finally:
        kanban_sync._PID_FILE = old_pid_file
        kanban_sync._LEGACY_PID_FILE = old_legacy_pid_file
        kanban_sync._pid_file_is_live_kanban = old_is_live
        kanban_sync.atexit.register = old_register

    # cmd_daemon shell contract: acquire lock first, then loop with requested interval.
    old_acquire = kanban_sync._acquire_pid_lock
    old_load_cfg = kanban_sync.load_cfg
    old_do_sync = kanban_sync.do_sync
    old_sleep = kanban_sync.time.sleep
    loop_calls = []
    try:
        def _fake_acquire():
            loop_calls.append(("acquire", None))

        def _fake_load_cfg():
            loop_calls.append(("load_cfg", None))
            return {"kanban_table_id": "tbl_demo"}

        def _fake_do_sync(cfg):
            loop_calls.append(("do_sync", dict(cfg)))
            return None

        def _fake_sleep(interval):
            loop_calls.append(("sleep", interval))
            raise SystemExit(0)

        kanban_sync._acquire_pid_lock = _fake_acquire
        kanban_sync.load_cfg = _fake_load_cfg
        kanban_sync.do_sync = _fake_do_sync
        kanban_sync.time.sleep = _fake_sleep

        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            try:
                kanban_sync.cmd_daemon(interval=7)
                assert False, "cmd_daemon should be interrupted by fake sleep"
            except SystemExit as exc:
                code = exc.code if isinstance(exc.code, int) else 0
                assert code == 0, code

        assert loop_calls == [
            ("acquire", None),
            ("load_cfg", None),
            ("do_sync", {"kanban_table_id": "tbl_demo"}),
            ("sleep", 7),
        ], loop_calls
        assert "每 7 秒同步一次" in out.getvalue(), out.getvalue()
    finally:
        kanban_sync._acquire_pid_lock = old_acquire
        kanban_sync.load_cfg = old_load_cfg
        kanban_sync.do_sync = old_do_sync
        kanban_sync.time.sleep = old_sleep


def test_kanban_service_import_and_injection_gate_when_present() -> None:
    service_file = ROOT / "src" / "claudeteam" / "integrations" / "feishu" / "kanban_service.py"
    if not service_file.exists():
        return

    blocked_imports = ("kanban_sync", "scripts.kanban_sync")
    side_effect_calls = []
    orig_import = builtins.__import__
    orig_subprocess_run = subprocess.run
    orig_subprocess_popen = subprocess.Popen
    orig_os_kill = os.kill

    sys.modules.pop("claudeteam.integrations.feishu.kanban_service", None)
    for name in blocked_imports:
        sys.modules.pop(name, None)

    def _guarded_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name in blocked_imports or any(name.startswith(prefix + ".") for prefix in blocked_imports):
            raise AssertionError(f"kanban_service gate forbids import: {name}")
        return orig_import(name, globals, locals, fromlist, level)

    def _forbidden_subprocess(*args, **kwargs):
        side_effect_calls.append(("subprocess.run", args, kwargs))
        raise AssertionError("kanban_service gate forbids subprocess.run (lark/tmux included)")

    def _forbidden_popen(*args, **kwargs):
        side_effect_calls.append(("subprocess.Popen", args, kwargs))
        raise AssertionError("kanban_service gate forbids subprocess.Popen (lark/tmux included)")

    def _forbidden_kill(*args, **kwargs):
        side_effect_calls.append(("os.kill", args, kwargs))
        raise AssertionError("kanban_service gate forbids os.kill")

    builtins.__import__ = _guarded_import
    subprocess.run = _forbidden_subprocess
    subprocess.Popen = _forbidden_popen
    os.kill = _forbidden_kill
    try:
        service = importlib.import_module("claudeteam.integrations.feishu.kanban_service")
    finally:
        builtins.__import__ = orig_import
        subprocess.run = orig_subprocess_run
        subprocess.Popen = orig_subprocess_popen
        os.kill = orig_os_kill

    for name in blocked_imports:
        assert name not in sys.modules, f"kanban_service imported forbidden module: {name}"
    assert not side_effect_calls, f"kanban_service import side-effects: {side_effect_calls!r}"

    helper_names = (
        "ensure_kanban_table_with_run",
        "fetch_all_agent_status_with_run",
        "get_all_kanban_record_ids_with_run",
        "delete_all_kanban_records_with_run",
        "bitable_batch_create_with_run",
    )
    for name in helper_names:
        assert hasattr(service, name), f"kanban_service missing {name}"
        assert callable(getattr(service, name)), f"kanban_service {name} not callable"

    cfg = {"bitable_app_token": "app_tok", "sta_table_id": "sta_tb", "kanban_table_id": "kanban_tb"}

    existing_cfg = {"bitable_app_token": "app_tok", "kanban_table_id": "tbl_existing"}
    existing_lark_calls = []
    save_calls = []

    def _existing_lark(args, label=""):
        existing_lark_calls.append((list(args), label))
        return {"should_not": "run"}

    def _save_cfg(payload):
        save_calls.append(dict(payload))

    ok, payload = service.ensure_kanban_table_with_run(existing_cfg, _existing_lark, _save_cfg)
    assert ok is True and payload == "tbl_existing", (ok, payload)
    assert not existing_lark_calls, existing_lark_calls
    assert not save_calls, save_calls

    create_cfg = {"bitable_app_token": "app_tok"}
    create_calls = []

    def _create_lark(args, label=""):
        create_calls.append((list(args), label))
        return {"table": {"id": "tbl_new"}}

    ok, payload = service.ensure_kanban_table_with_run(create_cfg, _create_lark, _save_cfg)
    assert ok is True and payload == "tbl_new", (ok, payload)
    assert create_cfg.get("kanban_table_id") == "tbl_new", create_cfg
    assert save_calls and save_calls[-1].get("kanban_table_id") == "tbl_new", save_calls
    assert create_calls and create_calls[-1][1] == "创建看板表", create_calls
    create_args = create_calls[-1][0]
    assert create_args[:2] == ["base", "+table-create"], create_args
    assert "--name" in create_args and "项目看板" in create_args, create_args
    assert "--fields" in create_args, create_args

    fail_cfg = {"bitable_app_token": "app_tok"}
    fail_calls = []

    def _fail_lark(args, label=""):
        fail_calls.append((list(args), label))
        return {"table": {}}

    ok, payload = service.ensure_kanban_table_with_run(fail_cfg, _fail_lark, _save_cfg)
    assert ok is False and payload == {"table": {}}, (ok, payload)
    assert "kanban_table_id" not in fail_cfg, fail_cfg
    assert fail_calls and fail_calls[-1][1] == "创建看板表", fail_calls

    fetch_calls = []

    def _fetch_run(args, label=""):
        fetch_calls.append((list(args), label))
        return {
            "items": [
                {
                    "fields": {
                        "Agent名称": [{"text": "toolsmith"}],
                        "状态": [{"text": "进行中"}],
                        "当前任务": [{"text": "门禁回归"}],
                        "更新时间": [{"value": 1712345678000}],
                    }
                }
            ]
        }

    status = service.fetch_all_agent_status_with_run(cfg, _fetch_run)
    assert status == {
        "toolsmith": {"状态": "进行中", "当前任务": "门禁回归", "更新时间": 1712345678000}
    }, status
    assert fetch_calls and fetch_calls[0][1] == "拉取状态表", fetch_calls
    fetch_args = fetch_calls[0][0]
    assert fetch_args[:2] == ["base", "+record-list"], fetch_args
    assert "--base-token" in fetch_args and "app_tok" in fetch_args, fetch_args
    assert "--table-id" in fetch_args and "sta_tb" in fetch_args, fetch_args
    assert service.fetch_all_agent_status_with_run(cfg, lambda *_args, **_kwargs: None) is None

    ids_calls = []

    def _ids_run(args, label=""):
        ids_calls.append((list(args), label))
        return {"items": [{"record_id": "rec_1"}, {"record_id": "rec_2"}, {"record_id": "rec_1"}]}

    ids = service.get_all_kanban_record_ids_with_run(cfg, _ids_run)
    assert ids == ["rec_1", "rec_2", "rec_1"], ids
    assert ids_calls and ids_calls[0][1] == "获取看板记录", ids_calls
    assert service.get_all_kanban_record_ids_with_run(cfg, lambda *_args, **_kwargs: None) is None

    delete_fail = service.delete_all_kanban_records_with_run(
        cfg,
        lambda _args, label="": None if label == "获取看板记录" else {},
    )
    assert delete_fail is False

    delete_calls = []

    def _delete_run(args, label=""):
        delete_calls.append((list(args), label))
        if label == "获取看板记录":
            return {"items": [{"record_id": "a"}, {"record_id": "b"}, {"record_id": "c"}]}
        return {"ok": True}

    delete_ok = service.delete_all_kanban_records_with_run(cfg, _delete_run, batch_delete_limit=2)
    assert delete_ok is True
    assert len(delete_calls) == 3, delete_calls
    batch_calls = delete_calls[1:]
    assert all(call[0][0:2] == ["api", "POST"] for call in batch_calls), delete_calls
    assert all("批删记录" in call[1] for call in batch_calls), delete_calls
    path = f"/open-apis/bitable/v1/apps/{cfg['bitable_app_token']}/tables/{cfg['kanban_table_id']}/records/batch_delete"
    assert all(path in call[0] for call in batch_calls), delete_calls

    delete_batch_fail_calls = []

    def _delete_batch_fail_run(args, label=""):
        delete_batch_fail_calls.append((list(args), label))
        if label == "获取看板记录":
            return {"items": [{"record_id": "a"}]}
        return None

    assert service.delete_all_kanban_records_with_run(cfg, _delete_batch_fail_run) is False
    assert len(delete_batch_fail_calls) == 2, delete_batch_fail_calls

    create_calls = []

    def _create_run(args, label=""):
        create_calls.append((list(args), label))
        return {"ok": True}

    payload = '{"fields":["任务ID"],"rows":[["TASK-1"]]}'
    assert service.bitable_batch_create_with_run(cfg, payload, _create_run) is True
    assert create_calls and create_calls[0][1] == "批量写入看板", create_calls
    create_args = create_calls[0][0]
    assert create_args[:2] == ["base", "+record-batch-create"], create_args
    assert "--json" in create_args and payload in create_args, create_args
    assert service.bitable_batch_create_with_run(cfg, payload, lambda *_args, **_kwargs: None) is False


def test_kanban_sync_wrapper_uses_service_contract() -> None:
    script_file = ROOT / "scripts" / "kanban_sync.py"
    text = script_file.read_text(encoding="utf-8")
    assert "from claudeteam.integrations.feishu import kanban_service as _kanban_service" in text
    assert "ok, payload = _kanban_service.ensure_kanban_table_with_run(cfg, _lark, save_cfg)" in text
    assert "if not ok:" in text and "sys.exit(1)" in text
    assert "_kanban_service.sync_kanban_snapshot_with_run(" in text


def test_kanban_cmd_init_service_delegate_contract() -> None:
    kanban_sync = importlib.import_module("kanban_sync")

    calls = []
    old_load_cfg = kanban_sync.load_cfg
    old_save_cfg = kanban_sync.save_cfg
    old_service_ensure = kanban_sync._kanban_service.ensure_kanban_table_with_run
    try:
        cfg = {"bitable_app_token": "app_tok"}

        def _fake_load_cfg():
            return cfg

        def _fake_save_cfg(payload):
            calls.append(("save_cfg", dict(payload)))

        def _fake_ensure_ok(got_cfg, lark_run, save_cfg):
            calls.append(("ensure_ok", dict(got_cfg), callable(lark_run), save_cfg is _fake_save_cfg))
            return True, "tbl_ok"

        kanban_sync.load_cfg = _fake_load_cfg
        kanban_sync.save_cfg = _fake_save_cfg
        kanban_sync._kanban_service.ensure_kanban_table_with_run = _fake_ensure_ok

        kanban_sync.cmd_init()
        assert calls and calls[0][0] == "ensure_ok", calls
        assert calls[0][1] == cfg, calls
        assert calls[0][2] is True and calls[0][3] is True, calls

        def _fake_ensure_fail(got_cfg, lark_run, save_cfg):
            calls.append(("ensure_fail", dict(got_cfg), callable(lark_run), save_cfg is _fake_save_cfg))
            return False, {"error": "create failed"}

        kanban_sync._kanban_service.ensure_kanban_table_with_run = _fake_ensure_fail
        out = io.StringIO()
        err = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                kanban_sync.cmd_init()
                assert False, "cmd_init should raise SystemExit on ensure failure"
            except SystemExit as exc:
                code = exc.code if isinstance(exc.code, int) else 1
                assert code == 1, code
        text = out.getvalue() + err.getvalue()
        assert "创建项目看板表失败" in text, text
        assert "create failed" in text, text
    finally:
        kanban_sync.load_cfg = old_load_cfg
        kanban_sync.save_cfg = old_save_cfg
        kanban_sync._kanban_service.ensure_kanban_table_with_run = old_service_ensure


def test_watchdog_entrypoint_import_and_main_contract() -> None:
    assert WATCHDOG_SCRIPT.exists(), f"missing watchdog entrypoint: {WATCHDOG_SCRIPT}"

    sys.modules.pop("watchdog", None)
    import_forbidden = []
    orig_subprocess_run = subprocess.run
    orig_os_kill = os.kill

    def _forbidden_subprocess(*args, **kwargs):
        import_forbidden.append(("subprocess.run", args, kwargs))
        raise AssertionError("watchdog import should not call subprocess.run")

    def _forbidden_kill(*args, **kwargs):
        import_forbidden.append(("os.kill", args, kwargs))
        raise AssertionError("watchdog import should not call os.kill")

    subprocess.run = _forbidden_subprocess
    os.kill = _forbidden_kill
    try:
        watchdog = importlib.import_module("watchdog")
    finally:
        subprocess.run = orig_subprocess_run
        os.kill = orig_os_kill

    assert callable(getattr(watchdog, "main", None)), "watchdog.main missing"
    assert not import_forbidden, f"watchdog import triggered side effects: {import_forbidden!r}"

    calls = {"acquire": 0, "check_once": 0}
    runtime_forbidden = []

    def _fake_acquire():
        calls["acquire"] += 1

    def _fake_check_once():
        calls["check_once"] += 1
        raise SystemExit(0)

    def _fake_sleep(_secs):
        return None

    def _forbidden_runtime_subprocess(*args, **kwargs):
        runtime_forbidden.append(("subprocess.run", args, kwargs))
        raise AssertionError("watchdog main gate forbids subprocess.run")

    def _forbidden_runtime_kill(*args, **kwargs):
        runtime_forbidden.append(("os.kill", args, kwargs))
        raise AssertionError("watchdog main gate forbids os.kill")

    old_acquire = watchdog._acquire_pid_lock
    old_check_once = watchdog.check_once
    old_sleep = watchdog.time.sleep
    old_startup_grace = watchdog.STARTUP_GRACE_SECS
    old_subprocess_run = watchdog.subprocess.run
    old_os_kill = watchdog.os.kill
    old_argv = list(sys.argv)

    out = io.StringIO()
    err = io.StringIO()
    try:
        watchdog._acquire_pid_lock = _fake_acquire
        watchdog.check_once = _fake_check_once
        watchdog.time.sleep = _fake_sleep
        watchdog.STARTUP_GRACE_SECS = 0
        watchdog.subprocess.run = _forbidden_runtime_subprocess
        watchdog.os.kill = _forbidden_runtime_kill
        sys.argv = [str(WATCHDOG_SCRIPT)]
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                watchdog.main()
                code = 0
            except SystemExit as exc:
                code = exc.code if isinstance(exc.code, int) else 0
    finally:
        watchdog._acquire_pid_lock = old_acquire
        watchdog.check_once = old_check_once
        watchdog.time.sleep = old_sleep
        watchdog.STARTUP_GRACE_SECS = old_startup_grace
        watchdog.subprocess.run = old_subprocess_run
        watchdog.os.kill = old_os_kill
        sys.argv = old_argv

    text = out.getvalue() + err.getvalue()
    assert code == 0, (code, text)
    assert calls["acquire"] == 1, calls
    assert calls["check_once"] == 1, calls
    assert "Watchdog 启动" in text, text
    assert not runtime_forbidden, f"watchdog main triggered side effects: {runtime_forbidden!r}"


def test_watchdog_daemon_helper_import_and_contract_gate_when_present() -> None:
    helper_file = ROOT / "src" / "claudeteam" / "supervision" / "watchdog_daemon.py"
    if not helper_file.exists():
        return

    blocked_imports = ("watchdog", "scripts.watchdog")
    side_effect_calls = []
    orig_import = builtins.__import__
    orig_subprocess_run = subprocess.run
    orig_subprocess_popen = subprocess.Popen
    orig_os_kill = os.kill

    sys.modules.pop("claudeteam.supervision.watchdog_daemon", None)
    for name in blocked_imports:
        sys.modules.pop(name, None)

    def _guarded_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name in blocked_imports or any(name.startswith(prefix + ".") for prefix in blocked_imports):
            raise AssertionError(f"watchdog_daemon helper gate forbids import: {name}")
        return orig_import(name, globals, locals, fromlist, level)

    def _forbidden_subprocess(*args, **kwargs):
        side_effect_calls.append(("subprocess.run", args, kwargs))
        raise AssertionError("watchdog_daemon helper import gate forbids subprocess.run")

    def _forbidden_popen(*args, **kwargs):
        side_effect_calls.append(("subprocess.Popen", args, kwargs))
        raise AssertionError("watchdog_daemon helper import gate forbids subprocess.Popen")

    def _forbidden_kill(*args, **kwargs):
        side_effect_calls.append(("os.kill", args, kwargs))
        raise AssertionError("watchdog_daemon helper import gate forbids os.kill")

    builtins.__import__ = _guarded_import
    subprocess.run = _forbidden_subprocess
    subprocess.Popen = _forbidden_popen
    os.kill = _forbidden_kill
    try:
        helper = importlib.import_module("claudeteam.supervision.watchdog_daemon")
    finally:
        builtins.__import__ = orig_import
        subprocess.run = orig_subprocess_run
        subprocess.Popen = orig_subprocess_popen
        os.kill = orig_os_kill

    for name in blocked_imports:
        assert name not in sys.modules, f"watchdog_daemon helper imported forbidden module: {name}"
    assert not side_effect_calls, f"watchdog_daemon helper import side-effects: {side_effect_calls!r}"

    for name in (
        "parse_pid_text",
        "is_expected_cmdline",
        "is_live_pid_probe",
        "pid_file_is_live",
    ):
        assert hasattr(helper, name), f"watchdog_daemon missing {name}"
        assert callable(getattr(helper, name)), f"watchdog_daemon {name} not callable"

    assert helper.parse_pid_text("123") == 123
    assert helper.parse_pid_text(" 456 ") == 456
    assert helper.parse_pid_text("") is None
    assert helper.parse_pid_text(None) is None

    assert helper.is_expected_cmdline("python3 scripts/watchdog.py --once") is True
    assert helper.is_expected_cmdline("python3 scripts/other.py") is False
    assert helper.is_expected_cmdline(None) is False

    assert helper.is_live_pid_probe(
        123,
        pid_alive=True,
        cmdline="python3 scripts/watchdog.py",
    ) is True
    assert helper.is_live_pid_probe(
        123,
        pid_alive=True,
        cmdline="python3 scripts/worker.py",
    ) is False
    assert helper.is_live_pid_probe(
        None,
        pid_alive=True,
        cmdline="python3 scripts/watchdog.py",
    ) is False
    assert helper.is_live_pid_probe(
        123,
        pid_alive=False,
        cmdline="python3 scripts/watchdog.py",
    ) is False

    missing_calls = []

    def _missing_exists(path):
        missing_calls.append(("exists", path))
        return False

    def _unexpected_read_text(_path):
        raise AssertionError("read_text should not be called when pid file is missing")

    def _unexpected_pid_alive(_pid):
        raise AssertionError("pid_is_alive should not be called when pid file is missing")

    def _unexpected_cmdline(_pid):
        raise AssertionError("read_cmdline should not be called when pid file is missing")

    assert helper.pid_file_is_live(
        "/tmp/missing-watchdog.pid",
        path_exists=_missing_exists,
        read_text=_unexpected_read_text,
        pid_is_alive=_unexpected_pid_alive,
        read_cmdline=_unexpected_cmdline,
    ) is False
    assert missing_calls == [("exists", "/tmp/missing-watchdog.pid")], missing_calls

    bad_pid_calls = []

    def _bad_exists(_path):
        bad_pid_calls.append("exists")
        return True

    def _bad_read_text(_path):
        bad_pid_calls.append("read_text")
        return "not-a-number"

    def _bad_pid_alive(_pid):
        bad_pid_calls.append("pid_is_alive")
        raise AssertionError("pid_is_alive should not be called for bad pid")

    def _bad_cmdline(_pid):
        bad_pid_calls.append("read_cmdline")
        raise AssertionError("read_cmdline should not be called for bad pid")

    assert helper.pid_file_is_live(
        "/tmp/bad-watchdog.pid",
        path_exists=_bad_exists,
        read_text=_bad_read_text,
        pid_is_alive=_bad_pid_alive,
        read_cmdline=_bad_cmdline,
    ) is False
    assert bad_pid_calls == ["exists", "read_text"], bad_pid_calls

    stale_calls = []

    def _stale_exists(_path):
        stale_calls.append("exists")
        return True

    def _stale_read_text(_path):
        stale_calls.append("read_text")
        return "5678"

    def _stale_pid_alive(pid):
        stale_calls.append(("pid_is_alive", pid))
        raise OSError("no such pid")

    def _stale_cmdline(_pid):
        stale_calls.append("read_cmdline")
        raise AssertionError("read_cmdline should not be called when pid probe fails")

    assert helper.pid_file_is_live(
        "/tmp/stale-watchdog.pid",
        path_exists=_stale_exists,
        read_text=_stale_read_text,
        pid_is_alive=_stale_pid_alive,
        read_cmdline=_stale_cmdline,
    ) is False
    assert stale_calls == [
        "exists",
        "read_text",
        ("pid_is_alive", 5678),
    ], stale_calls

    reuse_calls = []

    def _reuse_exists(_path):
        reuse_calls.append("exists")
        return True

    def _reuse_read_text(_path):
        reuse_calls.append("read_text")
        return "9999"

    def _reuse_pid_alive(pid):
        reuse_calls.append(("pid_is_alive", pid))
        return True

    def _reuse_cmdline(pid):
        reuse_calls.append(("read_cmdline", pid))
        return "python3 scripts/worker.py"

    assert helper.pid_file_is_live(
        "/tmp/reuse-watchdog.pid",
        path_exists=_reuse_exists,
        read_text=_reuse_read_text,
        pid_is_alive=_reuse_pid_alive,
        read_cmdline=_reuse_cmdline,
    ) is False
    assert reuse_calls == [
        "exists",
        "read_text",
        ("pid_is_alive", 9999),
        ("read_cmdline", 9999),
    ], reuse_calls

    live_calls = []

    def _live_exists(_path):
        live_calls.append("exists")
        return True

    def _live_read_text(_path):
        live_calls.append("read_text")
        return "4321"

    def _live_pid_alive(pid):
        live_calls.append(("pid_is_alive", pid))
        return True

    def _live_cmdline(pid):
        live_calls.append(("read_cmdline", pid))
        return "python3 scripts/watchdog.py"

    assert helper.pid_file_is_live(
        "/tmp/live-watchdog.pid",
        path_exists=_live_exists,
        read_text=_live_read_text,
        pid_is_alive=_live_pid_alive,
        read_cmdline=_live_cmdline,
    ) is True
    assert live_calls == [
        "exists",
        "read_text",
        ("pid_is_alive", 4321),
        ("read_cmdline", 4321),
    ], live_calls

    assert helper.pid_file_is_live(
        "/tmp/custom-fragment.pid",
        path_exists=lambda _path: True,
        read_text=lambda _path: "123",
        pid_is_alive=lambda _pid: True,
        read_cmdline=lambda _pid: "python3 /tmp/custom_watchdog_entry.py",
        expected_fragment="custom_watchdog_entry.py",
    ) is True


def test_watchdog_daemon_pid_wrapper_gate() -> None:
    helper_file = ROOT / "src" / "claudeteam" / "supervision" / "watchdog_daemon.py"
    if not helper_file.exists():
        return

    watchdog = importlib.import_module("watchdog")
    script_text = (ROOT / "scripts" / "watchdog.py").read_text(encoding="utf-8")

    # Static wrapper contract: script layer delegates watchdog pid liveness to src helper.
    assert "from claudeteam.supervision import watchdog_daemon as _watchdog_daemon" in script_text
    assert "def _pid_file_is_live_watchdog(path):" in script_text
    assert "return _watchdog_daemon.pid_file_is_live(" in script_text
    assert "expected_fragment=\"watchdog.py\"" in script_text
    assert "def _acquire_pid_lock():" in script_text
    assert "if _pid_file_is_live_watchdog(_PID_FILE):" in script_text
    assert "_acquire_pid_lock()" in script_text

    # Runtime wrapper contract: _pid_file_is_live_watchdog forwards to helper with injected fns.
    helper_calls = []
    old_helper = watchdog._watchdog_daemon.pid_file_is_live
    try:
        def _fake_pid_file_is_live(path, *, path_exists, read_text, pid_is_alive, read_cmdline, expected_fragment):
            helper_calls.append(
                {
                    "path": path,
                    "path_exists": callable(path_exists),
                    "read_text": callable(read_text),
                    "pid_is_alive": callable(pid_is_alive),
                    "read_cmdline": callable(read_cmdline),
                    "expected_fragment": expected_fragment,
                }
            )
            return False

        watchdog._watchdog_daemon.pid_file_is_live = _fake_pid_file_is_live
        assert watchdog._pid_file_is_live_watchdog("/tmp/watchdog.pid") is False
    finally:
        watchdog._watchdog_daemon.pid_file_is_live = old_helper

    assert helper_calls == [
        {
            "path": "/tmp/watchdog.pid",
            "path_exists": True,
            "read_text": True,
            "pid_is_alive": True,
            "read_cmdline": True,
            "expected_fragment": "watchdog.py",
        }
    ], helper_calls

    # _acquire_pid_lock must still use helper-gated live checks before writing pid lock.
    old_pid_file = watchdog._PID_FILE
    old_is_live = watchdog._pid_file_is_live_watchdog
    old_register = watchdog.atexit.register
    old_subprocess_run = watchdog.subprocess.run
    old_subprocess_popen = watchdog.subprocess.Popen
    old_os_kill = watchdog.os.kill
    try:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            pid_file = tmp_path / "watchdog.pid"
            pid_file.write_text("8899", encoding="utf-8")

            call_paths = []

            def _fake_is_live(path):
                call_paths.append(path)
                return True

            registered = []

            def _fake_register(fn):
                registered.append(fn)
                return fn

            def _forbidden_subprocess(*args, **kwargs):
                raise AssertionError(f"watchdog pid wrapper gate forbids subprocess side effect: {args!r} {kwargs!r}")

            def _forbidden_kill(*args, **kwargs):
                raise AssertionError(f"watchdog pid wrapper gate forbids os.kill side effect: {args!r} {kwargs!r}")

            watchdog._PID_FILE = str(pid_file)
            watchdog._pid_file_is_live_watchdog = _fake_is_live
            watchdog.atexit.register = _fake_register
            watchdog.subprocess.run = _forbidden_subprocess
            watchdog.subprocess.Popen = _forbidden_subprocess
            watchdog.os.kill = _forbidden_kill

            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                try:
                    watchdog._acquire_pid_lock()
                    assert False, "_acquire_pid_lock should exit when watchdog pid is still live"
                except SystemExit as exc:
                    code = exc.code if isinstance(exc.code, int) else 1
                    assert code == 1, code

            assert call_paths == [str(pid_file)], call_paths
            assert "Watchdog 已在运行 (PID 8899)" in out.getvalue(), out.getvalue()
            assert pid_file.read_text(encoding="utf-8").strip() == "8899"
            assert not registered, registered

            call_paths.clear()

            def _always_not_live(path):
                call_paths.append(path)
                return False

            watchdog._pid_file_is_live_watchdog = _always_not_live
            watchdog._acquire_pid_lock()
            assert call_paths == [str(pid_file)], call_paths
            assert pid_file.exists(), "pid lock file should be created when no live watchdog exists"
            assert pid_file.read_text(encoding="utf-8").strip() == str(os.getpid())
            assert registered and registered[-1] is watchdog._cleanup_pid, registered
    finally:
        watchdog._PID_FILE = old_pid_file
        watchdog._pid_file_is_live_watchdog = old_is_live
        watchdog.atexit.register = old_register
        watchdog.subprocess.run = old_subprocess_run
        watchdog.subprocess.Popen = old_subprocess_popen
        watchdog.os.kill = old_os_kill


def test_watchdog_state_helper_import_gate_when_present() -> None:
    helper_file = ROOT / "src" / "claudeteam" / "supervision" / "watchdog_state.py"
    if not helper_file.exists():
        return

    blocked_imports = ("watchdog", "scripts.watchdog")
    side_effect_calls = []
    orig_import = builtins.__import__
    orig_subprocess_run = subprocess.run
    orig_subprocess_popen = subprocess.Popen
    orig_os_kill = os.kill

    sys.modules.pop("claudeteam.supervision.watchdog_state", None)
    for name in blocked_imports:
        sys.modules.pop(name, None)

    def _guarded_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name in blocked_imports or any(name.startswith(prefix + ".") for prefix in blocked_imports):
            raise AssertionError(f"watchdog_state helper gate forbids import: {name}")
        return orig_import(name, globals, locals, fromlist, level)

    def _forbidden_subprocess(*args, **kwargs):
        side_effect_calls.append(("subprocess.run", args, kwargs))
        raise AssertionError("watchdog_state helper import gate forbids subprocess.run (lark/tmux included)")

    def _forbidden_popen(*args, **kwargs):
        side_effect_calls.append(("subprocess.Popen", args, kwargs))
        raise AssertionError("watchdog_state helper import gate forbids subprocess.Popen (lark/tmux included)")

    def _forbidden_kill(*args, **kwargs):
        side_effect_calls.append(("os.kill", args, kwargs))
        raise AssertionError("watchdog_state helper import gate forbids os.kill")

    builtins.__import__ = _guarded_import
    subprocess.run = _forbidden_subprocess
    subprocess.Popen = _forbidden_popen
    os.kill = _forbidden_kill
    try:
        helper = importlib.import_module("claudeteam.supervision.watchdog_state")
    finally:
        builtins.__import__ = orig_import
        subprocess.run = orig_subprocess_run
        subprocess.Popen = orig_subprocess_popen
        os.kill = orig_os_kill

    for name in blocked_imports:
        assert name not in sys.modules, f"watchdog_state helper imported forbidden module: {name}"

    assert callable(getattr(helper, "decide_watchdog_state", None)), "watchdog_state helper missing decide fn"
    assert not side_effect_calls, f"watchdog_state helper import side-effects: {side_effect_calls!r}"


def test_watchdog_specs_helper_import_and_contract_gate_when_present() -> None:
    helper_file = ROOT / "src" / "claudeteam" / "supervision" / "watchdog_specs.py"
    if not helper_file.exists():
        return

    blocked_imports = ("watchdog", "scripts.watchdog")
    side_effect_calls = []
    orig_import = builtins.__import__
    orig_subprocess_run = subprocess.run
    orig_subprocess_popen = subprocess.Popen
    orig_os_kill = os.kill

    sys.modules.pop("claudeteam.supervision.watchdog_specs", None)
    for name in blocked_imports:
        sys.modules.pop(name, None)

    def _guarded_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name in blocked_imports or any(name.startswith(prefix + ".") for prefix in blocked_imports):
            raise AssertionError(f"watchdog_specs helper gate forbids import: {name}")
        return orig_import(name, globals, locals, fromlist, level)

    def _forbidden_subprocess(*args, **kwargs):
        side_effect_calls.append(("subprocess.run", args, kwargs))
        raise AssertionError("watchdog_specs helper import gate forbids subprocess.run (lark/tmux included)")

    def _forbidden_popen(*args, **kwargs):
        side_effect_calls.append(("subprocess.Popen", args, kwargs))
        raise AssertionError("watchdog_specs helper import gate forbids subprocess.Popen (lark/tmux included)")

    def _forbidden_kill(*args, **kwargs):
        side_effect_calls.append(("os.kill", args, kwargs))
        raise AssertionError("watchdog_specs helper import gate forbids os.kill")

    builtins.__import__ = _guarded_import
    subprocess.run = _forbidden_subprocess
    subprocess.Popen = _forbidden_popen
    os.kill = _forbidden_kill
    try:
        helper = importlib.import_module("claudeteam.supervision.watchdog_specs")
    finally:
        builtins.__import__ = orig_import
        subprocess.run = orig_subprocess_run
        subprocess.Popen = orig_subprocess_popen
        os.kill = orig_os_kill

    for name in blocked_imports:
        assert name not in sys.modules, f"watchdog_specs helper imported forbidden module: {name}"
    assert not side_effect_calls, f"watchdog_specs helper import side-effects: {side_effect_calls!r}"

    for name in (
        "build_lark_event_subscribe_cmd",
        "build_process_specs",
        "env_enabled",
        "filter_enabled_processes",
    ):
        assert hasattr(helper, name), f"watchdog_specs missing {name}"
        assert callable(getattr(helper, name)), f"watchdog_specs {name} not callable"

    fake_env = {
        "YES_1": "1",
        "YES_TRUE": " true ",
        "YES_ON": "ON",
        "YES_YES": "yes",
        "NO_0": "0",
        "NO_FALSE": "false",
        "NO_OFF": "off",
    }
    assert helper.env_enabled("YES_1", env=fake_env) is True
    assert helper.env_enabled("YES_TRUE", env=fake_env) is True
    assert helper.env_enabled("YES_ON", env=fake_env) is True
    assert helper.env_enabled("YES_YES", env=fake_env) is True
    assert helper.env_enabled("NO_0", env=fake_env) is False
    assert helper.env_enabled("NO_FALSE", env=fake_env) is False
    assert helper.env_enabled("NO_OFF", env=fake_env) is False
    assert helper.env_enabled("MISSING", env=fake_env) is False

    lark_cmd = helper.build_lark_event_subscribe_cmd(["npx", "lark-cli"])
    assert "npx lark-cli event +subscribe" in lark_cmd, lark_cmd
    assert "--event-types im.message.receive_v1" in lark_cmd, lark_cmd
    assert "--compact --quiet --force --as bot" in lark_cmd, lark_cmd

    specs = helper.build_process_specs(
        lark_cli=["npx", "lark-cli"],
        router_pid_file="/tmp/router.pid",
        router_cursor_file="/tmp/router.cursor",
        kanban_pid_file="/tmp/kanban.pid",
    )
    assert isinstance(specs, list) and len(specs) == 2, specs

    by_match = {str(spec.get("match", "")): spec for spec in specs}
    assert "feishu_router.py" in by_match, by_match
    assert "kanban_sync.py daemon" in by_match, by_match

    router_spec = by_match["feishu_router.py"]
    assert router_spec.get("pid_file") == "/tmp/router.pid", router_spec
    assert router_spec.get("health_file") == "/tmp/router.cursor", router_spec
    assert router_spec.get("cmd", [None, None])[0:2] == ["bash", "-c"], router_spec
    router_cmd = router_spec.get("cmd", [None, None, ""])[2]
    assert "event +subscribe" in router_cmd and "scripts/feishu_router.py --stdin" in router_cmd, router_cmd
    assert router_spec.get("max_retries") == 3, router_spec
    assert router_spec.get("cooldown_secs") == 600, router_spec

    kanban_spec = by_match["kanban_sync.py daemon"]
    assert kanban_spec.get("pid_file") == "/tmp/kanban.pid", kanban_spec
    assert kanban_spec.get("cmd") == ["python3", "scripts/kanban_sync.py", "daemon"], kanban_spec
    assert kanban_spec.get("max_retries") == 3, kanban_spec
    assert kanban_spec.get("cooldown_secs") == 600, kanban_spec

    none_enabled = helper.filter_enabled_processes(specs, env={})
    assert none_enabled == [], none_enabled

    router_only = helper.filter_enabled_processes(
        specs,
        env={"CLAUDETEAM_ENABLE_FEISHU_REMOTE": "1"},
    )
    assert len(router_only) == 1 and router_only[0].get("match") == "feishu_router.py", router_only

    kanban_only = helper.filter_enabled_processes(
        specs,
        env={"CLAUDETEAM_ENABLE_BITABLE_LEGACY": "true"},
    )
    assert len(kanban_only) == 1 and kanban_only[0].get("match") == "kanban_sync.py daemon", kanban_only

    both_enabled = helper.filter_enabled_processes(
        specs,
        env={
            "CLAUDETEAM_ENABLE_FEISHU_REMOTE": "1",
            "CLAUDETEAM_ENABLE_BITABLE_LEGACY": "on",
        },
    )
    assert len(both_enabled) == 2, both_enabled

    both_enabled[0]["name"] = "__mutated__"
    assert specs[0].get("name") != "__mutated__", "filter_enabled_processes should return copied dicts"


def test_watchdog_health_helper_import_and_contract_gate_when_present() -> None:
    helper_file = ROOT / "src" / "claudeteam" / "supervision" / "watchdog_health.py"
    if not helper_file.exists():
        return

    blocked_imports = ("watchdog", "scripts.watchdog")
    side_effect_calls = []
    orig_import = builtins.__import__
    orig_subprocess_run = subprocess.run
    orig_subprocess_popen = subprocess.Popen
    orig_os_kill = os.kill

    sys.modules.pop("claudeteam.supervision.watchdog_health", None)
    for name in blocked_imports:
        sys.modules.pop(name, None)

    def _guarded_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name in blocked_imports or any(name.startswith(prefix + ".") for prefix in blocked_imports):
            raise AssertionError(f"watchdog_health helper gate forbids import: {name}")
        return orig_import(name, globals, locals, fromlist, level)

    def _forbidden_subprocess(*args, **kwargs):
        side_effect_calls.append(("subprocess.run", args, kwargs))
        raise AssertionError("watchdog_health helper import gate forbids subprocess.run (lark/tmux included)")

    def _forbidden_popen(*args, **kwargs):
        side_effect_calls.append(("subprocess.Popen", args, kwargs))
        raise AssertionError("watchdog_health helper import gate forbids subprocess.Popen (lark/tmux included)")

    def _forbidden_kill(*args, **kwargs):
        side_effect_calls.append(("os.kill", args, kwargs))
        raise AssertionError("watchdog_health helper import gate forbids os.kill")

    builtins.__import__ = _guarded_import
    subprocess.run = _forbidden_subprocess
    subprocess.Popen = _forbidden_popen
    os.kill = _forbidden_kill
    try:
        helper = importlib.import_module("claudeteam.supervision.watchdog_health")
    finally:
        builtins.__import__ = orig_import
        subprocess.run = orig_subprocess_run
        subprocess.Popen = orig_subprocess_popen
        os.kill = orig_os_kill

    for name in blocked_imports:
        assert name not in sys.modules, f"watchdog_health helper imported forbidden module: {name}"
    assert not side_effect_calls, f"watchdog_health helper import side-effects: {side_effect_calls!r}"

    for name in (
        "HealthCheckDecision",
        "should_skip_health_file_check",
        "is_health_file_stale",
        "decide_health_file_state",
    ):
        assert hasattr(helper, name), f"watchdog_health missing {name}"
        assert callable(getattr(helper, name)), f"watchdog_health {name} not callable"

    assert helper.should_skip_health_file_check(
        now=1000.0,
        last_restart_ts=950.0,
        restart_grace_secs=60.0,
    ) is True
    assert helper.should_skip_health_file_check(
        now=1000.0,
        last_restart_ts=900.0,
        restart_grace_secs=60.0,
    ) is False
    assert helper.should_skip_health_file_check(
        now=1000.0,
        last_restart_ts=995.0,
        restart_grace_secs=0.0,
    ) is False

    assert helper.is_health_file_stale(age_secs=301.0, health_stale_secs=300.0) is True
    assert helper.is_health_file_stale(age_secs=300.0, health_stale_secs=300.0) is False
    assert helper.is_health_file_stale(age_secs=299.0, health_stale_secs=300.0) is False

    in_grace = helper.decide_health_file_state(
        now=1000.0,
        last_restart_ts=950.0,
        restart_grace_secs=60.0,
        health_file_age_secs=9999.0,
        health_stale_secs=300.0,
    )
    assert in_grace.skip_health_file_check is True, in_grace
    assert in_grace.health_file_stale is False, in_grace

    missing_health_file = helper.decide_health_file_state(
        now=1000.0,
        last_restart_ts=900.0,
        restart_grace_secs=60.0,
        health_file_age_secs=None,
        health_stale_secs=300.0,
    )
    assert missing_health_file.skip_health_file_check is False, missing_health_file
    assert missing_health_file.health_file_stale is False, missing_health_file

    stale = helper.decide_health_file_state(
        now=1000.0,
        last_restart_ts=900.0,
        restart_grace_secs=60.0,
        health_file_age_secs=301.0,
        health_stale_secs=300.0,
    )
    assert stale.skip_health_file_check is False, stale
    assert stale.health_file_stale is True, stale

    fresh = helper.decide_health_file_state(
        now=1000.0,
        last_restart_ts=900.0,
        restart_grace_secs=60.0,
        health_file_age_secs=300.0,
        health_stale_secs=300.0,
    )
    assert fresh.skip_health_file_check is False, fresh
    assert fresh.health_file_stale is False, fresh


def test_watchdog_messages_helper_import_and_contract_gate_when_present() -> None:
    helper_file = ROOT / "src" / "claudeteam" / "supervision" / "watchdog_messages.py"
    if not helper_file.exists():
        return

    blocked_imports = ("watchdog", "scripts.watchdog")
    side_effect_calls = []
    orig_import = builtins.__import__
    orig_subprocess_run = subprocess.run
    orig_subprocess_popen = subprocess.Popen
    orig_os_kill = os.kill

    sys.modules.pop("claudeteam.supervision.watchdog_messages", None)
    for name in blocked_imports:
        sys.modules.pop(name, None)

    def _guarded_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name in blocked_imports or any(name.startswith(prefix + ".") for prefix in blocked_imports):
            raise AssertionError(f"watchdog_messages helper gate forbids import: {name}")
        return orig_import(name, globals, locals, fromlist, level)

    def _forbidden_subprocess(*args, **kwargs):
        side_effect_calls.append(("subprocess.run", args, kwargs))
        raise AssertionError("watchdog_messages helper import gate forbids subprocess.run (lark/tmux included)")

    def _forbidden_popen(*args, **kwargs):
        side_effect_calls.append(("subprocess.Popen", args, kwargs))
        raise AssertionError("watchdog_messages helper import gate forbids subprocess.Popen (lark/tmux included)")

    def _forbidden_kill(*args, **kwargs):
        side_effect_calls.append(("os.kill", args, kwargs))
        raise AssertionError("watchdog_messages helper import gate forbids os.kill")

    builtins.__import__ = _guarded_import
    subprocess.run = _forbidden_subprocess
    subprocess.Popen = _forbidden_popen
    os.kill = _forbidden_kill
    try:
        helper = importlib.import_module("claudeteam.supervision.watchdog_messages")
    finally:
        builtins.__import__ = orig_import
        subprocess.run = orig_subprocess_run
        subprocess.Popen = orig_subprocess_popen
        os.kill = orig_os_kill

    for name in blocked_imports:
        assert name not in sys.modules, f"watchdog_messages helper imported forbidden module: {name}"
    assert not side_effect_calls, f"watchdog_messages helper import side-effects: {side_effect_calls!r}"

    for name in (
        "build_burst_alert",
        "build_cooldown_alert",
    ):
        assert hasattr(helper, name), f"watchdog_messages missing {name}"
        assert callable(getattr(helper, name)), f"watchdog_messages {name} not callable"

    burst = helper.build_burst_alert("router")
    assert burst == "[watchdog] router 已崩溃并自动重启，请确认运行状态。", burst
    assert "[watchdog]" in burst and "router" in burst and "已崩溃并自动重启" in burst, burst

    cooldown = helper.build_cooldown_alert("kanban_sync.py", 3, 600)
    assert "[watchdog]" in cooldown, cooldown
    assert "kanban_sync.py" in cooldown, cooldown
    assert "连续 3 次重启失败" in cooldown, cooldown
    assert "已进入 600s cooldown" in cooldown, cooldown
    assert "cooldown 结束后自动重新尝试" in cooldown, cooldown
    assert cooldown == (
        "[watchdog] kanban_sync.py 连续 3 次重启失败，已进入 600s cooldown，期间 watchdog 不会重试。"
        "cooldown 结束后自动重新尝试。"
    ), cooldown


def test_watchdog_alert_delivery_helper_import_and_contract_gate_when_present() -> None:
    helper_file = ROOT / "src" / "claudeteam" / "supervision" / "watchdog_alert_delivery.py"
    if not helper_file.exists():
        return

    blocked_imports = ("watchdog", "scripts.watchdog")
    side_effect_calls = []
    orig_import = builtins.__import__
    orig_subprocess_run = subprocess.run
    orig_subprocess_popen = subprocess.Popen
    orig_os_kill = os.kill

    sys.modules.pop("claudeteam.supervision.watchdog_alert_delivery", None)
    for name in blocked_imports:
        sys.modules.pop(name, None)

    def _guarded_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name in blocked_imports or any(name.startswith(prefix + ".") for prefix in blocked_imports):
            raise AssertionError(f"watchdog_alert_delivery helper gate forbids import: {name}")
        return orig_import(name, globals, locals, fromlist, level)

    def _forbidden_subprocess(*args, **kwargs):
        side_effect_calls.append(("subprocess.run", args, kwargs))
        raise AssertionError("watchdog_alert_delivery helper import gate forbids subprocess.run (lark/tmux included)")

    def _forbidden_popen(*args, **kwargs):
        side_effect_calls.append(("subprocess.Popen", args, kwargs))
        raise AssertionError("watchdog_alert_delivery helper import gate forbids subprocess.Popen (lark/tmux included)")

    def _forbidden_kill(*args, **kwargs):
        side_effect_calls.append(("os.kill", args, kwargs))
        raise AssertionError("watchdog_alert_delivery helper import gate forbids os.kill")

    builtins.__import__ = _guarded_import
    subprocess.run = _forbidden_subprocess
    subprocess.Popen = _forbidden_popen
    os.kill = _forbidden_kill
    try:
        helper = importlib.import_module("claudeteam.supervision.watchdog_alert_delivery")
    finally:
        builtins.__import__ = orig_import
        subprocess.run = orig_subprocess_run
        subprocess.Popen = orig_subprocess_popen
        os.kill = orig_os_kill

    for name in blocked_imports:
        assert name not in sys.modules, f"watchdog_alert_delivery helper imported forbidden module: {name}"
    assert not side_effect_calls, f"watchdog_alert_delivery helper import side-effects: {side_effect_calls!r}"

    for name in (
        "summarize_alert_send_failure",
        "build_alert_delivery_log_line",
    ):
        assert hasattr(helper, name), f"watchdog_alert_delivery missing {name}"
        assert callable(getattr(helper, name)), f"watchdog_alert_delivery {name} not callable"

    # contract: stderr -> stdout -> (无输出), and truncate by limit.
    assert helper.summarize_alert_send_failure("stdout", "stderr", limit=300) == "stderr"
    assert helper.summarize_alert_send_failure("stdout-only", None, limit=300) == "stdout-only"
    assert helper.summarize_alert_send_failure(None, "   ", limit=300) == "(无输出)"
    long_text = "x" * 512
    summary = helper.summarize_alert_send_failure(None, long_text, limit=300)
    assert len(summary) == 300, len(summary)
    assert summary == "x" * 300, summary

    # contract: returncode classification semantics remain stable.
    ok_line = helper.build_alert_delivery_log_line(0, "router 重启", "ignored", "ignored")
    assert ok_line == "📨 已通知 manager: router 重启", ok_line

    warn_line = helper.build_alert_delivery_log_line(2, "router 重启", "ignored", "ignored")
    assert warn_line == "⚠️ 已通知 manager(收件箱OK,群通知失败): router 重启", warn_line

    fail_line = helper.build_alert_delivery_log_line(7, "router 重启", "stdout-fail", "stderr-fail")
    assert fail_line == "🚨 通知 manager 失败 (exit=7): router 重启 — stderr-fail", fail_line

    # injection gate: build_* failure branch must call summarize helper with limit=300.
    calls = []
    old_summarize = helper.summarize_alert_send_failure
    try:
        def _fake_summarize(stdout, stderr, limit=300):
            calls.append((stdout, stderr, limit))
            return "__SENTINEL__"

        helper.summarize_alert_send_failure = _fake_summarize
        injected = helper.build_alert_delivery_log_line(3, "kanban", "OUT", "ERR")
    finally:
        helper.summarize_alert_send_failure = old_summarize

    assert calls == [("OUT", "ERR", 300)], calls
    assert injected == "🚨 通知 manager 失败 (exit=3): kanban — __SENTINEL__", injected


def test_watchdog_alert_request_helper_import_and_contract_gate_when_present() -> None:
    helper_file = ROOT / "src" / "claudeteam" / "supervision" / "watchdog_alert_request.py"
    if not helper_file.exists():
        return

    blocked_imports = ("watchdog", "scripts.watchdog")
    side_effect_calls = []
    orig_import = builtins.__import__
    orig_subprocess_run = subprocess.run
    orig_subprocess_popen = subprocess.Popen
    orig_os_kill = os.kill

    sys.modules.pop("claudeteam.supervision.watchdog_alert_request", None)
    for name in blocked_imports:
        sys.modules.pop(name, None)

    def _guarded_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name in blocked_imports or any(name.startswith(prefix + ".") for prefix in blocked_imports):
            raise AssertionError(f"watchdog_alert_request helper gate forbids import: {name}")
        return orig_import(name, globals, locals, fromlist, level)

    def _forbidden_subprocess(*args, **kwargs):
        side_effect_calls.append(("subprocess.run", args, kwargs))
        raise AssertionError("watchdog_alert_request helper import gate forbids subprocess.run (lark/tmux included)")

    def _forbidden_popen(*args, **kwargs):
        side_effect_calls.append(("subprocess.Popen", args, kwargs))
        raise AssertionError("watchdog_alert_request helper import gate forbids subprocess.Popen (lark/tmux included)")

    def _forbidden_kill(*args, **kwargs):
        side_effect_calls.append(("os.kill", args, kwargs))
        raise AssertionError("watchdog_alert_request helper import gate forbids os.kill")

    builtins.__import__ = _guarded_import
    subprocess.run = _forbidden_subprocess
    subprocess.Popen = _forbidden_popen
    os.kill = _forbidden_kill
    try:
        helper = importlib.import_module("claudeteam.supervision.watchdog_alert_request")
    finally:
        builtins.__import__ = orig_import
        subprocess.run = orig_subprocess_run
        subprocess.Popen = orig_subprocess_popen
        os.kill = orig_os_kill

    for name in blocked_imports:
        assert name not in sys.modules, f"watchdog_alert_request helper imported forbidden module: {name}"
    assert not side_effect_calls, f"watchdog_alert_request helper import side-effects: {side_effect_calls!r}"

    for name in (
        "normalize_alert_message",
        "normalize_alert_log_label",
        "build_manager_alert_send_cmd",
        "build_testing_skip_log_line",
    ):
        assert hasattr(helper, name), f"watchdog_alert_request missing {name}"
        assert callable(getattr(helper, name)), f"watchdog_alert_request {name} not callable"

    raw_message = "  [watchdog] router 异常  "
    raw_label = "router 重启"
    assert helper.normalize_alert_message(raw_message) == raw_message
    assert helper.normalize_alert_log_label(raw_label) == raw_label

    send_cmd = helper.build_manager_alert_send_cmd(raw_message)
    assert send_cmd == [
        "python3",
        "scripts/feishu_msg.py",
        "send",
        "manager",
        "watchdog",
        raw_message,
        "高",
    ], send_cmd

    long_message = "x" * 200
    default_skip = helper.build_testing_skip_log_line("router 重启", long_message)
    assert default_skip == f"🧪 [TESTING] 已跳过真实 manager 告警: router 重启 — {'x' * 120}", default_skip
    custom_skip = helper.build_testing_skip_log_line("router 重启", long_message, preview_limit=5)
    assert custom_skip == "🧪 [TESTING] 已跳过真实 manager 告警: router 重启 — xxxxx", custom_skip


def test_watchdog_effect_plan_helper_import_and_contract_gate_when_present() -> None:
    helper_file = ROOT / "src" / "claudeteam" / "supervision" / "watchdog_effect_plan.py"
    if not helper_file.exists():
        return

    blocked_imports = ("watchdog", "scripts.watchdog")
    side_effect_calls = []
    orig_import = builtins.__import__
    orig_subprocess_run = subprocess.run
    orig_subprocess_popen = subprocess.Popen
    orig_os_kill = os.kill

    sys.modules.pop("claudeteam.supervision.watchdog_effect_plan", None)
    for name in blocked_imports:
        sys.modules.pop(name, None)

    def _guarded_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name in blocked_imports or any(name.startswith(prefix + ".") for prefix in blocked_imports):
            raise AssertionError(f"watchdog_effect_plan helper gate forbids import: {name}")
        return orig_import(name, globals, locals, fromlist, level)

    def _forbidden_subprocess(*args, **kwargs):
        side_effect_calls.append(("subprocess.run", args, kwargs))
        raise AssertionError("watchdog_effect_plan helper import gate forbids subprocess.run (lark/tmux included)")

    def _forbidden_popen(*args, **kwargs):
        side_effect_calls.append(("subprocess.Popen", args, kwargs))
        raise AssertionError("watchdog_effect_plan helper import gate forbids subprocess.Popen (lark/tmux included)")

    def _forbidden_kill(*args, **kwargs):
        side_effect_calls.append(("os.kill", args, kwargs))
        raise AssertionError("watchdog_effect_plan helper import gate forbids os.kill")

    builtins.__import__ = _guarded_import
    subprocess.run = _forbidden_subprocess
    subprocess.Popen = _forbidden_popen
    os.kill = _forbidden_kill
    try:
        helper = importlib.import_module("claudeteam.supervision.watchdog_effect_plan")
    finally:
        builtins.__import__ = orig_import
        subprocess.run = orig_subprocess_run
        subprocess.Popen = orig_subprocess_popen
        os.kill = orig_os_kill

    for name in blocked_imports:
        assert name not in sys.modules, f"watchdog_effect_plan helper imported forbidden module: {name}"
    assert not side_effect_calls, f"watchdog_effect_plan helper import side-effects: {side_effect_calls!r}"

    for name in (
        "EFFECT_CONTINUE",
        "EFFECT_ALERT_ONLY",
        "EFFECT_RESTART_NOTIFY",
        "WatchdogEffectPlan",
        "build_effect_plan",
    ):
        assert hasattr(helper, name), f"watchdog_effect_plan missing {name}"
    assert callable(helper.build_effect_plan), "watchdog_effect_plan build_effect_plan not callable"

    healthy = helper.build_effect_plan(
        proc_name="router",
        action="healthy",
        retry_count=0,
        action_healthy="healthy",
        action_healthy_reset="healthy_reset",
        action_cooldown_wait="cooldown_wait",
        action_enter_cooldown="enter_cooldown",
    )
    assert healthy.mark_unhealthy is False, healthy
    assert healthy.effect == helper.EFFECT_CONTINUE, healthy
    assert healthy.log_lines == (), healthy

    healthy_reset = helper.build_effect_plan(
        proc_name="router",
        action="healthy_reset",
        retry_count=0,
        action_healthy="healthy",
        action_healthy_reset="healthy_reset",
        action_cooldown_wait="cooldown_wait",
        action_enter_cooldown="enter_cooldown",
    )
    assert healthy_reset.mark_unhealthy is False, healthy_reset
    assert healthy_reset.effect == helper.EFFECT_CONTINUE, healthy_reset
    assert any("恢复健康" in line for line in healthy_reset.log_lines), healthy_reset

    cooldown_wait = helper.build_effect_plan(
        proc_name="router",
        action="cooldown_wait",
        retry_count=3,
        cooldown_remaining_secs=7,
        action_healthy="healthy",
        action_healthy_reset="healthy_reset",
        action_cooldown_wait="cooldown_wait",
        action_enter_cooldown="enter_cooldown",
    )
    assert cooldown_wait.mark_unhealthy is True, cooldown_wait
    assert cooldown_wait.effect == helper.EFFECT_CONTINUE, cooldown_wait
    assert any("剩余 7s" in line for line in cooldown_wait.log_lines), cooldown_wait

    alert_only = helper.build_effect_plan(
        proc_name="router",
        action="enter_cooldown",
        retry_count=4,
        cooldown_ended=True,
        max_retries=3,
        cooldown_secs=600,
        action_healthy="healthy",
        action_healthy_reset="healthy_reset",
        action_cooldown_wait="cooldown_wait",
        action_enter_cooldown="enter_cooldown",
    )
    assert alert_only.mark_unhealthy is True, alert_only
    assert alert_only.effect == helper.EFFECT_ALERT_ONLY, alert_only
    assert any("cooldown 结束" in line for line in alert_only.log_lines), alert_only
    assert any("连续 3 次重启失败" in line for line in alert_only.log_lines), alert_only

    restart_notify = helper.build_effect_plan(
        proc_name="router",
        action="restart",
        retry_count=2,
        cooldown_ended=False,
        action_healthy="healthy",
        action_healthy_reset="healthy_reset",
        action_cooldown_wait="cooldown_wait",
        action_enter_cooldown="enter_cooldown",
    )
    assert restart_notify.mark_unhealthy is True, restart_notify
    assert restart_notify.effect == helper.EFFECT_RESTART_NOTIFY, restart_notify
    assert any("第 2 次" in line for line in restart_notify.log_lines), restart_notify


def test_watchdog_check_once_effect_plan_wrapper_gate() -> None:
    watchdog = importlib.import_module("watchdog")
    script_text = (ROOT / "scripts" / "watchdog.py").read_text(encoding="utf-8")

    # Static wrapper contract: check_once still delegates decision->effect mapping to src helper.
    assert "from claudeteam.supervision import watchdog_effect_plan as _watchdog_effect_plan" in script_text
    assert "plan = _watchdog_effect_plan.build_effect_plan(" in script_text
    assert "if plan.effect == _watchdog_effect_plan.EFFECT_CONTINUE:" in script_text
    assert "if plan.effect == _watchdog_effect_plan.EFFECT_ALERT_ONLY:" in script_text
    assert "restart_process(proc)" in script_text and "notify_manager(name)" in script_text

    old_procs = watchdog.PROCS
    old_is_healthy = watchdog.is_healthy
    old_decide = watchdog._watchdog_state.decide_watchdog_state
    old_build_plan = watchdog._watchdog_effect_plan.build_effect_plan
    old_restart = watchdog.restart_process
    old_notify = watchdog.notify_manager
    old_alert = watchdog._send_manager_alert
    old_log = watchdog.log
    old_sleep = watchdog.time.sleep
    old_subprocess_run = watchdog.subprocess.run
    old_subprocess_popen = watchdog.subprocess.Popen
    old_os_kill = watchdog.os.kill

    logs = []
    decisions_seen = []
    plan_calls = []
    restart_calls = []
    notify_calls = []
    alert_calls = []

    class _Decision:
        def __init__(self, action, retry_count, cooldown_start_ts, cooldown_remaining_secs=0, cooldown_ended=False):
            self.action = action
            self.retry_count = retry_count
            self.cooldown_start_ts = cooldown_start_ts
            self.cooldown_remaining_secs = cooldown_remaining_secs
            self.cooldown_ended = cooldown_ended
            self.max_retries = 3
            self.cooldown_secs = 600

    try:
        watchdog.PROCS = [
            {"name": "wd-healthy", "retry_count": 0, "cooldown_start_ts": 0},
            {"name": "wd-alert", "retry_count": 0, "cooldown_start_ts": 0},
            {"name": "wd-restart", "retry_count": 0, "cooldown_start_ts": 0},
        ]

        def _fake_is_healthy(_proc):
            return False

        def _fake_decide(proc, *, healthy, now):
            decisions_seen.append((proc["name"], healthy, now))
            if proc["name"] == "wd-healthy":
                return _Decision("healthy", 0, 0.0)
            if proc["name"] == "wd-alert":
                return _Decision("enter_cooldown", 4, 1002.0)
            return _Decision("restart", 1, 0.0)

        def _fake_build_plan(**kwargs):
            plan_calls.append(dict(kwargs))
            proc_name = kwargs["proc_name"]
            if proc_name == "wd-healthy":
                return watchdog._watchdog_effect_plan.WatchdogEffectPlan(
                    mark_unhealthy=False,
                    effect=watchdog._watchdog_effect_plan.EFFECT_CONTINUE,
                    log_lines=("healthy-log",),
                )
            if proc_name == "wd-alert":
                return watchdog._watchdog_effect_plan.WatchdogEffectPlan(
                    mark_unhealthy=True,
                    effect=watchdog._watchdog_effect_plan.EFFECT_ALERT_ONLY,
                    log_lines=("alert-log",),
                )
            return watchdog._watchdog_effect_plan.WatchdogEffectPlan(
                mark_unhealthy=True,
                effect=watchdog._watchdog_effect_plan.EFFECT_RESTART_NOTIFY,
                log_lines=("restart-log",),
            )

        def _fake_restart(proc):
            restart_calls.append(proc["name"])

        def _fake_notify(name):
            notify_calls.append(name)

        def _fake_alert(msg, log_label):
            alert_calls.append((msg, log_label))

        def _fake_log(msg):
            logs.append(msg)

        def _fake_sleep(_secs):
            return None

        def _forbidden_subprocess(*args, **kwargs):
            raise AssertionError(f"check_once wrapper gate forbids subprocess side effect: {args!r} {kwargs!r}")

        def _forbidden_kill(*args, **kwargs):
            raise AssertionError(f"check_once wrapper gate forbids os.kill side effect: {args!r} {kwargs!r}")

        watchdog.is_healthy = _fake_is_healthy
        watchdog._watchdog_state.decide_watchdog_state = _fake_decide
        watchdog._watchdog_effect_plan.build_effect_plan = _fake_build_plan
        watchdog.restart_process = _fake_restart
        watchdog.notify_manager = _fake_notify
        watchdog._send_manager_alert = _fake_alert
        watchdog.log = _fake_log
        watchdog.time.sleep = _fake_sleep
        watchdog.subprocess.run = _forbidden_subprocess
        watchdog.subprocess.Popen = _forbidden_subprocess
        watchdog.os.kill = _forbidden_kill

        watchdog.check_once()

        assert [name for name, _, _ in decisions_seen] == ["wd-healthy", "wd-alert", "wd-restart"], decisions_seen
        assert [call["proc_name"] for call in plan_calls] == ["wd-healthy", "wd-alert", "wd-restart"], plan_calls
        assert all(call["action_healthy"] == watchdog._watchdog_state.ACTION_HEALTHY for call in plan_calls), plan_calls
        assert all(call["action_healthy_reset"] == watchdog._watchdog_state.ACTION_HEALTHY_RESET for call in plan_calls), plan_calls
        assert all(call["action_cooldown_wait"] == watchdog._watchdog_state.ACTION_COOLDOWN_WAIT for call in plan_calls), plan_calls
        assert all(call["action_enter_cooldown"] == watchdog._watchdog_state.ACTION_ENTER_COOLDOWN for call in plan_calls), plan_calls

        assert any(msg == "healthy-log" for msg in logs), logs
        assert any(msg == "alert-log" for msg in logs), logs
        assert any(msg == "restart-log" for msg in logs), logs

        assert restart_calls == ["wd-restart"], restart_calls
        assert notify_calls == ["wd-restart"], notify_calls
        assert len(alert_calls) == 1 and alert_calls[0][1] == "wd-alert 进入 cooldown", alert_calls
    finally:
        watchdog.PROCS = old_procs
        watchdog.is_healthy = old_is_healthy
        watchdog._watchdog_state.decide_watchdog_state = old_decide
        watchdog._watchdog_effect_plan.build_effect_plan = old_build_plan
        watchdog.restart_process = old_restart
        watchdog.notify_manager = old_notify
        watchdog._send_manager_alert = old_alert
        watchdog.log = old_log
        watchdog.time.sleep = old_sleep
        watchdog.subprocess.run = old_subprocess_run
        watchdog.subprocess.Popen = old_subprocess_popen
        watchdog.os.kill = old_os_kill


def test_watchdog_send_manager_alert_wrapper_gate() -> None:
    helper_file = ROOT / "src" / "claudeteam" / "supervision" / "watchdog_alert_request.py"
    if not helper_file.exists():
        return

    watchdog = importlib.import_module("watchdog")
    script_text = (ROOT / "scripts" / "watchdog.py").read_text(encoding="utf-8")

    # Static wrapper contract: script layer still delegates request-shaping to src helper.
    assert "from claudeteam.supervision import watchdog_alert_request as _watchdog_alert_request" in script_text
    assert "def _send_manager_alert(msg, log_label):" in script_text
    assert "normalized_msg = _watchdog_alert_request.normalize_alert_message(msg)" in script_text
    assert "normalized_log_label = _watchdog_alert_request.normalize_alert_log_label(log_label)" in script_text
    assert "_watchdog_alert_request.build_testing_skip_log_line(" in script_text
    assert "_watchdog_alert_request.build_manager_alert_send_cmd(normalized_msg)" in script_text
    assert "_watchdog_alert_delivery.build_alert_delivery_log_line(" in script_text

    old_testing = watchdog.TESTING
    old_log = watchdog.log
    old_subprocess_run = watchdog.subprocess.run
    old_subprocess_popen = watchdog.subprocess.Popen
    old_os_kill = watchdog.os.kill
    old_norm_msg = watchdog._watchdog_alert_request.normalize_alert_message
    old_norm_label = watchdog._watchdog_alert_request.normalize_alert_log_label
    old_build_cmd = watchdog._watchdog_alert_request.build_manager_alert_send_cmd
    old_build_skip = watchdog._watchdog_alert_request.build_testing_skip_log_line
    old_build_delivery = watchdog._watchdog_alert_delivery.build_alert_delivery_log_line

    logs = []
    helper_calls = []
    subprocess_calls = []
    delivery_calls = []

    def _fake_log(message):
        logs.append(message)

    def _forbidden_popen(*args, **kwargs):
        raise AssertionError(f"_send_manager_alert wrapper gate forbids subprocess.Popen: {args!r} {kwargs!r}")

    def _forbidden_kill(*args, **kwargs):
        raise AssertionError(f"_send_manager_alert wrapper gate forbids os.kill: {args!r} {kwargs!r}")

    try:
        # TESTING=True: only helper log line path, no subprocess.run.
        def _t_norm_msg(message):
            helper_calls.append(("normalize_alert_message", message))
            return f"NMSG<{message}>"

        def _t_norm_label(log_label):
            helper_calls.append(("normalize_alert_log_label", log_label))
            return f"NLBL<{log_label}>"

        def _t_build_skip(log_label, message, preview_limit=120):
            helper_calls.append(("build_testing_skip_log_line", log_label, message, preview_limit))
            return "__TESTING_SKIP_LINE__"

        def _t_forbidden_build_cmd(message):
            raise AssertionError(f"TESTING path should not call build_manager_alert_send_cmd: {message!r}")

        def _t_forbidden_subprocess(*args, **kwargs):
            raise AssertionError(f"TESTING path should not call subprocess.run: {args!r} {kwargs!r}")

        watchdog.TESTING = True
        watchdog.log = _fake_log
        watchdog.subprocess.run = _t_forbidden_subprocess
        watchdog.subprocess.Popen = _forbidden_popen
        watchdog.os.kill = _forbidden_kill
        watchdog._watchdog_alert_request.normalize_alert_message = _t_norm_msg
        watchdog._watchdog_alert_request.normalize_alert_log_label = _t_norm_label
        watchdog._watchdog_alert_request.build_testing_skip_log_line = _t_build_skip
        watchdog._watchdog_alert_request.build_manager_alert_send_cmd = _t_forbidden_build_cmd

        watchdog._send_manager_alert("raw-msg", "raw-label")

        assert helper_calls == [
            ("normalize_alert_message", "raw-msg"),
            ("normalize_alert_log_label", "raw-label"),
            ("build_testing_skip_log_line", "NLBL<raw-label>", "NMSG<raw-msg>", 120),
        ], helper_calls
        assert logs == ["__TESTING_SKIP_LINE__"], logs

        # TESTING=False: command + delivery log are delegated to helpers.
        logs.clear()
        helper_calls.clear()
        subprocess_calls.clear()
        delivery_calls.clear()

        def _f_norm_msg(message):
            helper_calls.append(("normalize_alert_message", message))
            return f"MSG::{message}"

        def _f_norm_label(log_label):
            helper_calls.append(("normalize_alert_log_label", log_label))
            return f"LBL::{log_label}"

        def _f_build_cmd(message):
            helper_calls.append(("build_manager_alert_send_cmd", message))
            return [
                "python3",
                "scripts/feishu_msg.py",
                "send",
                "manager",
                "watchdog",
                message,
                "高",
            ]

        def _f_forbidden_build_skip(*args, **kwargs):
            raise AssertionError(f"non-TESTING path should not call build_testing_skip_log_line: {args!r} {kwargs!r}")

        def _f_subprocess_run(*args, **kwargs):
            subprocess_calls.append((args, dict(kwargs)))
            return SimpleNamespace(returncode=2, stdout="OUT", stderr="ERR")

        def _f_build_delivery(returncode, log_label, stdout, stderr):
            delivery_calls.append((returncode, log_label, stdout, stderr))
            return "__DELIVERY_LINE__"

        watchdog.TESTING = False
        watchdog.log = _fake_log
        watchdog.subprocess.run = _f_subprocess_run
        watchdog.subprocess.Popen = _forbidden_popen
        watchdog.os.kill = _forbidden_kill
        watchdog._watchdog_alert_request.normalize_alert_message = _f_norm_msg
        watchdog._watchdog_alert_request.normalize_alert_log_label = _f_norm_label
        watchdog._watchdog_alert_request.build_manager_alert_send_cmd = _f_build_cmd
        watchdog._watchdog_alert_request.build_testing_skip_log_line = _f_forbidden_build_skip
        watchdog._watchdog_alert_delivery.build_alert_delivery_log_line = _f_build_delivery

        watchdog._send_manager_alert("msg-2", "label-2")

        assert helper_calls == [
            ("normalize_alert_message", "msg-2"),
            ("normalize_alert_log_label", "label-2"),
            ("build_manager_alert_send_cmd", "MSG::msg-2"),
        ], helper_calls
        assert len(subprocess_calls) == 1, subprocess_calls
        run_args, run_kwargs = subprocess_calls[0]
        assert run_args == (
            [
                "python3",
                "scripts/feishu_msg.py",
                "send",
                "manager",
                "watchdog",
                "MSG::msg-2",
                "高",
            ],
        ), run_args
        assert run_kwargs.get("cwd") == watchdog.PROJECT_ROOT, run_kwargs
        assert run_kwargs.get("capture_output") is True, run_kwargs
        assert run_kwargs.get("text") is True, run_kwargs
        assert delivery_calls == [(2, "LBL::label-2", "OUT", "ERR")], delivery_calls
        assert logs == ["__DELIVERY_LINE__"], logs
    finally:
        watchdog.TESTING = old_testing
        watchdog.log = old_log
        watchdog.subprocess.run = old_subprocess_run
        watchdog.subprocess.Popen = old_subprocess_popen
        watchdog.os.kill = old_os_kill
        watchdog._watchdog_alert_request.normalize_alert_message = old_norm_msg
        watchdog._watchdog_alert_request.normalize_alert_log_label = old_norm_label
        watchdog._watchdog_alert_request.build_manager_alert_send_cmd = old_build_cmd
        watchdog._watchdog_alert_request.build_testing_skip_log_line = old_build_skip
        watchdog._watchdog_alert_delivery.build_alert_delivery_log_line = old_build_delivery


def test_watchdog_proc_match_helper_import_and_contract_gate_when_present() -> None:
    helper_file = ROOT / "src" / "claudeteam" / "supervision" / "watchdog_proc_match.py"
    if not helper_file.exists():
        return

    blocked_imports = ("watchdog", "scripts.watchdog")
    side_effect_calls = []
    orig_import = builtins.__import__
    orig_subprocess_run = subprocess.run
    orig_subprocess_popen = subprocess.Popen
    orig_os_kill = os.kill

    sys.modules.pop("claudeteam.supervision.watchdog_proc_match", None)
    for name in blocked_imports:
        sys.modules.pop(name, None)

    def _guarded_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name in blocked_imports or any(name.startswith(prefix + ".") for prefix in blocked_imports):
            raise AssertionError(f"watchdog_proc_match helper gate forbids import: {name}")
        return orig_import(name, globals, locals, fromlist, level)

    def _forbidden_subprocess(*args, **kwargs):
        side_effect_calls.append(("subprocess.run", args, kwargs))
        raise AssertionError("watchdog_proc_match helper import gate forbids subprocess.run (lark/tmux included)")

    def _forbidden_popen(*args, **kwargs):
        side_effect_calls.append(("subprocess.Popen", args, kwargs))
        raise AssertionError("watchdog_proc_match helper import gate forbids subprocess.Popen (lark/tmux included)")

    def _forbidden_kill(*args, **kwargs):
        side_effect_calls.append(("os.kill", args, kwargs))
        raise AssertionError("watchdog_proc_match helper import gate forbids os.kill")

    builtins.__import__ = _guarded_import
    subprocess.run = _forbidden_subprocess
    subprocess.Popen = _forbidden_popen
    os.kill = _forbidden_kill
    try:
        helper = importlib.import_module("claudeteam.supervision.watchdog_proc_match")
    finally:
        builtins.__import__ = orig_import
        subprocess.run = orig_subprocess_run
        subprocess.Popen = orig_subprocess_popen
        os.kill = orig_os_kill

    for name in blocked_imports:
        assert name not in sys.modules, f"watchdog_proc_match helper imported forbidden module: {name}"
    assert not side_effect_calls, f"watchdog_proc_match helper import side-effects: {side_effect_calls!r}"

    assert hasattr(helper, "is_lark_subscribe_cmdline"), "watchdog_proc_match missing is_lark_subscribe_cmdline"
    assert callable(helper.is_lark_subscribe_cmdline), "is_lark_subscribe_cmdline not callable"

    assert helper.is_lark_subscribe_cmdline("npx lark-cli event +subscribe --compact") is True
    assert helper.is_lark_subscribe_cmdline("lark-cli event --compact") is False
    assert helper.is_lark_subscribe_cmdline("lark-cli +subscribe --compact") is False
    assert helper.is_lark_subscribe_cmdline("event +subscribe --compact") is False
    assert helper.is_lark_subscribe_cmdline("") is False
    assert helper.is_lark_subscribe_cmdline(None) is False


def test_watchdog_proc_match_wrapper_gate() -> None:
    watchdog = importlib.import_module("watchdog")
    script_text = (ROOT / "scripts" / "watchdog.py").read_text(encoding="utf-8")

    # Static wrapper contract: script layer still delegates cmdline matching to src helper.
    assert "from claudeteam.supervision import watchdog_proc_match as _watchdog_proc_match" in script_text
    assert "def _is_lark_subscribe(pid):" in script_text
    assert "return _watchdog_proc_match.is_lark_subscribe_cmdline(" in script_text
    assert "except OSError:" in script_text and "return False" in script_text

    old_open = builtins.open
    old_match = watchdog._watchdog_proc_match.is_lark_subscribe_cmdline
    calls = []
    open_calls = []
    try:
        def _fake_match(cmdline):
            calls.append(cmdline)
            return "lark-cli" in (cmdline or "") and "event" in (cmdline or "") and "+subscribe" in (cmdline or "")

        def _fake_open(path, mode="r", *args, **kwargs):
            if path == "/proc/123/cmdline" and mode == "rb":
                open_calls.append((path, mode))
                return io.BytesIO(b"npx lark-cli event +subscribe --as bot")
            return old_open(path, mode, *args, **kwargs)

        watchdog._watchdog_proc_match.is_lark_subscribe_cmdline = _fake_match
        builtins.open = _fake_open

        assert watchdog._is_lark_subscribe(123) is True
        assert open_calls == [("/proc/123/cmdline", "rb")], open_calls
        assert calls and "lark-cli" in calls[-1] and "event" in calls[-1] and "+subscribe" in calls[-1], calls

        calls.clear()

        def _raise_open(path, mode="r", *args, **kwargs):
            if path == "/proc/456/cmdline" and mode == "rb":
                raise OSError("proc not readable")
            return old_open(path, mode, *args, **kwargs)

        builtins.open = _raise_open
        assert watchdog._is_lark_subscribe(456) is False
        assert not calls, f"helper should not be called when /proc read raises OSError: {calls!r}"
    finally:
        builtins.open = old_open
        watchdog._watchdog_proc_match.is_lark_subscribe_cmdline = old_match


def test_watchdog_orphans_helper_import_and_contract_gate_when_present() -> None:
    helper_file = ROOT / "src" / "claudeteam" / "supervision" / "watchdog_orphans.py"
    if not helper_file.exists():
        return

    blocked_imports = ("watchdog", "scripts.watchdog")
    side_effect_calls = []
    orig_import = builtins.__import__
    orig_subprocess_run = subprocess.run
    orig_subprocess_popen = subprocess.Popen
    orig_os_kill = os.kill

    sys.modules.pop("claudeteam.supervision.watchdog_orphans", None)
    for name in blocked_imports:
        sys.modules.pop(name, None)

    def _guarded_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name in blocked_imports or any(name.startswith(prefix + ".") for prefix in blocked_imports):
            raise AssertionError(f"watchdog_orphans helper gate forbids import: {name}")
        return orig_import(name, globals, locals, fromlist, level)

    def _forbidden_subprocess(*args, **kwargs):
        side_effect_calls.append(("subprocess.run", args, kwargs))
        raise AssertionError("watchdog_orphans helper import gate forbids subprocess.run (lark/tmux included)")

    def _forbidden_popen(*args, **kwargs):
        side_effect_calls.append(("subprocess.Popen", args, kwargs))
        raise AssertionError("watchdog_orphans helper import gate forbids subprocess.Popen (lark/tmux included)")

    def _forbidden_kill(*args, **kwargs):
        side_effect_calls.append(("os.kill", args, kwargs))
        raise AssertionError("watchdog_orphans helper import gate forbids os.kill")

    builtins.__import__ = _guarded_import
    subprocess.run = _forbidden_subprocess
    subprocess.Popen = _forbidden_popen
    os.kill = _forbidden_kill
    try:
        helper = importlib.import_module("claudeteam.supervision.watchdog_orphans")
    finally:
        builtins.__import__ = orig_import
        subprocess.run = orig_subprocess_run
        subprocess.Popen = orig_subprocess_popen
        os.kill = orig_os_kill

    for name in blocked_imports:
        assert name not in sys.modules, f"watchdog_orphans helper imported forbidden module: {name}"
    assert not side_effect_calls, f"watchdog_orphans helper import side-effects: {side_effect_calls!r}"

    for name in (
        "parse_ppid_from_status_text",
        "select_router_tree_victims",
        "select_orphan_victims",
    ):
        assert hasattr(helper, name), f"watchdog_orphans missing {name}"
        assert callable(getattr(helper, name)), f"watchdog_orphans {name} not callable"

    assert helper.parse_ppid_from_status_text("Name:\tpython\nPPid:\t1\n") == 1
    assert helper.parse_ppid_from_status_text("Name:\tpython\nPPid:\t22\nState:\tR\n") == 22
    assert helper.parse_ppid_from_status_text("Name:\tpython\nPPid:\n") is None
    assert helper.parse_ppid_from_status_text("Name:\tpython\nPPid:\tbad\n") is None
    assert helper.parse_ppid_from_status_text("Name:\tpython\nState:\tS\n") is None
    assert helper.parse_ppid_from_status_text(None) is None

    router_victims = helper.select_router_tree_victims(
        tree_pids=[10, 11, 12, 13],
        router_pid=10,
        my_pid=11,
        is_lark_subscribe={10: True, 11: True, 12: False, 13: True},
    )
    assert router_victims == [13], router_victims

    orphan_victims = helper.select_orphan_victims(
        candidate_pids=[20, 21, 22, 23],
        my_pid=21,
        is_lark_subscribe={20: True, 21: True, 22: False, 23: True},
        ppid_by_pid={20: 1, 21: 1, 22: 1, 23: 2},
    )
    assert orphan_victims == [20], orphan_victims


def test_watchdog_orphans_wrapper_gate() -> None:
    helper_file = ROOT / "src" / "claudeteam" / "supervision" / "watchdog_orphans.py"
    if not helper_file.exists():
        return

    watchdog = importlib.import_module("watchdog")
    script_text = (ROOT / "scripts" / "watchdog.py").read_text(encoding="utf-8")

    # Static wrapper contract: script layer delegates victim selection to src helper.
    assert "from claudeteam.supervision import watchdog_orphans as _watchdog_orphans" in script_text
    assert "def _kill_orphan_lark_subscribers():" in script_text
    assert "victims = _watchdog_orphans.select_router_tree_victims(" in script_text
    assert "ppid_by_pid[pid] = _watchdog_orphans.parse_ppid_from_status_text(status_text)" in script_text
    assert "victims = _watchdog_orphans.select_orphan_victims(" in script_text
    assert "os.kill(pid, signal.SIGKILL)" in script_text

    old_router_pid_file = watchdog.ROUTER_PID_FILE
    old_legacy_router_pid_file = watchdog.LEGACY_ROUTER_PID_FILE
    old_open = builtins.open
    old_glob = watchdog.glob.glob
    old_is_subscribe = watchdog._is_lark_subscribe
    old_router_select = watchdog._watchdog_orphans.select_router_tree_victims
    old_orphan_select = watchdog._watchdog_orphans.select_orphan_victims
    old_parse_ppid = watchdog._watchdog_orphans.parse_ppid_from_status_text
    old_sleep = watchdog.time.sleep
    old_subprocess_run = watchdog.subprocess.run
    old_subprocess_popen = watchdog.subprocess.Popen
    old_os_kill = watchdog.os.kill

    my_pid = os.getpid()
    try:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            router_pid_file = tmp_path / "router.pid"
            legacy_router_pid_file = tmp_path / ".router.pid"
            children_file = tmp_path / "children.txt"
            children_file.write_text("200 201 bad", encoding="utf-8")

            # Subcase A: router pid exists, tree victims selected via helper.
            router_pid_file.write_text("100", encoding="utf-8")
            kill_calls = []
            router_select_calls = []

            def _fake_glob_router(pattern):
                if pattern == "/proc/100/task/*/children":
                    return [str(children_file)]
                return []

            def _fake_is_subscribe_router(pid):
                return pid in (201, my_pid)

            def _fake_router_select(**kwargs):
                router_select_calls.append(dict(kwargs))
                return [my_pid, 201]

            def _forbidden_orphan_select(**kwargs):
                raise AssertionError(f"router path should not call select_orphan_victims: {kwargs!r}")

            def _forbidden_parse_ppid(_status_text):
                raise AssertionError("router path should not call parse_ppid_from_status_text")

            def _fake_kill_router(pid, sig):
                kill_calls.append((pid, sig))
                if pid == 100 and sig == 0:
                    return None
                if sig == watchdog.signal.SIGKILL:
                    return None
                raise OSError("unexpected signal")

            def _fake_open_router(path, mode="r", *args, **kwargs):
                path_str = str(path)
                if path_str == str(children_file):
                    return io.StringIO("200 201 bad")
                return old_open(path, mode, *args, **kwargs)

            def _forbidden_subprocess(*args, **kwargs):
                raise AssertionError(f"_kill_orphan_lark_subscribers wrapper gate forbids subprocess side effect: {args!r} {kwargs!r}")

            watchdog.ROUTER_PID_FILE = str(router_pid_file)
            watchdog.LEGACY_ROUTER_PID_FILE = str(legacy_router_pid_file)
            watchdog.glob.glob = _fake_glob_router
            watchdog._is_lark_subscribe = _fake_is_subscribe_router
            watchdog._watchdog_orphans.select_router_tree_victims = _fake_router_select
            watchdog._watchdog_orphans.select_orphan_victims = _forbidden_orphan_select
            watchdog._watchdog_orphans.parse_ppid_from_status_text = _forbidden_parse_ppid
            watchdog.os.kill = _fake_kill_router
            watchdog.time.sleep = lambda _secs: None
            watchdog.subprocess.run = _forbidden_subprocess
            watchdog.subprocess.Popen = _forbidden_subprocess
            builtins.open = _fake_open_router

            watchdog._kill_orphan_lark_subscribers()

            assert len(router_select_calls) == 1, router_select_calls
            router_call = router_select_calls[0]
            assert router_call["router_pid"] == 100, router_call
            assert router_call["my_pid"] == my_pid, router_call
            assert set(router_call["tree_pids"]) == {100, 200, 201}, router_call
            assert router_call["is_lark_subscribe"][100] is False, router_call
            assert router_call["is_lark_subscribe"][200] is False, router_call
            assert router_call["is_lark_subscribe"][201] is True, router_call
            assert (100, 0) in kill_calls, kill_calls
            assert (201, watchdog.signal.SIGKILL) in kill_calls, kill_calls
            assert (my_pid, watchdog.signal.SIGKILL) not in kill_calls, kill_calls

            # Subcase B: router pid missing, orphan victims selected via helper.
            router_pid_file.unlink()
            kill_calls.clear()
            parse_calls = []
            orphan_select_calls = []

            def _fake_glob_orphan(pattern):
                if pattern == "/proc/[0-9]*":
                    return ["/proc/300", "/proc/301", "/proc/not-a-pid"]
                return []

            def _fake_is_subscribe_orphan(pid):
                return pid in (300, 301)

            def _forbidden_router_select(**kwargs):
                raise AssertionError(f"orphan path should not call select_router_tree_victims: {kwargs!r}")

            def _fake_parse_ppid(status_text):
                parse_calls.append(status_text)
                return 1 if "PPid:\t1" in status_text else 2

            def _fake_orphan_select(**kwargs):
                orphan_select_calls.append(dict(kwargs))
                return [300]

            def _fake_kill_orphan(pid, sig):
                kill_calls.append((pid, sig))
                if sig == watchdog.signal.SIGKILL:
                    return None
                raise OSError("unexpected signal")

            def _fake_open_orphan(path, mode="r", *args, **kwargs):
                path_str = str(path)
                if path_str == "/proc/300/status":
                    return io.StringIO("Name:\tlark\nPPid:\t1\n")
                if path_str == "/proc/301/status":
                    return io.StringIO("Name:\tlark\nPPid:\t2\n")
                return old_open(path, mode, *args, **kwargs)

            watchdog.glob.glob = _fake_glob_orphan
            watchdog._is_lark_subscribe = _fake_is_subscribe_orphan
            watchdog._watchdog_orphans.select_router_tree_victims = _forbidden_router_select
            watchdog._watchdog_orphans.parse_ppid_from_status_text = _fake_parse_ppid
            watchdog._watchdog_orphans.select_orphan_victims = _fake_orphan_select
            watchdog.os.kill = _fake_kill_orphan
            builtins.open = _fake_open_orphan

            watchdog._kill_orphan_lark_subscribers()

            assert len(parse_calls) == 2, parse_calls
            assert len(orphan_select_calls) == 1, orphan_select_calls
            orphan_call = orphan_select_calls[0]
            assert orphan_call["candidate_pids"] == [300, 301], orphan_call
            assert orphan_call["my_pid"] == my_pid, orphan_call
            assert orphan_call["is_lark_subscribe"] == {300: True, 301: True}, orphan_call
            assert orphan_call["ppid_by_pid"] == {300: 1, 301: 2}, orphan_call
            assert kill_calls == [(300, watchdog.signal.SIGKILL)], kill_calls
    finally:
        watchdog.ROUTER_PID_FILE = old_router_pid_file
        watchdog.LEGACY_ROUTER_PID_FILE = old_legacy_router_pid_file
        builtins.open = old_open
        watchdog.glob.glob = old_glob
        watchdog._is_lark_subscribe = old_is_subscribe
        watchdog._watchdog_orphans.select_router_tree_victims = old_router_select
        watchdog._watchdog_orphans.select_orphan_victims = old_orphan_select
        watchdog._watchdog_orphans.parse_ppid_from_status_text = old_parse_ppid
        watchdog.time.sleep = old_sleep
        watchdog.subprocess.run = old_subprocess_run
        watchdog.subprocess.Popen = old_subprocess_popen
        watchdog.os.kill = old_os_kill


def test_module_wrapper_scripts_execute_cleanly() -> None:
    for script in MODULE_WRAPPERS:
        result = run_python(script)
        assert result.returncode == 0, (
            f"{script} exited {result.returncode}: stderr={result.stderr!r}"
        )
        assert "Traceback" not in result.stderr, result.stderr


def test_resolve_usage_and_unknown_attribute_exit_codes() -> None:
    result = run_python(RESOLVE_SCRIPT)
    assert result.returncode == 2, result
    assert "用法:" in result.stderr

    env, holder = make_team_env("claude-code")
    try:
        result = run_python(RESOLVE_SCRIPT, "manager", "unknown_attr", env=env)
        assert result.returncode == 2, result
        assert "Unknown attribute" in result.stderr
    finally:
        holder.cleanup()


def test_resolve_spawn_and_resume_contracts() -> None:
    env, holder = make_team_env("claude-code")
    try:
        spawn = run_python(RESOLVE_SCRIPT, "manager", "spawn_cmd", "sonnet", env=env)
        assert spawn.returncode == 0, spawn
        assert "claude --dangerously-skip-permissions" in spawn.stdout
        assert "--model sonnet" in spawn.stdout
        assert "--name manager" in spawn.stdout

        resume = run_python(RESOLVE_SCRIPT, "manager", "resume_cmd", "sonnet", "sid-123", env=env)
        assert resume.returncode == 0, resume
        assert "--resume sid-123" in resume.stdout
    finally:
        holder.cleanup()


def test_resolve_markers_process_and_thinking_contracts() -> None:
    env, holder = make_team_env("claude-code")
    try:
        ready = run_python(RESOLVE_SCRIPT, "manager", "ready_markers", env=env)
        assert ready.returncode == 0, ready
        assert "\\|" in ready.stdout, ready.stdout
        assert "bypass permissions on" in ready.stdout

        busy = run_python(RESOLVE_SCRIPT, "manager", "busy_markers", env=env)
        assert busy.returncode == 0, busy
        assert busy.stdout.strip(), "busy_markers empty"

        proc = run_python(RESOLVE_SCRIPT, "manager", "process_name", env=env)
        assert proc.returncode == 0, proc
        assert proc.stdout.strip() == "claude"

        thinking_high = run_python(
            RESOLVE_SCRIPT,
            "manager",
            "thinking_init_hint",
            "high",
            env=env,
        )
        assert thinking_high.returncode == 0, thinking_high
        assert "extended thinking" in thinking_high.stdout

        thinking_default = run_python(
            RESOLVE_SCRIPT,
            "manager",
            "thinking_init_hint",
            "default",
            env=env,
        )
        assert thinking_default.returncode == 1, thinking_default
    finally:
        holder.cleanup()


def main() -> int:
    test_feishu_msg_entrypoint_import_and_usage_contracts()
    test_feishu_msg_main_delegate_compat_contract()
    test_feishu_msg_command_parser_contract_when_present()
    test_feishu_msg_command_dispatch_contract_when_present()
    test_kanban_sync_command_parser_dispatch_contract_when_present()
    test_kanban_sync_main_delegate_compat_contract()
    test_kanban_sync_entrypoint_delegate_contract_basic_branches()
    test_kanban_daemon_helper_import_and_contract_gate_when_present()
    test_kanban_sync_daemon_pid_wrapper_gate()
    test_kanban_service_import_and_injection_gate_when_present()
    test_kanban_sync_wrapper_uses_service_contract()
    test_kanban_cmd_init_service_delegate_contract()
    test_watchdog_entrypoint_import_and_main_contract()
    test_watchdog_daemon_helper_import_and_contract_gate_when_present()
    test_watchdog_daemon_pid_wrapper_gate()
    test_watchdog_state_helper_import_gate_when_present()
    test_watchdog_specs_helper_import_and_contract_gate_when_present()
    test_watchdog_health_helper_import_and_contract_gate_when_present()
    test_watchdog_messages_helper_import_and_contract_gate_when_present()
    test_watchdog_alert_delivery_helper_import_and_contract_gate_when_present()
    test_watchdog_alert_request_helper_import_and_contract_gate_when_present()
    test_watchdog_effect_plan_helper_import_and_contract_gate_when_present()
    test_watchdog_check_once_effect_plan_wrapper_gate()
    test_watchdog_send_manager_alert_wrapper_gate()
    test_watchdog_proc_match_helper_import_and_contract_gate_when_present()
    test_watchdog_proc_match_wrapper_gate()
    test_watchdog_orphans_helper_import_and_contract_gate_when_present()
    test_watchdog_orphans_wrapper_gate()
    test_module_wrapper_scripts_execute_cleanly()
    test_resolve_usage_and_unknown_attribute_exit_codes()
    test_resolve_spawn_and_resume_contracts()
    test_resolve_markers_process_and_thinking_contracts()
    print("OK: compat scripts entrypoints passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
