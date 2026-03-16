"""YouTube source handler: metadata, heatmap, transcripts, comments, frame extraction."""
from __future__ import annotations

import subprocess
import time
from datetime import datetime
from pathlib import Path

import yt_dlp
from youtube_transcript_api import YouTubeTranscriptApi
from youtube_transcript_api._errors import NoTranscriptFound, TranscriptsDisabled

from .base import Comment, FetchedContent, VideoFrame, VideoInfo


def fetch_youtube_channel(
    channel_url: str,
    max_videos: int = 5,
    include_transcripts: bool = True,
    include_comments: bool = True,
    max_comments: int = 30,
    extract_frames: bool = False,
    max_frames: int = 3,
    screenshots_dir: Path | None = None,
    seen_ids: set[str] | None = None,
    enrichment_source: str = "",
) -> FetchedContent:
    """Fetch latest videos from a YouTube channel with full metadata."""
    entity_name = channel_url  # caller replaces with entity name
    videos = []
    skipped = 0

    # Pre-fetch enrichment index if requested (cached for the process lifetime)
    enrichment_index: dict = {}
    if enrichment_source == "nate_transcripts":
        try:
            from .nate_enrichment import fetch_nate_index
            enrichment_index = fetch_nate_index()
        except Exception as e:
            print(f"[youtube] Enrichment index fetch failed: {e}")

    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
        "extract_flat": False,
        "playlistend": max_videos + 5,  # fetch a few extra to account for seen filtering
        "getcomments": include_comments,
        "extractor_args": {
            "youtube": {
                "max_comments": [str(max_comments), "all", "10", "5"],
                "comment_sort": ["top"],
            }
        },
    }

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        try:
            playlist_info = ydl.extract_info(channel_url, download=False)
        except Exception as e:
            print(f"[youtube] Failed to fetch channel {channel_url}: {e}")
            return FetchedContent(
                entity_name=entity_name, source_type="youtube", items=[], has_new_content=False
            )

        entries = playlist_info.get("entries", [])
        if not entries:
            # Single video URL, not a channel
            entries = [playlist_info]
        else:
            # yt-dlp sometimes returns channel *tabs* (Videos, Shorts, Live) as
            # top-level entries instead of actual videos. Tabs lack a `duration`
            # field and have channel IDs (not 11-char video IDs) as their `id`.
            # Detect this and re-extract from the Videos tab.
            # Real videos have 11-char IDs and positive duration; tabs have channel IDs and duration=0
            real_videos = [e for e in entries if e and (e.get("duration") or 0) > 0 and len(e.get("id", "")) == 11]
            if not real_videos:
                videos_tab = next(
                    (
                        e for e in entries
                        if e and (
                            (e.get("url", "") or "").rstrip("/").endswith("/videos")
                            or (e.get("webpage_url", "") or "").rstrip("/").endswith("/videos")
                        )
                    ),
                    entries[0] if entries else None,
                )
                tab_url = (videos_tab or {}).get("webpage_url") or (videos_tab or {}).get("url")
                if videos_tab and tab_url:
                    print(f"[youtube] Detected channel tabs — re-fetching Videos tab: {tab_url}")
                    try:
                        tab_info = ydl.extract_info(tab_url, download=False)
                        entries = tab_info.get("entries", []) or []
                        print(f"[youtube] Videos tab returned {len(entries)} entries")
                    except Exception as e:
                        print(f"[youtube] Failed to re-extract videos tab: {e}")
                        entries = []

    for entry in entries:
        if entry is None:
            continue
        video_id = entry.get("id", "")
        video_url = f"https://www.youtube.com/watch?v={video_id}"
        if seen_ids and video_url in seen_ids:
            skipped += 1
            continue
        if len(videos) >= max_videos:
            break

        video = _parse_video_entry(entry)

        # Attach pre-classified enrichment metadata if available
        if enrichment_index and video.video_id in enrichment_index:
            raw_entry = enrichment_index[video.video_id]
            video.enrichment = {
                "content_type": raw_entry.get("content_type", ""),
                "primary_topic": raw_entry.get("primary_topic", ""),
                "difficulty": raw_entry.get("difficulty", ""),
                "audience": raw_entry.get("audience", []),
                "entities_mentioned": (
                    raw_entry.get("entities", {}).get("companies", [])
                    + raw_entry.get("entities", {}).get("products", [])
                    + raw_entry.get("entities", {}).get("ai_models", [])
                ),
                "people_mentioned": raw_entry.get("entities", {}).get("people", []),
                "concepts": raw_entry.get("knowledge", {}).get("concepts", []),
                "pre_summary": raw_entry.get("knowledge", {}).get("summary", ""),
            }

        if include_transcripts:
            video.transcript = _fetch_transcript(video_id)
            time.sleep(1.5)  # rate limit mitigation

        if extract_frames and screenshots_dir:
            video.frames = _extract_frames(video, screenshots_dir, max_frames)

        videos.append(video)

    return FetchedContent(
        entity_name=entity_name,
        source_type="youtube",
        items=videos,
        has_new_content=len(videos) > 0,
        skipped_count=skipped,
    )


def _parse_video_entry(entry: dict) -> VideoInfo:
    video_id = entry.get("id", "")
    comments_raw = entry.get("comments", []) or []

    # Group replies by parent, attach to top-level comments
    top_level: list[tuple[str, Comment]] = []
    replies_map: dict[str, list[Comment]] = {}
    for c_raw in comments_raw:
        if not c_raw or not c_raw.get("text"):
            continue
        comment = Comment(
            text=c_raw.get("text", ""),
            author=c_raw.get("author", ""),
            like_count=c_raw.get("like_count", 0) or 0,
        )
        parent = c_raw.get("parent", "root")
        cid = c_raw.get("id", "")
        if parent == "root":
            top_level.append((cid, comment))
        else:
            replies_map.setdefault(parent, []).append(comment)

    for cid, comment in top_level:
        comment.replies = sorted(
            replies_map.get(cid, []), key=lambda c: c.like_count, reverse=True
        )

    comments = [c for _, c in top_level]
    comments.sort(key=lambda c: c.like_count, reverse=True)

    chapters = entry.get("chapters") or []
    heatmap = entry.get("heatmap") or []

    return VideoInfo(
        video_id=video_id,
        title=entry.get("title", ""),
        url=f"https://www.youtube.com/watch?v={video_id}",
        channel=entry.get("uploader", entry.get("channel", "")),
        upload_date=entry.get("upload_date", ""),
        duration_sec=entry.get("duration", 0) or 0,
        view_count=entry.get("view_count", 0) or 0,
        like_count=entry.get("like_count", 0) or 0,
        description=entry.get("description", "") or "",
        thumbnail_url=entry.get("thumbnail", ""),
        chapters=chapters,
        heatmap=heatmap,
        transcript=None,
        comments=comments,
    )


def _fetch_transcript(video_id: str) -> list[tuple[float, str]] | None:
    """Fetch transcript as list of (start_sec, text) tuples."""
    try:
        api = YouTubeTranscriptApi()
        segments = api.fetch(video_id)
        return [(s.start, s.text) for s in segments]
    except (TranscriptsDisabled, NoTranscriptFound):
        return None
    except Exception as e:
        print(f"[youtube] Transcript fetch failed for {video_id}: {e}")
        return None


def _get_key_timestamps(video: VideoInfo, max_frames: int) -> list[int]:
    """
    Determine which timestamps to extract frames at.
    Priority: heatmap peaks > chapter boundaries > evenly spaced.
    """
    timestamps: list[int] = []

    # 1. Heatmap peaks (most-replayed segments)
    if video.heatmap:
        sorted_segments = sorted(video.heatmap, key=lambda s: s.get("value", 0), reverse=True)
        for seg in sorted_segments[:max_frames]:
            ts = int(seg.get("start_time", 0))
            if ts not in timestamps:
                timestamps.append(ts)

    # 2. Chapter boundaries (if heatmap didn't fill slots)
    if len(timestamps) < max_frames and video.chapters:
        for chapter in video.chapters[:max_frames]:
            ts = int(chapter.get("start_time", 0))
            if ts not in timestamps and len(timestamps) < max_frames:
                timestamps.append(ts)

    # 3. Evenly spaced fallback
    if not timestamps and video.duration_sec > 0:
        interval = video.duration_sec / (max_frames + 1)
        timestamps = [int(interval * i) for i in range(1, max_frames + 1)]

    return sorted(timestamps[:max_frames])


def _extract_frames(
    video: VideoInfo, screenshots_dir: Path, max_frames: int
) -> list[VideoFrame]:
    """Download video and extract keyframes at key timestamps using ffmpeg."""
    video_dir = screenshots_dir / video.video_id
    video_dir.mkdir(parents=True, exist_ok=True)

    timestamps = _get_key_timestamps(video, max_frames)
    if not timestamps:
        return []

    # Download the video (smallest quality sufficient for screenshots)
    video_file = video_dir / f"{video.video_id}.mp4"
    if not video_file.exists():
        dl_opts = {
            "quiet": True,
            "format": "bestvideo[height<=480]+bestaudio/best[height<=480]/best",
            "outtmpl": str(video_dir / f"{video.video_id}.%(ext)s"),
        }
        try:
            with yt_dlp.YoutubeDL(dl_opts) as ydl:
                ydl.download([video.url])
        except Exception as e:
            print(f"[youtube] Video download failed for {video.video_id}: {e}")
            return []

    # Find the downloaded file (extension may vary)
    video_files = list(video_dir.glob(f"{video.video_id}.*"))
    video_files = [f for f in video_files if f.suffix in (".mp4", ".webm", ".mkv")]
    if not video_files:
        return []
    video_file = video_files[0]

    frames = []
    for ts in timestamps:
        frame_path = video_dir / f"frame_{ts:05d}.jpg"
        if not frame_path.exists():
            result = subprocess.run(
                [
                    "ffmpeg", "-ss", str(ts), "-i", str(video_file),
                    "-vframes", "1", "-q:v", "2", "-y", str(frame_path),
                ],
                capture_output=True,
                timeout=30,
            )
            if result.returncode != 0:
                print(f"[ffmpeg] Frame extraction failed at {ts}s for {video.video_id}")
                continue

        # Find chapter title for this timestamp
        chapter_title = ""
        for ch in video.chapters:
            if ch.get("start_time", 0) <= ts < ch.get("end_time", float("inf")):
                chapter_title = ch.get("title", "")
                break

        frames.append(
            VideoFrame(
                timestamp_sec=ts,
                image_path=str(frame_path),
                chapter_title=chapter_title,
            )
        )

    return frames


def transcript_to_text(transcript: list[tuple[float, str]]) -> str:
    """Join transcript segments into a single string."""
    return " ".join(text for _, text in transcript)


def format_timestamp_link(video_id: str, seconds: int) -> str:
    return f"https://youtu.be/{video_id}?t={seconds}"
