# HANDOFF — mango (formerly email-digest)

## Project identity
- **Name:** mango (lowercase, intentional)
- **CLI:** `uv run mango`
- **Package:** `src/mango/` (module: `mango`)
- **Branch:** `feat/digest-system`

## Current State (2026-03-06)
- Project renamed `email-digest` → `mango`: pyproject.toml, src/, CLI entry, comments all updated
- `logo.svg` created at project root (mango fruit + envelope motif, monospace wordmark)
- `README.md` fully rewritten — portfolio-grade, mango metaphor with PMC citation, architecture, full config reference, badges
- Pipeline is functionally complete: multi-user, parallel fetching, Claude analysis, dedup, Resend delivery, GitHub Actions
- Dry-run previews: `digest_preview_james.html`, `digest_preview_mom.html`
- Dedup DBs: `data/seen_james.db`, `data/seen_mom.db` (untracked, as intended)

## What Works
- Full pipeline: fetch → Claude analysis → Jinja2 HTML email → Resend delivery
- Multi-user config: `config/james.yaml` + `config/mom.yaml` (gitignored, on disk only)
- Per-user SeenDB: `data/seen_james.db`, `data/seen_mom.db`
- Nate Jones transcript enrichment layer (`sources/nate_enrichment.py`)
- Mom's template: `digest_mom.html.j2` (warm editorial light-mode)
- GitHub Actions cron: `30 15 * * *` (7:30 AM PST), `--config-dir config`
- dotenv loading: `.env.local` auto-loaded via `_load_env_files()` in `config.py`
- Playwright installed locally

## Bugs Fixed (previous sessions)
- SQLite threading: `check_same_thread=False` in `dedup.py`
- ANTHROPIC_API_KEY loaded as empty string from shell: custom `_load_env_files()` in `config.py`
- STAT news URL 404: switched `config/mom.yaml` from `type: web` to `type: rss`
- `python-dotenv` added to deps

## Currently Blocked
**API credits not set up on the API account.**

Root cause: Claude.ai Pro subscription ≠ Anthropic API credits. Separate products, separate billing.

**Fix:** console.anthropic.com/settings/billing → Add credits ($20 is plenty).
API key in `.env.local` is valid — just needs credits on the account.

Once credits are added, run:
```bash
uv run mango --config-dir config --user mom --dry-run
```
Then open `digest_preview_mom.html` in a browser to review.

## Pending Before Merge
1. ✅ Fix API auth / credit issue (user needs to add credits)
2. Successful dry run → review `digest_preview_mom.html` in browser
3. Remove "Generated with Claude Code" footer from PR #1 body on GitHub manually
4. Merge PR #1

## Pending After Merge
1. Add mom's real email to `config/mom.yaml` (currently `james@real-k9.com` for dry-run)
2. Trigger `workflow_dispatch` on GitHub Actions → verify end-to-end
3. Make repo public (config files already gitignored)

## Future Features
- **GUI / web dashboard** — a simple browser-based UI so non-technical users (e.g. mom) can configure their digest, preview it, and trigger a send without touching YAML or the CLI. Should be dead-simple: drag-and-drop source management, big "Send Digest" button, live HTML preview. Target user: comfortable with email, not with terminals.

## GitHub Secrets (already set in repo)
- `ANTHROPIC_API_KEY` ✅
- `RESEND_API_KEY` ✅
- `GH_PAT` ✅ (fine-grained, all repos, contents read-only)

## Key Files
| File | Purpose |
|------|---------|
| `src/mango/main.py` | Entry point, CLI, orchestrator |
| `src/mango/config.py` | YAML loading, dataclasses |
| `src/mango/dedup.py` | SQLite seen-IDs |
| `src/mango/agent/researcher.py` | Per-entity Claude analysis |
| `src/mango/sources/youtube.py` | yt-dlp, transcripts, frames |
| `src/mango/sources/nate_enrichment.py` | Nate Jones enrichment layer |
| `src/mango/digest/sender.py` | Resend API |
| `src/mango/digest/templates/digest_mom.html.j2` | Mom's email template |
| `config/james.yaml` | James's digest config (gitignored) |
| `config/mom.yaml` | Mom's digest config (gitignored) |
| `config/entities.example.yaml` | Public reference config |
| `.env.local` | Local API keys (gitignored) |

## Hookify Rules (global, `~/.claude/`)
- `hookify.session-start.local.md` — fires on every prompt; reminds to read CLAUDE.md + HANDOFF.md
- `hookify.session-end.local.md` — fires on stop; full checklist (HANDOFF, git, attribution, tools)
- `hookify.no-llm-attribution-bash.local.md` — blocks Co-Authored-By / Claude Code in bash
- `hookify.no-llm-attribution-file.local.md` — blocks same in file writes
