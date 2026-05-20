---
name: codebase-visualizer
description: Visualize any codebase as an interactive function map with App Flow view. Shows every function as a block, who calls it, dead code, linter errors, and AI-generated plain-English explanations with Cursor fix prompts. For Next.js projects, also shows App Flow: rich page cards with components, what each component queries and where it navigates, tables and services with call counts. Arrows target specific components inside page cards.
---

# Codebase Visualizer

Maps every function in a codebase into an interactive browser visualization with two views:

**App Flow** (default for Next.js): Shows pages as rich cards containing their components. Each component shows what data it pulls, what it renders, and where it navigates. Tables and services sit in dedicated columns; arrows connect from specific component rows. Click any node, component, or edge to see details in the side panel.

**Function Map**: Each function = one block, grouped by file. Click a block to see connections (which functions call it, which it calls). Bugs flagged automatically: dead code, linter errors, your own notes. Click any block to get: plain-English explanation, bug details, Cursor fix prompt.

## Setup

Install dependencies:
```bash
brew install universal-ctags ripgrep
pip install tree-sitter tree-sitter-typescript
```

tree-sitter is optional — the skill falls back to regex parsing if not installed, but component detection is less accurate.

## Skill directory

The Python scripts are colocated with this SKILL.md file. The system prompt above shows "Base directory for this skill: <absolute-path>" — that is `SKILL_DIR`. Use it in every command that follows. Do not substitute a hardcoded path.

## Step 0: Sanity check

Before running anything, verify the skill files exist:

```bash
ls $SKILL_DIR/analyze.py $SKILL_DIR/server.py $SKILL_DIR/flow_analyzer.py $SKILL_DIR/page_parser.py
```

If any file is missing, abort and tell the user: "Skill file missing: `<path>`. Re-install the codebase-visualizer skill."

## Step 1: Get the project path

Ask: "Which project do you want to map? Give me the full path."

## Step 2: Check tools are installed

```bash
/opt/homebrew/bin/ctags --version | head -1
/opt/homebrew/bin/rg --version | head -1
python3 -c "import tree_sitter_typescript; print('tree-sitter ok')" 2>/dev/null || echo "tree-sitter not installed (optional)"
```

If ctags not found: "Install universal-ctags: `brew install universal-ctags`"
If rg not found: "Install ripgrep: `brew install ripgrep`"

## Step 3: Run analysis

```bash
python3 $SKILL_DIR/analyze.py <PROJECT_PATH>
```

Parse the JSON output. You now have a list of all functions with:
- `id` — stable unique ID: `"<file>::<name>::<line>"`
- name, file, line, language
- `caller_fns` — objects `{id, name, file}` of functions that call this one
- `calls` — objects `{id, name, file}` of functions this one calls
- `is_dead_code` — true if nobody calls it
- `linter_issues` — list of strings from linters

Top-level fields: `fns`, `edges`, `files`, `total`.

## Step 4: Generate AI analysis for each function

If > 100 functions, tell the user: "Large codebase — analysis will take a few minutes."

Process functions in **batches of 10**. After each batch, print: `Enriched X of Y functions`.

For each function:

1. **Read the function body** — use the Read tool, start at `fn.line`, read 30–50 lines or until the next function starts. If the function is over 200 lines, read only the first 50 and append to description: `(function is long — only first 50 lines analyzed)`.

2. **Write a description** — 2–3 sentences in plain English. No technical jargon. Set `fn.description`.
   Example: "This function takes two numbers and adds them together. It is used in 3 places across the codebase."

3. **Explain each bug in plain English** — do not just repeat the linter message. Explain WHY it is a problem. Add to `fn.bugs` list.
   - Dead code: "This function is never called anywhere in the codebase. It might be leftover code from an older version that is safe to delete."
   - Linter issue "no-unused-vars": "There is a variable inside this function that is created but never actually used. This wastes memory and makes the code harder to read."

4. **Write a Cursor fix prompt** — ONLY if the function has bugs. Self-contained instruction Cursor can follow. Set `fn.cursor_prompt`.
   Format:
   - Start: "In `<filename>`, find the function `<fn_name>`."
   - Explain what to change and why, in plain English.
   - End: "Do not change anything outside this function."
   - If no bugs: set `fn.cursor_prompt` to `""` (empty string — do not omit the field).

## Step 5: Write the function data file

Use the Write tool to create `/tmp/codebase-viz-<timestamp>.json` with this structure:

```json
{
  "project_path": "<PROJECT_PATH>",
  "fns": [ ...enriched fn objects... ],
  "edges": [
    {"from_id": "<caller_id>", "to_id": "<callee_id>"}
  ],
  "files": [
    {"path": "app/api/route.ts", "fn_count": 3, "dead_count": 0, "issue_count": 1}
  ],
  "total": <number>
}
```

Each fn object must have all fields from analyze.py output plus: `description`, `bugs`, `cursor_prompt`. `cursor_prompt` must always be present — use `""` if no bugs.

Get timestamp with: `python3 -c "import time; print(int(time.time()))"`

## Step 5b: Run the App Flow analyzer (Next.js projects only)

Check if the project is a Next.js app:

```bash
ls <PROJECT_PATH>/app 2>/dev/null && echo "nextjs" || echo "not-nextjs"
```

If Next.js, run:

```bash
python3 $SKILL_DIR/flow_analyzer.py <PROJECT_PATH> > /tmp/codebase-flow-<timestamp>.json 2>/tmp/flow-analyzer-stderr.txt
cat /tmp/flow-analyzer-stderr.txt
```

Parse the JSON. Top-level fields: `is_nextjs`, `pages`, `tables`, `services`, `actions`, `routes`, `edges`, `user_journey`, `stats`.

Each page has `components[]` — each component has its own `data_calls`, `navigations`, and `server_actions_called`.
Edge `from_id` may be a component ID (`comp::{route}::{name}::{line}`) or a page/route ID.

Print stats: `App Flow: [pages] pages, [components] components, [routes] routes, [tables] tables, [services] services, [edges] edges`

If not Next.js, skip this step and set flow_file to empty.

## Step 5c: Enrich the App Flow with plain-English descriptions (Next.js only)

After flow_analyzer.py outputs the JSON, enrich it with descriptions. SKIP entries where `description` is already non-empty — those came from cache.

Process **pages in batches of 5**. After each batch: `Enriched 5 of N pages`.

**For each page** (in `pages[]`):
- Read the page file (`page.file`).
- `description`: 1–2 sentences from a user's perspective. Example: "The main dashboard users see after logging in. Shows their active agents and recent activity."

**For each component** on that page (`pages[].components[]`):
- Read `component.source_file` if non-empty; otherwise the page file.
- `description`: 1 sentence. What this component does on the page.
- `data_summary`: 1 sentence describing what data it pulls. Look at `data_calls`. Example: "Pulls the user's 6 most recent agents from the agents table."

**For each table** (in `tables[]`):
- `description`: 1–2 sentences on what's stored and what it's used for.
- For each call_site, `summary`: 1 short phrase. Example: "Reads 6 most recent for current user."

**For each service** (in `services[]`):
- `description`: 1 sentence. What this package is and what the app uses it for.
- For each call_site, `summary`: 1 short phrase.

**For each action** (in `actions[]`):
- Read `action.file`.
- `description`: 1 sentence on what this server action does.

**For each route** (in `routes[]`):
- Read `route.file`.
- `description`: 1 sentence on what this API endpoint does.

If `parse_quality` is "failed" or "low", still write a description but prepend `[partial parse] `.

After enriching, write the updated JSON back to `/tmp/codebase-flow-<timestamp>.json`.

**MANDATORY VERIFICATION — do not proceed to Step 6 until this passes:**

```bash
python3 -c "
import json; d=json.load(open('/tmp/codebase-flow-<timestamp>.json'))
empty_p=[p['route_path'] for p in d['pages'] if not p.get('description')]
empty_c=[(p['route_path'],c['name']) for p in d['pages'] for c in p['components'] if not c.get('description')]
empty_t=[t['name'] for t in d['tables'] if not t.get('description')]
empty_a=[a['name'] for a in d['actions'] if not a.get('description')]
empty_r=[r['id'] for r in d['routes'] if not r.get('description')]
total=len(empty_p)+len(empty_c)+len(empty_t)+len(empty_a)+len(empty_r)
print(f'Empty: pages={len(empty_p)} comps={len(empty_c)} tables={len(empty_t)} actions={len(empty_a)} routes={len(empty_r)}')
if total: print('FAIL — missing descriptions:', empty_p[:3], empty_c[:3])
else: print('PASS — all descriptions present')
"
```

If FAIL: go back and fill in the missing descriptions before continuing.

Also write each file's parsed entry to the cache:
- Cache path: `$SKILL_DIR/cache/<sha256-of-project-path>/<sha256-of-file-path>.json`
- Get sha256: `python3 -c "import hashlib; print(hashlib.sha256('<path>'.encode()).hexdigest()[:16])"`
- Write the page/component object with `file_hash` field populated.

## Step 6: Start the server

Run in background. Pass both files if App Flow was generated:

```bash
# Next.js projects (with flow):
python3 $SKILL_DIR/server.py /tmp/codebase-viz-<timestamp>.json /tmp/codebase-flow-<timestamp>.json 8742

# Other projects (function map only):
python3 $SKILL_DIR/server.py /tmp/codebase-viz-<timestamp>.json 8742
```

Then tell the user:

```
Your codebase map is ready at http://localhost:8742

App Flow view (default): [N] pages · [C] components · [T] tables · [S] services · [R] routes
Function Map view: [F] functions across [X] files

Click any page to see its components and what they do.
Click any table to see every place it's queried from.
Toggle views with the buttons in the top bar.
Stop the server with Ctrl+C in the terminal.
```

## Notes

- If ctags finds 0 functions: "ctags couldn't find any functions. Make sure the project has source code files and that universal-ctags (not the older system ctags) is installed at /opt/homebrew/bin/ctags."
- Notes added in the browser are saved to the data file automatically.
- The server keeps running until stopped with Ctrl+C.
