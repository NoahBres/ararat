---
name: contacts-search
description: Fuzzy search contacts by name. Use when you need to find a contact's phone number or email, especially when you're unsure of the exact spelling or the contact may not be saved. Returns the best matching contacts with their identifiers.
---

# Contacts Search

Fuzzy-search AddressBook contacts by name and return their phone numbers / emails.

## Usage

```bash
python3 ~/Developer/ararat/tools/contacts-search.py "NAME" [--top N] [--exact]
```

- `NAME`: full or partial name (fuzzy matched — handles typos, partial first names, etc.)
- `--top N`: return top N matches (default: 5)
- `--exact`: substring-only mode, no fuzzy scoring

## Example output

```
  [1.00] Kirill Ivanov | +14155550101
  [0.82] Kirsten Park | kirspark@gmail.com
```

Score of 1.0 = exact substring match. Scores ≥ 0.5 are shown by default.

## When to use

- User asks "do I have Kirill's number?" or "what's Alex's email?"
- Before querying iMessages when the contact's phone number is unknown
- When the imessage-lookup skill fails to find a contact (not in AddressBook or wrong spelling)

## Integration with imessage-lookup

When `imessage-lookup` Step 1 returns no results, run this script as a fallback to find possible identifiers, then retry with the phone number found.
