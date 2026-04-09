# Contributing to ClaudeTeam

Thank you for your interest in contributing! This guide will help you get started.

---

## How to Contribute

### Reporting Issues

- Use [GitHub Issues](https://github.com/zylMozart/ClaudeTeam/issues) to report bugs or request features
- Search existing issues before creating a new one
- Include the following in bug reports:
  - Steps to reproduce
  - Expected behavior vs actual behavior
  - Environment info (OS, Python version, Claude Code version)
  - Relevant logs or error messages

### Suggesting Features

- Open an issue with the `feature-request` label
- Describe the use case and why it would benefit the project
- If possible, outline a rough implementation approach

---

## Pull Request Process

### 1. Fork and Branch

```bash
git clone https://github.com/zylMozart/ClaudeTeam.git
cd ClaudeTeam
git checkout -b feature/your-feature-name
```

**Branch naming convention:**

| Type | Pattern | Example |
|------|---------|---------|
| Feature | `feature/<name>` | `feature/slack-support` |
| Bug fix | `fix/<name>` | `fix/router-crash` |
| Documentation | `docs/<name>` | `docs/faq-update` |
| Refactor | `refactor/<name>` | `refactor/msg-bus` |

### 2. Make Your Changes

- Keep changes focused — one feature or fix per PR
- Follow the code style guide below
- Add or update tests if applicable
- Update documentation if your change affects user-facing behavior

### 3. Commit Messages

Use clear, descriptive commit messages:

```
<type>: <short description>

<optional body — explain what and why, not how>
```

**Types:** `feat`, `fix`, `docs`, `refactor`, `test`, `chore`

**Examples:**
```
feat: add Slack message bus adapter
fix: router crashes when group chat is empty
docs: add FAQ about memory management
refactor: extract token refresh logic into helper
```

### 4. Submit the PR

```bash
git push origin feature/your-feature-name
```

Then open a Pull Request on GitHub:

- **Title:** Short and clear (under 70 characters)
- **Description:** Explain what the PR does, why it's needed, and how to test it
- **Link related issues:** Use `Closes #123` or `Fixes #123`

### 5. Review Process

- A maintainer will review your PR within a few days
- Address review feedback by pushing new commits (don't force-push)
- Once approved, a maintainer will merge the PR

---

## Code Style

### Python

- **Version:** Python 3.8+ compatible
- **Formatting:** Follow PEP 8 conventions
- **Indentation:** 4 spaces (no tabs)
- **Imports:** Group in order — stdlib, third-party, local — with blank lines between groups
- **Strings:** Use double quotes for user-facing strings, single quotes for internal identifiers
- **Type hints:** Optional but welcome for public functions
- **Docstrings:** Not required for private functions; use them for public APIs

### Shell Scripts

- Use `#!/bin/bash` shebang
- Use `set -e` for error handling
- Quote variables: `"$VAR"` not `$VAR`
- Use `$(command)` not backticks

### Markdown

- Use ATX-style headers (`#` not underlines)
- One sentence per line (for cleaner diffs)
- Code blocks with language specifier (` ```bash `, ` ```python `, etc.)

---

## Project Architecture

Before contributing, understand the key components:

| Component | File | Role |
|-----------|------|------|
| Message Bus | `scripts/feishu_msg.py` | All inter-agent communication |
| Router | `scripts/feishu_router.py` | Feishu → tmux message delivery |
| Config | `scripts/config.py` | Central configuration loader |
| Setup | `scripts/setup.py` | One-time Feishu resource creation |
| Launcher | `scripts/start-team.sh` | tmux session + agent startup |
| Guidance | `CLAUDE.md` | Claude Code reads this on startup |

**Key principles:**
- `scripts/` contains runtime infrastructure — changes here affect all users
- `templates/` contains identity templates — changes here affect new agents
- `CLAUDE.md` is the entry point for Claude Code — keep it clear and machine-readable
- Runtime data (`agents/`, `team.json`, `.env`) is never committed

---

## Testing Your Changes

### Manual Testing

1. Clone a fresh copy of the repo
2. Walk through the CLAUDE.md setup flow
3. Verify your changes work end-to-end

### Checklist Before Submitting

- [ ] Code follows the style guide
- [ ] No credentials, API keys, or personal data in the commit
- [ ] `CLAUDE.md` still works as a setup guide (if you modified it)
- [ ] `setup.py` runs without errors (if you modified scripts)
- [ ] New features are documented in README.md and/or docs/README_EN.md

---

## Adding a New Script

If you're adding a new script to `scripts/`:

1. Follow existing patterns in the codebase
2. Use `config.py` for configuration access
3. Use `token_cache.py` for Feishu API authentication
4. Add the script to the project structure section in both README files
5. If it's a daemon process, add monitoring support in `watchdog.py`

## Adding a New Agent Role

To add a new built-in role template:

1. Add role description to the mapping table in `CLAUDE.md` (Phase 3)
2. Consider whether a specialized template is needed in `templates/`
3. Update the team template tables in both READMEs

---

## Code of Conduct

- Be respectful and constructive in all interactions
- Focus feedback on the code, not the person
- Help newcomers feel welcome
- If you see unacceptable behavior, report it to the maintainers

---

## Questions?

- Open a [Discussion](https://github.com/zylMozart/ClaudeTeam/discussions) for general questions
- Tag your issue with `help-wanted` if you need guidance on implementation

Thank you for helping make ClaudeTeam better!
