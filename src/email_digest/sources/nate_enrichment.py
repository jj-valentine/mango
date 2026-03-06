"""
Nate Jones transcript enrichment layer.

Fetches the pre-classified metadata from kani3894/nate-jones-transcripts
(index.json) and returns a lookup dict keyed by YouTube video_id.

Used as an optional enrichment source for the Nate B. Jones YouTube entity.
The enrichment data augments (not replaces) yt-dlp metadata and our own transcripts.
"""
from __future__ import annotations

import time
from functools import lru_cache

import requests

INDEX_URL = (
    "https://raw.githubusercontent.com/kani3894/nate-jones-transcripts"
    "/main/index.json"
)

_HEADERS = {
    "User-Agent": "email-digest/1.0",
    "Accept": "application/json",
}


@lru_cache(maxsize=1)
def fetch_nate_index() -> dict[str, dict]:
    """
    Fetch and cache the full index of pre-classified Nate Jones videos.
    Returns: {video_id: {content_type, difficulty, audience, entities, summary, ...}}

    Cached for the duration of a process run (one digest run = one fetch).
    """
    try:
        resp = requests.get(INDEX_URL, timeout=20, headers=_HEADERS)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        print(f"[nate_enrichment] Failed to fetch index: {e}")
        return {}

    if isinstance(data, list):
        return {item["video_id"]: item for item in data if item.get("video_id")}
    if isinstance(data, dict):
        # Index might be {video_id: metadata} already
        return data

    return {}


def get_enrichment(video_id: str) -> dict | None:
    """Return enrichment metadata for a video_id, or None if not found."""
    index = fetch_nate_index()
    entry = index.get(video_id)
    if not entry:
        return None

    # Normalise to a clean subset we actually use in the researcher
    return {
        "content_type": entry.get("content_type", ""),
        "primary_topic": entry.get("primary_topic", ""),
        "difficulty": entry.get("difficulty", ""),
        "audience": entry.get("audience", []),
        "entities_mentioned": entry.get("entities", {}).get("companies", [])
            + entry.get("entities", {}).get("products", [])
            + entry.get("entities", {}).get("ai_models", []),
        "people_mentioned": entry.get("entities", {}).get("people", []),
        "concepts": entry.get("knowledge", {}).get("concepts", []),
        "pre_summary": entry.get("knowledge", {}).get("summary", ""),
    }
