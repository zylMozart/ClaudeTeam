# Slash Matrix

This matrix defines the canonical slash-command coverage for smoke runs.
The command may be triggered by a real Feishu user message or by
`feishu_router.handle_event` fake user events when validating the router slash
handler itself. In either case, the response must be posted to the real smoke
group and captured in the run evidence.

Real Feishu user events are required to prove the full lark subscription and
router path. Fake `handle_event` events are acceptable only for slash-handler
coverage and must be labeled that way in the run log.

The standard smoke matrix covers `manager`, `worker_cc`, `worker_kimi`,
`worker_codex`, and `worker_gemini`. It validates Claude Code, Kimi, Codex, and
Gemini behavior. Other code-supported adapters, such as `qwen-code`, are outside
this matrix until a dedicated smoke extension adds agent fixtures, usage
expectations, and tmux boundary criteria.

## Non-Agent Commands

| Command | Expected Group Result | Pass Criteria | Fail Criteria |
| --- | --- | --- | --- |
| `/help` | Help card/text listing supported commands. | Lists at least `/help`, `/team`, `/usage`, `/health`, `/tmux`, `/send`, `/compact`, `/stop`, `/clear`. | Missing major command group or no group response. |
| `/health` | Host/container/agent resource summary. | Shows CPU, memory, disk, and agent resource rows. | Empty card, stale container, or missing resource fields. |
| `/team` | Agent/tmux status summary. | Shows manager plus four workers and a summary count. | Missing worker, wrong session, or misleading status. |
| `/tmux` | Manager pane tail. | Shows recent manager lines in a readable block. | Empty, wrong pane, or truncated beyond usefulness. |
| `/usage` | Claude Code usage card. | Shows 5h/7d usage and reset time. | Missing reset time or silently fails. |
| `/usage cc` | Claude Code usage card. | Same required fields as `/usage`; proves explicit alias works. | Alias fails or differs from `/usage` without explanation. |
| `/usage kimi` | Kimi usage section. | Shows Kimi usage/remaining/reset, or a precise login/pane blocker. | Generic failure, missing reset, or unsafe injection into busy Kimi pane. |
| `/usage codex` | Codex usage section. | Shows Codex plan/usage/remaining/reset, or a precise permission/login blocker. | HTTP/auth failure misclassified or section omitted. |
| `/usage gemini` | Gemini usage section. | Shows Gemini auth/quota/model usage/refresh, or a precise login blocker. | Expired OAuth or quota errors hidden as success. |
| `/usage all` | All CLI quota sections. | Shows Claude Code, Kimi, Codex, and Gemini sections. Each section includes usage/remaining/reset or a precise blocker. | Any CLI omitted without explanation; no reset/refresh time; wrong credential diagnosis. |

## State-Mutating Commands

These commands are part of the current slash dispatcher and should be smoke
covered deliberately. They are not read-only.

| Command | Expected Result | Risk Notes | Pass Criteria |
| --- | --- | --- | --- |
| `/compact [agent]` | Sends `/compact` to manager by default or the named agent. | Changes CLI context and may take time. Use a smoke-safe pane and record before/after state. | Target pane receives one compact request and returns to a stable prompt or documented busy state. |
| `/stop <agent>` | Sends `C-c` to the named agent. | Interrupts active work. Use only on a controlled probe task. | Active probe is interrupted and pane remains usable. |
| `/clear <agent>` | Sends `/clear`, then a rehire/init message. | Resets conversation context and reinjects onboarding. | Target receives `/clear` and one init message; no shell pollution or duplicate init. |

## Worker Command Chain

Current `/send` is implemented as raw `tmux send-keys` to the target pane. It is
therefore a tmux boundary risk probe. It is not equivalent to router lazy-wake,
`feishu_msg.py inbox`, or `tmux_utils.inject_when_idle()` delivery.

| Command | Target | Expected Pane Result | Pass Criteria | Fail Criteria |
| --- | --- | --- | --- | --- |
| `/send worker_cc <probe>` | Claude Code | Claude pane receives and answers the probe. | Probe appears once and worker responds from CLI. | Message lands in shell, duplicate injection, prompt remains dirty. |
| `/send worker_kimi <probe>` | Kimi CLI | Kimi pane receives and answers the probe. | Probe appears once and worker responds from CLI. | Kimi stays at stale prompt, message is not submitted, or response is absent. |
| `/send worker_codex <probe>` | Codex CLI | Codex pane receives and answers the probe. | Probe appears once and worker responds from CLI. | `CODEX_AGENT=...` residue, stale queued input, or wrong prompt target. |
| `/send worker_gemini <probe>` | Gemini CLI | Gemini pane receives and answers the probe. | Probe appears once and worker responds from CLI. | Message remains in Gemini input box, duplicates, or shell receives text. |

## Usage-All Required Fields

`/usage all` must capture:

- Claude Code: 5-hour window, 7-day all-models, 7-day Sonnet, reset times.
- Kimi: weekly and/or 5-hour quota where available, remaining percentage, reset
  time.
- Codex: usage percentage and reset time. If `codex-cli-usage` returns HTTP 403,
  record it as an account entitlement/API permission blocker, not as a generic
  tool failure.
- Gemini: login status plus model-level quota, remaining percentage, and refresh
  time.

## Evidence Format

For each command, store:

- Trigger method: real user event or fake `handle_event` user event.
- Feishu message timestamp and card summary.
- Any lark-cli error code.
- For `/send`, target pane tail showing the received probe and response.
- Formatting notes when card/newline rendering makes evidence hard to read.
  Treat these as prompt/memory convention issues unless a separate code-change
  task exists.
