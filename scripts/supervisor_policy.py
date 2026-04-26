#!/usr/bin/env python3
"""
supervisor 监工策略 — 纯函数版 (lazy_wake_v2 ADR §A.4 / §A.7)

为什么单独成文件:
  - cron tick (supervisor_tick.sh) 真正落 Haiku 调用、读 Bitable、scan tmux pane
  - 这个模块只做"已经搜集完证据后,该判 SUSPEND 还是 KEEP"的纯函数决策,
    可以脱离 tmux/lark 完整跑单测,coder 写完即给 tester 14 条 RED 转 GREEN

API 契约 (与 test_lazy_wake_supervisor_keep.py 对齐):

    WHITELIST: frozenset
    class HaikuUnavailable(Exception)
    classify(agent_name: str,
             evidence: dict,
             *,
             haiku_call=None,
             overrides: dict | None = None,
             warn_manager=None) -> dict

evidence 字段:
    unread_inbox:        int
    busy_marker:         bool
    pane_tail:           Optional[str]      None = tmux 窗口已消失
    state_table_row:     Optional[dict]     None = Bitable 读失败
    last_pane_mtime_min: Optional[int]      仅给 haiku_call 透传,本模块不解释

返回:
    {"verdict": "KEEP" | "SUSPEND",
     "reason":  str,
     "override_hit": None | "keep_alive" | "force_sleep" | "pause_until"}

不变式 (test 直接断言):
    1. agent_name in WHITELIST → ValueError("白名单 agent 不能被分类")
    2. 任何异常路径默认 KEEP,绝不 SUSPEND
    3. 决策优先级:
       overrides.pause_until > overrides.keep_alive > overrides.force_sleep
       > 硬规则 (unread_inbox / busy_marker / window_gone / state_table_read_failed)
       > haiku_call 返回值
    4. keep_alive 与 force_sleep 同时命中同一 agent → keep_alive 胜,
       warn_manager(msg) 发告警 (msg 必须含 agent 名 + "conflict" 或 "冲突")
"""
from __future__ import annotations

from datetime import datetime, timezone


WHITELIST = frozenset({"manager", "router", "kanban", "watchdog", "supervisor"})


class HaikuUnavailable(Exception):
    """haiku_call 用来表达"判断不了"的语义异常 (超时 / 429 / 网络)."""


def _make(verdict, reason, override_hit=None):
    return {"verdict": verdict, "reason": reason, "override_hit": override_hit}


def _parse_pause_until(value):
    """支持 ISO8601 字符串 ('2099-01-01T00:00:00Z') 或 epoch 秒数 (int/float).
    解析失败返回 None — 调用方按"不在 pause 状态"处理 (保守 KEEP 仍由后续逻辑保证).
    """
    if value is None:
        return None
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(float(value), tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    if isinstance(value, str):
        # Python 3.11 fromisoformat 已支持 'Z' 后缀,但兼容老版本写 explicit 替换
        s = value.strip()
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        try:
            dt = datetime.fromisoformat(s)
        except ValueError:
            return None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    return None


def classify(agent_name, evidence, *, haiku_call=None, overrides=None, warn_manager=None):
    if agent_name in WHITELIST:
        raise ValueError(
            f"{agent_name} 在 WHITELIST 内,supervisor 不允许对其分类 "
            f"(策略与编制分离: 白名单永不被 suspend)"
        )

    overrides = overrides or {}

    # ── 1. pause_until: 全局暂停 supervisor (任何 agent 都直接 KEEP) ──
    pu = _parse_pause_until(overrides.get("pause_until"))
    if pu is not None and pu > datetime.now(timezone.utc):
        return _make("KEEP",
                     f"pause_until={overrides.get('pause_until')} 仍在未来",
                     override_hit="pause_until")

    # ── 2. per-agent override: keep_alive > force_sleep,冲突时告警 ──
    keep_alive = list(overrides.get("keep_alive") or [])
    force_sleep = list(overrides.get("force_sleep") or [])
    in_keep = agent_name in keep_alive
    in_force = agent_name in force_sleep
    if in_keep and in_force:
        # 冲突 — keep_alive 赢,但必须告警,避免 audit 时这种状态被静默吞掉
        if callable(warn_manager):
            try:
                warn_manager(
                    f"[supervisor] override conflict on {agent_name}: "
                    f"keep_alive 与 force_sleep 同时命中,以 keep_alive 为准"
                )
            except Exception:
                pass  # warn 失败也不能让分类崩
        return _make("KEEP", "override conflict resolved to keep_alive",
                     override_hit="keep_alive")
    if in_keep:
        return _make("KEEP", f"{agent_name} 在 overrides.keep_alive",
                     override_hit="keep_alive")
    if in_force:
        return _make("SUSPEND", f"{agent_name} 在 overrides.force_sleep",
                     override_hit="force_sleep")

    # ── 3. 硬规则: 任意一条命中都直接 KEEP,不调 haiku ─────────────
    # 顺序:
    #   (a) unread_inbox > 0       — 业务消息没处理,绝不能睡
    #   (b) busy_marker            — pane 显示 spinner/Thinking
    #   (c) pane_tail is None      — tmux 窗口已没,不能 suspend 一个不存在的目标
    #   (d) state_table_row is None — Bitable 读失败,信号不全保守 KEEP
    if int(evidence.get("unread_inbox") or 0) > 0:
        return _make("KEEP",
                     f"unread_inbox={evidence['unread_inbox']} (硬规则短路)")
    if evidence.get("busy_marker"):
        return _make("KEEP", "pane busy_marker (硬规则短路)")
    if evidence.get("pane_tail") is None:
        return _make("KEEP", "window_gone: tmux pane 不存在,跳过本轮")
    if evidence.get("state_table_row") is None:
        return _make("KEEP", "state_table_read_failed: Bitable 不可读,保守 KEEP")

    # ── 4. NL 决策: 把 evidence 丢给 haiku_call ──────────────────
    # 任何异常都退回 KEEP,异常 reason 必须非空供决策日志回溯。
    if not callable(haiku_call):
        return _make("KEEP", "needs_nl: 未注入 haiku_call,本模块不做 NL 判断")
    try:
        result = haiku_call(evidence)
    except HaikuUnavailable as e:
        return _make("KEEP", f"haiku_unavailable: {e}")
    except Exception as e:
        return _make("KEEP",
                     f"haiku_exception: {type(e).__name__}: {e}")

    # 校验返回格式 — 任何不规范都退回 KEEP
    if not isinstance(result, dict):
        return _make("KEEP", f"haiku_invalid_response: 非 dict ({type(result).__name__})")
    verdict = result.get("verdict")
    reason = result.get("reason") or "haiku 未给 reason"
    if verdict == "SUSPEND":
        return _make("SUSPEND", reason)
    if verdict == "KEEP":
        return _make("KEEP", reason)
    return _make("KEEP", f"haiku_invalid_verdict={verdict!r},保守 KEEP")
