"""Unit tests for UniversityScraperService and AcademicAgent university grounding."""

import pytest
from unittest.mock import MagicMock, patch

from tools.academic_scraper_tool import UniversityScraperService, clean_html_to_text, TARGET_UNIVERSITIES
from agents.academic.academic_agent import AcademicAgent


def test_clean_html_to_text():
    html = """
    <html>
      <head><style>.nav { color: red; }</style></head>
      <body>
        <nav>Navigation links to ignore</nav>
        <header>Header info</header>
        <h1>CS50: Introduction to Computer Science</h1>
        <p>This course covers algorithms, data structures, and software engineering.</p>
        <script>console.log('secret');</script>
        <footer>Copyright 2026 Harvard</footer>
      </body>
    </html>
    """
    text = clean_html_to_text(html)
    assert "CS50: Introduction to Computer Science" in text
    assert "algorithms, data structures" in text
    assert "console.log" not in text
    assert "Navigation links to ignore" not in text


def test_format_for_prompt():
    service = UniversityScraperService()
    curricula = [
        {
            "university": "Harvard University",
            "title": "CS50 Introduction to Computer Science",
            "url": "https://cs50.harvard.edu",
            "content": "Week 0: Computational Thinking. Week 1: C. Week 2: Arrays.",
        },
        {
            "university": "University of Oxford",
            "title": "Design and Analysis of Algorithms",
            "url": "https://ox.ac.uk/courses/algorithms",
            "content": "Prerequisites: Discrete Mathematics. Topics: Graph Algorithms, Dynamic Programming.",
        },
    ]

    formatted = service.format_for_prompt(curricula)
    assert "GROUNDED UNIVERSITY SYLLABI & SLIVERS" in formatted
    assert "Harvard University" in formatted
    assert "University of Oxford" in formatted
    assert "Week 0: Computational Thinking" in formatted
    assert "Prerequisites: Discrete Mathematics" in formatted


def test_academic_agent_injects_university_syllabi():
    mock_scraper = MagicMock()
    mock_scraper.get_grounded_curricula_sync.return_value = [
        {
            "university": "Harvard University",
            "title": "CS109 Data Science",
            "url": "https://harvard.edu/cs109",
            "content": "Modules: Exploratory Data Analysis, Bayesian Modeling, Deep Learning.",
        }
    ]
    mock_scraper.format_for_prompt.return_value = "=== HARVARD DATA SCIENCE SYLLABUS ==="

    agent = AcademicAgent(push_to_langsmith=False, scraper=mock_scraper)
    agent.llm = MagicMock()
    agent.llm.invoke = MagicMock(return_value=MagicMock(content="SYLLABUS_ROADMAP"))

    result = agent.provide_academic_roadmap("I want to learn Data Science")

    assert result.content == "SYLLABUS_ROADMAP"
    assert agent.llm.invoke.called
    invoked_messages = agent.llm.invoke.call_args[0][0]
    prompt_content = str(invoked_messages)
    assert "HARVARD DATA SCIENCE SYLLABUS" in prompt_content
    assert "Incorporate the domain-specific course slivers" in prompt_content
