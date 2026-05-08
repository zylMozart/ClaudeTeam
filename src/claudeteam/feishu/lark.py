"""Thin wrapper around the lark-cli binary.

Single function: `call(args, *, profile, timeout) -> dict | None`.

Returns the `data` field of lark-cli's JSON response on success, `{}` if
stdout is empty, `None` on any failure.  Proxy bypass is automatic when
`LARK_CLI_NO_PROXY=1` is set in the environment.

Round-86 perf note: an earlier draft of this docstring claimed
"lark-cli routinely takes ~73 seconds per call". That was npx's
package-lookup overhead, not the API. `resolve_cli_prefix` now picks
the direct binary when one is on disk (`lark-cli` on PATH or the npx
cache binary at `~/.npm/_npx/<hash>/node_modules/.bin/lark-cli`), so
real round-trip is ~0.6s on macOS host. Default timeout = 90s gives
plenty of margin; bump via `CLAUDETEAM_LARK_TIMEOUT` only if your
network actually IS slow.

Tests inject a fake subprocess.run via the `run=` kwarg.
"""
from __future__ import annotations

import json
import os
import pwd
import shutil
import subprocess
import time
from typing import Callable

from claudeteam.util import env_str


_PROXY_KEYS = ("HTTPS_PROXY", "HTTP_PROXY", "https_proxy", "http_proxy")

# Container-deploy token bootstrap. lark-cli on macOS host reads app
# secrets from the system keychain; that path doesn't work in a
# Linux container and lark-cli answers "no access token available
# for bot" even when FEISHU_APP_SECRET / FEISHU_APP_ID are set in
# env. Auto-fetching `LARKSUITE_CLI_TENANT_ACCESS_TOKEN` from
# app_id+app_secret here means both one-shot `lark.call()` and the
# long-running `event +subscribe` daemon pick up a fresh token
# without an entrypoint script.
_TENANT_TOKEN_URL = (
    "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal")
_TENANT_TOKEN_CACHE = "/tmp/claudeteam_tenant_token.json"
_TENANT_TOKEN_REFRESH_BUFFER_S = 60   # refetch when within 60s of expiry


def _fetch_tenant_token(app_id: str, app_secret: str) -> dict | None:
    """POST app_id+app_secret → Feishu tenant_access_token endpoint.

    Returns `{"token": str, "expire_at": <epoch_seconds>}` on success
    (with the buffer subtracted so the cache flips before the wire
    expiry hits) or None on any network / parse / API failure.
    """
    import json as _json
    import time as _time
    import urllib.error
    import urllib.request
    body = _json.dumps({"app_id": app_id, "app_secret": app_secret}).encode()
    req = urllib.request.Request(
        _TENANT_TOKEN_URL, data=body,
        headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = _json.loads(resp.read().decode("utf-8", errors="ignore"))
    except (urllib.error.URLError, OSError, _json.JSONDecodeError):
        return None
    token = data.get("tenant_access_token")
    expire = int(data.get("expire", 0) or 0)
    if not token:
        return None
    return {
        "token": str(token),
        "expire_at": int(_time.time()) + max(0, expire - _TENANT_TOKEN_REFRESH_BUFFER_S),
    }


def _ensure_tenant_token(*, fetch: Callable | None = None,
                         now: Callable | None = None,
                         cache_path: str | None = None) -> str | None:
    """Return a usable tenant_access_token from env / cache / live fetch.

    Resolution order:
      1. `LARKSUITE_CLI_TENANT_ACCESS_TOKEN` already in env — use as-is.
      2. Cache file at `cache_path` with `expire_at > now` — use it.
      3. `FEISHU_APP_ID` + `FEISHU_APP_SECRET` (or `LARKSUITE_CLI_*`
         aliases) in env — fetch a fresh token, write to cache, return.
      4. None of the above — return None and let lark-cli's own auth
         path try (works on macOS host with keychain).

    `fetch` and `now` are injectable for tests so we don't hit the
    network during unit tests.
    """
    import json as _json
    import time as _time
    # Resolve cache_path at call time so test patches of the
    # module-level _TENANT_TOKEN_CACHE constant take effect; default
    # args bind at function-definition time and would freeze the
    # original /tmp path before any patch could land.
    if cache_path is None:
        cache_path = _TENANT_TOKEN_CACHE
    existing = env_str("LARKSUITE_CLI_TENANT_ACCESS_TOKEN")
    if existing:
        return existing
    now_fn = now or _time.time
    now_t = int(now_fn())
    try:
        with open(cache_path, "r", encoding="utf-8") as fh:
            cached = _json.loads(fh.read())
        if int(cached.get("expire_at", 0)) > now_t and cached.get("token"):
            return str(cached["token"])
    except (OSError, _json.JSONDecodeError, ValueError):
        pass
    app_id = env_str("FEISHU_APP_ID") or env_str("LARKSUITE_CLI_APP_ID")
    app_secret = (env_str("FEISHU_APP_SECRET")
                  or env_str("LARKSUITE_CLI_APP_SECRET"))
    if not (app_id and app_secret):
        return None
    fresh = (fetch or _fetch_tenant_token)(app_id, app_secret)
    if not fresh or not fresh.get("token"):
        return None
    try:
        with open(cache_path, "w", encoding="utf-8") as fh:
            fh.write(_json.dumps(fresh))
    except OSError:
        pass  # cache write best-effort; the in-memory return is the load-bearing path
    return str(fresh["token"])


def subprocess_env() -> dict[str, str]:
    """Build the env to hand to any lark-cli subprocess (one-shot `call` or
    long-running `event +subscribe`). Strips HTTP/HTTPS proxy vars when
    LARK_CLI_NO_PROXY is truthy, since lark-cli doesn't honor that variable
    itself — it's a wrapper-side flag.

    Also injects `LARKSUITE_CLI_TENANT_ACCESS_TOKEN` when env vars
    supply app_id+app_secret but lark-cli has no keychain access
    (the Linux container case). No-op on macOS host where the token
    is empty and lark-cli's keychain path takes over.

    Pins HOME to the host user's pw_dir so lark-cli finds
    `~/.lark-cli/config.json` regardless of caller HOME. Claude panes
    spawn with HOME=<state_dir>/agent-home/<agent> for ~/.claude.json
    isolation; without this pin, `claudeteam say` from inside an agent
    pane inherited the per-agent HOME and lark-cli failed to locate
    its profile/keychain entry (rc=2). Use pw_dir, not the env's HOME,
    so the override is robust against env tampering by the caller.
    """
    env = os.environ.copy()
    # `feishu.no_proxy` cascade: legacy env LARK_CLI_NO_PROXY first
    # (predates tunables), then tunable lookup. Truthy => strip proxies.
    legacy = env_str("LARK_CLI_NO_PROXY").lower()
    if legacy in {"1", "true", "yes", "on"}:
        no_proxy = True
    elif legacy in {"0", "false", "no", "off"}:
        no_proxy = False
    else:
        from claudeteam.runtime import tunables
        no_proxy = bool(tunables.tunable("feishu.no_proxy", False))
    if no_proxy:
        for key in _PROXY_KEYS:
            env.pop(key, None)
    env["HOME"] = pwd.getpwuid(os.getuid()).pw_dir
    token = _ensure_tenant_token()
    if token:
        # lark-cli refuses to start if TENANT_ACCESS_TOKEN is set without a
        # matching LARKSUITE_CLI_APP_ID/SECRET pair — token alone gets
        # `Error: blocked by env: LARKSUITE_CLI_TENANT_ACCESS_TOKEN is set
        # but LARKSUITE_CLI_APP_ID is missing`; token+app_id-only fails
        # the WebSocket subscribe with `app_id or app_secret is null`
        # (lark-cli's persistent-connection SDK re-auths off env-vars,
        # not just the cached token). Both caught 2026-05-07 host smoke.
        # Propagate all three together; if app_id/secret aren't available
        # in env, skip injection and let lark-cli's profile/keychain
        # path take over.
        app_id = (env_str("LARKSUITE_CLI_APP_ID")
                  or env_str("FEISHU_APP_ID"))
        app_secret = (env_str("LARKSUITE_CLI_APP_SECRET")
                      or env_str("FEISHU_APP_SECRET"))
        if app_id and app_secret:
            env["LARKSUITE_CLI_TENANT_ACCESS_TOKEN"] = token
            env["LARKSUITE_CLI_APP_ID"] = app_id
            env["LARKSUITE_CLI_APP_SECRET"] = app_secret
    return env


def resolve_cli_prefix() -> list[str]:
    """Return the argv prefix for invoking lark-cli, preferring direct
    binaries over `npx` to skip npm's package-lookup overhead.

    Resolution order (first hit wins):
      1. `CLAUDETEAM_LARK_CLI_BIN` env — operator explicit override.
      2. `lark-cli` on PATH — npm global install (`npm i -g @larksuite/cli`).
      3. The npx cache binary at
         `~/.npm/_npx/<hash>/node_modules/.bin/lark-cli` (auto-installed
         once when npx ran).
      4. `npx @larksuite/cli` — fallback when nothing direct is on disk.

    Resolved fresh on each call so a newly-installed lark-cli takes
    effect without restarting daemons. Direct binary saves ~250–500
    ms per send vs going through `npx`.
    """
    # `feishu.cli_bin` cascade: legacy env first, then tunable lookup.
    override = env_str("CLAUDETEAM_LARK_CLI_BIN")
    if not override:
        from claudeteam.runtime import tunables
        override = str(tunables.tunable("feishu.cli_bin", "") or "")
    if override and os.path.exists(override):
        return [override]
    direct = shutil.which("lark-cli")
    if direct:
        return [direct]
    home = os.path.expanduser("~/.npm/_npx")
    if os.path.isdir(home):
        for entry in os.listdir(home):
            candidate = os.path.join(home, entry,
                                      "node_modules/.bin/lark-cli")
            if os.path.exists(candidate):
                return [candidate]
    return ["npx", "@larksuite/cli"]


def _build_argv(args: list[str], profile: str) -> list[str]:
    base = resolve_cli_prefix()
    if profile:
        base += ["--profile", profile]
    return base + list(args)


def _resolve_timeout(explicit: int | None) -> int:
    """Resolve subprocess timeout in seconds. Caller arg wins; otherwise
    routes through tunables (env > claudeteam.toml > default 90).

    Round-64: clamp the final value to >=1 — a garbage env like
    CLAUDETEAM_LARK_TIMEOUT=0 used to make subprocess.run
    insta-TimeoutExpired on every call. Legacy `CLAUDETEAM_LARK_TIMEOUT`
    env var still honored as backwards-compat alias.
    """
    if explicit is not None:
        return max(1, int(explicit))
    legacy = env_str("CLAUDETEAM_LARK_TIMEOUT").strip()
    if legacy:
        try:
            return max(1, int(legacy))
        except ValueError:
            pass
    from claudeteam.runtime import tunables
    return max(1, int(tunables.tunable("router.lark_call_timeout_s", 90)))


def call(args: list[str], *, profile: str = "", timeout: int | None = None,
         run: Callable = subprocess.run) -> dict | None:
    """Execute lark-cli; return parsed `data` JSON, `{}` on empty stdout, None on failure.

    `profile` selects the lark-cli profile (`--profile X`).  Pass empty
    string to use the default profile.

    The function intentionally swallows network / lark-cli errors and
    prints a one-line warning instead of raising — callers that need
    to distinguish failure modes should check the return value.
    """
    cmd = _build_argv(args, profile)
    timeout_s = _resolve_timeout(timeout)
    t0 = time.monotonic()
    try:
        r = run(cmd, capture_output=True, text=True, timeout=timeout_s, env=subprocess_env())
    except subprocess.TimeoutExpired:
        elapsed = (time.monotonic() - t0)
        print(f"  ⚠️ lark-cli timeout ({timeout_s}s after {elapsed:.1f}s): {' '.join(args[:3])}")
        return None
    except FileNotFoundError:
        # npx itself isn't on PATH. claudeteam say / router / chat all hit
        # this — better one-line warn than a top-level traceback.
        print(f"  ⚠️ npx not found on PATH; install Node.js to enable lark-cli")
        return None
    except OSError as e:
        # Other Popen-time OS failures (permission, fork failed, etc.).
        # Caller will see None and propagate as "send failed".
        print(f"  ⚠️ lark-cli could not be launched: {e}")
        return None
    if r.returncode != 0:
        # Smoke v3 caught: lark-cli sometimes prints structured JSON
        # ({"ok":false,"msg":"invalid receive_id","code":230001}) to
        # stdout AND exits non-zero. Old `stderr.splitlines()[-1]` returned
        # just the trailing `}` and lost the real cause. Try JSON first
        # (stdout, then stderr); fall back to the first non-empty line.
        for blob in (r.stdout, r.stderr):
            blob = (blob or "").strip()
            if not blob:
                continue
            try:
                parsed = json.loads(blob)
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, dict):
                reason = _extract_error_message(parsed)
                print(f"  ⚠️ lark-cli failed (rc={r.returncode}): {reason}"[:200])
                return None
        head = next(
            (line for line in ((r.stderr or "") + "\n" + (r.stdout or "")).splitlines() if line.strip()),
            "",
        )
        print(f"  ⚠️ lark-cli failed (rc={r.returncode}): {head}"[:200])
        return None
    if not r.stdout.strip():
        return {}
    try:
        full = json.loads(r.stdout)
    except json.JSONDecodeError as e:
        # Don't silently swallow — JSON corruption from lark-cli is rare
        # but when it happens, the operator wants to know (typically means
        # lark-cli printed banner text into stdout, or got proxied to an
        # auth wall). One-line preview helps debugging without flooding
        # the daemon log.
        preview = r.stdout.strip().splitlines()[0][:120] if r.stdout.strip() else "(empty)"
        print(f"  ⚠️ lark-cli returned non-JSON ({e}): {preview}")
        return None
    # lark-cli wraps results in {"ok": ..., "data": ...} or returns data directly.
    # `ok: false` means the API returned an error even though lark-cli exited 0.
    if isinstance(full, dict) and full.get("ok") is False:
        reason = _extract_error_message(full)
        print(f"  ⚠️ lark-cli API error: {reason}"[:200])
        return None
    return full.get("data", full)


def _extract_error_message(full: dict) -> str:
    """Pull the most informative human-readable string out of lark-cli's
    error-shape variants. Real responses seen in the wild:

      {"ok": false, "msg": "plain message"}
      {"ok": false, "error": "plain string"}
      {"ok": false, "error": {"type": "validation", "message": "..."}}
      {"ok": false, "error": {"type": "api_error", "code": 230002,
                              "message": "HTTP 400: Bot/User can NOT be out of the chat."}}

    Round-58 smoke caught this: when error is a structured dict, the
    old `or "?"` chain returned the dict and the warning line printed
    `{'type': ..., 'message': '...'}` — useless to operators. Now we
    extract `error.message` when error is a dict, falling back through
    msg / code / "?" if nothing useful is present.
    """
    if msg := full.get("msg"):
        return str(msg)
    err = full.get("error")
    if isinstance(err, dict):
        # Prefer message; tag with type/code if present so the line
        # gives operators both the human string AND the API code.
        message = err.get("message") or err.get("code") or "?"
        kind = err.get("type")
        return f"{message} (type={kind})" if kind else str(message)
    if isinstance(err, str) and err:
        return err
    if code := full.get("code"):
        return str(code)
    return "?"
