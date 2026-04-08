# macOS-Specific Instructions

## Things 3

### Reading todos (via `uvx things-cli`)
```sh
uvx things-cli today          # today's tasks
uvx things-cli inbox          # inbox
uvx things-cli todos          # all todos
uvx things-cli anytime        # anytime list
uvx things-cli someday        # someday list
uvx things-cli projects       # all projects
uvx things-cli search "query" # search

# Filters
uvx things-cli -p "Project Name" todos   # filter by project
uvx things-cli -a "Area Name" todos      # filter by area
uvx things-cli -t "tag" todos            # filter by tag

# Output formats
uvx things-cli --json today              # JSON output
uvx things-cli --csv --recursive all     # CSV with nesting
uvx things-cli --recursive areas         # nested tree view
```

### Writing todos (via URL scheme)
```sh
# Add to inbox
open "things:///add?title=My%20Todo"

# Add to today
open "things:///add?title=My%20Todo&when=today"

# Add with notes, deadline, tags
open "things:///add?title=My%20Todo&when=today&notes=Some%20notes&deadline=2026-04-01&tags=work"

# Add to a specific list/project
open "things:///add?title=My%20Todo&list=Project%20Name"

# Add to someday
open "things:///add?title=My%20Todo&when=someday"

# Create a project
open "things:///add-project?title=My%20Project&when=today"

# Navigate to a view
open "things:///show?id=today"
open "things:///show?id=inbox"
```

`when` accepts: `today`, `tomorrow`, `someday`, `anytime`, or a date like `2026-04-01`.

For **updating** existing todos, Things 3 requires an auth token (Settings > General > Enable Things URLs).
