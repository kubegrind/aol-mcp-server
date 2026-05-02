# AOL Mail MCP Server

A production-ready [Model Context Protocol](https://modelcontextprotocol.io) server for AOL Mail.  
Works as a local stdio MCP server compatible with **VS Code GitHub Copilot Agent Mode** and **Claude Desktop**.  
Uses **[uv](https://docs.astral.sh/uv/)** for dependency and environment management.

---

## Prerequisites

- **uv** package manager (Python 3.11+ included automatically)
- An AOL Mail account with IMAP access enabled
- An AOL **app password** (not your main AOL password)

### Install uv

```bash
# macOS / Linux
curl -LsSf https://astral.sh/uv/install.sh | sh

# Windows (PowerShell)
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

Verify: `uv --version`

---

## Generate an AOL App Password

1. Sign in at [myaccount.aol.com](https://myaccount.aol.com).
2. Go to **Security** → **Manage app passwords**.
3. Enter a label (e.g. `MCP Server`) and click **Generate password**.
4. Copy the generated password — this is your `AOL_APP_PASSWORD`.

> AOL requires an app password for all third-party IMAP/SMTP clients.  
> Standard account passwords will not work.

---

## Usage

There are three ways to run the server depending on your situation.

### Option A — `uvx` (recommended, no install required)

Runs directly from PyPI without cloning or installing anything permanently.

```bash
uvx aol-mcp
```

Pass credentials via environment variables (see VS Code / Claude Desktop config below).

### Option B — Install as a persistent tool

Install once, run anywhere by name.

```bash
# From PyPI
uv tool install aol-mcp

# From GitHub (before PyPI publish)
uv tool install git+https://github.com/kubegrind/aol-mcp-server

# From a local clone
uv tool install .
```

Then run:

```bash
AOL_EMAIL=you@aol.com AOL_APP_PASSWORD=yourpassword aol-mcp
```

### Option C — Local clone (for development / contributors)

```bash
git clone https://github.com/kubegrind/aol-mcp-server
cd aol-mcp-server
uv sync
cp .env.example .env
# Edit .env with your credentials
uv run server.py
```

---

## VS Code (GitHub Copilot Agent Mode) Setup

Create or edit `.vscode/mcp.json` in your workspace:

**Recommended — `uvx` (no install needed):**

```json
{
  "servers": {
    "aol-mail": {
      "type": "stdio",
      "command": "uvx",
      "args": ["aol-mcp"],
      "env": {
        "AOL_EMAIL": "your_email@aol.com",
        "AOL_APP_PASSWORD": "your_app_password"
      }
    }
  }
}
```

**Alternative — local clone:**

```json
{
  "servers": {
    "aol-mail": {
      "type": "stdio",
      "command": "uv",
      "args": ["run", "--directory", "/path/to/aol-mcp-server", "server.py"],
      "env": {
        "AOL_EMAIL": "your_email@aol.com",
        "AOL_APP_PASSWORD": "your_app_password"
      }
    }
  }
}
```

Open Copilot Chat, switch to **Agent Mode** — AOL Mail tools appear automatically.

---

## Claude Desktop Setup

Edit your Claude Desktop config:

- **macOS:** `~/Library/Application Support/Claude/claude_desktop_config.json`
- **Windows:** `%APPDATA%\Claude\claude_desktop_config.json`

**Recommended — `uvx`:**

```json
{
  "mcpServers": {
    "aol-mail": {
      "command": "uvx",
      "args": ["aol-mcp"],
      "env": {
        "AOL_EMAIL": "your_email@aol.com",
        "AOL_APP_PASSWORD": "your_app_password"
      }
    }
  }
}
```

**Alternative — local clone:**

```json
{
  "mcpServers": {
    "aol-mail": {
      "command": "uv",
      "args": ["run", "--directory", "/full/path/to/aol-mcp-server", "server.py"],
      "env": {
        "AOL_EMAIL": "your_email@aol.com",
        "AOL_APP_PASSWORD": "your_app_password"
      }
    }
  }
}
```

Restart Claude Desktop after saving — AOL Mail tools appear in the tools panel.

---

## Available Tools

| Tool | Description |
|------|-------------|
| `read_inbox` | List recent emails from any folder (default: INBOX) |
| `read_folder` | List recent emails from a named folder (e.g. LinkedIn, GitHub) |
| `read_email` | Fetch full body and headers by IMAP message ID, with optional folder |
| `search_emails` | Search by keyword in any folder across FROM, SUBJECT, BODY, or ALL |
| `send_email` | Compose and send a new email |
| `reply_email` | Reply to an existing email by message ID, with optional folder |
| `delete_email` | Move an email to Trash by message ID, with optional folder |
| `delete_all_in_folder` | Bulk delete — move every email in a folder to Trash |
| `move_email` | Move an email to any named folder, with optional source folder |
| `move_all_emails` | Bulk move — move all emails from one folder to another |
| `list_folders` | List all IMAP folders in the mailbox |
| `mark_read` | Mark one or multiple emails as read, with optional folder |
| `get_attachments` | List all attachments (name, MIME type, size) in an email |

### Example Prompts

```
Show me the last 5 emails in my inbox.
Show me the last 10 emails in my LinkedIn folder.
Read email ID 42 from my GitHub folder.
Search for emails from boss@example.com in my Sent folder.
Send an email to alice@example.com with subject "Hello" and body "Hi Alice!".
Reply to email 17 with "Thanks, got it!".
Delete email 99 from my LinkedIn folder.
Delete all emails in my Spam folder.
Move email 55 to folder Work.
Move all emails from LinkedIn to Archive.
List all my mail folders.
Mark emails 10, 11, 12 as read in my GitHub folder.
List attachments in email 33.
```

---

## Connection Details

| Protocol | Host | Port | Security |
|----------|------|------|----------|
| IMAP | imap.aol.com | 993 | SSL/TLS |
| SMTP | smtp.aol.com | 465 | SSL/TLS |

---

## Troubleshooting

### "AUTHENTICATE failed" / login rejected
- Make sure `AOL_APP_PASSWORD` is the **app password** from myaccount.aol.com, not your AOL login password.
- Re-generate the app password and update your config.
- Confirm IMAP is enabled in your AOL account security settings.

### "Connection refused" / timeout on ports 993 or 465
- Your firewall or network may be blocking outbound SSL ports.
- Test connectivity: `telnet imap.aol.com 993`

### Emails not found by ID
- IMAP message IDs are session-scoped integers. Run `read_inbox` first to retrieve current IDs.

### `uvx` / `uv` not found in VS Code or Claude Desktop
- Ensure `uv` is on your system `PATH` (the installer normally handles this).
- Find the full path with `which uv` (macOS/Linux) or `where uv` (Windows) and use it as `command`.
- macOS example: `"command": "/Users/you/.local/bin/uvx"`

### `uv tool install` fails
- Ensure Python 3.11+ is available: `uv python install 3.11`
- Try `uv tool install --reinstall aol-mcp-server` to force a clean install.

### SSL certificate errors
- Upgrade your CA bundle: `uv run --with certifi python -m certifi`
- On macOS, run the **Install Certificates** script in your Python.app folder.

---

## Security Notes

- Credentials are passed via environment variables — never stored in the package.
- `.env` is in `.gitignore` and must never be committed.
- Passwords are never logged or included in error messages.
- All IMAP and SMTP connections are closed in `finally` blocks — no connection leaks.
