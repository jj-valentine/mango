"""
Jinja2 + premailer email renderer.

Produces HTML and plain-text versions of the daily digest.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path

import markdown
from jinja2 import Environment, FileSystemLoader, select_autoescape
from premailer import transform

from ..agent.recommender import RecommenderOutput
from ..agent.researcher import EntitySummary


_TEMPLATES_DIR = Path(__file__).parent / "templates"

# Markdown extensions for richer HTML output
_MD_EXTENSIONS = ["fenced_code", "tables", "nl2br", "sane_lists"]
# Regex to strip metadata lines Claude appends (TOOLS:, CONCEPTS:)
_META_LINE_RE = re.compile(r"^(TOOLS|CONCEPTS):\s*\[.*?\]\s*$", re.MULTILINE)


def render_email(
    summaries: list[EntitySummary],
    recommendations: RecommenderOutput | None,
    run_at: datetime | None = None,
    template_html: str = "digest.html.j2",
    template_txt: str = "digest.txt.j2",
) -> tuple[str, str]:
    """
    Returns (html_body, plain_body) for the digest.
    Both are fully rendered strings ready to pass to the Resend API.
    """
    if run_at is None:
        run_at = datetime.now(timezone.utc)

    date_str = run_at.strftime("%A, %B %-d, %Y")
    run_at_str = run_at.strftime("%Y-%m-%d %H:%M UTC")

    no_new_content = [s.entity_name for s in summaries if not s.has_new_content]
    active_summaries = [s for s in summaries if s.has_new_content]

    # Pre-render markdown in analysis fields
    active_summaries = [_render_summary_markdown(s) for s in active_summaries]

    # Pre-render recommendations markdown
    recs = _render_recs_markdown(recommendations) if recommendations else None

    context = {
        "summaries": active_summaries,
        "all_summaries": summaries,
        "recommendations": recs,
        "date": date_str,
        "run_at": run_at_str,
        "no_new_content": no_new_content,
    }

    env = Environment(
        loader=FileSystemLoader(str(_TEMPLATES_DIR)),
        autoescape=select_autoescape(["html"]),
    )
    # Disable HTML auto-escaping for the | safe filter to work correctly
    env.autoescape = False

    html_template = env.get_template(template_html)
    txt_template = env.get_template(template_txt)

    raw_html = html_template.render(**context)
    # Inline all CSS for email client compatibility
    try:
        final_html = transform(
            raw_html,
            base_url=None,
            preserve_internal_links=True,
            remove_classes=False,
        )
    except Exception:
        final_html = raw_html  # premailer failure is non-fatal

    plain = txt_template.render(**context)

    return final_html, plain


def _render_summary_markdown(summary: EntitySummary) -> EntitySummary:
    """Convert analysis text fields from markdown to HTML in-place (returns same obj)."""
    for vs in summary.video_summaries:
        vs.analysis = _md_to_html(vs.analysis)
    for fs in summary.feed_summaries:
        fs.analysis = _md_to_html(fs.analysis)
    return summary


def _render_recs_markdown(recs: RecommenderOutput) -> RecommenderOutput:
    recs.raw_text = _md_to_html(recs.raw_text)
    return recs


def _md_to_html(text: str) -> str:
    if not text:
        return ""
    # Strip metadata lines before rendering
    clean = _META_LINE_RE.sub("", text).strip()
    return markdown.markdown(clean, extensions=_MD_EXTENSIONS)
