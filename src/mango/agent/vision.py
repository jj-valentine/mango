"""Claude Vision frame analysis — describes video frames extracted by youtube.py."""
from __future__ import annotations

import base64
from pathlib import Path

import anthropic

from ..sources.base import VideoFrame, VideoInfo


def analyze_frames(video: VideoInfo, client: anthropic.Anthropic | None = None) -> None:
    """
    Fill in `vision_description` for each frame in `video.frames`.
    Modifies frames in-place.
    """
    if not video.frames:
        return

    if client is None:
        client = anthropic.Anthropic()

    for frame in video.frames:
        if frame.vision_description:
            continue  # already analyzed (e.g. from cache)
        frame.vision_description = _describe_frame(
            frame, video.title, client
        )


def _describe_frame(
    frame: VideoFrame, video_title: str, client: anthropic.Anthropic
) -> str:
    path = Path(frame.image_path)
    if not path.exists():
        return ""

    try:
        with open(path, "rb") as f:
            image_b64 = base64.standard_b64encode(f.read()).decode()
    except OSError as e:
        print(f"[vision] Cannot read {path}: {e}")
        return ""

    ts = frame.timestamp_sec
    ts_str = f"{ts // 60}:{ts % 60:02d}"
    chapter_hint = f" (chapter: {frame.chapter_title})" if frame.chapter_title else ""

    prompt = (
        f"Video: '{video_title}' — frame at {ts_str}{chapter_hint}.\n"
        "Describe what is visible in 1-2 concise sentences: any diagrams, code, "
        "text on screen, charts, or key visuals. If nothing notable, say so briefly."
    )

    try:
        response = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=200,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": "image/jpeg",
                                "data": image_b64,
                            },
                        },
                        {"type": "text", "text": prompt},
                    ],
                }
            ],
        )
        return response.content[0].text.strip()
    except Exception as e:
        print(f"[vision] Frame analysis failed for {path.name}: {e}")
        return ""
