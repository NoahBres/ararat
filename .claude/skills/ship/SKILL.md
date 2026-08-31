---
name: ship
description: Commit and push all unstaged git changes to the current branch. Use this skill whenever Noah says "ship", "ship it", "commit and push", "push changes", "push this", or any variation meaning "commit my work and push it". Always use this skill — do NOT run git commands manually when Noah asks to ship. The skill handles secrets scanning, atomic commit splitting, and push with Telegram updates throughout.
---

# Ship

Commit all unstaged changes as atomic commits and push to the remote branch.

## Steps

### 1. Acknowledge via Telegram immediately

Before any git commands, send a Telegram reply acknowledging the task.

### 2. Gather the diff

```sh
git status
git diff
git diff --staged
```

If there are no changes (clean working tree and no staged files), reply via Telegram: "Nothing to ship — working tree is clean." and stop.

### 3. Secrets scan (Sonnet subagent)

Spawn a **Sonnet subagent** with the full diff output and this task:

> Scan this git diff for secrets: API keys, tokens, passwords, hardcoded credentials, private keys, or any sensitive values that should not be committed. Look for patterns like hardcoded tokens in example commands, `.env`-style values embedded in docs, base64-encoded blobs that look like keys, etc. Report: (a) CLEAN if nothing found, or (b) list each finding with file, line, and what looks suspicious. Be thorough but don't flag obviously safe things like placeholder names (e.g. `$VAR_NAME`, `YOUR_KEY_HERE`).

**If the subagent reports any findings:**
- Reply via Telegram with the specific findings
- Abort — do not commit anything
- Tell Noah to fix or redact before shipping again

**If clean:** continue.

### 4. Plan atomic commits

Look at the diff and group changes by logical subject. Each commit should represent one coherent unit of work. Think about what changed and why — file groupings should follow the same boundary as "why did these files change together?"

Examples of good groupings:
- A new feature's implementation files together
- A config change separate from code changes  
- Tracker file updates (e.g. caffeine-tracker.md) separate from CLAUDE.md updates
- Multiple unrelated bug fixes as separate commits

Don't split a single logical change across multiple commits, and don't bundle unrelated changes into one.

### 5. Stage and commit each group

For each logical group:

```sh
git add <specific files>
git commit -m "$(cat <<'EOF'
<subject line>

<optional body if needed>

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>
EOF
)"
```

Commit message style:
- Subject line: imperative mood, under 72 chars, no period
- Body: only if context adds value beyond the subject
- Focus on "why", not "what" (the diff shows what)

Never use `git add -A` or `git add .` — always stage specific files by name to avoid accidentally including unintended files.

### 6. Push

```sh
git push
```

### 7. Reply via Telegram

Send a summary: how many commits, what each one was, and confirm pushed. Keep it concise — one line per commit is enough.

## Secrets scan detail

The subagent prompt should include the raw `git diff` output. Common patterns to flag:
- Strings matching `sk-...`, `ghp_...`, `xoxb-...`, `AKIA...` (AWS), `ya29....` (Google OAuth)
- Lines like `token = "abc123xyz"` with real-looking values (not placeholders)
- Private key headers (`-----BEGIN RSA PRIVATE KEY-----`)
- Hardcoded passwords in connection strings
- API keys in example curl commands with real values

If `git diff` is very large, the subagent can work from a summary — include file names and any lines containing `key`, `token`, `secret`, `password`, `credential`, `auth`.
