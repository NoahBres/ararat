# rtk-api

Personal API + (optional) MCP server exposing tools -- Things 3 first, more
later -- to AI agents (Claude Code, scripts, Claude.ai). See
`notes/plan.md` for the full design and rollout phases, and `notes/NOTES.md` for
the current state of the deployed service (auth, credentials, gotchas).

Phase 1 (this code): REST server with auth, the tool registry, Things read
tools, and a system smoke-test tool. MCP mounting is best-effort (see
"MCP mount" below) -- REST is the primary deliverable.

## Run locally

```sh
cd rtk-api
uv sync
RTK_API_BEARER_TOKEN=test-token uv run rtk-api serve --host 127.0.0.1 --port 8787
```

Or drop the same variables into `~/.config/rtk-api/env` (`chmod 600`) so
you don't have to export them every time -- real environment variables
still take precedence over that file.

### Environment variables

| Variable | Purpose | Default |
|---|---|---|
| `RTK_API_BEARER_TOKEN` | Static bearer token accepted on `Authorization: Bearer <token>` | none (bearer auth disabled if unset) |
| `RTK_API_CLIENTS` | JSON map of named, scoped API clients (see [Clients and scopes](#clients-and-scopes)) | empty (no scoped clients) |
| `RTK_API_MCP_SECRET` | Capability-URL secret; if set, mounts the MCP app at `/<secret>/mcp` and that path prefix bypasses auth | none (MCP mount disabled if unset) |
| `CF_ACCESS_TEAM_DOMAIN` | Cloudflare Access team domain, e.g. `myteam.cloudflareaccess.com` | none (CF Access auth disabled if unset) |
| `CF_ACCESS_AUD` | Cloudflare Access application AUD tag | none |
| `CF_ACCESS_OWNER_COMMON_NAME` | The owner's own Access service token's client id (the `common_name` claim in its JWT, e.g. `2c58...4e7.access`). Required for a Cloudflare JWT to grant unscoped owner -- see [Auth](#auth) | none (no JWT grants owner if unset) |
| `THINGS_AUTH_TOKEN` | Things URL scheme auth token (needed for write tools in Phase 2) | none |
| `IMESSAGE_WRITE_ENABLED` | Kill switch for iMessage sends (Phase 3) | `false` |
| `IMESSAGE_WRITE_ALLOWLIST` | Comma-separated identifiers allowed to receive sends (Phase 3) | empty |
| `FAIRBRIDGE_PARTICIPANTS` | Comma-separated identifiers of the Fairbridge group chat's members; the `fairbridge.*` tools resolve their chat from this (see [Fairbridge](#fairbridge-one-group-chat)) | empty (tools refuse) |
| `RTK_API_HOST` | Bind host | `127.0.0.1` |
| `RTK_API_PORT` | Bind port | `8787` |

At least one of `RTK_API_BEARER_TOKEN` / `CF_ACCESS_TEAM_DOMAIN`+`CF_ACCESS_AUD`
/ `RTK_API_MCP_SECRET` should be set or every non-`/health` request will
be rejected with 401.

## Auth

Authentication resolves a **principal**; authorization then checks that
principal's allowlist against the tool being called.

Every request except `GET /health` must resolve to a principal via the first
of these that matches:

1. Request path starts with `/<RTK_API_MCP_SECRET>/` (only the MCP mount
   lives there) -> principal `mcp`, unscoped.
2. A named client token in `X-Rtk-Client-Token` or `Authorization: Bearer
   <token>`, matching an entry in `RTK_API_CLIENTS` -> that client's
   principal, scoped.
3. `Cf-Access-Jwt-Assertion` header, verified against the Cloudflare Access
   JWKS (`https://<team>/cdn-cgi/access/certs`) with the configured `aud`,
   *and* the JWT's `common_name` claim matching `CF_ACCESS_OWNER_COMMON_NAME`
   -> principal `owner`, unscoped.
4. `Authorization: Bearer <RTK_API_BEARER_TOKEN>` -> principal `owner`,
   unscoped.

Anything else is a 401. A principal that authenticates but isn't allowed the
tool it called gets a **403**.

Order matters: the client token is checked *before* the Cloudflare JWT, so a
scoped identity always wins over the generic one. An external client behind
Cloudflare Access presents both -- the Access service-token headers get it
past the edge, the client token tells this server who it is.

**Why the JWT alone isn't enough for owner.** A Cloudflare Access JWT proves
the caller passed *some* service token valid for this Access app -- not
*which* one. Every scoped client (e.g. `instinct`) needs its own Access
service token so it can be revoked independently (see `notes/NOTES.md`), and
that token's JWT is just as "valid" as the owner's. Without checking
`common_name`, a scoped client that simply omitted its `X-Rtk-Client-Token`
would fall through to step 3 and get unscoped owner -- silently defeating
`RTK_API_CLIENTS`. Checking `common_name` closes that: only the specific
service token named in `CF_ACCESS_OWNER_COMMON_NAME` can reach owner via the
JWT path; any other valid JWT falls through to step 4 (probably a 401, since
external clients don't have the bearer token).

### Clients and scopes

`RTK_API_CLIENTS` is a JSON object mapping a client name to its token and
scopes. `allow` and `require_approval` are fnmatch patterns over tool names:

```json
{
  "instinct": {
    "token": "<random secret>",
    "allow": ["things.*", "imessage.chats", "imessage.recent",
              "imessage.with_contact", "imessage.search", "imessage.unread",
              "fairbridge.info", "fairbridge.read", "fairbridge.send"],
    "require_approval": ["imessage.send"]
  }
}
```

Settings are cached at process start, so **editing `RTK_API_CLIENTS` requires a
restart** to take effect (`launchctl kickstart -k gui/$UID/com.noahbres.rtk-api`
on rtk).

`GET /v1/tools` is filtered to what the caller may actually call, so a scoped
client can't discover tools outside its grant. Calls to unknown tools outside
the grant return 403 rather than 404, for the same reason.

### Bootstrap discovery for agents (`GET /v1/help`)

An authenticated agent that's never seen this API before can `GET /v1/help`
(same auth as everything else) and get a **markdown document scoped to its
own grant**: how to call a tool, the response envelope, and one section per
tool it may actually invoke right now (description + example curl), plus a
separate "Gated" section for anything in its grant that's blocked pending the
approval queue. It's generated fresh from the live registry every request
via the same filtering `/v1/tools` uses, so the two surfaces can't drift
apart, and it never lists (or gives schemas for) tools outside the caller's
`allow`.

Default response is `text/markdown`; send `Accept: application/json` to get
it wrapped as `{"ok": true, "result": "<markdown>"}` instead. This is the URL
to hand a new agent as its starting point -- point it at
`https://api.noahbres.com/v1/help` with its credentials and it can discover
everything else itself.

**`require_approval` is not implemented yet.** It is checked *before* `allow`
and overrides it, so listing a tool there gates it out of a broader grant.
The intended mechanism is a human-in-the-loop approval queue -- the tool
returns `202` with an `approval_id`, a dedicated Telegram bot DMs an
Allow/Deny prompt, and the caller polls for the decision. Until that ships, a
tool matching `require_approval` is refused with a 403 rather than allowed
through unattended. See `notes/NOTES.md` for the full design.

**The MCP mount is not scoped.** `_build_mcp_app` registers the entire
registry and the `/<secret>/` path short-circuits every check above, so
holding the MCP secret means holding every tool. Per-client scoping applies
to the REST surface only.

Two credentials that a malformed `RTK_API_CLIENTS` must never break: the
owner bearer and the Cloudflare JWT. Parsing fails closed (no clients
loaded, error logged) rather than taking the server down.

## curl examples

```sh
export T=test-token

curl -s localhost:8787/health

curl -s localhost:8787/v1/tools -H "Authorization: Bearer $T" | jq .

curl -s localhost:8787/v1/system/ping -H "Authorization: Bearer $T" -d '{}'

# As a scoped client (see RTK_API_CLIENTS above):
curl -s localhost:8787/v1/things/list -H "X-Rtk-Client-Token: $INSTINCT_TOKEN" \
  -H 'content-type: application/json' -d '{"view":"today"}'

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

Drop a new module in `src/rtk-api/tools/` -- it's auto-imported by
`rtk-api/tools/__init__.py` (via `pkgutil.iter_modules`), so nothing else
needs to change. Example:

```python
# src/rtk-api/tools/example.py
from rtk_api.registry import tool


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
(`~/.local/state/rtk-api/audit.log`) before executing. Write tools should
also fail loudly (raise) rather than silently no-op when a required feature
flag is off.

## iMessage

Phase 3 adds read tools over `~/Library/Messages/chat.db` plus a gated
`imessage.send`. Shared logic lives in `src/rtk-api/lib/imessage_db.py`
(chat.db reading + attributedBody decoding, ported from
`tools/imessage-query.py`) and `src/rtk-api/lib/contacts.py` (fuzzy name ->
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
| `imessage.send(to, text)` | **Write.** `to` must be an exact phone/email, not a name. Gated -- see below. |

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

Reading chat.db requires Full Disk Access. An interactive SSH session gets it
for free, but the launchd agent does not. The agent runs through
`~/Applications/rtk-api.app` (a tiny launcher, see `launcher/`), so macOS
attributes permissions to "rtk-api" rather than a generic python binary. If a
call raises `ImessageAccessError`, grant it once on rtk:

1. System Settings > Privacy & Security > Full Disk Access > `+` > pick
   `~/Applications/rtk-api.app` > enable.
2. `restart-rtk-api`.

The grant survives Python/uv upgrades. It only needs redoing if the launcher
is rebuilt (`launcher/build.sh --force`), since that changes its signature.

Verify: `curl -H "Authorization: Bearer $T" localhost:8787/v1/imessage/recent -d '{}'`
should return messages, not an FDA error.

### Write kill switch

`imessage.send` refuses to do anything unless both are true:

- `IMESSAGE_WRITE_ENABLED=true`
- the recipient identifier is in `IMESSAGE_WRITE_ALLOWLIST`
  (comma-separated phone numbers/emails, e.g. `+15551234567,friend@example.com`)

`to` must already be a phone number or email address; it is normalised
(digits plus leading `+` for phones, lowercased for emails) and compared to
the allowlist as-is. Contact *names* are rejected with a 400 -- there is no
fuzzy matching on the send path, because a name that resolves to the wrong
person would send the message to them. Resolve the name first with a read
tool (`imessage.with_contact` accepts fuzzy names and returns the `sender`
identifiers) and pass the exact identifier back.

It also refuses group chats and caps `text` at 2000 characters. It sends via
`osascript` with `on run argv` so the recipient and text are passed as
arguments -- never interpolated into the AppleScript source. The **first**
send triggers a macOS Automation permission prompt for Messages.app on the
server's screen -- approve it via Screen Sharing (see
`notes/plan.md` section 6.1/6.3). After sending, it polls
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

# write -- only works once IMESSAGE_WRITE_ENABLED=true and the recipient is allowlisted;
# `to` must be a phone/email, never a contact name
curl -s localhost:8787/v1/imessage/send -H "Authorization: Bearer $T" \
  -d '{"to": "+15551234567", "text": "running late, be there in 10"}'
```

## Fairbridge (one group chat)

`fairbridge.read` / `fairbridge.send` / `fairbridge.info` expose exactly one
iMessage group chat and nothing else. They exist because `imessage.send` is
the wrong shape for "let the agent text this one group": it can reach any
allowlisted handle, it refuses group chats outright, and it's gated behind
the (unbuilt) approval queue.

The scoping is structural, not just a policy check: **`fairbridge.send` takes
a `text` and no recipient**. There is no parameter an agent could pass to
redirect a message somewhere else -- the destination lives in rtk's env file,
not in the request. `fairbridge.read` is the same in reverse: no chat
selector, so it can only ever return that one conversation.

### How the chat is identified

By **participant set**, resolved fresh from chat.db on every call --
deliberately not by guid. Modern iMessage group-chat guids are *device-local*:
the same group chat has different guids on Noah's laptop and on rtk, so a
guid copied from one machine resolves to
nothing on the other. A participant set is the same everywhere.

Set `FAIRBRIDGE_PARTICIPANTS` to the members' phone numbers/emails, comma
separated. Matching is exact in both directions after normalisation: a chat
with those people *plus one more* is a different conversation and will not
match (chat.db accumulates such near-misses). Where Messages has kept several
duplicate rows for the same chat, the most recently active one wins.

`FAIRBRIDGE_PARTICIPANTS` has **no default in the code** -- it's real people's
phone numbers and this repo is public. Unset, the tools refuse rather than
guessing at a chat. Use `fairbridge.info` to confirm what they're pointed at.

### Sending

`fairbridge.send` rejects empty text and caps `text` at 2000 characters.
There is deliberately **no kill-switch env var**: unlike `imessage.send`,
which can reach any allowlisted handle and needs a global off switch, this
tool has one hardcoded destination and no recipient parameter. The scoping is
the safety property, and the grant in `RTK_API_CLIENTS` is what decides who
may call it. Unsetting `FAIRBRIDGE_PARTICIPANTS` disables it as a side effect,
since there is then no chat to resolve.

It sends via `osascript` with `on run argv`, so the chat guid and the text are
passed as arguments and never interpolated into the AppleScript source. Note
the script uses the bare `chat id "..."` form: Messages' dictionary parses
`text chat id "..."` as `text of (chat id "...")` and fails with `-1728`.
Afterwards it polls chat.db for ~3s scoped to that chat -- the 1:1
`find_recent_outbound` joins through a single handle and can't confirm a group
send -- and returns `{"sent": "confirmed" | "unconfirmed", ...}`.

Sending needs macOS Automation permission for Messages.app for whatever runs
the process. Already granted on rtk.

### curl examples

```sh
curl -s localhost:8787/v1/fairbridge/info -H "Authorization: Bearer $T" -d '{}'

curl -s localhost:8787/v1/fairbridge/read -H "Authorization: Bearer $T" \
  -d '{"limit": 20, "days": 7}'

curl -s localhost:8787/v1/fairbridge/send -H "Authorization: Bearer $T" \
  -d '{"text": "running late, be there in 10"}'
```

## MCP mount

If `RTK_API_MCP_SECRET` is set, `create_app()` builds a `fastmcp` server
registering every tool in the registry (dots in tool names become
underscores for MCP, e.g. `things.list` -> `things_list`) and mounts its
ASGI app at `/<RTK_API_MCP_SECRET>/mcp`. The FastAPI app's `lifespan` is
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
claude mcp add --transport http rtk-api http://localhost:8787/<secret>/mcp
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
./rtk-api/deploy.sh           # run directly on rtk
./rtk-api/deploy.sh --remote  # run from rnn over the Cloudflare-tunneled SSH alias
```

`deploy.sh` pulls, `uv sync --frozen`s, kicks the launchd agent, and polls
`/health`. The launchd agent itself (Phase 1 step 5 of the plan) is defined
in `nixos-config/hosts/rtk/home.nix` -- not part of this directory -- and
only needs re-applying (`just switch-rtk`) when `home.nix` itself changes.
