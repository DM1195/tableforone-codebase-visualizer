# Changelog

All notable changes to this project will be documented in this file.

Format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).
Versioning follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.0.0] — 2026-05-20

### Added

- **App Flow view** for Next.js App Router projects
  - Pages grouped by URL section (Admin, Onboarding, Dashboard, etc.)
  - Components shown as rows inside each page card with data calls and navigations
  - Indirect import tracing: detects Supabase/Prisma/Drizzle queries made inside lib functions called by pages or components (one level deep)
  - External service detection (Stripe, Resend, etc.) via lib file import tracing
  - Tables column with per-call-site descriptions and operation breakdown
  - Services column for external packages
  - Server actions and API routes with caller attribution
  - SVG arrows connecting components to tables, services, and routes
- **Function Map view** for any codebase
  - Every function as a block, grouped by file, with call graph edges
  - Dead code detection and ESLint / Pyflakes linter issue surfacing
  - AI-generated plain-English descriptions for every function
  - Cursor fix prompts for functions with bugs
  - Stack trace parser: paste an error trace to highlight the relevant functions
  - Notes: add annotations to any function, saved to the data file
- **ORM support**: Supabase, Prisma, and Drizzle ORM
- **tree-sitter parsing** for accurate JSX component detection (falls back to regex)
- **Analysis cache**: parsed file results cached by content hash — reruns skip unchanged files
