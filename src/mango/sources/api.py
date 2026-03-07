"""Generic JSON API source handler — supports simple list endpoints and Hacker News."""
from __future__ import annotations

import time

import requests

from .base import Comment, FeedItem, FetchedContent

_HEADERS = {
    "User-Agent": "email-digest/1.0",
    "Accept": "application/json",
}


def fetch_api_source(
    url: str,
    entity_name: str = "",
    max_items: int = 10,
    item_url: str = "",
    include_comments: bool = True,
    max_comments: int = 5,
    seen_ids: set[str] | None = None,
) -> FetchedContent:
    """
    Fetch items from a JSON API.

    Supports two patterns:
    1. Flat list endpoint — returns a list of objects directly
    2. ID list endpoint (e.g. Hacker News) — returns a list of IDs, then fetches
       each item via `item_url` template (e.g. "https://.../{id}.json")
    """
    try:
        resp = requests.get(url, timeout=20, headers=_HEADERS)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        print(f"[api] Failed to fetch {url}: {e}")
        return FetchedContent(
            entity_name=entity_name, source_type="api", items=[], has_new_content=False
        )

    # ID-list pattern (e.g. HN topstories returns a plain list of ints)
    if item_url and isinstance(data, list) and data and not isinstance(data[0], dict):
        return _fetch_id_list(
            ids=data,
            entity_name=entity_name,
            item_url=item_url,
            max_items=max_items,
            include_comments=include_comments,
            max_comments=max_comments,
            seen_ids=seen_ids,
        )

    # Flat object-list pattern
    if isinstance(data, list):
        items = []
        skipped = 0
        for obj in data:
            if not isinstance(obj, dict):
                continue
            item_id = str(obj.get("id", obj.get("url", "")))
            if seen_ids and item_id in seen_ids:
                skipped += 1
                continue
            if len(items) >= max_items:
                break
            items.append(_dict_to_feed_item(obj))

        return FetchedContent(
            entity_name=entity_name,
            source_type="api",
            items=items,
            has_new_content=len(items) > 0,
            skipped_count=skipped,
        )

    # Single-object response — wrap in a list
    if isinstance(data, dict):
        item = _dict_to_feed_item(data)
        return FetchedContent(
            entity_name=entity_name,
            source_type="api",
            items=[item],
            has_new_content=bool(item.title or item.content),
        )

    return FetchedContent(
        entity_name=entity_name, source_type="api", items=[], has_new_content=False
    )


# ---------------------------------------------------------------------------
# Hacker News helpers
# ---------------------------------------------------------------------------

def _fetch_id_list(
    ids: list,
    entity_name: str,
    item_url: str,
    max_items: int,
    include_comments: bool,
    max_comments: int,
    seen_ids: set[str] | None,
) -> FetchedContent:
    items = []
    skipped = 0

    for item_id in ids:
        if len(items) >= max_items:
            break
        str_id = str(item_id)
        if seen_ids and str_id in seen_ids:
            skipped += 1
            continue

        url = item_url.replace("{id}", str_id)
        try:
            resp = requests.get(url, timeout=10, headers=_HEADERS)
            resp.raise_for_status()
            obj = resp.json()
        except Exception as e:
            print(f"[api] Failed to fetch item {item_id}: {e}")
            continue

        if not isinstance(obj, dict):
            continue

        # Skip deleted/dead items and non-story types
        if obj.get("deleted") or obj.get("dead"):
            continue
        if obj.get("type") not in ("story", None):
            continue

        feed_item = _hn_item_to_feed_item(obj)

        if include_comments and obj.get("kids"):
            feed_item.comments = _fetch_hn_comments(
                obj["kids"][:max_comments], item_url
            )

        items.append(feed_item)
        time.sleep(0.1)  # gentle rate limiting

    return FetchedContent(
        entity_name=entity_name,
        source_type="api",
        items=items,
        has_new_content=len(items) > 0,
        skipped_count=skipped,
    )


def _fetch_hn_comments(kid_ids: list[int], item_url: str) -> list[Comment]:
    comments = []
    for kid_id in kid_ids:
        url = item_url.replace("{id}", str(kid_id))
        try:
            resp = requests.get(url, timeout=8, headers=_HEADERS)
            resp.raise_for_status()
            c = resp.json()
        except Exception:
            continue

        if not isinstance(c, dict):
            continue
        if c.get("deleted") or c.get("dead") or not c.get("text"):
            continue

        comments.append(
            Comment(
                text=c.get("text", ""),
                author=c.get("by", ""),
                like_count=0,  # HN has no likes
                timestamp=str(c.get("time", "")),
            )
        )
        time.sleep(0.05)

    return comments


def _hn_item_to_feed_item(obj: dict) -> FeedItem:
    title = obj.get("title", "")
    url = obj.get("url", f"https://news.ycombinator.com/item?id={obj.get('id', '')}")
    score = obj.get("score", 0) or 0
    comment_count = obj.get("descendants", 0) or 0
    by = obj.get("by", "")

    summary = f"Score: {score} | Comments: {comment_count} | By: {by}"
    content = obj.get("text", "") or ""  # self-post text

    return FeedItem(
        title=title,
        url=url,
        summary=summary,
        published=str(obj.get("time", "")),
        content=content,
    )


def _dict_to_feed_item(obj: dict) -> FeedItem:
    """Generic dict → FeedItem mapping (best-effort field discovery)."""
    title = (
        obj.get("title")
        or obj.get("name")
        or obj.get("headline")
        or ""
    )
    url = obj.get("url") or obj.get("link") or obj.get("href") or ""
    summary = obj.get("summary") or obj.get("description") or obj.get("excerpt") or ""
    published = str(obj.get("published") or obj.get("date") or obj.get("created_at") or "")
    content = obj.get("content") or obj.get("body") or obj.get("text") or ""

    return FeedItem(
        title=str(title),
        url=str(url),
        summary=str(summary),
        published=published,
        content=str(content),
    )
