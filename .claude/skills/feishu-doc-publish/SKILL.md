---
name: feishu-doc-publish
description: "Publish or update a formal Feishu document from Markdown with review and reporting steps."
---

# Feishu Doc Publish

Use this skill when a local Markdown report must become a formal Feishu document
or an existing Feishu document needs a reviewed update.

## Inputs

- `$ARGUMENTS`: `<markdown-path> [folder-token-or-doc-url]`
- Required context: target audience, publish/update intent, and manager task ID.

## Boundary

- Read-only: inspect Markdown, check for secrets, review links and local paths.
- Write-producing: live Feishu doc create/update via lark-cli.
- This skill does not own Feishu auth, doc APIs, or content rendering internals.

## Procedure

1. Read the Markdown source and confirm it is a final report, not scratch notes.
2. Check that the document contains no credentials, raw tokens, or private profile IDs.
3. Confirm whether this is create-new or update-existing.
4. For create-new, use the approved lark-cli docs workflow when live publishing is allowed.
5. For update-existing, preserve the existing document URL and summarize changed sections.
6. Record the published link in the manager report.

## Command Pattern

Live publish commands depend on the deployment profile and must be run only
after confirming target folder or document URL. Use lark-cli directly; do not
hard-code tenant-specific IDs in this skill.

Example pattern:

```bash
npx @larksuite/cli docs +create --as bot --folder-token <folder-token> --markdown "@<markdown-path>"
```

## Verification

- Re-open the generated Feishu link.
- Confirm headings, code blocks, tables, and links render acceptably.
- Confirm no local-only path is presented as an external link unless intended.

## Report

```bash
python3 scripts/feishu_msg.py send manager <agent> "<doc link + summary + verification>" 高
```
