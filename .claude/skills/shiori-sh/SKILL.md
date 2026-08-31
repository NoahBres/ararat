---
name: shiori-sh
description: Save and search Shiori bookmarks. Use this skill whenever Noah wants to save a URL/link as a bookmark, add something to his bookmarks, or search/find/look up his saved bookmarks. Trigger phrases include "save this bookmark", "bookmark this", "add to bookmarks", "save this link", "search my bookmarks", "find my bookmark for X", "look up my bookmarks", "what did I bookmark about X", "pull up my bookmarks". Always use this skill for any task involving Shiori or his personal link library.
---

# Shiori Bookmarks

Manage Noah's Shiori link library via `bunx @shiori-sh/cli`.

## Authentication

Credentials are stored in `~/.shiori/config.json`. Generate an API key at shiori.sh/home → Settings, then:

```sh
bunx @shiori-sh/cli auth        # interactive setup
bunx @shiori-sh/cli auth --status   # check auth status
bunx @shiori-sh/cli auth --logout   # remove credentials
```

Alternatively, set `SHIORI_API_KEY` as an environment variable (overrides the config file).
For a custom server: set `SHIORI_BASE_URL` (default: `https://www.shiori.sh`).

## Saving a bookmark

```sh
# Basic save
bunx @shiori-sh/cli save <url>

# With custom title
bunx @shiori-sh/cli save <url> --title "My Title"

# Mark as read immediately
bunx @shiori-sh/cli save <url> --read

# Get JSON output (useful to capture the ID for tagging)
bunx @shiori-sh/cli save <url> --title "My Title" --json
```

After saving, if the user mentioned tags, set them using the link ID from the JSON output:

```sh
bunx @shiori-sh/cli tags set <link-id> tag1,tag2
```

## Searching bookmarks

```sh
# Basic search
bunx @shiori-sh/cli search "query"

# Limit results
bunx @shiori-sh/cli search "query" --limit 10

# Filter by recency (e.g. last week)
bunx @shiori-sh/cli search "query" --since 7d

# JSON output
bunx @shiori-sh/cli search "query" --json
```

## Listing recent bookmarks

```sh
bunx @shiori-sh/cli list                      # newest first
bunx @shiori-sh/cli list --read unread        # unread only
bunx @shiori-sh/cli list --tag "design"       # filter by tag
bunx @shiori-sh/cli list --since 2w           # last 2 weeks
bunx @shiori-sh/cli list --content --json     # with full markdown content
```

## Getting full content

```sh
bunx @shiori-sh/cli get <id>       # link details + content
bunx @shiori-sh/cli content <id>   # raw markdown only (good for summarizing)
```

## Updating a bookmark

```sh
bunx @shiori-sh/cli update <id> --read         # mark as read
bunx @shiori-sh/cli update <id> --unread       # mark as unread
bunx @shiori-sh/cli update <id> --title "..."  # rename
bunx @shiori-sh/cli update --ids id1,id2 --read  # bulk mark read
```

## Deleting

```sh
bunx @shiori-sh/cli delete <id>               # move to trash
bunx @shiori-sh/cli delete --ids id1,id2      # bulk trash
bunx @shiori-sh/cli trash                     # list trashed links
bunx @shiori-sh/cli trash --empty             # permanently delete all trash
```

## Response format

When saving: confirm what was saved (title + URL), mention the ID, and note any tags set.

When searching/listing: present results as a concise list — title, URL, and any tags. Keep it scannable. If there are no results, say so and suggest a broader query.

If auth fails, tell Noah to run `bunx @shiori-sh/cli auth` in his terminal.
