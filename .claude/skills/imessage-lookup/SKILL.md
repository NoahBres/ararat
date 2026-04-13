---
name: imessage-lookup
description: Look up iMessages by contact name. Use this skill whenever the user asks about something someone sent them over iMessage, wants to find a message from a specific person, or asks questions like "what did X send me", "find the address Kirill sent", "did Y message me about Z", "what was the link Alex shared", "check my messages from Mom", etc. Always use this skill for iMessage/SMS lookups — don't try to query chat.db directly without it.
---

# iMessage Lookup

Look up iMessages by contact name, handling name-to-identifier resolution and disambiguation.

## Step 1: Resolve the contact name

Query the AddressBook database to find phone numbers / emails for the contact name the user mentioned. There may be multiple AddressBook database files — glob for all of them.

```python
import sqlite3, glob, sys

name = "CONTACT_NAME_HERE"  # replace with what the user said

dbs = glob.glob(
    f"{__import__('os').path.expanduser('~')}/Library/Application Support/AddressBook/Sources/*/AddressBook-v22.abcddb"
)

results = []
for db_path in dbs:
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        rows = conn.execute("""
            SELECT
                r.ZFIRSTNAME, r.ZLASTNAME, r.ZORGANIZATION,
                p.ZFULLNUMBER,
                e.ZADDRESS
            FROM ZABCDRECORD r
            LEFT JOIN ZABCDPHONENUMBER p ON p.ZOWNER = r.Z_PK
            LEFT JOIN ZABCDEMAILADDRESS e ON e.ZOWNER = r.Z_PK
            WHERE
                r.ZFIRSTNAME LIKE :q
                OR r.ZLASTNAME LIKE :q
                OR r.ZORGANIZATION LIKE :q
                OR (r.ZFIRSTNAME || ' ' || r.ZLASTNAME) LIKE :q
        """, {"q": f"%{name}%"}).fetchall()
        conn.close()
        for row in rows:
            fname, lname, org, phone, email = row
            display = " ".join(filter(None, [fname, lname])) or org or "Unknown"
            if phone or email:
                results.append({"name": display, "phone": phone, "email": email})
    except Exception as ex:
        pass

# Deduplicate by (name, phone, email)
seen = set()
unique = []
for r in results:
    key = (r["name"], r["phone"], r["email"])
    if key not in seen:
        seen.add(key)
        unique.append(r)

print(unique)
```

Run this with the Bash tool (via `python3 -c "..."` or a temp file).

## Step 2: Handle the results

**No matches:** `imessage-query.py` has built-in fallbacks — proceed directly to Step 3 using the raw name as the identifier. The script will automatically:
1. Re-query the local AddressBook SQLite
2. Fall back to Contacts.app via osascript (reads iCloud contacts not in local SQLite)
3. Fall back to group chat display names in chat.db

```bash
python3 ~/Developer/ararat/tools/imessage-query.py "CONTACT_NAME" ["KEYWORD"] --days 90
```

If this also returns nothing, the contact simply isn't resolvable by name on this machine — ask the user for the phone number or email directly.

**One contact, one identifier:** Proceed directly to Step 3.

**One contact, multiple phone numbers / emails:** Use the most likely one (mobile > home > work for phone; first email otherwise). If genuinely ambiguous, ask the user which to use.

**Multiple distinct contacts:** List them clearly and ask which one:

> I found a few contacts matching "Kirill":
> 1. Kirill Ivanov (+1 415-555-0101)
> 2. Kirill Petrov (kirill@example.com)
>
> Which one did you mean?

Wait for the user's answer before continuing.

## Step 3: Query iMessages

Once you have a phone number or email, run `imessage-query.py` from the Ararat repo:

```bash
python3 ~/Developer/ararat/tools/imessage-query.py "IDENTIFIER" ["KEYWORD"] [--days N] [--limit N]
```

- `IDENTIFIER`: the raw phone number (e.g. `+14155550101`) or email from AddressBook
- `KEYWORD`: pull a relevant word from the user's question if they're looking for something specific (e.g. "address", "link", "invoice")
- `--days`: default is 90; go higher (180, 365) if the user says "a while back" or "last year"
- `--limit`: default 20; increase if searching broadly

## Step 4: Answer the question

Read the returned messages and answer the user's original question directly. Quote the relevant message text. If nothing was found, say so and offer to search further back (`--days`) or with a different keyword.

## Permissions note

If the script fails with a permissions error, Terminal needs Full Disk Access: System Settings > Privacy & Security > Full Disk Access.
