"""Base types shared across all source handlers."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class Comment:
    text: str
    author: str
    like_count: int = 0
    reply_count: int = 0
    timestamp: str = ""
    replies: list[Comment] = field(default_factory=list)


@dataclass
class VideoFrame:
    timestamp_sec: int
    image_path: str          # local path on disk
    github_url: str = ""     # filled in after commit
    vision_description: str = ""
    chapter_title: str = ""


@dataclass
class VideoInfo:
    video_id: str
    title: str
    url: str
    channel: str
    upload_date: str         # YYYYMMDD
    duration_sec: int
    view_count: int
    like_count: int
    description: str
    thumbnail_url: str
    chapters: list[dict]     # [{start_time, end_time, title}]
    heatmap: list[dict]      # [{start_time, end_time, value}]
    transcript: list[tuple[float, str]] | None  # [(start_sec, text), ...]
    comments: list[Comment]
    frames: list[VideoFrame] = field(default_factory=list)
    enrichment: dict | None = None  # pre-classified metadata from external sources (e.g. nate_transcripts)


@dataclass
class FeedItem:
    title: str
    url: str
    summary: str
    published: str
    content: str = ""
    comments: list[Comment] = field(default_factory=list)


@dataclass
class FetchedContent:
    entity_name: str
    source_type: str         # "youtube" | "rss" | "api" | "web"
    items: list[Any]         # list[VideoInfo] | list[FeedItem]
    has_new_content: bool = True
    skipped_count: int = 0   # items filtered by dedup
