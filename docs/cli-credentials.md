# CLI Credential Setup (Docker)

ClaudeTeam supports heterogeneous teams — different agents can run different CLI tools. Each CLI has its own credential flow when running inside Docker.

## Kimi CLI

After `docker compose up`, kimi-code agents will prompt for login via a **device code flow**. You'll see a message like:

```
Please visit the following URL to finish authorization.
Verification URL: https://www.kimi.com/code/authorize_device?user_code=XXXX-YYYY
```

Open the URL in your browser, authorize with your Moonshot account, and the kimi CLI will save credentials to `$HOME/.kimi/` inside the container.

**Credential persistence:** The `.kimi-credentials/` directory in the project root is bind-mounted into the container (see `docker-compose.yml`). After first login, subsequent container recreations (`docker compose down && up`) reuse the saved tokens automatically — no re-login needed.

If the host already has a known-good Kimi login:

```bash
mkdir -p .kimi-credentials
rsync -a ~/.kimi/ .kimi-credentials/
```

**If kimi login expires:** Remove the `.kimi-credentials/` directory and restart the container. The kimi agents will prompt for login again.

```bash
rm -rf .kimi-credentials/
docker compose restart
```

## Codex CLI

Codex agents prompt for login via **device code flow** on first run:

```
https://auth.openai.com/codex/device
Enter this one-time code: XXXX-XXXXX
```

Open the URL, sign in with your ChatGPT account, and enter the code. Credentials are saved to `.codex-credentials/` (bind-mounted) and persist across container recreations.

If the host already has a Codex login:

```bash
mkdir -p .codex-credentials
rsync -a ~/.codex/ .codex-credentials/
```

Copying `.codex` is necessary but not always sufficient for Codex usage in a new container. If `/usage codex` or `/usage all` still reports HTTP `403` from the usage API and `401` during refresh, treat it as a container login/permission problem, not as a missing mount. Run `codex` inside the container and complete ChatGPT login there, or use host-side `codex-cli-usage status` until a host-side usage bridge is available.

## Gemini CLI

Gemini agents prompt for **Google OAuth** on first run. You'll see a long Google OAuth URL — open it in your browser, authorize, and paste the authorization code back into the terminal. Credentials are saved to `.gemini-credentials/` (bind-mounted) and persist across container recreations.

If the host already has a Gemini login:

```bash
mkdir -p .gemini-credentials
rsync -a ~/.gemini/ .gemini-credentials/
```

If copied Gemini credentials are expired, run `gemini` inside the container and complete Google login again.

Do not commit `.kimi-credentials/`, `.codex-credentials/`, or `.gemini-credentials/`; they contain OAuth/token material and are intentionally listed in `.gitignore`.

## Credential persistence summary

| CLI | Credential dir | Bind mount | First login | Persists? |
|---|---|---|---|---|
| Claude Code | `~/.claude/` | built-in | OAuth (automatic) | yes |
| Kimi | `.kimi-credentials/` | yes | Device code | yes |
| Codex | `.codex-credentials/` | yes | Device code | yes |
| Gemini | `.gemini-credentials/` | yes | Google OAuth | yes |

All CLIs are pre-configured with auto-approve flags (e.g., `--yolo`, `--dangerously-bypass-approvals-and-sandbox`) so agents never see permission prompts during operation.

## `/usage` per-CLI quota dependencies

The `/usage` slash command queries real-time quota for each CLI. These tools are pre-installed in the Docker image:

| CLI | Quota tool | Install | What it shows |
|---|---|---|---|
| Claude Code | `usage_snapshot.py` | Built-in | 5h/7d/Sonnet % + Extra usage |
| Kimi | `/usage` (built into kimi CLI) | N/A | Weekly % + 5h % + reset time |
| Codex | `codex-cli-usage` | `uv tool install codex-cli-usage` | Session % + reset time |
| Gemini | `gemini-cli-usage` | `uv tool install gemini-cli-usage` | Per-model % + reset time |

Usage: `/usage` (CC default), `/usage kimi`, `/usage codex`, `/usage gemini`, `/usage all`.
