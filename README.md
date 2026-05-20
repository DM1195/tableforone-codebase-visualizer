# Codebase Visualizer — Claude Code Skill

An interactive browser visualization for any codebase. Point it at a project and get a live map in your browser.

**App Flow** (Next.js projects): Pages as rich cards showing their components, what each component queries, what tables/services it touches, and where it navigates. Arrows connect everything.

**Function Map** (any project): Every function as a block, grouped by file. Click to see callers, callees, dead code, linter errors, and AI-generated plain-English explanations with Cursor fix prompts.

---

## Install

Two commands in Claude Code:

```
/plugin marketplace add DM1195/tableforone-codebase-visualizer
/plugin install codebase-visualizer@tableforone-codebase-visualizer
```

## Dependencies

```bash
brew install universal-ctags ripgrep
pip install tree-sitter tree-sitter-typescript
```

`tree-sitter` is optional — the skill falls back to regex parsing without it, but component detection is less accurate.

## Usage

In any Claude Code session, type:

```
/codebase-visualizer
```

Claude will ask for your project path, run the analysis, enrich every function with a plain-English description, and open the visualization at `http://localhost:8742`.

---

## What it detects

**App Flow (Next.js App Router):**
- Pages and layouts grouped by URL section (Admin, Onboarding, Dashboard, etc.)
- Components used on each page, with props
- Direct Supabase / Prisma / Drizzle queries
- Indirect queries via lib functions (one level deep)
- External services (Stripe, Resend, etc.) via import tracing
- Server actions and API routes
- Navigation links and router pushes

**Function Map (all projects):**
- Every function found by ctags (JS/TS/Python/Go/Ruby/...)
- Call graph edges (who calls what)
- Dead code detection
- ESLint / Pyflakes linter issues
- AI descriptions + Cursor fix prompts

---

## ORM support

Supabase, Prisma, and Drizzle ORM are all supported. The analyzer detects `.from('table')`, `prisma.model.findMany()`, and `db.select().from(table)` patterns — including indirect calls through lib files.

---

## Manual install (alternative)

```bash
git clone https://github.com/DM1195/tableforone-codebase-visualizer /tmp/codebase-viz-install
cp -r /tmp/codebase-viz-install/skills/codebase-visualizer ~/.claude/skills/
rm -rf /tmp/codebase-viz-install
```

---

Made by [Durva Mathure](https://tableforone.co) / [@durva](https://x.com/durvamathure)
