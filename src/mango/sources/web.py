"""Web page source handler: requests + readability, Playwright fallback for JS-heavy sites."""
from __future__ import annotations

import asyncio

import requests
from readability import Document

from .base import FeedItem, FetchedContent

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
}


def fetch_web_page(url: str, entity_name: str = "", focus: str = "") -> FetchedContent:
    """Fetch a single web page, extract readable content."""
    content = _fetch_content(url)
    item = FeedItem(
        title=content.get("title", url),
        url=url,
        summary="",
        published="",
        content=content.get("text", ""),
    )
    return FetchedContent(
        entity_name=entity_name,
        source_type="web",
        items=[item],
        has_new_content=bool(item.content),
    )


def _fetch_content(url: str) -> dict:
    """Try requests first; fall back to Playwright for JS-heavy pages."""
    try:
        resp = requests.get(url, timeout=20, headers=_HEADERS)
        resp.raise_for_status()
        doc = Document(resp.text)
        text = doc.summary(html_partial=True)
        # Strip HTML tags for plain text
        import re
        text = re.sub(r"<[^>]+>", " ", text)
        text = re.sub(r"\s+", " ", text).strip()
        if len(text) > 200:
            return {"title": doc.title(), "text": text}
    except Exception as e:
        print(f"[web] requests failed for {url}: {e}")

    # Playwright fallback
    return asyncio.run(_fetch_with_playwright(url))


async def _fetch_with_playwright(url: str) -> dict:
    try:
        from playwright.async_api import async_playwright
        import re

        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page()
            await page.goto(url, wait_until="networkidle", timeout=30000)
            html = await page.content()
            title = await page.title()
            await browser.close()

        doc = Document(html)
        text = doc.summary(html_partial=True)
        text = re.sub(r"<[^>]+>", " ", text)
        text = re.sub(r"\s+", " ", text).strip()
        return {"title": title or doc.title(), "text": text}
    except Exception as e:
        print(f"[web] Playwright fallback failed for {url}: {e}")
        return {"title": "", "text": ""}
