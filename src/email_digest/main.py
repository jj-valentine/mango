"""
Async orchestrator — entry point for the daily email digest.

Usage:
    uv run python -m email_digest.main               # full run + send
    uv run python -m email_digest.main --dry-run     # fetch + analyze, no email
    uv run python -m email_digest.main --entity "Nate B. Jones"  # single entity
    uv run python -m email_digest.main --entity "Hacker News" --dry-run
"""
from __future__ import annotations

import argparse
import asyncio
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

import anthropic

from .agent.recommender import generate_recommendations
from .agent.researcher import analyze_entity
from .agent.vision import analyze_frames
from .config import AppConfig, EntityConfig, load_config
from .dedup import SeenDB
from .digest.formatter import render_email
from .digest.sender import send_email
from .sources.api import fetch_api_source
from .sources.base import FetchedContent
from .sources.rss import fetch_rss_feed
from .sources.web import fetch_web_page
from .sources.youtube import fetch_youtube_channel

_SCREENSHOTS_DIR = Path(__file__).parent.parent.parent / "data" / "screenshots"


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
# GitHub commit of seen.db + screenshots after successful run
# ---------------------------------------------------------------------------

def _commit_cache() -> None:
    import subprocess
    try:
        subprocess.run(
            ["git", "config", "user.name", "digest-bot"],
            capture_output=True,
        )
        subprocess.run(
            ["git", "config", "user.email", "bot@noreply"],
            capture_output=True,
        )
        subprocess.run(["git", "add", "data/seen.db", "data/screenshots/"], capture_output=True)
        result = subprocess.run(
            ["git", "diff", "--staged", "--quiet"],
            capture_output=True,
        )
        if result.returncode != 0:
            subprocess.run(
                ["git", "commit", "-m", "chore: update digest cache [skip ci]"],
                capture_output=True,
            )
            subprocess.run(["git", "push"], capture_output=True)
            print("[main] Cache committed and pushed.")
    except Exception as e:
        print(f"[main] Cache commit failed (non-fatal): {e}")


# ---------------------------------------------------------------------------
# Main orchestration
# ---------------------------------------------------------------------------

async def run_digest(
    config: AppConfig,
    dry_run: bool = False,
    entity_filter: str | None = None,
) -> int:
    """
    Run the full digest pipeline. Returns 0 on success, 1 on failure.
    """
    start = time.monotonic()
    client = anthropic.Anthropic(api_key=config.anthropic_api_key)
    _SCREENSHOTS_DIR.mkdir(parents=True, exist_ok=True)

    entities = config.entities
    if entity_filter:
        entities = [e for e in entities if e.name.lower() == entity_filter.lower()]
        if not entities:
            print(f"[main] Entity '{entity_filter}' not found in config.")
            return 1

    # ── Phase 1: Fetch all sources in parallel ───────────────────────────
    print(f"[main] Fetching {len(entities)} entit{'y' if len(entities) == 1 else 'ies'}…")
    loop = asyncio.get_running_loop()

    with SeenDB() as db:
        with ThreadPoolExecutor(max_workers=min(len(entities), 8)) as pool:
            fetch_futures = [
                loop.run_in_executor(pool, _fetch_entity_sources, entity, db)
                for entity in entities
            ]
            raw_sources = await asyncio.gather(*fetch_futures, return_exceptions=True)

    fetched: list[FetchedContent] = []
    for entity, result in zip(entities, raw_sources):
        if isinstance(result, Exception):
            print(f"[main] Fetch failed for {entity.name}: {result}")
            fetched.append(FetchedContent(
                entity_name=entity.name, source_type="error", items=[], has_new_content=False
            ))
        else:
            merged = _merge_fetched(entity, result)
            fetched.append(merged)

    # ── Phase 2: Claude Vision on extracted frames ───────────────────────
    for fc in fetched:
        if fc.source_type != "youtube":
            continue
        for video in fc.items:
            if video.frames:
                print(f"[main] Analyzing {len(video.frames)} frame(s) for '{video.title}'…")
                analyze_frames(video, client=client)

    # ── Phase 3: Per-entity Claude analysis ──────────────────────────────
    print("[main] Running entity analysis…")
    summaries = []
    for entity, fc in zip(entities, fetched):
        print(f"  → {entity.name}…")
        summary = analyze_entity(entity, fc, client=client)
        summaries.append(summary)

    # ── Phase 4: Post-analysis recommendations ───────────────────────────
    recs = None
    if config.projects:
        print("[main] Generating build/integrate recommendations…")
        recs = generate_recommendations(summaries, config, client=client)

    # ── Phase 5: Format ───────────────────────────────────────────────────
    run_at = datetime.now(timezone.utc)
    html_body, plain_body = render_email(summaries, recs, run_at=run_at)

    elapsed = time.monotonic() - start
    print(f"[main] Pipeline complete in {elapsed:.1f}s.")

    if dry_run:
        out_path = Path("digest_preview.html")
        out_path.write_text(html_body)
        print(f"[main] Dry run — email saved to {out_path} (not sent).")
        return 0

    # ── Phase 6: Send ─────────────────────────────────────────────────────
    print("[main] Sending email…")
    try:
        msg_id = send_email(html_body, plain_body, config)
        print(f"[main] Email sent. ID: {msg_id}")
    except Exception as e:
        print(f"[main] Email send FAILED: {e}")
        return 1

    # ── Phase 7: Persist dedup cache ─────────────────────────────────────
    with SeenDB() as db:
        for entity, fc in zip(entities, fetched):
            for item in fc.items:
                url = getattr(item, "url", "")
                title = getattr(item, "title", "")
                if url:
                    db.mark_seen(entity.name, url, title)

    _commit_cache()
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Daily email digest runner")
    parser.add_argument("--dry-run", action="store_true", help="Skip email send, write HTML to disk")
    parser.add_argument("--entity", metavar="NAME", help="Run only this entity (exact name)")
    parser.add_argument("--config", metavar="PATH", help="Path to entities.yaml")
    args = parser.parse_args()

    config = load_config(args.config)

    if not config.anthropic_api_key:
        print("[main] ERROR: ANTHROPIC_API_KEY not set.")
        sys.exit(1)
    if not args.dry_run and not config.resend_api_key:
        print("[main] ERROR: RESEND_API_KEY not set (use --dry-run to skip sending).")
        sys.exit(1)

    rc = asyncio.run(
        run_digest(config, dry_run=args.dry_run, entity_filter=args.entity)
    )
    sys.exit(rc)


if __name__ == "__main__":
    main()
