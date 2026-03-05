"""RSS/Atom feed source handler using feedparser."""
from __future__ import annotations

from datetime import datetime, timezone

import feedparser

from .base import FeedItem, FetchedContent


def fetch_rss_feed(
    url: str,
    entity_name: str = "",
    max_items: int = 10,
    seen_ids: set[str] | None = None,
) -> FetchedContent:
    """Fetch and parse an RSS or Atom feed."""
    try:
        feed = feedparser.parse(url)
    except Exception as e:
        print(f"[rss] Failed to fetch {url}: {e}")
        return FetchedContent(
            entity_name=entity_name, source_type="rss", items=[], has_new_content=False
        )

    items = []
    skipped = 0

    for entry in feed.entries:
        item_id = entry.get("id") or entry.get("link", "")
        if seen_ids and item_id in seen_ids:
            skipped += 1
            continue
        if len(items) >= max_items:
            break

        items.append(
            FeedItem(
                title=entry.get("title", ""),
                url=entry.get("link", ""),
                summary=_extract_summary(entry),
                published=_parse_date(entry),
                content=_extract_content(entry),
            )
        )

    return FetchedContent(
        entity_name=entity_name,
        source_type="rss",
        items=items,
        has_new_content=len(items) > 0,
        skipped_count=skipped,
    )


def _extract_summary(entry: dict) -> str:
    return entry.get("summary", "") or ""


def _extract_content(entry: dict) -> str:
    """Try to get full content, fall back to summary."""
    content_list = entry.get("content", [])
    if content_list:
        return content_list[0].get("value", "")
    return entry.get("summary", "") or ""


def _parse_date(entry: dict) -> str:
    """Return ISO date string from feedparser's parsed date tuple."""
    if entry.get("published_parsed"):
        try:
            dt = datetime(*entry.published_parsed[:6], tzinfo=timezone.utc)
            return dt.isoformat()
        except Exception:
            pass
    if entry.get("updated_parsed"):
        try:
            dt = datetime(*entry.updated_parsed[:6], tzinfo=timezone.utc)
            return dt.isoformat()
        except Exception:
            pass
    return entry.get("published", "") or entry.get("updated", "")
