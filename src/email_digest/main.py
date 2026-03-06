"""
Async orchestrator — entry point for the daily email digest.

Usage:
    uv run python -m email_digest.main                              # all users in config/
    uv run python -m email_digest.main --user james                 # single user
    uv run python -m email_digest.main --dry-run                    # no email, write HTML
    uv run python -m email_digest.main --user james --dry-run
    uv run python -m email_digest.main --entity "Nate B. Jones" --dry-run
    uv run python -m email_digest.main --config path/to/file.yaml   # legacy single-file
"""
from __future__ import annotations

import argparse
import asyncio
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

import anthropic

from .agent.recommender import generate_recommendations
from .agent.researcher import analyze_entity
from .agent.vision import analyze_frames
from .config import AppConfig, EntityConfig, load_config, load_configs
from .dedup import SeenDB
from .digest.formatter import render_email
from .digest.sender import send_email
from .sources.api import fetch_api_source
from .sources.base import FetchedContent
from .sources.rss import fetch_rss_feed
from .sources.web import fetch_web_page
from .sources.youtube import fetch_youtube_channel

_DATA_DIR = Path(__file__).parent.parent.parent / "data"
_SCREENSHOTS_DIR = _DATA_DIR / "screenshots"


# ---------------------------------------------------------------------------
# Source fetching
# ---------------------------------------------------------------------------

def _fetch_entity_sources(
    entity: EntityConfig, db: SeenDB
) -> list[FetchedContent]:
    """Fetch all sources for one entity (runs in a thread pool worker)."""
    results: list[FetchedContent] = []

    for source in entity.sources:
        seen_ids = db.seen_ids_for(entity.name)

        try:
            if source.type == "youtube":
                fc = fetch_youtube_channel(
                    channel_url=source.url,
                    max_videos=source.max_videos,
                    include_transcripts=source.include_transcripts,
                    include_comments=source.include_comments,
                    max_comments=entity.max_comments,
                    extract_frames=source.extract_frames,
                    max_frames=source.max_frames,
                    screenshots_dir=_SCREENSHOTS_DIR,
                    seen_ids=seen_ids,
                    enrichment_source=source.enrichment_source,
                )
                fc.entity_name = entity.name

            elif source.type == "rss":
                fc = fetch_rss_feed(
                    url=source.url,
                    entity_name=entity.name,
                    max_items=source.max_items,
                    seen_ids=seen_ids,
                )

            elif source.type == "api":
                fc = fetch_api_source(
                    url=source.url,
                    entity_name=entity.name,
                    max_items=source.max_items,
                    item_url=source.item_url,
                    include_comments=entity.include_comments,
                    max_comments=entity.max_comments,
                    seen_ids=seen_ids,
                )

            elif source.type == "web":
                fc = fetch_web_page(url=source.url, entity_name=entity.name)

            else:
                print(f"[main] Unknown source type '{source.type}' for {entity.name}")
                continue

            results.append(fc)

        except Exception as e:
            print(f"[main] Source fetch failed for {entity.name} ({source.url}): {e}")

    return results


def _merge_fetched(entity: EntityConfig, sources: list[FetchedContent]) -> FetchedContent:
    """Merge multiple FetchedContent objects for the same entity into one."""
    if not sources:
        return FetchedContent(
            entity_name=entity.name,
            source_type="none",
            items=[],
            has_new_content=False,
        )
    if len(sources) == 1:
        return sources[0]

    merged_items = []
    skipped = 0
    for fc in sources:
        merged_items.extend(fc.items)
        skipped += fc.skipped_count

    return FetchedContent(
        entity_name=entity.name,
        source_type=sources[0].source_type,
        items=merged_items,
        has_new_content=any(fc.has_new_content for fc in sources),
        skipped_count=skipped,
    )


# ---------------------------------------------------------------------------
# Per-user paths
# ---------------------------------------------------------------------------

def _db_path_for_user(user_label: str) -> Path:
    """data/seen_james.db, data/seen_mom.db, etc."""
    return _DATA_DIR / f"seen_{user_label}.db"


def _preview_path_for_user(user_label: str) -> Path:
    return Path(f"digest_preview_{user_label}.html")


# ---------------------------------------------------------------------------
# Core pipeline (one user / one config)
# ---------------------------------------------------------------------------

async def run_digest(
    config: AppConfig,
    dry_run: bool = False,
    entity_filter: str | None = None,
    user_label: str = "default",
) -> int:
    """Run the full digest pipeline for one config. Returns 0 on success, 1 on failure."""
    start = time.monotonic()
    client = anthropic.Anthropic(api_key=config.anthropic_api_key)
    _SCREENSHOTS_DIR.mkdir(parents=True, exist_ok=True)

    entities = config.entities
    if entity_filter:
        entities = [e for e in entities if e.name.lower() == entity_filter.lower()]
        if not entities:
            print(f"[{user_label}] Entity '{entity_filter}' not found in config.")
            return 1

    # ── Phase 1: Fetch sources in parallel ───────────────────────────────
    print(f"[{user_label}] Fetching {len(entities)} entit{'y' if len(entities) == 1 else 'ies'}…")
    loop = asyncio.get_running_loop()
    db_path = _db_path_for_user(user_label)

    with SeenDB(db_path=db_path) as db:
        with ThreadPoolExecutor(max_workers=min(len(entities), 8)) as pool:
            fetch_futures = [
                loop.run_in_executor(pool, _fetch_entity_sources, entity, db)
                for entity in entities
            ]
            raw_sources = await asyncio.gather(*fetch_futures, return_exceptions=True)

    fetched: list[FetchedContent] = []
    for entity, result in zip(entities, raw_sources):
        if isinstance(result, Exception):
            print(f"[{user_label}] Fetch failed for {entity.name}: {result}")
            fetched.append(FetchedContent(
                entity_name=entity.name, source_type="error", items=[], has_new_content=False
            ))
        else:
            fetched.append(_merge_fetched(entity, result))

    # ── Phase 2: Claude Vision on extracted frames ────────────────────────
    for fc in fetched:
        if fc.source_type != "youtube":
            continue
        for video in fc.items:
            if video.frames:
                print(f"[{user_label}] Analyzing {len(video.frames)} frame(s) for '{video.title}'…")
                analyze_frames(video, client=client)

    # ── Phase 3: Per-entity Claude analysis ──────────────────────────────
    print(f"[{user_label}] Running entity analysis…")
    summaries = []
    for entity, fc in zip(entities, fetched):
        print(f"  → {entity.name}…")
        summaries.append(analyze_entity(entity, fc, client=client))

    # ── Phase 4: Recommendations ─────────────────────────────────────────
    recs = None
    if config.projects:
        print(f"[{user_label}] Generating recommendations…")
        recs = generate_recommendations(summaries, config, client=client)

    # ── Phase 5: Format ───────────────────────────────────────────────────
    run_at = datetime.now(timezone.utc)
    html_body, plain_body = render_email(
        summaries, recs, run_at=run_at,
        template_html=config.template_html,
        template_txt=config.template_txt,
    )

    elapsed = time.monotonic() - start
    print(f"[{user_label}] Pipeline complete in {elapsed:.1f}s.")

    if dry_run:
        out = _preview_path_for_user(user_label)
        out.write_text(html_body)
        print(f"[{user_label}] Dry run → {out} (not sent)")
        return 0

    # ── Phase 6: Send ─────────────────────────────────────────────────────
    print(f"[{user_label}] Sending email to {config.digest.email_to}…")
    try:
        msg_id = send_email(html_body, plain_body, config)
        print(f"[{user_label}] Email sent. ID: {msg_id}")
    except Exception as e:
        print(f"[{user_label}] Email send FAILED: {e}")
        return 1

    # ── Phase 7: Persist dedup cache ─────────────────────────────────────
    with SeenDB(db_path=db_path) as db:
        for entity, fc in zip(entities, fetched):
            for item in fc.items:
                url = getattr(item, "url", "")
                title = getattr(item, "title", "")
                if url:
                    db.mark_seen(entity.name, url, title)

    return 0


# ---------------------------------------------------------------------------
# Multi-user orchestrator
# ---------------------------------------------------------------------------

async def run_all_digests(
    config_dir: str | None = None,
    dry_run: bool = False,
    entity_filter: str | None = None,
    user_filter: str | None = None,
) -> int:
    """Load all YAMLs from config dir and run the pipeline for each user."""
    configs = load_configs(config_dir)

    if not configs:
        print("[main] No user configs found.")
        return 1

    if user_filter:
        if user_filter not in configs:
            print(f"[main] User '{user_filter}' not found. Available: {list(configs)}")
            return 1
        configs = {user_filter: configs[user_filter]}

    anthropic_key = configs[next(iter(configs))].anthropic_api_key
    if not anthropic_key:
        print("[main] ERROR: ANTHROPIC_API_KEY not set.")
        return 1

    results: dict[str, int] = {}
    for user_label, config in configs.items():
        try:
            rc = await run_digest(
                config,
                dry_run=dry_run,
                entity_filter=entity_filter,
                user_label=user_label,
            )
            results[user_label] = rc
        except Exception as e:
            print(f"[main] Digest failed for {user_label}: {e}")
            results[user_label] = 1

    ok = [u for u, rc in results.items() if rc == 0]
    failed = [u for u, rc in results.items() if rc != 0]
    print(f"\n[main] {len(ok)}/{len(results)} digest(s) succeeded.{' Failed: ' + ', '.join(failed) if failed else ''}")
    return 0 if not failed else 1


# ---------------------------------------------------------------------------
# Git commit helper (called by GitHub Actions step, optional locally)
# ---------------------------------------------------------------------------

def _commit_cache() -> None:
    try:
        subprocess.run(["git", "config", "user.name", "digest-bot"], capture_output=True)
        subprocess.run(["git", "config", "user.email", "bot@noreply.github.com"], capture_output=True)
        subprocess.run(["git", "add", "data/", "--", "data/seen_*.db", "data/screenshots/"], capture_output=True)
        result = subprocess.run(["git", "diff", "--staged", "--quiet"], capture_output=True)
        if result.returncode != 0:
            subprocess.run(["git", "commit", "-m", "chore: update digest cache [skip ci]"], capture_output=True)
            subprocess.run(["git", "push"], capture_output=True)
            print("[main] Cache committed and pushed.")
    except Exception as e:
        print(f"[main] Cache commit failed (non-fatal): {e}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Daily email digest runner")
    parser.add_argument("--dry-run", action="store_true", help="Skip email send, write HTML to disk")
    parser.add_argument("--entity", metavar="NAME", help="Run only this entity (exact name)")
    parser.add_argument("--user", metavar="NAME", help="Run only this user (YAML stem, e.g. 'james')")
    parser.add_argument("--config-dir", metavar="DIR", help="Directory of user YAML configs (default: config/)")
    parser.add_argument("--config", metavar="PATH", help="Single YAML config (legacy single-user mode)")
    args = parser.parse_args()

    if args.config:
        # Legacy single-file mode (backward compat)
        config = load_config(args.config)
        if not config.anthropic_api_key:
            print("[main] ERROR: ANTHROPIC_API_KEY not set.")
            sys.exit(1)
        if not args.dry_run and not config.resend_api_key:
            print("[main] ERROR: RESEND_API_KEY not set.")
            sys.exit(1)
        rc = asyncio.run(run_digest(config, dry_run=args.dry_run, entity_filter=args.entity))
    else:
        # Multi-user mode (default)
        rc = asyncio.run(
            run_all_digests(
                config_dir=args.config_dir,
                dry_run=args.dry_run,
                entity_filter=args.entity,
                user_filter=args.user,
            )
        )

    sys.exit(rc)


if __name__ == "__main__":
    main()
