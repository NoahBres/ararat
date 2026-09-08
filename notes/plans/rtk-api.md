# Plan: `rtk-api` — personal API + MCP server on the Mac mini (`rtk`)

Status: Written 2026-09-07. **Phases 1–3 implemented 2026-09-08** (code in `rtk-api/`, launchd agent in nix, verified end-to-end on rtk ad hoc). Remaining: Phase 0 manual steps (Cloudflare hostnames + Access, `just switch` on rtk, FDA grant) and optional Phase 4. MCP mount exists but is deprioritized.
Audience: implementing agents (any model). Read the whole doc before starting a phase.
Owner: Noah. Anything marked **[NOAH]** is a manual step only Noah can do (dashboard clicks, TCC prompts, secrets).

---

## 0. Goal and decisions already made

Noah wants a private, authenticated HTTP surface on his home Mac mini (`rtk`) where he can
expose arbitrary personal "tools" to AI agents (Claude.ai web chat via MCP, Claude Code,
scripts, other agents via plain REST). First tools: **Things 3** (read + write) and
**iMessage** (read, and optionally write behind a kill switch). Not for public use.

Decisions (don't re-litigate; ask Noah if you think one is wrong):

| Topic | Decision |
|---|---|
| Hostnames | `api.noahbres.com` (REST) and `mcp.noahbres.com` (MCP). Both route through the **existing** Cloudflare Tunnel on `rtk` to **one** local process. |
| Why subdomains, not `noahbres.com/api` | Notion custom domains only claim the specific hostnames you CNAME to them. Today only `www.noahbres.com` points at Notion; the bare apex `noahbres.com` has **no DNS record at all** (it doesn't resolve). Subdomains are fully independent records, so `api.`/`mcp.` are the right call. Path-based (`/api`) would require proxying the apex through Cloudflare and splitting paths with a Worker — possible, pointless. |
| Runtime | Python 3.12 managed by `uv`. Reasons: the existing tools (`tools/imessage-query.py`, `tools/things-today-tracker.py`) are Python; `things.py` is the best Things 3 read library; the `fastmcp` package gives MCP over HTTP with pluggable auth. |
| Framework | `fastapi` (REST) + `fastmcp` (MCP, mounted into the same ASGI app) + `uvicorn`. One tool definition registers into both. |
| Process | One launchd user agent on `rtk`, defined in nix (`nixos-config/hosts/rtk/home.nix`), listening on `127.0.0.1:8787` only. |
| Repo location | `rtk-api/` subfolder of this repo (`~/Developer/ararat/rtk-api`). rtk already pulls this repo on restart. |
| Auth (REST) | Cloudflare Access **service token** enforced at the edge, plus the server verifies the `Cf-Access-Jwt-Assertion` JWT. Also accepts a server-side bearer token (`Authorization: Bearer …`) for clients that can't do Access headers. |
| Auth (MCP) | Claude.ai custom connectors cannot send custom headers (consumer accounts get only OAuth client id/secret; static headers are an org-admin beta). Phase 1 uses a **capability URL**: `https://mcp.noahbres.com/<long-random-secret>/mcp`, no Cloudflare Access on that hostname, plus a Cloudflare rate-limit rule. Phase 4 (optional) upgrades to real OAuth restricted to Noah's Google account. |
| Secrets | Never in the repo. Runtime secrets live in `~/.config/rtk-api/env` on rtk (`chmod 600`), copied from 1Password. |
| iMessage write | Off by default. Enabled by env flag AND a recipient allowlist. Every send is appended to an audit log. |

---

## 1. Current state of the machines (verified 2026-09-07)

- `rnn` = Noah's laptop. `rtk` = Mac mini at home, headless, macOS 26.6.2, Apple Silicon.
- SSH to rtk from rnn: `ssh rtk-cloudflare` (host alias in nix; goes through `ssh-rtk.noahbres.com` via `cloudflared access ssh`). Works non-interactively.
- On rtk: `uv`, `bun`, `node` at `/etc/profiles/per-user/noah/bin/`, `/usr/bin/python3` is 3.9 (too old — we will install 3.12 via uv). Repo at `~/Developer/ararat`.
- Cloudflare tunnel: `cloudflared` 2026.3.0 runs as launchd agent `com.noahbres.cloudflared` with a **remote-managed token** (`/etc/cloudflared/tunnel-token`). Public hostnames are therefore configured in the **Cloudflare Zero Trust dashboard**, not in a local `config.yml`. Currently only `ssh-rtk.noahbres.com` → ssh. (Side note: cloudflared is outdated vs 2026.8.3; a `nix flake update` in `nixos-config/` would bump it. Not required for this project.)
- DNS: `noahbres.com` nameservers are Cloudflare. `www` → Cloudflare proxy → Notion. `api.` and `mcp.` do not exist yet.
- Things 3 is installed and running on rtk. DB: `~/Library/Group Containers/JLMPQHK86H.com.culturedcode.ThingsMac/ThingsData-*/Things Database.thingsdatabase/main.sqlite`. `uvx things-cli --json today` works on rtk. `THINGS_AUTH_TOKEN` exists in `~/Developer/ararat/.env` on rtk (needed for `things:///update`).
- iMessage: `~/Library/Messages/chat.db` is readable from an SSH session on rtk (284k messages). **This is because SSH sessions have Full Disk Access; a launchd agent will NOT until its Python binary is granted FDA** — see §6. Messages.app is not currently running.
- Existing launchd agents on rtk: `com.noahbres.ararat`, `com.noahbres.cloudflared`, `com.noahbres.things-today-tracker`. Copy their style.
- Existing reusable code: `tools/imessage-query.py` (chat.db reading + attributedBody decoding), `tools/contacts-search.py` (name → phone/email), `tools/things-today-tracker.py` (things-cli usage).
- 1Password (`op` CLI works on rnn): items named `Cloudflare` and `Things` exist.

---

## 2. Architecture

```
Claude.ai connector ──HTTPS──▶ mcp.noahbres.com ─┐
Claude Code / curl ──HTTPS──▶ api.noahbres.com ──┤ Cloudflare edge (TLS, Access, rate limit)
                                                 ▼
                                   cloudflared tunnel (rtk)  ──▶ http://127.0.0.1:8787
                                                                     rtk-api (uvicorn)
                                                                     ├─ /health
                                                                     ├─ /v1/<tool>/<action>   (REST, JSON)
                                                                     ├─ /<MCP_SECRET>/mcp     (MCP streamable HTTP)
                                                                     └─ tools/
                                                                         ├─ things.py   (things.py lib + things:// URL scheme)
                                                                         ├─ imessage.py (chat.db + osascript)
                                                                         └─ system.py   (ping/echo/version)
```

### Tool registry contract (the "arbitrary commands in the future" part)

Each tool module in `rtk-api/tools/` exposes plain functions with type hints and docstrings.
A registry decorator registers each function once, and the app generates **both** a FastAPI
route and an MCP tool from it:

```python
# rtk-api/registry.py (sketch)
from dataclasses import dataclass, field
from typing import Callable, Any

@dataclass
class ToolSpec:
    name: str            # "things.list_today"
    fn: Callable[..., Any]
    write: bool = False  # write tools get an extra guard + audit log
    tags: list[str] = field(default_factory=list)

REGISTRY: dict[str, ToolSpec] = {}

def tool(name: str, *, write: bool = False, tags: list[str] | None = None):
    def deco(fn):
        REGISTRY[name] = ToolSpec(name, fn, write, tags or [])
        return fn
    return deco
```

- REST route: `POST /v1/{tool}/{action}` with JSON body = kwargs; response `{"ok": true, "result": …}` or `{"ok": false, "error": …}` with proper HTTP status. Also `GET /v1/tools` listing the registry (name, description, params schema, write flag).
- MCP: register the same function via `fastmcp`'s `@mcp.tool` (or `mcp.add_tool(fn, name=…)`) so the MCP tool name is e.g. `things_list_today`.
- Use pydantic models for params so both surfaces get schema for free.
- Write tools must (a) be no-ops with a clear error when their enable flag is off, (b) log to `~/.local/state/rtk-api/audit.log` (timestamp, tool, caller principal, args) before executing.

### Auth middleware (`rtk-api/auth.py`)

Request is authorised if ANY of:

1. `Cf-Access-Jwt-Assertion` header present and verifies against `https://<TEAM>.cloudflareaccess.com/cdn-cgi/access/certs` with `aud == CF_ACCESS_AUD`. Cache the JWKS. (Library: `PyJWT` with `PyJWKClient`.)
2. `Authorization: Bearer <RTK_API_BEARER_TOKEN>` (constant-time compare).
3. Path starts with `/{RTK_API_MCP_SECRET}/` (the MCP capability URL). Only the MCP mount lives under that prefix.

`/health` is unauthenticated and returns `{"ok": true, "version": …}` only (no details).
Everything else → 401. Log auth failures with source IP (`CF-Connecting-IP`) but never log secrets.

Note: because uvicorn binds to `127.0.0.1` and the only ingress is the tunnel, all traffic arrives
through Cloudflare. Do not add a "trust localhost" bypass; tests should pass the bearer token.

---

## 3. Phase 0 — prerequisites **[NOAH]**

Do these once; agents can't. Agents: if any of these are missing, stop and tell Noah exactly which.

1. **Cloudflare tunnel public hostnames.** Zero Trust → Networks → Tunnels → (the rtk tunnel) → Public Hostname → add:
   - `api.noahbres.com` → service `HTTP` → `localhost:8787`
   - `mcp.noahbres.com` → service `HTTP` → `localhost:8787`
   (This auto-creates proxied CNAME records in DNS.)
2. **Cloudflare Access for `api.noahbres.com`.** Zero Trust → Access → Applications → Add → Self-hosted; domain `api.noahbres.com`. Policy: action **Service Auth**, include **Service Token** (create one: Access → Service Auth → Service Tokens → "rtk-api"; save Client ID + Client Secret to 1Password as item `rtk-api api service token`). Copy the application's **AUD tag** (Overview tab) and the team domain (`<team>.cloudflareaccess.com`).
3. **Do NOT put Access on `mcp.noahbres.com`** (Claude.ai can't present headers). Instead: Security → WAF → Rate limiting rules → e.g. hostname `mcp.noahbres.com`, >60 req / 10s per IP → block 1 min. Optional extra: a WAF custom rule blocking `mcp.noahbres.com` requests whose path does NOT start with `/<secret>/` (this makes the secret defense-in-depth at the edge too).
4. **Secrets file on rtk** at `~/.config/rtk-api/env`, `chmod 600`:
   ```
   RTK_API_BEARER_TOKEN=<openssl rand -hex 32>
   RTK_API_MCP_SECRET=<openssl rand -hex 24>   # url-safe, no slashes
   CF_ACCESS_TEAM_DOMAIN=<team>.cloudflareaccess.com
   CF_ACCESS_AUD=<aud tag from step 2>
   THINGS_AUTH_TOKEN=<same as in ~/Developer/ararat/.env>
   IMESSAGE_WRITE_ENABLED=false
   IMESSAGE_WRITE_ALLOWLIST=            # comma-separated phone/email identifiers
   ```
   Also save the bearer token and MCP secret to 1Password (`rtk-api`).
5. **Full Disk Access for the server's Python** (needed for chat.db only; Things works without it). See §6 — this must be done via Screen Sharing on rtk after Phase 1 installs the uv-managed Python, because you grant FDA to a specific binary path.
6. **Automation permission** (only if iMessage write is enabled): first `osascript` call to Messages triggers a TCC prompt on rtk's screen; approve via Screen Sharing.

---

## 4. Phase 1 — server skeleton + Things read (deliverable: `api.noahbres.com` answers)

All work happens in `rtk-api/` in this repo. Develop on rnn, deploy by pushing + pulling on rtk.

1. **Scaffold** with `uv init --package rtk-api` (Python `>=3.12`). Deps: `fastapi`, `uvicorn[standard]`, `fastmcp`, `pydantic`, `pyjwt[crypto]`, `httpx`, `things.py` (PyPI name `things.py`, import `things`). Dev deps: `pytest`, `pytest-asyncio`, `httpx`, `ruff`.
   Check the current `fastmcp` docs for how to get an ASGI app (`mcp.http_app(path="/mcp")`) and mount it under the secret prefix in FastAPI; the exact API has moved between versions — read the installed version's docs, don't guess.
2. **Files:**
   - `rtk-api/pyproject.toml`
   - `rtk-api/rtk-api/__init__.py` (`__version__`)
   - `rtk-api/rtk-api/config.py` — pydantic-settings, reads env + `~/.config/rtk-api/env`.
   - `rtk-api/rtk-api/registry.py` — as §2.
   - `rtk-api/rtk-api/auth.py` — as §2.
   - `rtk-api/rtk-api/app.py` — builds FastAPI app, mounts MCP, exposes `main()` for uvicorn (`rtk-api serve --port 8787`).
   - `rtk-api/rtk-api/tools/system.py` — `system.ping`, `system.version`, `system.echo`.
   - `rtk-api/rtk-api/tools/things.py` — read tools (below).
   - `rtk-api/tests/` — unit tests with a fake registry tool and auth tests (bearer ok / missing / wrong; secret path ok; fake JWT rejected).
   - `rtk-api/README.md` — how to run locally, env vars, how to add a tool (copy from §2).
3. **Things read tools** (use the `things` library directly, not `uvx things-cli`, to avoid subprocess overhead; it reads the same sqlite file):
   - `things.list(view: Literal["inbox","today","upcoming","anytime","someday","logbook"], limit=200)`
   - `things.search(query: str, limit=50)`
   - `things.get(uuid: str)` — includes notes, checklist, project/area, tags, dates.
   - `things.projects()` / `things.areas()` (with counts)
   - `things.tasks_in(project_uuid | area_uuid)`
   Return compact JSON: uuid, title, status, notes, start/deadline dates, project/area titles, tags. Strip `index`-type noise.
4. **Local run on rnn**: `cd rtk-api && uv run rtk-api serve` → `curl -H "Authorization: Bearer $T" localhost:8787/v1/things/list -d '{"view":"today"}'`. (rnn has Things too, so this is testable locally.)
5. **launchd agent on rtk** in `nixos-config/hosts/rtk/home.nix`, modelled on `things-today-tracker`:
   - Label `com.noahbres.rtk-api`; ProgramArguments = a `pkgs.writeShellScript` that does `cd ~/Developer/ararat/rtk-api && exec uv run --frozen rtk-api serve --host 127.0.0.1 --port 8787`; `EnvironmentVariables.PATH` same as the other agents; `KeepAlive.SuccessfulExit=false`; `RunAtLoad=true`; logs to `/tmp/rtk-api.log` / `/tmp/rtk-api-error.log`.
   - The start script should `set -a; source ~/.config/rtk-api/env; set +a` before exec.
   - Pin Python: `uv python install 3.12` on rtk once, and set `requires-python`/`.python-version` so `uv run` uses it (stable binary path under `~/.local/share/uv/python/…` — needed for FDA in §6).
   - Add shell aliases `restart-rtk-api` / `rtk-api-log` like the ararat ones.
   - Apply with `just switch-rtk` from `nixos-config/` (see `justfile`). Verify `launchctl list | grep rtk-api` and `curl localhost:8787/health` on rtk.
6. **Edge verification** from rnn:
   ```
   curl -s https://api.noahbres.com/health                     # 403 from Cloudflare Access (expected)
   curl -s https://api.noahbres.com/v1/things/list \
     -H "CF-Access-Client-Id: $CF_ID" -H "CF-Access-Client-Secret: $CF_SECRET" \
     -H 'content-type: application/json' -d '{"view":"today"}'   # 200 + tasks
   ```
7. **Docs**: add `rtk-api` to the "Available Capabilities" section of `CLAUDE.md` and a short section in `notes/NOTES.md` (hostnames, where secrets live, how to restart).

Acceptance: REST works through Cloudflare with the service token; MCP endpoint at `https://mcp.noahbres.com/<secret>/mcp` responds to `initialize` (test with `npx @modelcontextprotocol/inspector` or `claude mcp add --transport http rtk-api https://mcp.noahbres.com/<secret>/mcp` then `claude mcp list`).

---

## 5. Phase 2 — Things write + Claude.ai connector

1. **Things write tools** via the URL scheme (`open "things:///…"` with `subprocess.run(["open", url])`). This works from a launchd *user* agent because it runs inside Noah's GUI login session; it will silently fail if Things is not running — the URL scheme launches it, but add a retry.
   - `things.add(title, notes=None, when=None, deadline=None, tags=[], list=None, checklist=[])` → `things:///add?...`. Use `x-success`? No — instead, after `open`, poll the sqlite DB (up to ~3s) for a new to-do with that title created in the last few seconds and return its uuid.
   - `things.update(uuid, title=None, notes=None, when=None, deadline=None, tags=None, completed=None, canceled=None)` → `things:///update?auth-token=…&id=…` (`append-notes` for appending). Requires `THINGS_AUTH_TOKEN`.
   - `things.complete(uuid)` = update with `completed=true`.
   - `things.add_project(title, area=None, notes=None, when=None, todos=[])`.
   - Batch: `things.json(commands: list)` → `things:///json?auth-token=…&data=<urlencoded JSON>` for multi-item operations (see Things URL scheme docs "JSON" section).
   - There is **no delete** in the URL scheme; document that. Mark all of these `write=True` (audit log).
   - URL-encode with `urllib.parse.quote(…, safe="")`; Things is picky about `+` vs `%20`.
2. **Claude.ai connector [NOAH]**: claude.ai → Settings → Connectors → Add custom connector → URL `https://mcp.noahbres.com/<secret>/mcp`, no OAuth. Confirm tools appear and `things_list` runs from a chat.
3. Add `resources` on the MCP side only if useful (e.g. `things://today` as a resource) — optional.

Acceptance: from Claude.ai chat, "add 'buy milk' to Things today" creates the task on rtk; "what's on my today list" reads it back.

---

## 6. Phase 3 — iMessage read (+ gated write)

### 6.1 Full Disk Access [NOAH, one-time, via Screen Sharing on rtk]
TCC grants FDA per executable, and for a launchd job it's the job's main executable that counts.
The agent runs through `~/Applications/rtk-api.app` (built once by `rtk-api/launcher/build.sh`,
ad-hoc signed, identifier `com.noahbres.rtk-api`), so the grant is to a named app and survives
Python/uv upgrades. System Settings → Privacy & Security → Full Disk Access → `+` → pick
`~/Applications/rtk-api.app` → enable. Then `restart-rtk-api`. Re-grant only if the launcher is
rebuilt. (Original plan granted FDA to the python binary; superseded 2026-09-08.)
Verification: on rtk, `launchctl kickstart -k gui/$UID/com.noahbres.rtk-api` then
`curl -H "Authorization: Bearer $T" localhost:8787/v1/imessage/recent -d '{}'` → messages, not "unable to open database".

### 6.2 Read tools
Refactor `tools/imessage-query.py` into an importable module (`rtk-api/rtk-api/lib/imessage_db.py`); keep the CLI script working by importing from it (or leave the script as-is and copy the decoding logic — either is fine, but don't fork-and-diverge silently; leave a comment pointing at the canonical copy). Open chat.db read-only (`?mode=ro`, `uri=True`).
   - `imessage.chats(limit=50)` — recent chats: chat guid, display name/participants, last message date.
   - `imessage.recent(limit=50, days=7)` — newest messages across all chats.
   - `imessage.with_contact(name_or_identifier, days=90, limit=50, keyword=None)` — resolve names using the logic in `tools/contacts-search.py` (fuzzy) when the input isn't a phone/email.
   - `imessage.search(query, days=365, limit=50)`.
   - `imessage.unread()` — `is_read = 0 AND is_from_me = 0`.
   Output: `{date_utc, date_pacific, from (identifier + resolved name), is_from_me, text, chat, attachments: [types]}`.

### 6.3 Write (optional, gated)
   - `imessage.send(to: str, text: str)` — resolves `to` like above; refuses unless `IMESSAGE_WRITE_ENABLED=true` **and** the resolved identifier is in `IMESSAGE_WRITE_ALLOWLIST` (start with just Noah's own number for testing). Implementation: `osascript -e 'tell application "Messages" to send "…" to participant "+1…" of (1st account whose service type = iMessage)'` — pass text via argv/`on run argv`, never string-interpolate into the script. Messages.app gets launched on first use; the first call triggers an Automation TCC prompt on rtk's screen **[NOAH approves via Screen Sharing]**.
   - After sending, poll chat.db for ~3s to confirm the outbound row exists and return its rowid/date; otherwise return `sent: "unconfirmed"`.
   - Append to audit log. Cap text length (e.g. 2000 chars). Never allow group chats in v1.

Acceptance: `imessage.with_contact("Mom", days=30)` returns messages via `api.`; `imessage.send` refuses when disabled and works for an allowlisted number when enabled.

---

## 7. Phase 4 (optional, later) — nicer MCP auth than a secret URL

Two viable upgrades, both keep the capability URL as fallback:
- **OAuth via `fastmcp`'s Google OAuth proxy provider** (`GoogleProvider`), with Claude.ai's redirect `https://claude.ai/api/mcp/auth_callback` allowed, plus a custom check that the authenticated email == `noahbres@gmail.com` (FastMCP has no built-in allowed-emails setting; add a small middleware/`verify_token` hook). Known reports of rough edges with Claude.ai + Google provider — read the fastmcp issues before committing.
- **Cloudflare "MCP server portal" / Access for SaaS (OIDC)** in front of `mcp.noahbres.com` so Cloudflare does the OAuth and Claude.ai sees a standards-compliant AS. Check current Cloudflare docs; this was beta in 2025–2026.

Not planned: exposing the ssh hostname to Access is a separate, existing hardening TODO in `notes/NOTES.md`.

---

## 8. Conventions for implementers

- Small commits per phase, on `main` (Noah's repo; he pushes directly). Run `ruff` + `pytest` before committing. The `ship` skill handles commit/push if you're the Ararat session; otherwise plain git.
- Never commit anything from `~/.config/rtk-api/env`, `.env`, or 1Password. `rtk-api/.env*` must be in `.gitignore`.
- Deploy loop: push from rnn → on rtk `cd ~/Developer/ararat && git pull --ff-only && cd rtk-api && uv sync --frozen && restart-rtk-api` (put this in `rtk-api/deploy.sh` and mention it in README). The nix agent only needs re-applying when `home.nix` changes.
- Log to stdout in one-line JSON; launchd captures it. Include request id, principal (`cf-service-token` / `bearer` / `mcp-secret`), tool name, duration ms. Never log request bodies for write tools beyond the audit log.
- Timezone: return UTC ISO timestamps plus a `_pacific` sibling where humans will read it (Noah's home tz is America/Los_Angeles).
- If something on rtk needs a GUI click (TCC), stop and tell Noah precisely what to click; don't try to work around TCC.

## 9. Open questions for Noah (non-blocking; defaults chosen)

1. Name: the service is called `rtk-api` in this plan. Rename freely before Phase 1 starts.
2. Should REST accept the MCP secret path too, or keep the two surfaces strictly separate? Default: separate (secret only unlocks `/mcp`).
3. iMessage write allowlist initial contents — default empty (feature effectively off).
4. Do you want `mcp.noahbres.com` reachable from Claude Code on rnn as well as Claude.ai? Default yes (same URL works for both).
