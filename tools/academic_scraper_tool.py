"""University Syllabus Search & Scraper Tool for Academic Agent.

Scrapes syllabi, course structures, and curriculum outlines from top-tier
universities (Harvard, Oxford, MIT, Stanford, Cambridge, UC Berkeley, CMU).
Provides grounded, domain-specific academic slivers to construct rigorous skill graphs.
"""

import asyncio
import logging
import re
from typing import Any, Dict, List, Optional
import httpx

logger = logging.getLogger(__name__)

# Premier target university domains
TARGET_UNIVERSITIES = [
    {"name": "Harvard University", "domain": "harvard.edu"},
    {"name": "University of Oxford", "domain": "ox.ac.uk"},
    {"name": "Massachusetts Institute of Technology (MIT)", "domain": "mit.edu"},
    {"name": "Stanford University", "domain": "stanford.edu"},
    {"name": "University of Cambridge", "domain": "cam.ac.uk"},
    {"name": "UC Berkeley", "domain": "berkeley.edu"},
    {"name": "Carnegie Mellon University", "domain": "cmu.edu"},
]

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

_HTML_TAG_RE = re.compile(r"<[^>]+>")
_MULTIPLE_NEWLINES = re.compile(r"\n\s*\n+")
_MULTIPLE_SPACES = re.compile(r"[ \t]+")


def clean_html_to_text(html: str, max_chars: int = 1500) -> str:
    """Strips boilerplate, scripts, and HTML tags, returning compact text."""
    if not html:
        return ""
    # Strip script and style blocks
    cleaned = re.sub(r"<(script|style|nav|header|footer|form)[^>]*>.*?</\1>", " ", html, flags=re.DOTALL | re.IGNORECASE)
    # Strip remaining tags
    cleaned = _HTML_TAG_RE.sub(" ", cleaned)
    # Normalize whitespace
    cleaned = _MULTIPLE_SPACES.sub(" ", cleaned)
    cleaned = _MULTIPLE_NEWLINES.sub("\n", cleaned)
    return cleaned.strip()[:max_chars]


class UniversityScraperService:
    """Searches and scrapes syllabi from Harvard, Oxford, MIT, Stanford, etc."""

    def __init__(self, timeout_seconds: float = 8.0, max_results_per_query: int = 4):
        self.timeout = timeout_seconds
        self.max_results = max_results_per_query

    def search_university_syllabi(self, domain_topic: str) -> List[Dict[str, Any]]:
        """Searches for syllabi and course outlines across target universities.
        Uses ddgs to search Google/DuckDuckGo index for university syllabus pages.
        """
        results_list = []
        try:
            from ddgs import DDGS

            # Construct targeted site-specific search query
            # e.g., 'machine learning syllabus course curriculum site:harvard.edu OR site:ox.ac.uk ...'
            site_filter = " OR ".join(f"site:{u['domain']}" for u in TARGET_UNIVERSITIES[:5])
            query = f"{domain_topic} syllabus OR curriculum OR course outline ({site_filter})"

            logger.info("UniversityScraper: searching university curricula for query %r", domain_topic)
            with DDGS() as ddgs:
                raw_results = list(ddgs.text(query, max_results=self.max_results))

            for r in raw_results:
                url = (r.get("href") or r.get("url") or "").strip()
                title = (r.get("title") or "").strip()
                snippet = (r.get("body") or "").strip()

                # Determine which university this belongs to
                matched_uni = "Accredited University"
                for u in TARGET_UNIVERSITIES:
                    if u["domain"] in url.lower():
                        matched_uni = u["name"]
                        break

                results_list.append({
                    "university": matched_uni,
                    "title": title,
                    "url": url,
                    "snippet": snippet,
                })

            logger.info("UniversityScraper: found %d syllabus link(s)", len(results_list))
        except Exception:
            logger.exception("UniversityScraper: error searching university syllabi")
        return results_list

    async def scrape_page_content(self, url: str) -> Optional[str]:
        """Asynchronously fetches and cleans the text of a syllabus page."""
        if not url:
            return None
        try:
            async with httpx.AsyncClient(timeout=self.timeout, headers=DEFAULT_HEADERS, follow_redirects=True) as client:
                resp = await client.get(url)
                if resp.status_code == 200 and "text/html" in resp.headers.get("content-type", ""):
                    return clean_html_to_text(resp.text)
        except Exception as e:
            logger.debug("UniversityScraper: failed to fetch page %s (%s)", url, e)
        return None

    async def get_grounded_curricula(self, domain_topic: str) -> List[Dict[str, Any]]:
        """Searches target universities and scrapes syllabus content."""
        search_results = self.search_university_syllabi(domain_topic)
        if not search_results:
            return []

        # Scrape top 2-3 links concurrently to stay fast and avoid blocking
        tasks = [self.scrape_page_content(item["url"]) for item in search_results[:3]]
        scraped_texts = await asyncio.gather(*tasks, return_exceptions=True)

        grounded_items = []
        for i, item in enumerate(search_results[:3]):
            text = scraped_texts[i] if i < len(scraped_texts) and isinstance(scraped_texts[i], str) else None
            # If full page fetch failed, use the search snippet as reliable fallback
            content = text if text and len(text) > 100 else item["snippet"]
            if content:
                grounded_items.append({
                    "university": item["university"],
                    "title": item["title"],
                    "url": item["url"],
                    "content": content[:1200],
                })

        return grounded_items

    def get_grounded_curricula_sync(self, domain_topic: str) -> List[Dict[str, Any]]:
        """Synchronous wrapper for graph nodes and unit test compatibility."""
        try:
            return asyncio.run(self.get_grounded_curricula(domain_topic))
        except RuntimeError:
            # If already running inside an active event loop (e.g. inside FastAPI / LangGraph async runner)
            search_results = self.search_university_syllabi(domain_topic)
            return [
                {
                    "university": r["university"],
                    "title": r["title"],
                    "url": r["url"],
                    "content": r["snippet"][:1000],
                }
                for r in search_results
            ]

    def format_for_prompt(self, curricula: List[Dict[str, Any]]) -> str:
        """Formats scraped university curricula into a clean prompt block."""
        if not curricula:
            return ""

        blocks = ["=== GROUNDED UNIVERSITY SYLLABI & SLIVERS (Harvard, Oxford, MIT, Stanford, etc.) ==="]
        for c in curricula:
            blocks.append(
                f"- Institution: {c['university']}\n"
                f"  Course: {c['title']}\n"
                f"  URL: {c['url']}\n"
                f"  Curriculum / Syllabus Content:\n    {c['content']}\n"
            )
        return "\n".join(blocks)
