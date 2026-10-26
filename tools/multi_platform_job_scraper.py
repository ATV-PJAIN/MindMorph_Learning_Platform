"""Multi-Platform Open Job Description Search & Scraper Tool for Market Agent.

Gathers real-time job descriptions across open platforms without requiring login:
  - Indeed (indeed.com)
  - Naukri (naukri.com)
  - Glassdoor (glassdoor.com)
  - LinkedIn (open JDs + optional Apify MCP)
  - Google / Open Web Job Boards (Greenhouse, Lever, Workday)

Parses titles, organizations, key skills, and requirements into standardized job
dictionaries for employer grouping and skill frequency calculation.
"""

import asyncio
import logging
import re
from typing import Any, Dict, List, Optional
import httpx

logger = logging.getLogger(__name__)

# Common tech skill keywords to reliably extract from job postings and snippets
COMMON_SKILL_PATTERNS = [
    # Languages
    r"\bPython\b", r"\bJavaScript\b", r"\bTypeScript\b", r"\bJava\b", r"\bC\+\+\b", r"\bGolang\b", r"\bGo\b",
    r"\bRust\b", r"\bRuby\b", r"\bPHP\b", r"\bScala\b", r"\bKotlin\b", r"\bSwift\b", r"\bC#\b",
    # Frameworks / Web
    r"\bReact\b", r"\bNext\.js\b", r"\bVue\b", r"\bAngular\b", r"\bNode\.js\b", r"\bExpress\b", r"\bDjango\b",
    r"\bFastAPI\b", r"\bFlask\b", r"\bSpring Boot\b", r"\bGraphQL\b", r"\bREST APIs?\b",
    # Data & AI / ML
    r"\bMachine Learning\b", r"\bDeep Learning\b", r"\bNLP\b", r"\bComputer Vision\b", r"\bLLMs?\b",
    r"\bPyTorch\b", r"\bTensorFlow\b", r"\bScikit-Learn\b", r"\bPandas\b", r"\bNumPy\b", r"\bKeras\b",
    r"\bLangChain\b", r"\bLangGraph\b", r"\bHuggingFace\b", r"\bTransformers\b",
    # Cloud & DevOps
    r"\bAWS\b", r"\bAzure\b", r"\bGCP\b", r"\bDocker\b", r"\bKubernetes\b", r"\bTerraform\b", r"\bCI/CD\b",
    r"\bLinux\b", r"\bKafka\b", r"\bAirflow\b", r"\bSpark\b",
    # Databases
    r"\bPostgreSQL\b", r"\bMySQL\b", r"\bMongoDB\b", r"\bRedis\b", r"\bNeo4j\b", r"\bSnowflake\b", r"\bBigQuery\b",
    r"\bSQL\b", r"\bNoSQL\b", r"\bVector Databases?\b", r"\bPinecone\b",
]

_SKILL_REGEXES = [re.compile(p, re.IGNORECASE) for p in COMMON_SKILL_PATTERNS]

PLATFORM_SEARCH_TARGETS = [
    {"name": "Indeed", "site": "site:indeed.com", "label": "Indeed"},
    {"name": "Naukri", "site": "site:naukri.com", "label": "Naukri"},
    {"name": "Glassdoor", "site": "site:glassdoor.com", "label": "Glassdoor"},
    {"name": "LinkedIn", "site": "site:linkedin.com/jobs", "label": "LinkedIn"},
    {"name": "Open Web Boards", "site": "site:boards.greenhouse.io OR site:jobs.lever.co", "label": "Greenhouse/Lever"},
]

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}


CANONICAL_SKILL_NAMES = {
    "postgresql": "PostgreSQL",
    "mysql": "MySQL",
    "mongodb": "MongoDB",
    "graphql": "GraphQL",
    "pytorch": "PyTorch",
    "tensorflow": "TensorFlow",
    "scikit-learn": "Scikit-Learn",
    "langchain": "LangChain",
    "langgraph": "LangGraph",
    "huggingface": "HuggingFace",
    "aws": "AWS",
    "gcp": "GCP",
    "sql": "SQL",
    "nosql": "NoSQL",
    "rest": "REST APIs",
    "ci/cd": "CI/CD",
    "nlp": "NLP",
    "llm": "LLMs",
    "llms": "LLMs",
}


def extract_skills_from_text(text: str) -> List[str]:
    """Extracts recognized technical skills from job description text."""
    if not text:
        return []
    found_skills = set()
    for regex in _SKILL_REGEXES:
        match = regex.search(text)
        if match:
            raw = match.group(0).strip()
            canonical = CANONICAL_SKILL_NAMES.get(raw.lower(), raw.title())
            found_skills.add(canonical)
    return sorted(list(found_skills))


def extract_org_from_title_or_snippet(title: str, snippet: str, platform_label: str) -> str:
    """Heuristic extraction of company name from job search hit."""
    # Often formatted as: "Job Title - Company Name - Location | Platform"
    parts = re.split(r"[-–|·]", title)
    if len(parts) >= 2:
        candidate = parts[1].strip()
        # Filter out platform names
        if not any(p.lower() in candidate.lower() for p in ("indeed", "naukri", "glassdoor", "linkedin", "job")):
            return candidate
    # Fallback to snippet match: "at [Company]"
    match = re.search(r"\bat\s+([A-Z][A-Za-z0-9&.\s]{2,25})\b", snippet)
    if match:
        return match.group(1).strip()
    return f"{platform_label} Employer"


class MultiPlatformJobScraper:
    """Searches and scrapes open JDs across Indeed, Naukri, Glassdoor, LinkedIn, and Web."""

    def __init__(self, timeout_seconds: float = 6.0, max_postings_per_platform: int = 3):
        self.timeout = timeout_seconds
        self.max_per_platform = max_postings_per_platform

    def search_platform_jobs(self, role_title: str, target: Dict[str, str]) -> List[Dict[str, Any]]:
        """Searches DuckDuckGo / Google index for open JDs on a specific platform."""
        postings = []
        try:
            from ddgs import DDGS

            # Simpler, cleaner query that matches across all search backends
            query = f"{role_title} hiring job description {target['name']}"
            logger.info("MultiPlatformJobScraper: searching %s for %r", target["name"], role_title)

            with DDGS(timeout=8) as ddgs:
                results = list(ddgs.text(query, max_results=self.max_per_platform))

            for r in results:
                url = (r.get("href") or r.get("url") or "").strip()
                title = (r.get("title") or "").strip()
                snippet = (r.get("body") or "").strip()

                org = extract_org_from_title_or_snippet(title, snippet, target["label"])
                skills = extract_skills_from_text(f"{title} {snippet}")

                postings.append({
                    "title": title[:80] or role_title,
                    "organization": org,
                    "platform": target["name"],
                    "locations_derived": ["Remote / Multiple Locations"],
                    "employment_type": ["Full-Time"],
                    "seniority": "Mid-Senior Level" if "senior" in title.lower() else "Entry-Mid Level",
                    "ai_experience_level": "3+ years" if "senior" in title.lower() else "1-3 years",
                    "ai_salary_min_value": None,
                    "ai_salary_max_value": None,
                    "ai_salary_currency": "USD",
                    "ai_salary_unit_text": "YEAR",
                    "ai_core_responsibilities": snippet[:250],
                    "ai_requirements_summary": snippet[:350],
                    "ai_key_skills": skills if skills else [role_title],
                    "url": url,
                })
        except Exception:
            logger.exception("MultiPlatformJobScraper: error searching %s", target["name"])
        return postings

    async def fetch_open_job_postings(self, role_title: str, max_total: int = 10) -> List[Dict[str, Any]]:
        """Asynchronously aggregates open JDs across Indeed, Naukri, Glassdoor, and open boards."""
        # Query platforms in parallel threads to avoid blocking
        loop = asyncio.get_running_loop()
        tasks = [
            loop.run_in_executor(None, self.search_platform_jobs, role_title, target)
            for target in PLATFORM_SEARCH_TARGETS
        ]

        platform_results = await asyncio.gather(*tasks, return_exceptions=True)

        all_postings = []
        for res in platform_results:
            if isinstance(res, list):
                all_postings.extend(res)

        logger.info(
            "MultiPlatformJobScraper: gathered %d open postings across %d platforms for %r",
            len(all_postings), len(PLATFORM_SEARCH_TARGETS), role_title
        )
        return all_postings[:max_total]

    def fetch_open_job_postings_sync(self, role_title: str, max_total: int = 10) -> List[Dict[str, Any]]:
        """Synchronous wrapper for graph / test execution."""
        try:
            return asyncio.run(self.fetch_open_job_postings(role_title, max_total))
        except RuntimeError:
            # Active event loop fallback
            all_postings = []
            for target in PLATFORM_SEARCH_TARGETS[:3]:
                all_postings.extend(self.search_platform_jobs(role_title, target))
            return all_postings[:max_total]
