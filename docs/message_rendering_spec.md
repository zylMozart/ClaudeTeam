# Message Rendering Spec

Status: draft for review. This document defines how ClaudeTeam should generate
business messages for Feishu cards, inbox records, tmux prompts, and logs.

## Goals

- Keep business text separate from runtime control commands.
- Render the same message consistently across Feishu card, inbox text, tmux
  prompt, and workspace log surfaces.
- Avoid relying on each agent to hand-write card components or fragile Markdown.
- Make formatting failures testable with local snapshot-style regressions before
  any live Feishu or tmux delivery.

## Non-goals

- No live Feishu send, tmux injection, or dangerous slash smoke is required for
  this phase.
- No replacement of current `build_card`, `cmd_say`, router prompt, or queue
  entry points until this spec and local tests are accepted.

## Message Envelope

All team messages should be represented by a neutral envelope before rendering:

```json
{
  "kind": "task|status|reply|alert|slash_result|log",
  "audience": "agent|manager|group|system",
  "priority": "高|中|低",
  "title": "short title",
  "body_plain": "canonical plain text body",
  "blocks": [],
  "actions": [],
  "source": {
    "from": "manager",
    "to": "toolsmith",
    "channel": "feishu|router|cli|slash"
  },
  "safety": {
    "strip_runtime_commands": true,
    "max_card_chars": 6000,
    "max_prompt_chars": 30000
  }
}
```

`body_plain` is the source of truth. `blocks` are optional structured hints for
renderers. Runtime command lines such as `CODEX_AGENT=... codex ...` are not
valid business content.

## Render Targets

- `feishu_card`: user-facing group card. Uses controlled Markdown and structured
  card components.
- `inbox_text`: durable Bitable message body. Plain text plus conservative
  Markdown only.
- `tmux_prompt`: direct prompt delivered to an agent TUI. Plain text, explicit
  instructions, no card syntax.
- `log_text`: workspace/audit log. Plain text; preserve enough context for
  debugging but avoid secrets.

## Feishu Markdown Boundary

The safe subset below is based on observed card behavior and Feishu's public
Markdown guidance.

### Paragraphs And Line Breaks

- Use blank lines between paragraphs.
- Avoid relying on trailing spaces for hard line breaks.
- Keep cards scannable: short paragraphs, no wall-of-text blocks.

### Lists

- Use flat unordered lists with `- item`.
- Use flat ordered lists with `1. item`.
- Do not use nested or indented lists; Feishu Markdown support for indentation is
  limited and inconsistent.
- If hierarchy is needed, use bold section labels followed by flat lists.

### Code

- Use inline code for commands, paths, IDs, and env vars.
- Use fenced code blocks only for logs, JSON, shell snippets, or multiline
  output.
- Always close fenced blocks.
- Prefer a language hint for readability when known.
- Very long code/log content should be split across multiple cards or stored as
  an explicit file/document link. User-facing cards must not silently truncate
  business text or show internal preview-truncation notices.

### Links

- Use `[label](https://example.com)` for external references.
- Do not expose tokens, signed URLs, OAuth secrets, or raw credentials in labels
  or URLs.
- Internal local paths should stay plain text in Feishu unless a renderer later
  adds a controlled file-link format.

### Tables

- Do not emit Markdown pipe tables in business messages.
- For Feishu cards, render tabular data using explicit card table/column
  components where supported.
- Observe card table limits: at most 10 columns; at most 5 tables per card.
- If a table would exceed limits or the target is not `feishu_card`, degrade to
  a flat list:

```text
Metric
- p50: 120 ms
- p95: 430 ms
- error_rate: 0.2%
```

### Headings

- Do not rely on `#` headings inside cards.
- Use card header for the primary title.
- Use `**Section**` markdown labels for internal sections.

### Feishu-Specific Tags

- Agents must not hand-write `<at>`, `<font>`, `<button>`, `<table>`, `<row>`,
  `<col>`, `<highlight>`, or similar card component tags.
- Only renderers may generate Feishu-specific tags/components.
- Unknown or unsafe tags in `body_plain` should be escaped or preserved as plain
  text according to the target.

### Emoji And Multilingual Text

- Emoji and Chinese/English mixed text are allowed.
- Do not use emoji as the only semantic carrier; include text labels.
- Preserve Unicode in business text.

## Runtime Command Boundary

The following are runtime/control content, not business text:

- CLI spawn lines: `CODEX_AGENT=... codex --dangerously-bypass-approvals-and-sandbox`
- tmux/lifecycle control lines
- shell bootstrap fragments
- credentials, tokens, OAuth codes, API keys

Policy:

- Prompt and memory instructions should tell agents not to copy runtime control
  lines into task messages.
- Renderers should reject or sanitize runtime command fragments before producing
  user-facing output.
- Sanitization is a safety net and should emit an audit signal in a later
  implementation phase; it must not become the primary formatting strategy.

## Agent Authoring Rules

Agents should output:

- Business result or question.
- Short sections with bold labels.
- Flat lists.
- Inline code for commands/paths.
- Fenced blocks for short logs/snippets only.

Agents should not output:

- Feishu card JSON.
- Feishu-specific component tags.
- Runtime CLI launch commands.
- Raw tables in pipe Markdown.
- Secrets or auth material.

## Local Regression Matrix

The local regression script must cover these cases for every renderer:

| Case | Required behavior |
| --- | --- |
| paragraphs | blank-line paragraphs preserved |
| lists | flat lists preserved; nested lists degraded |
| code blocks | fenced blocks remain balanced |
| links | Markdown links preserved; suspicious URLs rejected later |
| table fallback | pipe tables become flat lists |
| long message | over-limit content split into multiple cards/blocks without user-visible truncation text |
| runtime command | `CODEX_AGENT=... codex ...` removed from rendered output |
| multilingual | Chinese/English text preserved |
| emoji | emoji preserved with adjacent text |
| Feishu tags | raw `<at>`, `<button>`, `<table>` escaped or neutralized |

## Proposed Implementation Phases

1. Keep this spec and local regression snapshots under review.
2. Add a small renderer module with pure functions only.
3. Wire `build_card`, `cmd_say`, `send/direct`, router prompts, queue delivery,
   slash card text, and workspace logs to the renderer.
4. Add safe Feishu card snapshot checks.
5. Keep truncation out of renderer output; delivery layers must split card
   markdown before sending.
6. Run live safe smoke only after manager approval.
