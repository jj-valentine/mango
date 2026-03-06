"""
Per-entity Claude researcher.

Runs a single Claude call (with prompt caching on large content) per entity
and returns a structured EntitySummary.
"""
from __future__ import annotations

import json
import textwrap
from dataclasses import dataclass, field

import anthropic

from ..config import EntityConfig
from ..sources.base import Comment, FetchedContent, VideoInfo, FeedItem
from ..sources.youtube import transcript_to_text, format_timestamp_link


# ---------------------------------------------------------------------------
# Output types
# ---------------------------------------------------------------------------

@dataclass
class VideoSummary:
    video_id: str
    title: str
    url: str
    duration_str: str
    view_count: int
    like_count: int
    thumbnail_url: str
    analysis: str                     # Claude's markdown analysis
    tool_mentions: list[str] = field(default_factory=list)
    key_concepts: list[str] = field(default_factory=list)
    frame_descriptions: list[dict] = field(default_factory=list)  # [{ts, url, description}]


@dataclass
class FeedSummary:
    title: str
    url: str
    published: str
    analysis: str
    tool_mentions: list[str] = field(default_factory=list)
    key_concepts: list[str] = field(default_factory=list)


@dataclass
class EntitySummary:
    entity_name: str
    model: str
    source_type: str
    video_summaries: list[VideoSummary] = field(default_factory=list)
    feed_summaries: list[FeedSummary] = field(default_factory=list)
    has_new_content: bool = True
    skipped_count: int = 0
    error: str = ""

    @property
    def tool_mentions(self) -> list[str]:
        tools: list[str] = []
        for v in self.video_summaries:
            tools.extend(v.tool_mentions)
        for f in self.feed_summaries:
            tools.extend(f.tool_mentions)
        return list(dict.fromkeys(tools))  # deduplicate, preserve order

    @property
    def key_concepts(self) -> list[str]:
        concepts: list[str] = []
        for v in self.video_summaries:
            concepts.extend(v.key_concepts)
        for f in self.feed_summaries:
            concepts.extend(f.key_concepts)
        return list(dict.fromkeys(concepts))


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def analyze_entity(
    entity: EntityConfig,
    content: FetchedContent,
    client: anthropic.Anthropic | None = None,
) -> EntitySummary:
    """Run Claude analysis for a single entity and return an EntitySummary."""
    if client is None:
        client = anthropic.Anthropic()

    if not content.has_new_content or not content.items:
        return EntitySummary(
            entity_name=entity.name,
            model=entity.model,
            source_type=content.source_type,
            has_new_content=False,
            skipped_count=content.skipped_count,
        )

    if content.source_type == "youtube":
        return _analyze_youtube(entity, content, client)
    else:
        return _analyze_feed(entity, content, client)


# ---------------------------------------------------------------------------
# YouTube analysis
# ---------------------------------------------------------------------------

def _analyze_youtube(
    entity: EntityConfig, content: FetchedContent, client: anthropic.Anthropic
) -> EntitySummary:
    videos: list[VideoInfo] = content.items
    video_summaries = []

    for video in videos:
        vs = _analyze_single_video(entity, video, client)
        video_summaries.append(vs)

    return EntitySummary(
        entity_name=entity.name,
        model=entity.model,
        source_type="youtube",
        video_summaries=video_summaries,
        has_new_content=True,
        skipped_count=content.skipped_count,
    )


def _analyze_single_video(
    entity: EntityConfig, video: VideoInfo, client: anthropic.Anthropic
) -> VideoSummary:
    # Build content blocks with caching on the large transcript
    content_blocks: list[dict] = []

    # 1. Transcript (cacheable — large, static per video)
    transcript_text = ""
    if video.transcript:
        transcript_text = transcript_to_text(video.transcript)
        if transcript_text:
            content_blocks.append({
                "type": "text",
                "text": f"TRANSCRIPT:\n{transcript_text}",
                "cache_control": {"type": "ephemeral"},
            })

    # 2. Metadata + comments + frame descriptions (non-cached)
    metadata = _build_video_metadata(video)
    content_blocks.append({"type": "text", "text": metadata})

    # 3. Directive
    content_blocks.append({
        "type": "text",
        "text": (
            f"Entity: {entity.name} ({entity.description})\n\n"
            f"DIRECTIVE:\n{entity.directive.strip()}\n\n"
            "Respond in markdown. Be specific and concise. "
            "For tool mentions, list them as a JSON array on a line starting with "
            "`TOOLS:` (e.g. `TOOLS: [\"n8n\", \"LangChain\"]`). "
            "For key concepts, list them as `CONCEPTS: [\"...\"]`."
        ),
    })

    try:
        response = client.messages.create(
            model=entity.model,
            max_tokens=2000,
            system=(
                "You are an expert research analyst. Produce concise, opinionated, "
                "and specific analysis. Avoid filler. Surface what is genuinely novel."
            ),
            messages=[{"role": "user", "content": content_blocks}],
        )
        analysis_text = response.content[0].text
    except Exception as e:
        print(f"[researcher] Analysis failed for {video.title}: {e}")
        analysis_text = f"[Analysis failed: {e}]"

    tools = _extract_json_list(analysis_text, "TOOLS:")
    concepts = _extract_json_list(analysis_text, "CONCEPTS:")

    # Build frame description dicts for the email
    frame_descs = []
    for frame in video.frames:
        frame_descs.append({
            "timestamp_sec": frame.timestamp_sec,
            "youtube_url": format_timestamp_link(video.video_id, frame.timestamp_sec),
            "github_url": frame.github_url,
            "description": frame.vision_description,
            "chapter_title": frame.chapter_title,
        })

    duration_str = _format_duration(video.duration_sec)

    return VideoSummary(
        video_id=video.video_id,
        title=video.title,
        url=video.url,
        duration_str=duration_str,
        view_count=video.view_count,
        like_count=video.like_count,
        thumbnail_url=video.thumbnail_url,
        analysis=analysis_text,
        tool_mentions=tools,
        key_concepts=concepts,
        frame_descriptions=frame_descs,
    )


def _build_video_metadata(video: VideoInfo) -> str:
    lines = [
        f"VIDEO: {video.title}",
        f"URL: {video.url}",
        f"Channel: {video.channel}",
        f"Uploaded: {video.upload_date}",
        f"Duration: {_format_duration(video.duration_sec)}",
        f"Views: {video.view_count:,}  Likes: {video.like_count:,}",
        "",
        f"DESCRIPTION:\n{video.description[:1000]}",
        "",
    ]

    if video.chapters:
        lines.append("CHAPTERS:")
        for ch in video.chapters:
            ts = int(ch.get("start_time", 0))
            lines.append(f"  [{_format_duration(ts)}] {ch.get('title', '')}")
        lines.append("")

    if video.heatmap:
        peaks = sorted(video.heatmap, key=lambda s: s.get("value", 0), reverse=True)[:3]
        lines.append("HEATMAP PEAKS (most replayed):")
        for p in peaks:
            ts = int(p.get("start_time", 0))
            lines.append(f"  {_format_duration(ts)} — replay value {p.get('value', 0):.2f}")
        lines.append("")

    if video.frames:
        lines.append("EXTRACTED FRAMES (key visual moments):")
        for frame in video.frames:
            ts = frame.timestamp_sec
            chapter = f" [{frame.chapter_title}]" if frame.chapter_title else ""
            desc = frame.vision_description or "(not analyzed)"
            lines.append(f"  {_format_duration(ts)}{chapter}: {desc}")
        lines.append("")

    if video.comments:
        lines.append("TOP COMMENTS (by likes):")
        for c in video.comments[:10]:
            lines.append(
                f"  [{c.like_count} likes] {c.author}: "
                + textwrap.shorten(c.text, width=200, placeholder="...")
            )
        lines.append("")

    # Pre-classified enrichment metadata (from external source, e.g. nate_transcripts)
    if video.enrichment:
        e = video.enrichment
        lines.append("PRE-CLASSIFIED ENRICHMENT (external source):")
        if e.get("content_type"):
            lines.append(f"  Content type: {e['content_type']}")
        if e.get("difficulty"):
            lines.append(f"  Difficulty: {e['difficulty']}")
        if e.get("audience"):
            audience = e["audience"] if isinstance(e["audience"], str) else ", ".join(e["audience"])
            lines.append(f"  Audience: {audience}")
        if e.get("primary_topic"):
            lines.append(f"  Primary topic: {e['primary_topic']}")
        if e.get("entities_mentioned"):
            lines.append(f"  Tools/entities: {', '.join(e['entities_mentioned'][:15])}")
        if e.get("people_mentioned"):
            lines.append(f"  People mentioned: {', '.join(e['people_mentioned'][:10])}")
        if e.get("concepts"):
            lines.append(f"  Concepts: {', '.join(e['concepts'][:10])}")
        if e.get("pre_summary"):
            lines.append(f"  Pre-written summary: {textwrap.shorten(e['pre_summary'], 400, placeholder='...')}")
        lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Feed/API analysis (RSS, Hacker News, etc.)
# ---------------------------------------------------------------------------

def _analyze_feed(
    entity: EntityConfig, content: FetchedContent, client: anthropic.Anthropic
) -> EntitySummary:
    items: list[FeedItem] = content.items

    # Build a single combined prompt for all items (HN style: list summarization)
    items_text = _build_feed_items_text(items)

    prompt = (
        f"Entity: {entity.name} ({entity.description})\n\n"
        f"ITEMS:\n{items_text}\n\n"
        f"DIRECTIVE:\n{entity.directive.strip()}\n\n"
        "Respond in markdown. Be specific and concise. "
        "List tool mentions as `TOOLS: [\"...\"]` and key concepts as `CONCEPTS: [\"...\"]`."
    )

    try:
        response = client.messages.create(
            model=entity.model,
            max_tokens=2000,
            system=(
                "You are an expert research analyst. Surface what is genuinely useful "
                "and novel. Be direct and specific."
            ),
            messages=[{"role": "user", "content": prompt}],
        )
        analysis_text = response.content[0].text
    except Exception as e:
        print(f"[researcher] Feed analysis failed for {entity.name}: {e}")
        analysis_text = f"[Analysis failed: {e}]"

    tools = _extract_json_list(analysis_text, "TOOLS:")
    concepts = _extract_json_list(analysis_text, "CONCEPTS:")

    feed_summaries = [
        FeedSummary(
            title=item.title,
            url=item.url,
            published=item.published,
            analysis="",  # full analysis is the combined text above
            tool_mentions=tools,
            key_concepts=concepts,
        )
        for item in items
    ]

    # Attach the combined analysis to the first summary for display purposes
    if feed_summaries:
        feed_summaries[0].analysis = analysis_text

    return EntitySummary(
        entity_name=entity.name,
        model=entity.model,
        source_type=content.source_type,
        feed_summaries=feed_summaries,
        has_new_content=True,
        skipped_count=content.skipped_count,
    )


def _build_feed_items_text(items: list[FeedItem]) -> str:
    lines = []
    for i, item in enumerate(items, 1):
        lines.append(f"{i}. {item.title}")
        lines.append(f"   URL: {item.url}")
        if item.summary:
            lines.append(f"   Summary: {textwrap.shorten(item.summary, 300, placeholder='...')}")
        if item.comments:
            top = item.comments[0]
            lines.append(f"   Top comment ({top.author}): {textwrap.shorten(top.text, 200, placeholder='...')}")
        lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------

def _format_duration(seconds: int) -> str:
    h, rem = divmod(int(seconds), 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


def _extract_json_list(text: str, marker: str) -> list[str]:
    """Extract a JSON array from a line starting with `marker`."""
    for line in text.splitlines():
        if line.strip().startswith(marker):
            json_part = line.strip()[len(marker):].strip()
            try:
                result = json.loads(json_part)
                if isinstance(result, list):
                    return [str(x) for x in result]
            except json.JSONDecodeError:
                pass
    return []
