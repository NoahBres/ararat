# hometools

Personal API + (optional) MCP server exposing tools -- Things 3 first, more
later -- to AI agents (Claude Code, scripts, Claude.ai). See
`../notes/plans/hometools-api.md` for the full design and rollout phases.

Phase 1 (this code): REST server with auth, the tool registry, Things read
tools, and a system smoke-test tool. MCP mounting is best-effort (see
"MCP mount" below) -- REST is the primary deliverable.

## Run locally

```sh
cd hometools
uv sync
HOMETOOLS_BEARER_TOKEN=test-token uv run hometools serve --host 127.0.0.1 --port 8787
```

Or drop the same variables into `~/.config/hometools/env` (`chmod 600`) so
you don't have to export them every time -- real environment variables
still take precedence over that file.

### Environment variables

| Variable | Purpose | Default |
|---|---|---|
| `HOMETOOLS_BEARER_TOKEN` | Static bearer token accepted on `Authorization: Bearer <token>` | none (bearer auth disabled if unset) |
| `HOMETOOLS_MCP_SECRET` | Capability-URL secret; if set, mounts the MCP app at `/<secret>/mcp` and that path prefix bypasses auth | none (MCP mount disabled if unset) |
| `CF_ACCESS_TEAM_DOMAIN` | Cloudflare Access team domain, e.g. `myteam.cloudflareaccess.com` | none (CF Access auth disabled if unset) |
| `CF_ACCESS_AUD` | Cloudflare Access application AUD tag | none |
| `THINGS_AUTH_TOKEN` | Things URL scheme auth token (needed for write tools in Phase 2) | none |
| `IMESSAGE_WRITE_ENABLED` | Kill switch for iMessage sends (Phase 3) | `false` |
| `IMESSAGE_WRITE_ALLOWLIST` | Comma-separated identifiers allowed to receive sends (Phase 3) | empty |
| `HOMETOOLS_HOST` | Bind host | `127.0.0.1` |
| `HOMETOOLS_PORT` | Bind port | `8787` |

At least one of `HOMETOOLS_BEARER_TOKEN` / `CF_ACCESS_TEAM_DOMAIN`+`CF_ACCESS_AUD`
/ `HOMETOOLS_MCP_SECRET` should be set or every non-`/health` request will
be rejected with 401.

## Auth

Every request except `GET /health` must satisfy one of:

1. `Cf-Access-Jwt-Assertion` header, verified against the Cloudflare Access
   JWKS (`https://<team>/cdn-cgi/access/certs`) with the configured `aud`.
2. `Authorization: Bearer <HOMETOOLS_BEARER_TOKEN>`.
3. Request path starts with `/<HOMETOOLS_MCP_SECRET>/` (only the MCP mount
   lives there).

## curl examples

```sh
export T=test-token

curl -s localhost:8787/health

curl -s localhost:8787/v1/tools -H "Authorization: Bearer $T" | jq .

curl -s localhost:8787/v1/system/ping -H "Authorization: Bearer $T" -d '{}'

curl -s localhost:8787/v1/system/echo -H "Authorization: Bearer $T" \
  -d '{"text": "hello"}'

curl -s localhost:8787/v1/things/list -H "Authorization: Bearer $T" \
  -H 'content-type: application/json' -d '{"view": "today"}'

curl -s localhost:8787/v1/things/search -H "Authorization: Bearer $T" \
  -d '{"query": "milk"}'

# Write tools (Phase 2) -- these shell out to `open things:///...` so they
# only work on a machine with Things 3 installed and running (or launchable).

curl -s localhost:8787/v1/things/add -H "Authorization: Bearer $T" \
  -d '{"title": "buy milk", "when": "today", "tags": ["errand"]}'

curl -s localhost:8787/v1/things/update -H "Authorization: Bearer $T" \
  -d '{"uuid": "<uuid>", "completed": true}'

curl -s localhost:8787/v1/things/complete -H "Authorization: Bearer $T" \
  -d '{"uuid": "<uuid>"}'

curl -s localhost:8787/v1/things/add_project -H "Authorization: Bearer $T" \
  -d '{"title": "Q4 Planning", "area": "Work", "todos": ["kickoff", "budget"]}'

curl -s localhost:8787/v1/things/batch -H "Authorization: Bearer $T" \
  -d '{"commands": [{"type": "to-do", "attributes": {"title": "buy milk"}}]}'
```

Every response is `{"ok": true, "result": ...}` or `{"ok": false, "error": ...}`
with a 4xx/5xx status.

### Things write tools (Phase 2)

`things.add`, `things.update`, `things.complete`, `things.add_project`, and
`things.batch` drive Things 3 via the `things:///...` URL scheme
(`open "things:///add?..."` etc.) rather than the sqlite DB -- writing to
the DB directly isn't supported by Things. This means:

- They only do anything useful on a machine where Things 3 is installed and
  either running or launchable (the URL scheme launches it if needed).
- `things.update`, `things.batch`, and by extension `things.complete`
  require `THINGS_AUTH_TOKEN` (Settings > General > Enable Things URLs in
  the Things app) -- calls raise a clear `ValueError` if it's unset.
- **There is no delete action in the Things URL scheme.** To retire a
  to-do, complete or cancel it (`things.complete`, or
  `things.update(..., canceled=True)`); actual deletion has to happen by
  hand in the app.
- `things.add` and `things.add_project` can't get a return value back from
  the `open` call, so after firing the URL they poll the Things DB (via the
  `things` library) for up to ~3s for a new item with that exact title
  created just now, and return `{"uuid", "title", "confirmed"}`.
  `confirmed: false` (with `uuid: null`) means the poll didn't find it in
  time -- the to-do may still have been created a moment later.
- `things.batch` passes a list of Things URL-scheme JSON commands straight
  through to `things:///json` with minimal validation (each entry needs
  `type` and `attributes`); see the "JSON" section of the
  [Things URL scheme docs](https://culturedcode.com/things/support/articles/2803573/).

## How to add a tool

Drop a new module in `src/hometools/tools/` -- it's auto-imported by
`hometools/tools/__init__.py` (via `pkgutil.iter_modules`), so nothing else
needs to change. Example:

```python
# src/hometools/tools/example.py
from hometools.registry import tool


@tool("example.hello", write=False, tags=["example"])
def hello(name: str) -> dict:
    """Say hello to name."""
    return {"greeting": f"hello {name}"}
```

This immediately:
- Appears in `GET /v1/tools` with a generated JSON schema for its params.
- Is callable at `POST /v1/example/hello` with body `{"name": "..."}`.
- Registers as an MCP tool named `example_hello` (if the MCP mount is
  enabled).

Mark tools that mutate state `write=True` -- they get an audit-log entry
(`~/.local/state/hometools/audit.log`) before executing. Write tools should
also fail loudly (raise) rather than silently no-op when a required feature
flag is off.

## iMessage

Phase 3 adds read tools over `~/Library/Messages/chat.db` plus a gated
`imessage.send`. Shared logic lives in `src/hometools/lib/imessage_db.py`
(chat.db reading + attributedBody decoding, ported from
`tools/imessage-query.py`) and `src/hometools/lib/contacts.py` (fuzzy name ->
phone/email resolution, ported from `tools/contacts-search.py`) -- those two
scripts are unchanged and still work standalone, they just point at the
canonical copies here now.

### Tools

| Tool | Description |
|---|---|
| `imessage.chats(limit=50)` | Recent chats: id, name/participants, last message date. |
| `imessage.recent(limit=50, days=7)` | Newest messages across all chats. |
| `imessage.with_contact(contact, days=90, limit=50, keyword=None)` | Messages to/from a contact (fuzzy name or phone/email), optionally filtered by keyword. |
| `imessage.search(query, days=365, limit=50)` | Search message bodies. |
| `imessage.unread()` | Unread inbound messages. |
| `imessage.send(to, text)` | **Write.** Gated -- see below. |

Read tool rows look like:

```json
{
  "date_utc": "2026-09-08T18:00:00+00:00",
  "date_pacific": "2026-09-08T11:00:00-07:00",
  "sender": "+15551234567",
  "sender_name": "Kirill",
  "is_from_me": false,
  "text": "hey are we still on for saturday?",
  "chat_id": "chat-guid-abc",
  "chat_name": "+15551234567",
  "attachments": ["image/jpeg"]
}
```

### Full Disk Access (required for all iMessage reads)

Reading chat.db requires Full Disk Access for whatever Python interpreter
runs the server -- an interactive SSH session gets this for free, but a
launchd agent's Python binary does not. If a call raises an error mentioning
Full Disk Access (`ImessageAccessError`), grant it:

1. Find the exact interpreter path: `cd hometools && uv python find 3.12`.
2. System Settings > Privacy & Security > Full Disk Access > `+` > press
   `Cmd+Shift+G` and paste that path > enable.
3. `restart-hometools` (the path changes if uv bumps the Python patch
   version, so pin `.python-version` and don't bump it casually -- re-grant
   FDA if you do).

Verify: `curl -H "Authorization: Bearer $T" localhost:8787/v1/imessage/recent -d '{}'`
should return messages, not an FDA error.

### Write kill switch

`imessage.send` refuses to do anything unless both are true:

- `IMESSAGE_WRITE_ENABLED=true`
- the resolved recipient identifier is in `IMESSAGE_WRITE_ALLOWLIST`
  (comma-separated phone numbers/emails, e.g. `+15551234567,friend@example.com`)

It also refuses group chats and caps `text` at 2000 characters. It sends via
`osascript` with `on run argv` so the recipient and text are passed as
arguments -- never interpolated into the AppleScript source. The **first**
send triggers a macOS Automation permission prompt for Messages.app on the
server's screen -- approve it via Screen Sharing (see
`notes/plans/hometools-api.md` section 6.1/6.3). After sending, it polls
chat.db for ~3s to confirm the outbound row landed and returns
`{"sent": "confirmed" | "unconfirmed", "to": ..., "rowid": ...}`.

### curl examples

```sh
curl -s localhost:8787/v1/imessage/chats -H "Authorization: Bearer $T" -d '{"limit": 20}'

curl -s localhost:8787/v1/imessage/recent -H "Authorization: Bearer $T" -d '{"days": 3}'

curl -s localhost:8787/v1/imessage/with_contact -H "Authorization: Bearer $T" \
  -d '{"contact": "Mom", "days": 30}'

curl -s localhost:8787/v1/imessage/search -H "Authorization: Bearer $T" \
  -d '{"query": "address", "days": 90}'

curl -s localhost:8787/v1/imessage/unread -H "Authorization: Bearer $T" -d '{}'

# write -- only works once IMESSAGE_WRITE_ENABLED=true and the recipient is allowlisted
curl -s localhost:8787/v1/imessage/send -H "Authorization: Bearer $T" \
  -d '{"to": "+15551234567", "text": "running late, be there in 10"}'
```

## MCP mount

If `HOMETOOLS_MCP_SECRET` is set, `create_app()` builds a `fastmcp` server
registering every tool in the registry (dots in tool names become
underscores for MCP, e.g. `things.list` -> `things_list`) and mounts its
ASGI app at `/<HOMETOOLS_MCP_SECRET>/mcp`. The FastAPI app's `lifespan` is
set to the mounted app's `lifespan` so fastmcp's session manager task group
actually starts -- this is required, not optional (see fastmcp's ASGI
integration docs at https://gofastmcp.com/deployment/asgi).

This wiring is best-effort for Phase 1: if it throws for any reason at
startup, `create_app()` logs the exception and falls back to a REST-only
app rather than crashing the whole server. REST is the deliverable here.

To test the MCP endpoint once it's running:

```sh
npx @modelcontextprotocol/inspector

# or, from Claude Code:
claude mcp add --transport http hometools http://localhost:8787/<secret>/mcp
claude mcp list
```

A minimal manual check (`initialize` over streamable HTTP) needs both
`Accept: application/json` and `Accept: text/event-stream`:

```sh
curl -s http://localhost:8787/<secret>/mcp \
  -H 'content-type: application/json' \
  -H 'accept: application/json, text/event-stream' \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"curl","version":"0"}}}'
```

## Tests

```sh
uv run ruff check .
uv run pytest
```

Things tests monkeypatch the `things` module -- they never touch a real
`.thingsdatabase` file. (You can sanity check the real library separately
with `uv run python -c "import things; print(things.today()[:1])"` on a
machine that has Things installed.)

## Deploy loop (rtk)

```sh
./hometools/deploy.sh           # run directly on rtk
./hometools/deploy.sh --remote  # run from rnn over the Cloudflare-tunneled SSH alias
```

`deploy.sh` pulls, `uv sync --frozen`s, kicks the launchd agent, and polls
`/health`. The launchd agent itself (Phase 1 step 5 of the plan) is defined
in `nixos-config/hosts/rtk/home.nix` -- not part of this directory -- and
only needs re-applying (`just switch-rtk`) when `home.nix` itself changes.
