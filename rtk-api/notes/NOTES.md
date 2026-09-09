<!-- Operational notes for rtk-api. Current state of the running service:
hostnames, auth, credentials, Cloudflare objects, gotchas. The design
rationale and rollout phases live in `rtk-api/notes/plan.md`; day-to-day dev usage lives in
`rtk-api/README.md`. Host-level notes (the tunnel, deploy-rs, DNS) stay in
`notes/NOTES.md`. -->

# rtk-api (api.noahbres.com)

`rtk-api` is a private, authenticated HTTP API running on `rtk` (the home Mac mini) that exposes
personal "tools" — Things 3 and iMessage today — to AI agents and scripts. Built 2026-09-08.
Design doc: `rtk-api/notes/plan.md`. Dev/usage docs: `rtk-api/README.md`. Code: `rtk-api/`.

## Architecture in one paragraph

One Python 3.12 process (FastAPI + fastmcp, managed by `uv`) listens on `127.0.0.1:8787` on rtk.
A tool is a plain function registered with `@tool("things.list")` in `rtk-api/src/rtk_api/tools/`;
the registry turns it into both a REST route (`POST /v1/things/list`, JSON kwargs in, `{"ok",
"result"|"error"}` out) and an MCP tool. New tool modules dropped into `tools/` are auto-discovered.
Write tools are flagged `write=True` and append to `~/.local/state/rtk-api/audit.log`. Tool calls
run in a threadpool so one blocked call never freezes `/health`. Ingress is the existing
Cloudflare Tunnel; nothing is bound publicly.

## Calling it

```sh
curl https://api.noahbres.com/v1/things/list \
  -H "CF-Access-Client-Id: $ID" -H "CF-Access-Client-Secret: $SECRET" \
  -H "content-type: application/json" -d '{"view":"today"}'
curl -H ... https://api.noahbres.com/v1/tools        # registry with JSON schemas
curl https://api.noahbres.com/health                  # unauthenticated (still needs Access headers at the edge)
```

Tools (20): `system.{ping,version,echo}`; `things.{list,search,get,projects,areas,tasks_in}` (read),
`things.{add,update,complete,add_project,batch}` (write, via `things:///` URL scheme; there is no
delete in the scheme); `imessage.{chats,recent,with_contact,search,unread}` (read, chat.db +
Contacts name resolution), `imessage.send` (write, **off** unless `IMESSAGE_WRITE_ENABLED=true`
AND recipient in `IMESSAGE_WRITE_ALLOWLIST`; first real send will pop an Automation prompt for
Messages.app on rtk's screen).

## Auth (three layers)

1. **Cloudflare Access** at the edge (team domain `<team>.cloudflareaccess.com`, see
   `private-data/infra-ids.md`; Zero Trust Free). Access app
   `rtk-api` on `api.noahbres.com`, one policy: Service Auth for service token `rtk-api` (expires
   2027-09-08). Anything without valid `CF-Access-Client-Id`/`-Secret` headers gets 403 before
   reaching rtk. Consequence: clients that can't set custom headers (Claude.ai's connector UI)
   can't use `api.`; that's what the future `mcp.noahbres.com` capability-URL design is for.
2. **The server authenticates** into a *principal*: a named client token in `X-Rtk-Client-Token`
   or `Authorization: Bearer` matching `RTK_API_CLIENTS` (scoped), else the `Cf-Access-Jwt-Assertion`
   JWT verified against the team's JWKS with the app's AUD **and** the JWT's `common_name` claim
   matching `CF_ACCESS_OWNER_COMMON_NAME` (principal `owner`, unscoped), else
   `Authorization: Bearer $RTK_API_BEARER_TOKEN` (`owner`, unscoped, only reachable from localhost
   now), else the path starts with `/$RTK_API_MCP_SECRET/` (principal `mcp`, unscoped — checked
   first of all). Failed auth → 401. The client token is deliberately checked **before** the CF JWT:
   an external client presents both (Access headers to pass the edge, client token for identity),
   and the scoped identity must win. The `common_name` check (added 2026-09-08, after the instinct
   rollout below surfaced the gap) matters independently of that ordering: a scoped client's own
   Access service token produces an equally "valid" JWT, so without pinning `owner` to one specific
   `common_name`, a client that simply omitted its client token would fall through to unscoped owner.
3. **The server authorizes** the principal's fnmatch allowlist against the tool name. Denied → 403
   (not 401 — the caller *is* authenticated). `GET /v1/tools` is filtered to the grant, and an
   unknown tool outside the grant returns 403 rather than 404, so a scoped client can't enumerate
   the registry.

**Why the client token and not the Access JWT's `common_name` claim.** Service-token JWTs are
believed to carry the client id in `common_name`, which would let the server distinguish tokens
without a second credential — but the only way to confirm the claim's shape is to patch `auth.py`
on a live rtk (the JWT exists only in flight; nothing echoes headers). Not worth the risk when a
client token we control works regardless. Two header spellings are accepted because Cloudflare
Access has a history of being fussy about forwarding `Authorization` to the origin;
`X-Rtk-Client-Token` passes through cleanly and is preferred. Which one actually fires is visible
as `principal` in the access log.

**Scoped clients.** `RTK_API_CLIENTS` is a JSON map, name → `{token, allow, require_approval}`,
with fnmatch patterns over tool names. Malformed JSON fails closed (no clients, error logged)
rather than crashing the server, so a bad edit can't lock the owner out. Full docs:
`rtk-api/README.md`.

### Client: `instinct` (credentials minted, not yet live)

`instinct` is Noah's iMessage agent, running **in the cloud** (not on rtk), so it must come through
Cloudflare Access. Intended grant: all of `things.*` (read + write, ungated) and `imessage.*` reads,
with `imessage.send` gated behind approval. It needs **its own Access service token** — not for
identity (the client token does that) but for revocation: a separate token can be cut at the edge
without invalidating Noah's own access, and it keeps the Cloudflare audit log legible.

**Credentials created 2026-09-08** (both in 1Password, Private vault):

| Item | Header | Purpose |
|---|---|---|
| `rtk-api cloudflare access service token - instinct` | `CF-Access-Client-Id` / `-Secret` | edge: get past Cloudflare Access |
| `rtk-api client token - instinct` | `X-Rtk-Client-Token` | identity: principal name + tool allowlist |

Cloudflare service token `instinct` (token id and client id in `private-data/infra-ids.md`),
expires **2027-09-08**. Given its **own** Access policy
(`instinct service token`, policy id in `private-data/infra-ids.md`) on the `rtk-api` app rather
than being added to the existing policy's includes — a separate policy means revoking instinct is
deleting one object, with zero risk to Noah's own token. The two `non_identity` policies are OR'd;
verified 2026-09-08 that both tokens get a 200 on `/health` and that no credentials still gets 403.

Incidental: the service-token client id ends in `.access`, confirming the `common_name` shape the
design deliberately chose not to depend on.

**Deployed and verified live, 2026-09-08:**

1. Committed code (already on `main` as of `e567861`) deployed via `rtk-api/deploy.sh --remote`.
2. `RTK_API_CLIENTS` added to `~/.config/rtk-api/env` on rtk with instinct's grant (the exact JSON
   in `README.md`'s example), agent restarted.
3. Verified against the live `api.noahbres.com`: Access headers + `X-Rtk-Client-Token` on
   `/v1/things/list` → 200; the same on `/v1/imessage/send` → 403 "requires approval, and the
   approval backend is not implemented yet".

**Gap found during step 3, then closed same day.** Access headers *alone* (no client token) still
returned 200 with the full task list — confirmed empirically, matching what was flagged above before
deploy. This wasn't a deploy-order problem: `RTK_API_CLIENTS` was already configured and the scoped
path worked correctly; the JWT path (`Cf-Access-Jwt-Assertion` → owner) simply doesn't care *which*
service token authenticated at the edge, only that *some* valid one did. Any scoped client's own
Access credentials, presented without its client token, would fall through to unscoped owner —
silently defeating the grant instinct was supposed to get.

Fixed by pinning the JWT path to one `common_name` (`CF_ACCESS_OWNER_COMMON_NAME`, set to the
owner's own service token's client id, `<client-id>.access` — value in `private-data/infra-ids.md`) — see
[Auth (three layers)](#auth-three-layers) above and `README.md`'s Auth section. Re-verified after
this fix: Access-headers-only on `/v1/things/list` now 401s; Access + client token still 200s as
before. Tests added in `tests/test_auth.py` (`test_cf_jwt_owner_common_name_grants_owner`,
`test_cf_jwt_non_owner_common_name_denied`, `test_cf_jwt_unconfigured_owner_common_name_denies_everyone`).

`imessage.send` is still independently blocked by `IMESSAGE_WRITE_ENABLED=false`.

### Planned: approval queue (`require_approval`) — designed, not built

`require_approval` is honored in code today only as a refusal: it is checked *before* `allow` and
overrides it, so a listed tool returns 403 instead of running unattended. The intended mechanism:

- `imessage.send` → server persists a pending approval and returns `202 {"ok": false, "status":
  "pending", "approval_id": ...}`; the caller polls. **Not** a blocking wait — the threadpool exists
  precisely so no call can freeze `/health`, and a cloud agent's HTTP client would time out before
  Noah reached his phone anyway.
- Notification goes out over a **dedicated second Telegram bot**, polled from inside the rtk-api
  process. Telegram allows exactly one `getUpdates` consumer per token (see
  `telegram-plugin/server.ts:79`, and the 409 handling at :1101), so rtk-api cannot receive button
  callbacks on ararat's bot — ararat's poller owns them. It could *send* through ararat's bot, but
  ararat's poller only runs while that Claude session runs, and restarting ararat is routine; a bot
  polled by rtk-api is up exactly when rtk-api is up. Rejected alternative: patching the plugin's
  `callback_query` handler to forward an `rtk:` prefix to localhost — same availability coupling,
  plus it forks a vendored upstream.
- The inline-keyboard Allow/Deny UX already exists in the plugin (`server.ts:436-452`, callback at
  :736) and is worth copying — **including** its sender check at :746. Authorize on `ctx.from.id`,
  not `chat_id`: Telegram bots are discoverable by username, so an unauthenticated Allow button is
  a human gate any stranger can press.
- Persist the pending queue to `~/.local/state/rtk-api/` (where `audit.log` already lives) and
  reconcile on boot. rtk-api is a daemon that gets redeployed; an in-memory map would silently drop
  every in-flight approval on restart.
- Keep `IMESSAGE_WRITE_ALLOWLIST` enforced *underneath* approvals. Approval decides "this message";
  the allowlist decides "this recipient is ever reachable at all". Both, not either.
- Resolve the recipient through `contacts.lookup_name` when composing the prompt. Approving
  "send to +1555…" tells you nothing; "send to Kirill" tells you everything.
- **Do the Messages.app Automation grant first, as a separate step over Screen Sharing.** The first
  real send pops a TCC prompt on rtk's screen, and TCC prompts block silently under launchd. Wiring
  approvals and flipping `IMESSAGE_WRITE_ENABLED` in one change stacks two invisible hangs.

## Credentials index (all in 1Password, Private vault)

| Item | What |
|---|---|
| `rtk-api` | bearer token + MCP secret (mirror of `~/.config/rtk-api/env` on rtk) |
| `rtk-api cloudflare access service token` | client id + secret for the Access headers |
| `cloudflare-rtk-api-token` | scoped Cloudflare API token (Tunnel/Access/DNS/WAF on noahbres.com), expires 2026-10-08 |
| `cloudflare-token-creator` | Cloudflare token that can mint other tokens |
| `rtk-api cloudflare access service token - instinct` | instinct's edge credential (client id + secret) |
| `rtk-api client token - instinct` | instinct's identity credential (`X-Rtk-Client-Token`) |
| `Things` | Things auth token also lives in `~/Developer/ararat/.env` on rtk |

Runtime secrets file on rtk: `~/.config/rtk-api/env` (`chmod 600`, never in git):
`RTK_API_BEARER_TOKEN`, `RTK_API_MCP_SECRET`, `CF_ACCESS_TEAM_DOMAIN`, `CF_ACCESS_AUD`,
`THINGS_AUTH_TOKEN`, `IMESSAGE_WRITE_ENABLED`, `IMESSAGE_WRITE_ALLOWLIST`, and optionally
`RTK_API_CLIENTS`.

## Cloudflare objects

Account id, zone id, and every object id below live in `private-data/infra-ids.md` (gitignored).

- Tunnel `ararat` (id in `infra-ids.md`), remote-managed by token in
  `/etc/cloudflared/tunnel-token` on rtk. Ingress: `ssh-rtk.noahbres.com -> ssh://localhost:22`,
  `api.noahbres.com -> http://localhost:8787`. Public hostnames live in the Zero Trust dashboard /
  API, not in a local `config.yml`.
- DNS: proxied CNAME `api` -> `<tunnel-id>.cfargotunnel.com` (exact target in `infra-ids.md`).
- Access app `rtk-api` (app id in `infra-ids.md`) on `api.noahbres.com`, with two
  `non_identity` policies, one per service token: `rtk-api service token` (expires 2027-09-08) and
  `instinct service token` (expires 2027-09-08); token ids and policy ids in `infra-ids.md`.
  One policy per client, so each can
  be revoked independently. `ssh-rtk.noahbres.com` still has **no** Access policy (SSH keys are the
  only guard there).
- Not created: `mcp.noahbres.com`, WAF rate-limit rules.

## Running on rtk

- launchd agent `com.noahbres.rtk-api` (nix: `nixos-config/hosts/rtk/home.nix`). It runs
  `~/Applications/rtk-api.app/Contents/MacOS/rtk-api <start-script>`; the start script sources the
  env file and execs `uv run --frozen rtk-api serve`. Logs `/tmp/rtk-api.log`,
  `/tmp/rtk-api-error.log`. Aliases on rtk: `restart-rtk-api`, `kill-rtk-api`, `rtk-api-log`.
- **`rtk-api.app` is a tiny C launcher** (`rtk-api/launcher/`, built once on rtk with
  `launcher/build.sh`, ad-hoc signed, bundle id `com.noahbres.rtk-api`). It spawns the real command
  as a child and forwards signals. Purpose: macOS attributes TCC permissions to a launchd job's
  main executable, so this makes the prompts and the Full Disk Access list say "rtk-api" instead
  of "python3.12"/"uvx". **Full Disk Access is granted to it** (covers Things' group container,
  chat.db, AddressBook). Rebuilding the launcher (`--force`) changes its signature and requires
  re-granting.
- Python is uv-managed CPython 3.12.13 (`~/.local/share/uv/python/...`), pinned in
  `rtk-api/.python-version`. Not nix-managed on purpose (nix store paths churn on every rebuild).
- **Code deploy** (no root, agent-runnable): `rtk-api/deploy.sh --remote` from rnn — pulls,
  `uv sync --frozen`, kickstarts the agent, polls `/health`. Nix deploy only when `home.nix` changes.

## Gotchas learned

- **`RTK_API_CLIENTS` changes need a process restart.** `get_settings` is `lru_cache`d and
  `Settings.clients` is a `cached_property`, so the env file is parsed once at start:
  `launchctl kickstart -k gui/$UID/com.noahbres.rtk-api`.
- **A scoped client is only as distinct as its token.** The principal name comes from the matching
  key in `RTK_API_CLIENTS`, so two agents sharing one token are indistinguishable in the access log
  and the audit log. One token per agent. Cloudflare service tokens have no bearing on the
  principal — identity is the client token alone.
- **Minting a Cloudflare service token returns the client secret exactly once.** Create it and
  write it to 1Password in a single step; there is no way to read it back afterwards.

- **TCC prompts block silently under launchd.** First access to another app's container pops
  "rtk-api would like to access data from other apps" on rtk's screen and the syscall blocks until
  clicked. `things` is imported lazily and tools run in a threadpool so the server still boots;
  the affected call just hangs. Approve via Screen Sharing (or FDA covers it).
- SSH sessions have Full Disk Access implicitly, so "it works over ssh" proves nothing about
  launchd.
- `things.search` only returns incomplete items by default.
- The service-token JWT check needs outbound HTTPS from rtk to
  `<team>.cloudflareaccess.com/cdn-cgi/access/certs` (team domain in `private-data/infra-ids.md`;
  cached after first fetch).

## Not done / future

- `mcp.noahbres.com` for Claude.ai (design in plan §2/§5/§7): same process, capability URL, no
  Access, plus a Cloudflare rate-limit rule. Code path exists behind `RTK_API_MCP_SECRET`; untested
  against Claude.ai.
- iMessage send enablement (flip the two env vars, approve the Automation prompt).
- Access policy on `ssh-rtk.noahbres.com`.
- The approval queue for `imessage.send` (designed above, not built).
- `things-today-tracker` still runs as bare `/usr/bin/python3` and triggers its own "uvx" TCC
  prompts; could be routed through the same launcher.
### Bootstrap discovery: `GET /v1/help` (2026-09-08)

Added so a brand-new agent (instinct, or any future scoped client) can be handed just its two
credentials and a URL — `https://api.noahbres.com/v1/help` — and self-discover the rest: response
envelope, how to call a tool, and one section per tool actually in its grant (description + example
curl), generated fresh per request from the same filter `/v1/tools` uses so the two can't drift.
Tools outside the caller's `allow` are invisible on both endpoints, same as before. Gated
(`require_approval`) tools that are otherwise in-grant get their own "Gated" section instead of
being silently omitted, so an agent understands *why* it can't call them rather than guessing at a
403. Default is raw `text/markdown`; `Accept: application/json` wraps it in the usual envelope.
Deployed via `rtk-api/deploy.sh --remote` and verified live against `api.noahbres.com`.

**instinct is fully live, 2026-09-08.** `RTK_API_CLIENTS` on rtk already had instinct's grant
configured (matching the token in 1Password); the agent just needed a restart
(`launchctl kickstart -k gui/$UID/com.noahbres.rtk-api`) to pick it up after the settings cache was
last populated. Verified against the public URL: `X-Rtk-Client-Token` (instinct's) + Cloudflare
Access headers → principal `instinct`; `/v1/help` renders instinct's scoped doc; `/v1/tools`
returns exactly its grant (`things.*` + the five iMessage reads, no `imessage.send`, no
`system.*`). Remaining step for instinct itself: point it at `https://api.noahbres.com/v1/help`
with its two credentials as its starting point (its own dedicated Cloudflare Access service token,
plus its `X-Rtk-Client-Token` — both in 1Password, see the credentials table above).

### Fairbridge group-chat tools (2026-09-09)

`fairbridge.info` / `fairbridge.read` / `fairbridge.send` — one iMessage group chat (Daniel Yim,
Andrew Motz, Niranjan Sahi), exposed to instinct so it can read and post there unattended.
Added rather than widening `imessage.send`, which is the wrong shape for this: it refuses group
chats, its allowlist is per-handle, and it's gated behind the approval queue that doesn't exist.

**The scoping is structural.** `fairbridge.send` takes `text` and no recipient; `fairbridge.read`
takes no chat selector. There is no argument an agent can pass to reach a different conversation —
the destination is `FAIRBRIDGE_PARTICIPANTS` in rtk's env file, outside the request.

**Chats are addressed by participant set, never by guid.** Modern iMessage group-chat guids are
*device-local* — verified 2026-09-09, the same three-person chat is `any;+;98f4406b…` on Noah's
laptop (289 msgs) and `any;+;47ca19e8…` on rtk (291 msgs). A guid
hardcoded from either machine would resolve to nothing on the other. `find_chat_by_participants`
requires an exact set match in both directions: chat.db holds near-miss groups with the same three
people plus a fourth, and those are genuinely different conversations. Among duplicate rows for the
same chat, most-recently-active wins.

**Name collision worth remembering.** The chat *displaying* as "Fairbridge " is a different group —
Andrew, Niranjan, and a fourth number not in Noah's contacts, with Daniel Yim absent
(1623 msgs on rtk). Noah confirmed
2026-09-09 that "fairbridge" here means the three people he named, not the similarly-named chat.
The targeted chat has no display name, which is why `fairbridge.info` reports its guid as the name.

**AppleScript gotcha.** Messages parses `text chat id "..."` as `text of (chat id "...")` and fails
with `-1728`; the bare `chat id "..."` form works. Guid and text go through `on run argv`, never
interpolated into the script. Confirmation polls chat.db scoped to the chat guid — the 1:1
`find_recent_outbound` joins through a single handle and can't see a group send.

**Automation TCC was NOT granted on rtk — that check was wrong.** Enumerating `text chats` over
SSH returned data, and it was read as proof the service could drive Messages. It isn't: TCC keys
Automation grants by *responsible process*, and an SSH command's is `sshd-keygen-wrapper`, which
does have the grant. The launchd service is a different client (`com.noahbres.rtk-api`) with its
own entry. See the correction below.

`FAIRBRIDGE_PARTICIPANTS` has no default in code on purpose — three real phone numbers, public
repo. It lives only in `~/.config/rtk-api/env` on rtk. Unset, the tools refuse.

Incidental refactor: `_enrich_with_sender_names`, duplicated between the two tool modules, is now
`contacts.annotate_senders`.

**Confirmation-timestamp bug, found and fixed 2026-09-09.** Both send tools captured
`sent_at = datetime.now(UTC)` *after* `subprocess.run` returned. Messages writes the chat.db row
*during* the osascript call, so that timestamp sits past the row's own date and the
`m.date >= since_ns` filter excluded the very message being confirmed — every successful send would
have polled for 3s and reported `"unconfirmed"`. Fixed in both `imessage.send` and
`fairbridge.send` by capturing `sent_at` before the send. This had never surfaced because
`imessage.send` has never run for real (`IMESSAGE_WRITE_ENABLED=false` since it shipped) —
`fairbridge.send` is the **first real send this codebase has ever done**. Regression test
(`test_send_confirms_via_the_real_chat_db_query`) inserts the row mid-call against the real sqlite
query rather than stubbing the lookup, and was verified to fail against the old ordering.

**Deployed and verified live, 2026-09-09.** `rtk-api/deploy.sh --remote`; `~/.config/rtk-api/env`
on rtk gained `FAIRBRIDGE_PARTICIPANTS`, and instinct's
`RTK_API_CLIENTS` grant gained the three `fairbridge.*` names (env backed up to
`env.bak-20260909-013302` first — `_parse_clients` fails closed, so a botched edit to that
single-line JSON would silently drop *all* of instinct's tools while `/health` stayed green).
Verified through the public URL with instinct's own credentials, not `/health`: `/v1/tools`
returns the three new tools *and* still the full `things.*` + five iMessage reads;
`/v1/fairbridge/info` resolves to rtk's own guid for the chat with the right three participants
(291 msgs), confirming the participant-set resolution works across machines; `/v1/fairbridge/read`
returns that chat only.

**Still unverified: actual delivery.** `chat id "..."` was confirmed to *resolve* on rtk (returns
the 3 participants), but `send theText to chat id theChatId` can only be tested by sending a real
message to three real people — pending Noah's go-ahead on the wording. A `"sent": "confirmed"`
response is also the empirical check on the timestamp fix above.

**Kill switch removed, 2026-09-09** (Noah: "i dont really care about these flags. we'll just
litter it"). `FAIRBRIDGE_WRITE_ENABLED` is gone from the code and from rtk's env. The reasoning
holds up: `IMESSAGE_WRITE_ENABLED` earns its keep because `imessage.send` can reach any allowlisted
handle, so a global off switch is meaningful. `fairbridge.send` has one hardcoded destination and
no recipient parameter — the scoping *is* the safety property, and `RTK_API_CLIENTS` decides who
may call it. A second gate per feature is env-file clutter that would grow with every tool.
Unsetting `FAIRBRIDGE_PARTICIPANTS` still disables sending as a side effect (no chat resolves), so
there is a rollback lever without a dedicated flag.

### `require_approval` now grants and audits (2026-09-09)

Noah: *"please just return true and pass it in. just ensure everything requiring approval is
logged."* `require_approval` used to return a 403 for any matching tool. It now routes through
`rtk_api/approvals.py::request_approval`, which returns True and records the call.

The old behaviour made the list unusable rather than safe: with no queue to approve *through*,
listing a tool meant "never", and its 403 read identically to a scoping mistake. It is now the
answer to "which calls do I want a record of" instead of "which calls are blocked".

What changed concretely:

- **Ordering.** The check moved *after* `allow` and after the body parse. It used to run first and
  override `allow`; it now only applies to calls that are already permitted, so listing a tool
  under `require_approval` can never widen a grant. Parsing first means the audit record carries
  the real arguments. Pinned by `test_approval_does_not_substitute_for_allow`.
- **Audit.** `audit_log` grew an `event` field — `"write"` (default, unchanged shape otherwise)
  and `"approval.auto_granted"`. A gated write emits both lines. Audit happens *before* the call,
  so an attempt is recorded even if the tool then fails.
- **Discovery.** These tools are no longer subtracted from `/v1/tools` or `/v1/help` — they are
  callable, so hiding them misrepresented the grant. `/v1/help`'s "Gated" section became
  "Audited", listing them alongside their normal callable entries.

**Live consequence:** instinct's grant still omits `imessage.send` from `allow`, so it remains
refused — on scope now, not approval. Nothing was silently un-gated by this change. `imessage.send`
is additionally still behind `IMESSAGE_WRITE_ENABLED=false` and its recipient allowlist on rtk.

The seam for the real queue is one function: make `request_approval` consult it and return the
decision. A False return already produces a 403 (`"was not approved"`); no caller changes.

### Automation TCC: the first real sends failed (2026-09-09)

instinct called `fairbridge.send` twice at 08:49 UTC. Both took **exactly 15070ms** — the
`subprocess.run(timeout=15)` — and returned a generic 500. Neither message reached the chat.

Cause: `TCC.db` has `com.noahbres.rtk-api | kTCCServiceAppleEvents | com.apple.MobileSMS` with
`auth_value=0` (**denied**), `last_modified` 08:51:36 — i.e. the modal Automation prompt appeared
on rtk's screen at 08:49, nobody was there, and macOS recorded a denial two minutes later.

**The earlier "already granted" check was measuring the wrong thing.** Automation grants are keyed
by responsible process. Enumerating `text chats` over SSH exercised `sshd-keygen-wrapper`
(`auth_value=2`), not the service. The TCC table makes the distinction plain:

```
/usr/bin/python3              | com.apple.MobileSMS | 2   <- things-today-tracker
/usr/libexec/sshd-keygen-wrapper | com.apple.MobileSMS | 2   <- ad-hoc ssh, what got tested
com.noahbres.trash-reminder   | com.apple.MobileSMS | 2
com.noahbres.rtk-api          | com.apple.MobileSMS | 0   <- the service, DENIED
```

Generalisable: **an ad-hoc SSH test can never establish what a launchd service is permitted to do.**
Check `TCC.db` for the service's own bundle id, or exercise the service itself.

**Fix (needs Noah at rtk's screen):** System Settings > Privacy & Security > Automation > rtk-api >
enable Messages. If the toggle is absent, `tccutil reset AppleEvents com.noahbres.rtk-api` clears
the recorded denial so the prompt fires again on the next send — which must be triggered while
watching over Screen Sharing, since it times back into a denial if unanswered.

**Also fixed in code:** a hung `osascript` (timeout) or an error containing `-1743` /
"Not authorized to send Apple events" now raises `PermissionError` carrying the exact remediation
steps, surfaced to the caller as a 403 — instead of a 15-second wait ending in an opaque
`{"error": "internal error"}`. Applied to `imessage.send` as well, which has the same path.

### ~~Root cause: rtk is running a stale generation without the TCC launcher~~ — WRONG, see below (2026-09-09)

After Noah granted Automation (`auth_value=2` at 01:57:28 local), the next send **still** hung 15s
and TCC flipped the entry straight back to `0` at 01:59:59. An existing allow does not re-prompt —
unless macOS can't match the grant to the requesting process.

It can't, because **the launcher isn't in the loop on rtk.** The live plist is:

```
ProgramArguments = ["/nix/store/d1irh9…-rtk-api/bin/rtk-api"]
```

while `nixos-config/hosts/rtk/home.nix` (committed, `nix eval` verified) produces:

```
/Users/noah/Applications/rtk-api.app/Contents/MacOS/rtk-api
/nix/store/0cjgm6…-rtk-api-start
```

`~/Library/LaunchAgents/com.noahbres.rtk-api.plist` is dated Sep 8 01:24 — rtk is on an older
home-manager generation, from before the launcher was wired in. So the service runs the venv
python directly. `AssociatedBundleIdentifiers` is enough to *label* the job (Login Items, and the
bundle-id row TCC creates), but it does not make an unsigned python the responsible process, so the
grant never sticks and every send re-prompts into a timeout.

`rtk-api.app` itself is fine — built, ad-hoc signed, "satisfies its Designated Requirement",
smoke-tested on rtk. It is simply not being used.

**Fix: deploy.** Needs Noah's sudo (`just build-deploy-rtk`), then:

1. `tccutil reset AppleEvents com.noahbres.rtk-api` — clears the recorded denial.
2. Trigger one send while watching rtk over Screen Sharing, click Allow.
3. Confirm `readlink /nix/var/nix/profiles/system` advanced past `system-50-link` (silent rollbacks
   have happened before).

Worth re-checking FDA after the switch: reads work today under the current attribution, and the
responsible process is changing.

**Generalisable:** `AssociatedBundleIdentifiers` is cosmetic for TCC purposes. Only the actual
`ProgramArguments[0]` binary establishes the responsible process — and a committed nix config
proves nothing about a host until the generation is deployed.

### Correction: the launcher was wired in correctly all along (2026-09-09)

The section above is wrong. It read the live plist's `ProgramArguments[0]` —
`/nix/store/d1irh9…-rtk-api/bin/rtk-api` — as "the nix start script, no launcher". That path is a
**two-line wrapper script**, and its contents were never checked:

```sh
#!/bin/sh
exec /Users/noah/Applications/rtk-api.app/Contents/MacOS/rtk-api /nix/store/0cjgm6…-rtk-api-start
```

It execs the launcher. The live process tree confirms it — `pid 1002` is
`~/Applications/rtk-api.app/Contents/MacOS/rtk-api`, parented directly to launchd, with uv and
python as its descendants. The launcher *is* the responsible process, and has been since
`90204cf` (Sep 8 01:05). The plist's Sep 8 01:24 mtime and its reference to the same
`0cjgm6…-rtk-api-start` that `nix eval` produces today both say the deployed config was already
current; the Sep 9 deploy was a no-op for this agent.

**Lesson: a nix store path in a plist is not self-describing.** `bin/rtk-api` inside a package
called `rtk-api` looked like the service itself; it was a wrapper around the launcher. `cat` the
path before concluding anything from its name.

**The actual remaining problem is how the grant was made.** Toggling rtk-api on in System Settings
> Privacy & Security > Automation set `auth_value=2` for the bundle-id row, but the next send still
re-prompted and timed back out into a denial — the entry TCC had didn't match the process actually
asking. Answering the *live prompt* records the entry against the requesting process's own code
identity, which the toggle can't do. That prompt has never been answered: the first two attempts
timed out unattended, and every attempt since hit the stale denial.

So the remaining step is unchanged from the start, just for a different reason than the section
above claimed: with the entry now reset, trigger one send **while watching rtk over Screen
Sharing** and click Allow.
