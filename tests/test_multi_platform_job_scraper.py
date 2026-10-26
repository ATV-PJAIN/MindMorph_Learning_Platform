"""Unit tests for MultiPlatformJobScraper and MarketAnalysisAgent multi-platform sampling."""

import pytest
from unittest.mock import AsyncMock, MagicMock

from tools.multi_platform_job_scraper import (
    MultiPlatformJobScraper,
    extract_skills_from_text,
    extract_org_from_title_or_snippet,
)
from agents.market.market_agent import MarketAnalysisAgent


def test_extract_skills_from_text():
    sample_text = (
        "We are looking for a Senior Python Developer with strong experience in Django, "
        "PostgreSQL, Docker, and AWS. Knowledge of PyTorch and Kubernetes is a big plus."
    )
    skills = extract_skills_from_text(sample_text)
    assert "Python" in skills
    assert "Django" in skills
    assert "PostgreSQL" in skills
    assert "Docker" in skills
    assert "AWS" in skills
    assert "PyTorch" in skills
    assert "Kubernetes" in skills


def test_extract_org_from_title_or_snippet():
    title = "Senior ML Engineer - Stripe - San Francisco | Indeed"
    snippet = "Work at Stripe on fraud detection models using Python and Kafka."
    org = extract_org_from_title_or_snippet(title, snippet, "Indeed")
    assert org == "Stripe"


def test_employer_block_includes_platform_attribution():
    group = {
        "job": {
            "title": "Data Engineer",
            "organization": "Netflix",
            "platform": "Indeed",
            "locations_derived": ["Remote"],
            "employment_type": ["Full-Time"],
            "seniority": "Senior",
            "ai_experience_level": "5+ years",
            "ai_salary_min_value": 150000,
            "ai_salary_max_value": 220000,
            "ai_salary_currency": "USD",
            "ai_salary_unit_text": "YEAR",
            "ai_core_responsibilities": "Build real-time Spark pipelines",
            "ai_requirements_summary": "Expert in Python, Spark, and AWS",
        },
        "skills": {"Python": None, "Spark": None, "AWS": None},
        "postings": 1,
    }

    block = MarketAnalysisAgent._employer_block(1, 1, group)
    assert "[Source: Indeed]" in block
    assert "Company: Netflix" in block
    assert "Skills named: " in block
    assert "Python" in block


@pytest.mark.asyncio
async def test_fetch_multi_platform_jobs_aggregates_sources():
    mock_scraper = MagicMock()
    mock_scraper.token = "fake_token"
    mock_scraper.initialize = AsyncMock()
    mock_scraper.search_jobs = AsyncMock(return_value="dataset_123")
    mock_scraper.fetch_job_results = AsyncMock(return_value=[
        {"title": "AI Engineer", "organization": "OpenAI", "ai_key_skills": ["Python", "PyTorch"]}
    ])

    mock_multi = MagicMock()
    mock_multi.fetch_open_job_postings = AsyncMock(return_value=[
        {"title": "ML Engineer", "organization": "Anthropic", "platform": "Indeed", "ai_key_skills": ["Python", "JAX"]},
        {"title": "Data Scientist", "organization": "Google", "platform": "Naukri", "ai_key_skills": ["Python", "SQL"]},
        {"title": "Backend Engineer", "organization": "Meta", "platform": "Glassdoor", "ai_key_skills": ["Python", "GraphQL"]},
    ])

    agent = MarketAnalysisAgent(scraper=mock_scraper, multi_scraper=mock_multi)
    jobs = await agent.fetch_multi_platform_jobs("AI Engineer", location="United States")

    assert len(jobs) == 4
    orgs = [j["organization"] for j in jobs]
    assert "OpenAI" in orgs
    assert "Anthropic" in orgs
    assert "Google" in orgs
    assert "Meta" in orgs
