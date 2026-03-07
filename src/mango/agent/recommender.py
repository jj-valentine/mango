"""
Post-analysis project recommender.

After all entity summaries are generated, fetches the current state of the
user's GitHub projects and asks Claude to suggest "Build / Integrate" actions.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import anthropic
import requests

from ..config import AppConfig, ProjectConfig
from .researcher import EntitySummary


@dataclass
class Recommendation:
    category: str   # "build" | "integrate" | "quick_win"
    title: str
    description: str
    project: str = ""  # relevant existing project name, if any


@dataclass
class RecommenderOutput:
    recommendations: list[Recommendation] = field(default_factory=list)
    raw_text: str = ""
    error: str = ""


def generate_recommendations(
    summaries: list[EntitySummary],
    config: AppConfig,
    client: anthropic.Anthropic | None = None,
) -> RecommenderOutput:
    """Fetch project state from GitHub, then ask Claude for build/integrate recs."""
    if client is None:
        client = anthropic.Anthropic()

    if not config.projects and not summaries:
        return RecommenderOutput()

    # Collect all tool/concept mentions across entities
    all_tools: list[str] = []
    all_concepts: list[str] = []
    for s in summaries:
        all_tools.extend(s.tool_mentions)
        all_concepts.extend(s.key_concepts)
    all_tools = list(dict.fromkeys(all_tools))
    all_concepts = list(dict.fromkeys(all_concepts))

    # Fetch project context from GitHub
    project_sections: list[str] = []
    for project in config.projects:
        section = _fetch_project_context(project, config.github_pat)
        if section:
            project_sections.append(section)

    prompt_parts = ["You have just analyzed today's content and surfaced the following:\n"]

    if all_tools:
        prompt_parts.append(f"Tools mentioned: {', '.join(all_tools[:30])}")
    if all_concepts:
        prompt_parts.append(f"Key concepts: {', '.join(all_concepts[:30])}")

    if project_sections:
        prompt_parts.append(
            "\nCurrent state of the user's projects:\n" + "\n\n".join(project_sections)
        )
    else:
        prompt_parts.append("\n(No project context available.)")

    prompt_parts.append(
        "\nGenerate concise, specific recommendations in three categories:\n"
        "1. **Build** — new standalone tools or scripts worth creating (not covered by existing projects)\n"
        "2. **Integrate** — features or integrations to add to existing projects (name the project)\n"
        "3. **Quick Win** — one thing buildable in under 2 hours\n\n"
        "Be specific. No vague suggestions. Skip anything already implemented."
    )

    prompt = "\n".join(prompt_parts)

    try:
        response = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=1500,
            system=(
                "You are a senior software architect reviewing what the user learned today "
                "and what they are building. Give direct, actionable recommendations."
            ),
            messages=[{"role": "user", "content": prompt}],
        )
        raw_text = response.content[0].text
    except Exception as e:
        print(f"[recommender] Failed: {e}")
        return RecommenderOutput(error=str(e))

    return RecommenderOutput(
        recommendations=_parse_recommendations(raw_text),
        raw_text=raw_text,
    )


def _fetch_project_context(project: ProjectConfig, github_pat: str) -> str:
    """Fetch README and other files for a project from GitHub."""
    headers: dict[str, str] = {"Accept": "application/vnd.github.v3.raw"}
    if github_pat:
        headers["Authorization"] = f"Bearer {github_pat}"

    sections = [f"## {project.repo}"]

    for filename in project.files:
        url = f"https://api.github.com/repos/{project.repo}/contents/{filename}"
        try:
            resp = requests.get(url, headers=headers, timeout=10)
            resp.raise_for_status()
            content = resp.text
            # Truncate very large files
            if len(content) > 3000:
                content = content[:3000] + "\n...[truncated]"
            sections.append(f"### {filename}\n{content}")
        except Exception as e:
            print(f"[recommender] Could not fetch {project.repo}/{filename}: {e}")
            sections.append(f"### {filename}\n(unavailable)")

    return "\n\n".join(sections)


def _parse_recommendations(text: str) -> list[Recommendation]:
    """Best-effort parse of Claude's markdown output into Recommendation objects."""
    recs: list[Recommendation] = []
    current_category = ""
    current_title = ""
    current_desc_lines: list[str] = []

    def flush():
        if current_title:
            recs.append(Recommendation(
                category=current_category,
                title=current_title,
                description=" ".join(current_desc_lines).strip(),
            ))

    for line in text.splitlines():
        stripped = line.strip()
        lower = stripped.lower()

        if "**build**" in lower or lower.startswith("## build") or lower.startswith("1."):
            flush()
            current_category = "build"
            current_title = ""
            current_desc_lines = []
        elif "**integrate**" in lower or lower.startswith("## integrate") or lower.startswith("2."):
            flush()
            current_category = "integrate"
            current_title = ""
            current_desc_lines = []
        elif "**quick win**" in lower or lower.startswith("## quick") or lower.startswith("3."):
            flush()
            current_category = "quick_win"
            current_title = ""
            current_desc_lines = []
        elif stripped.startswith("- ") or stripped.startswith("* "):
            flush()
            current_title = stripped.lstrip("-* ").strip()
            current_desc_lines = []
        elif stripped.startswith("**") and stripped.endswith("**"):
            flush()
            current_title = stripped.strip("*").strip()
            current_desc_lines = []
        elif stripped and current_title:
            current_desc_lines.append(stripped)

    flush()
    return recs
