# Team templates

Ready-made `claudeteam.toml` configs + per-role **playbooks** for common kinds of
team. Each folder is a complete starting point: a team config plus a role
instruction doc (`<role>.md`) per agent.

The `playbook` field on an agent points at one of these `.md` files; ClaudeTeam
projects it into that agent's identity (its native `CLAUDE.md` / `AGENTS.md` /
`GEMINI.md` / …), **layered on top of the team protocol** — so each agent shows up
knowing how its role actually works, not just a one-line title. You write the role
content; the say/send/memory mechanics are added automatically.

Agents on `claude-code` / `codex-cli` in these templates run on the **ACP runner**
by default — install the protocol adapters first
(`npm i -g @zed-industries/claude-code-acp @zed-industries/codex-acp`; see
[DEPLOYMENT](../docs/DEPLOYMENT.md)). **Standup** progress reports are also on by
default; tune the cadence under `[standup]` in `claudeteam.toml`.

## Use one

1. Copy a folder's contents next to your `claudeteam.toml` — the `playbook` paths
   resolve relative to the config file:
   ```bash
   cp templates/software-dev/* .          # the toml + its *.md role docs, together
   # Docker: templates are baked at /app/templates, config lives in /data —
   #   docker compose exec --workdir /data claudeteam cp -r /app/templates/software-dev/. .
   ```
   The template **is** a complete `claudeteam.toml`, so it replaces (not merges
   with) one that `claudeteam init` generated — init's extra `[router]`/`[watchdog]`/
   `[feishu]` blocks all default safely, so dropping them is fine. (Or copy just the
   `[team.agents.*]` blocks + the `.md` files into your existing config to keep them.)
2. Adjust each agent's `cli` / `model` / `role` / `playbook`, and edit the `.md`
   playbooks to fit your project.
3. `claudeteam feishu connect` (fills `chat_id`) → `claudeteam install-hooks` → `claudeteam up`.

## Available

| Folder | Team |
| --- | --- |
| [`software-dev/`](software-dev/) | Tech Lead + Backend + Frontend + Code Review/QA |
| [`automated-research/`](automated-research/) | Research Lead + Literature Reviewer + Data Analyst + Experiment Runner |
| [`marketing-growth/`](marketing-growth/) | Growth Lead + Content Strategist + Paid Media Strategist + Marketing Analyst |
| [`data-analysis/`](data-analysis/) | Analytics Lead + Data Engineer + Data Analyst + Reporting & Viz |
| [`content-ops/`](content-ops/) | Content Lead + Writer + Editor + SEO Specialist |

Need a different domain? Write your own (next section) — it's just a toml + a few `.md` files.

## Write your own / find more

A `playbook` is just an `.md` — write role docs for **any** domain and point
agents at them. For inspiration, browse community agent libraries like
[**msitarzewski/agency-agents**](https://github.com/msitarzewski/agency-agents)
(engineering / design / marketing / sales / security / finance / … — hundreds of
role definitions), grab the ones that fit, and distill each into a focused playbook
(drop the framework-specific frontmatter + long code samples; keep the role's
judgment, rules, and definition-of-done).

## Attribution

The `software-dev/` playbooks are **adapted from**
[msitarzewski/agency-agents](https://github.com/msitarzewski/agency-agents)
(MIT © 2025 AgentLand Contributors), distilled to ClaudeTeam's playbook style.
Each adapted file notes its source at the bottom.
