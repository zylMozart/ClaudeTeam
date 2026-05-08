"""Tests for agents/identity.py — per-agent identity markdown rendering."""
from __future__ import annotations

from helpers import isolated_env
from claudeteam.agents import identity
from claudeteam.store import memory


# ── render() — template selection ─────────────────────────────────


def test_render_manager_uses_manager_template():
    """Round-85: manager identity rewritten in Chinese with reference/main's
    rich management discipline (角色边界 / 秒回闭环 / 巡视核实 / 集合指令铁律)."""
    text = identity.render("manager", role="团队主管",
                           cli="claude-code", model="opus")
    assert "团队主管" in text
    assert "manager" in text
    # Core management rules from main's manager.identity.md
    assert "管理分发铁律" in text
    # R174: manager is the sole interface to boss; all routing is
    # 老板 → manager → claudeteam send → workers. The identity now
    # spells out the dispatch flow + visibility into worker say replies.
    assert "唯一接口" in text
    assert "claudeteam send" in text
    # Argument-order contract carried over from rebuild's earlier version
    assert "claudeteam send <recipient> <sender>" in text


def test_render_worker_uses_worker_template():
    text = identity.render("worker_cc", role="frontend",
                           cli="claude-code", model="sonnet")
    assert "team worker" in text
    assert "Pick up tasks" in text


# ── render() — substitutions ──────────────────────────────────────


def test_render_substitutes_name_role_cli_model():
    text = identity.render("worker_codex", role="backend",
                           cli="codex-cli", model="gpt-5.5")
    assert "worker_codex" in text
    assert "backend" in text
    assert "codex-cli" in text
    assert "gpt-5.5" in text


def test_manager_has_collective_dispatch_hard_constraint():
    """Boss-flagged 2026-05-06: main 分支主管 identity 里"硬约束：集合类
    指令必须 dispatch，不得代替汇总" 这段非常重要——每个 manager 都得
    学会。rebuild 派活流程提到了，但要作为带关键词触发器 + 强约束语
    的独立 hard-constraint 段呈现，不只是 R174 路由说明顺带带过。"""
    text = identity.render("manager", role="主管", cli="claude-code", model="opus")
    # 独立小节标题（强强约束）
    assert "硬约束" in text
    assert "集合类指令" in text or "集合类" in text
    # 触发关键词列表（main 的原 5 条 + rebuild 自己加的 @team / @all）
    for kw in ("全员", "all hands", "@team", "大家都", "每个人都"):
        assert kw in text, f"missing trigger keyword: {kw}"
    # 严厉约束语
    assert "绝不代替员工发汇总" in text
    assert "绝不一条 say 代替 N 次 send" in text


def test_render_argument_order_contract_present_in_manager():
    text = identity.render("manager", role="r", cli="c", model="m")
    assert "claudeteam send <recipient> <sender>" in text
    assert "claudeteam say <agent>" in text
    assert "❌" in text and "✅" in text


def test_render_argument_order_contract_present_in_worker():
    text = identity.render("w", role="r", cli="c", model="m")
    assert "claudeteam send <recipient> <sender>" in text
    assert "claudeteam say <agent>" in text
    assert "❌" in text and "✅" in text


def test_render_warns_against_cd_in_both_templates():
    """REGRESSION: round 5 smoke caught worker_cc prefixing \`cd /repo &&\`
    on its first reply attempt, which broke chat_id resolution. Both
    templates must include an explicit "do not cd" rule."""
    for agent in ("manager", "w"):
        text = identity.render(agent, role="r", cli="c", model="m")
        assert "Working directory rule" in text
        assert "do NOT" in text and "cd" in text
        assert "runtime_config.json" in text


# ── render() — defaults from team.json ────────────────────────────


def test_render_pulls_defaults_from_team_json_when_args_omitted():
    team = {"agents": {"manager": {"cli": "claude-code", "model": "opus",
                                   "role": "captain"}}}
    with isolated_env(team=team):
        text = identity.render("manager")
    assert "captain" in text
    assert "claude-code" in text
    assert "opus" in text


def test_render_falls_back_when_team_json_missing_fields():
    team = {"agents": {"w": {}}}
    with isolated_env(team=team):
        text = identity.render("w")
    # name is the agent name; cli defaults to claude-code; model empty
    assert "**w**" in text
    assert "claude-code" in text


# ── identity_path() / write() ─────────────────────────────────────


def test_identity_path_under_state_dir():
    with isolated_env() as tmp:
        p = identity.identity_path("worker_kimi")
        assert p == tmp / "state" / "agents" / "worker_kimi" / "identity.md"


def test_write_persists_file_and_creates_parents():
    team = {"agents": {"manager": {"cli": "claude-code", "model": "opus",
                                    "role": "团队主管"}}}
    with isolated_env(team=team) as tmp:
        path = identity.write("manager")
        assert path.exists()
        assert path == tmp / "state" / "agents" / "manager" / "identity.md"
        text = path.read_text(encoding="utf-8")
        # Round-85: manager body now in Chinese, anchored on "管理分发铁律"
        assert "团队主管" in text
        assert "管理分发铁律" in text


# ── Step 2: specialty / tone / notes 字段 ───────────────────────


def test_render_includes_specialty_section_when_set():
    team = {"agents": {"worker_cc": {
        "cli": "claude-code", "model": "sonnet", "role": "员工",
        "specialty": ["内容审核", "文案润色"],
    }}}
    with isolated_env(team=team):
        text = identity.render("worker_cc")
    assert "## 专长" in text
    assert "内容审核" in text
    assert "文案润色" in text


def test_render_omits_specialty_section_when_unset():
    team = {"agents": {"worker_cc": {
        "cli": "claude-code", "model": "sonnet", "role": "员工",
    }}}
    with isolated_env(team=team):
        text = identity.render("worker_cc")
    assert "## 专长" not in text


def test_render_includes_tone_section_when_set():
    team = {"agents": {"worker_cc": {
        "cli": "claude-code", "model": "sonnet", "role": "员工",
        "tone": "细致、礼貌、详尽",
    }}}
    with isolated_env(team=team):
        text = identity.render("worker_cc")
    assert "## 风格" in text
    assert "细致、礼貌、详尽" in text


def test_render_includes_notes_section_when_set():
    team = {"agents": {"worker_cc": {
        "cli": "claude-code", "model": "sonnet", "role": "员工",
        "notes": "擅长长文本审阅; 不擅长数据工作",
    }}}
    with isolated_env(team=team):
        text = identity.render("worker_cc")
    assert "## 备注" in text
    assert "擅长长文本审阅" in text


def test_manager_renders_team_specialties_block():
    """Manager should see each non-manager agent's specialty so it can
    dispatch with awareness."""
    team = {"agents": {
        "manager": {"cli": "claude-code", "model": "opus", "role": "主管"},
        "worker_cc": {"cli": "claude-code", "model": "sonnet", "role": "策划",
                      "specialty": ["文案", "排版"]},
        "worker_codex": {"cli": "codex-cli", "model": "gpt-5.5", "role": "数据",
                         "specialty": ["SQL", "数据可视化"]},
    }}
    with isolated_env(team=team):
        text = identity.render("manager")
    assert "## 团队成员专长" in text
    assert "worker_cc" in text and "文案" in text
    assert "worker_codex" in text and "SQL" in text


def test_worker_does_not_get_team_specialties_block():
    team = {"agents": {
        "manager": {"cli": "claude-code", "model": "opus", "role": "主管"},
        "worker_cc": {"cli": "claude-code", "model": "sonnet", "role": "策划",
                      "specialty": ["文案"]},
    }}
    with isolated_env(team=team):
        text = identity.render("worker_cc")
    assert "## 团队成员专长" not in text


def test_manager_omits_team_specialties_block_when_no_worker_has_specialty():
    team = {"agents": {
        "manager": {"cli": "claude-code", "model": "opus", "role": "主管"},
        "worker_cc": {"cli": "claude-code", "model": "sonnet", "role": "策划"},
    }}
    with isolated_env(team=team):
        text = identity.render("manager")
    # 没人有 specialty → block 也不出现
    assert "## 团队成员专长" not in text


# ── Step 4b: identity 模板教 LLM 用 --to ────────────────────


def test_manager_identity_teaches_to_user():
    team = {"agents": {"manager": {"cli": "claude-code", "model": "opus",
                                    "role": "主管"}}}
    with isolated_env(team=team):
        text = identity.render("manager")
    # manager 必须看到 `--to user` 用法和 chat.publish 提示
    assert "--to user" in text
    assert "chat.publish" in text


def test_manager_identity_dispatch_step_uses_to_user():
    team = {"agents": {"manager": {"cli": "claude-code", "model": "opus",
                                    "role": "主管"}}}
    with isolated_env(team=team):
        text = identity.render("manager")
    # 派活流程 step 3 例子要带 --to user
    assert 'claudeteam say manager "<已派给 N 位...>" --to user' in text


def test_worker_identity_teaches_both_to_targets():
    team = {"agents": {"worker_cc": {"cli": "claude-code", "model": "sonnet",
                                      "role": "员工"}}}
    with isolated_env(team=team):
        text = identity.render("worker_cc")
    # worker 要知道两个常见 --to 值
    assert "--to user" in text
    assert "--to manager" in text


def test_identity_requires_to_explicit():
    """两个 body 都要明确告诉 LLM "每条 say 都必须显式带 --to" — 避免 LLM
    偷懒省略。Step 4b 烟测发现 prompt 里"省略等价"豁免句让 LLM 不再带
    --to，于是改成强约束。"""
    team = {"agents": {
        "manager": {"cli": "claude-code", "model": "opus", "role": "主管"},
        "worker_cc": {"cli": "claude-code", "model": "sonnet", "role": "员工"},
    }}
    with isolated_env(team=team):
        mgr = identity.render("manager")
        wkr = identity.render("worker_cc")
    # 强约束句出现在两个 body 中
    assert "必须显式带" in mgr or "必须" in mgr and "--to" in mgr
    assert "必须显式带" in wkr or "必须" in wkr and "--to" in wkr
    # 不再有"省略等价"的豁免句
    assert "省略 `--to` 等价" not in mgr
    assert "省略 `--to` 等价" not in wkr


def test_write_overwrites_existing_file():
    """Round-88 caught: worker body now mentions 'oldest auto-drop' so a
    naive 'old' substring leaks. Pin the role line explicitly so the
    override is what's being tested."""
    team = {"agents": {"w": {"cli": "claude-code", "model": "opus",
                              "role": "FIRST_ROLE"}}}
    with isolated_env(team=team):
        path = identity.write("w")
        first = path.read_text(encoding="utf-8")
        assert "FIRST_ROLE" in first
        # render again with override
        identity.write("w", role="SECOND_ROLE")
        second = path.read_text(encoding="utf-8")
        assert "SECOND_ROLE" in second
        assert "FIRST_ROLE" not in second


# ── init_prompt() — round-84 memory injection ─────────────────────


def test_init_prompt_omits_memory_section_when_empty():
    """Brand-new agent: no memory file, no extra section appended.
    Avoids confusing the agent with a `## 既往记忆` block that's empty."""
    with isolated_env():
        prompt = identity.init_prompt("manager")
        assert "claudeteam inbox manager" in prompt
        assert "既往记忆" not in prompt


def test_init_prompt_uses_absolute_identity_path():
    """The Read instruction must use an absolute path so panes whose
    CWD isn't the project root can still resolve the file. Container
    deploys spawn at /app; codex pane in 2026-05-07 docker smoke
    surfaced 'agents/worker_codex/identity.md was missing' because
    the relative form didn't resolve from /app."""
    with isolated_env():
        prompt = identity.init_prompt("worker_cc")
        # The path in the prompt must be absolute (starts with `/`)
        # AND must end at the canonical state-relative location.
        import re
        m = re.search(r"Read (\S+identity\.md)", prompt)
        assert m, f"prompt must contain `Read <path>identity.md`; got: {prompt[:200]}"
        path = m.group(1)
        assert path.startswith("/"), \
            f"identity path must be absolute, got relative: {path!r}"
        assert path.endswith("/agents/worker_cc/identity.md")


def test_init_prompt_teaches_to_explicit_say():
    """Step 4c: init prompt 也要强调 --to 必带。烟测 (step4-llm-1778077887)
    发现仅靠 identity body 不够 — LLM 处理 inbox 时直接看 init prompt 的
    say 例子。例子不带 --to → LLM 跟着省略。"""
    with isolated_env():
        prompt = identity.init_prompt("worker_cc")
    # 例子带 --to user
    assert "--to user" in prompt
    # 强约束语出现
    assert "MUST" in prompt or "必须" in prompt
    # 提示 manager / user 两个目标
    assert "manager" in prompt and "user" in prompt


def test_init_prompt_manager_targets_user_only_in_hint():
    """manager 的 init prompt 提示只列 --to user（manager 没有"对自己说"
    的场景）。"""
    with isolated_env():
        prompt = identity.init_prompt("manager")
    assert "--to user" in prompt


def test_init_prompt_teaches_inbox_processing_after_R168():
    """R168: the prompt now tells agents to PROCESS unread messages
    (post a chat response when it's a status / 报道, mark each read),
    not just count them. Boss-flagged after the 全员报道 e2e where
    worker_cc read its inbox but didn't follow up with a chat reply.
    Step 4c: --no-card teaching dropped (R169 made it a no-op)."""
    with isolated_env():
        prompt = identity.init_prompt("worker_cc")
        # Per-message processing instruction
        assert "For EACH unread inbox message" in prompt
        # Tells agent to use claudeteam say for status reports
        assert "claudeteam say worker_cc" in prompt
        # Tells agent to mark each message read
        assert "claudeteam read" in prompt


def test_init_prompt_appends_memory_when_present():
    """After memory.append, the next init_prompt should include the
    memory block so a /clear-ed pane re-reads its prior context on wake."""
    with isolated_env():
        memory.append("manager", "task_assigned", "fix login bug", ref="om_1")
        memory.append("manager", "learning", "auth uses bcrypt")
        prompt = identity.init_prompt("manager")
        # Base reporting still present
        assert "claudeteam inbox manager" in prompt
        # Memory block present
        assert "## 既往记忆" in prompt
        assert "[task_assigned] fix login bug (ref=om_1)" in prompt
        assert "[learning] auth uses bcrypt" in prompt
        # Tail nudge tells agent what to do with the recall
        assert "继续之前未完成的工作" in prompt
