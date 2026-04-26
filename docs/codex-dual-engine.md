# Claude + Codex Dual-Engine Collaboration Guide

> Integrate OpenAI Codex into ClaudeTeam for independent cross-review and assisted coding.

---

## 1. Overview

The **dual-engine** model pairs Claude (primary agent) with Codex (on-demand plugin). The two engines never share context, ensuring genuinely independent reviews.

| Use Case | Who | How |
|----------|-----|-----|
| Cross-review | analyst / verifier | `/codex:review` for independent second opinion |
| Security audit | verifier | `/codex:adversarial-review` for vulnerability probing |
| Task delegation | coder / coder-vision | `codex exec --yolo "..."` for subtasks |
| Stuck rescue | any agent | `/codex:rescue` when blocked |

---

## 2. Installation

**Requirements:** ChatGPT Plus/Pro/Team subscription, Node.js >= 18.18, Claude Code CLI.

```bash
# In Claude Code interactive session:
/plugin marketplace add openai/codex-plugin-cc
/plugin install codex@openai-codex
/reload-plugins
```

**Authentication:**

```bash
codex login          # Browser OAuth (recommended) or API Key
codex whoami         # Verify login
```

- Auth is stored in `~/.codex/auth.json` — **never commit this file**
- Set `chmod 600 ~/.codex/auth.json`
- Optional config: `~/.codex/config.toml` for model and reasoning effort settings
- Headless/container environments: use API Key mode or copy `auth.json` from a machine with a browser

---

## 3. Commands

| Command | Mode | Description |
|---------|------|-------------|
| `/codex:review` | read-only | Code review (quality, correctness, style) |
| `/codex:adversarial-review` | read-only | Adversarial audit (bugs, security, edge cases) |
| `/codex:rescue` | write | Hand off a stuck problem to Codex |
| `codex exec --yolo "..."` | write | Delegate a self-contained subtask |
| `/codex:status` | — | Check background task progress |
| `/codex:result` | — | Retrieve completed task output |
| `/codex:setup` | — | Diagnose connection and config |

Common flags: `--base main` (diff base), `--background` (non-blocking), `--model spark --effort high`.

---

## 4. Reviewer Workflow: Cross-Review

**Principle:** Claude reviews first, then Codex reviews independently. Neither sees the other's conclusions.

```
Step 1: Claude Review — read code, note issues
Step 2: Codex Cross-Review — /codex:review <files>, /codex:adversarial-review <critical-files>
Step 3: Compare
  - Both found it       → must fix (high priority)
  - Only one found it   → human judgment needed
  - Conclusions conflict → escalate for tiebreak
Step 4: Merge into final report with verdict (approve / request changes / reject)
```

**Guidelines:**
- Always double-review data processing, training scripts, and API calls
- Review 1-3 files at a time for depth
- Save reports to `agents/<name>/workspace/` for traceability

---

## 5. Coder Workflow: Assisted Coding

### Mode A: Claude Writes, Codex Reviews

Claude finishes coding, then requests review:
```bash
codex exec --yolo "Review scripts/pipeline.py: check imports, error handling, and memory leaks"
```

### Mode B: Codex Writes, Claude Runs

Delegate well-defined tasks (boilerplate, format conversion, data cleaning):
```bash
codex exec --yolo "Write a data conversion script that groups by conversation,
  one clip per utterance, outputs data.jsonl and annotations.json"
# Claude reviews generated code, then executes
```

### Mode C: Parallel Work

Claude works on task A while Codex handles task B in background:
```bash
codex exec --yolo "Write an evaluation script — compute accuracy, F1, confusion matrix"
/codex:status    # check progress
/codex:result    # retrieve output
```

### Rescue Mode

When stuck after multiple failed attempts:
```bash
/codex:rescue
# Describe: problem, what you tried, error messages, environment constraints
```

**Task description tips:** Be specific — include file paths, error messages, environment constraints, and expected output format. Vague descriptions produce poor results.

---

## 6. Best Practices

**Reviewers:**
- Claude first, Codex second — preserve independence
- Critical paths get standard + adversarial review
- Conflicts need third-party tiebreak

**Coders:**
- Codex generates, Claude executes — always review before running
- Use Codex for repetitive work (conversion, batch checks, docs)
- Don't delegate destructive operations (file deletion, DB drops)
- If rescue also fails, change approach or escalate — don't loop

**Security:**
- **Never send secrets to Codex** — no `.env`, API keys, credentials, or internal URIs
- Pre-scan files before review: `grep -rE "password|secret|token|key=" <file>`
- Codex has no conversation memory — include all necessary context in each invocation
