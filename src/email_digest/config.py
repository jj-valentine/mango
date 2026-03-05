"""Configuration loading from entities.yaml and environment variables."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


@dataclass
class SourceConfig:
    type: str                        # youtube | rss | api | web
    url: str
    max_videos: int = 3
    max_items: int = 10
    include_transcripts: bool = True
    include_comments: bool = True
    extract_frames: bool = False
    max_frames: int = 3
    item_url: str = ""               # for API sources: URL template for individual items


@dataclass
class EntityConfig:
    name: str
    description: str
    model: str
    directive: str
    sources: list[SourceConfig]
    include_comments: bool = True
    max_comments: int = 30


@dataclass
class ProjectConfig:
    repo: str
    files: list[str] = field(default_factory=lambda: ["README.md"])


@dataclass
class DigestConfig:
    email_to: str
    email_from: str
    subject: str = "Daily Intelligence Brief — {date}"
    send_time: str = "07:00 UTC"


@dataclass
class AppConfig:
    digest: DigestConfig
    entities: list[EntityConfig]
    projects: list[ProjectConfig] = field(default_factory=list)

    # API keys from environment
    anthropic_api_key: str = ""
    resend_api_key: str = ""
    github_pat: str = ""


def load_config(config_path: str | None = None) -> AppConfig:
    if config_path is None:
        config_path = Path(__file__).parent.parent.parent / "config" / "entities.yaml"

    with open(config_path) as f:
        raw = yaml.safe_load(f)

    digest = DigestConfig(**raw["digest"])

    entities = []
    for e in raw.get("entities", []):
        sources = [SourceConfig(**s) for s in e.pop("sources", [])]
        entities.append(EntityConfig(sources=sources, **e))

    projects = [ProjectConfig(**p) for p in raw.get("projects", [])]

    return AppConfig(
        digest=digest,
        entities=entities,
        projects=projects,
        anthropic_api_key=os.environ.get("ANTHROPIC_API_KEY", ""),
        resend_api_key=os.environ.get("RESEND_API_KEY", ""),
        github_pat=os.environ.get("GH_PAT", os.environ.get("GITHUB_TOKEN", "")),
    )
