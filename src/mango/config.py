"""Configuration loading from per-user YAML files and environment variables."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml
from dotenv import dotenv_values


_CONFIG_DIR = Path(__file__).parent.parent.parent / "config"
_PROJECT_ROOT = Path(__file__).parent.parent.parent


def _load_env_files() -> None:
    """Load .env then .env.local, skipping keys already set to non-empty values.
    This handles the edge case where a shell exports a key as '' (empty string),
    which standard dotenv override=False treats as 'already set'."""
    for env_file in [_PROJECT_ROOT / ".env", _PROJECT_ROOT / ".env.local"]:
        if env_file.exists():
            for key, value in dotenv_values(env_file).items():
                if value and not os.environ.get(key):
                    os.environ[key] = value


_load_env_files()


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
    enrichment_source: str = ""      # e.g. "nate_transcripts" — activates enrichment module


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
    local_path: str = ""


@dataclass
class DigestConfig:
    email_to: str
    email_from: str
    subject: str = "Daily Intelligence Brief — {date}"
    send_time: str = "15:30 UTC"


@dataclass
class AppConfig:
    digest: DigestConfig
    entities: list[EntityConfig]
    projects: list[ProjectConfig] = field(default_factory=list)

    # Optional per-user template overrides (filename only, resolved from templates/)
    template_html: str = "digest.html.j2"
    template_txt: str = "digest.txt.j2"

    # API keys from environment (not in YAML)
    anthropic_api_key: str = ""
    resend_api_key: str = ""
    github_pat: str = ""


def load_config(config_path: str | None = None) -> AppConfig:
    """Load a single YAML config file. Falls back to config/james.yaml."""
    if config_path is None:
        # Prefer james.yaml; fall back to legacy entities.yaml
        for candidate in ("james.yaml", "entities.yaml"):
            p = _CONFIG_DIR / candidate
            if p.exists():
                config_path = str(p)
                break
        if config_path is None:
            raise FileNotFoundError(f"No config YAML found in {_CONFIG_DIR}")

    with open(config_path) as f:
        raw = yaml.safe_load(f)

    digest = DigestConfig(**raw["digest"])

    entities = []
    for e in raw.get("entities", []):
        sources_raw = e.pop("sources", [])
        sources = [SourceConfig(**s) for s in sources_raw]
        # Strip unknown keys so YAML can have extra metadata fields
        known = {f.name for f in EntityConfig.__dataclass_fields__.values()}
        e = {k: v for k, v in e.items() if k in known}
        entities.append(EntityConfig(sources=sources, **e))

    known_proj = {f.name for f in ProjectConfig.__dataclass_fields__.values()}
    projects = [
        ProjectConfig(**{k: v for k, v in p.items() if k in known_proj})
        for p in raw.get("projects", [])
    ]

    return AppConfig(
        digest=digest,
        entities=entities,
        projects=projects,
        template_html=raw.get("template_html", "digest.html.j2"),
        template_txt=raw.get("template_txt", "digest.txt.j2"),
        anthropic_api_key=os.environ.get("ANTHROPIC_API_KEY", ""),
        resend_api_key=os.environ.get("RESEND_API_KEY", ""),
        github_pat=os.environ.get("GH_PAT", os.environ.get("GITHUB_TOKEN", "")),
    )


def load_configs(config_dir: str | Path | None = None) -> dict[str, AppConfig]:
    """
    Load all *.yaml files from a directory, keyed by filename stem.
    Skips files prefixed with '_' or containing 'example'.
    """
    d = Path(config_dir) if config_dir else _CONFIG_DIR
    configs: dict[str, AppConfig] = {}

    for yaml_file in sorted(d.glob("*.yaml")):
        stem = yaml_file.stem
        if stem.startswith("_") or "example" in stem:
            continue
        try:
            configs[stem] = load_config(str(yaml_file))
        except Exception as e:
            print(f"[config] Failed to load {yaml_file.name}: {e}")

    return configs
