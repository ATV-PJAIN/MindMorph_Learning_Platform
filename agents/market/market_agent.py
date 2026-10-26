import logging
import sys
import os
from collections import Counter

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.append(PROJECT_ROOT)



from config import llm
from dotenv import load_dotenv
from tools.job_scrapper_tool import JobScraperService
from tools.multi_platform_job_scraper import MultiPlatformJobScraper
import asyncio
import os


# Import LLM from your existing config
from config import llm

load_dotenv()

logger = logging.getLogger(__name__)

class MarketAnalysisAgent:
    def __init__(self, scraper=None, multi_scraper=None):
        self.scraper = scraper or JobScraperService()
        self.multi_scraper = multi_scraper or MultiPlatformJobScraper()
        self.llm = llm

    # The scraper always fetches 10 (the actor's schema minimum) and the round-trip costs the
    # same for 1 as for 10, so sampling fewer than all of them discards evidence already paid for.
    MAX_POSTINGS = 10
    # A skill named by a single employer is one employer's preference, not a frequency signal. It
    # still gets counted below; only the ranked table is cut here, to keep the Consensus slot small.
    MIN_FREQUENCY_TO_RANK = 2

    @staticmethod
    def _salary_line(job: dict) -> str:
        # Keys are ai_salary_min_value / _max_value / _unit_text — the earlier spelling
        # (ai_salary_minvalue) matches nothing this actor returns, so every posting read as
        # "Not specified" whatever it paid.
        low = job.get('ai_salary_min_value')
        high = job.get('ai_salary_max_value')
        if not (low and high):
            return "not stated"
        currency = job.get('ai_salary_currency') or 'USD'
        unit = job.get('ai_salary_unit_text') or 'YEAR'
        return f"{low:,} - {high:,} {currency} per {unit}"

    @staticmethod
    def _group_by_employer(jobs: list) -> list:
        """Collapse postings to one entry per distinct employer.

        Ten rows is not ten employers. On the verified run the ten postings were four employers, six
        of them one aggregator's repost series of the same role ("Senior Level", "New Grad",
        "Intern", ...). Counting those six separately turns one org's repost volume into "6/10
        postings" — market demand, to the learner and to Consensus, from a single employer.

        Skills are unioned across an employer's postings rather than taking the first posting's
        list, so collapsing reposts drops no skill evidence — only the double-counting. A posting
        with no `organization` is its own employer: grouping the blanks together would merge
        unrelated roles and undercount.
        """
        groups, index = [], {}
        for job in jobs:
            key = (job.get('organization') or '').strip().casefold()
            # ponytail: exact-string match only. "Jobright.ai" vs "Jobright Inc" reads as two
            # employers; add normalization only if a real run shows that split mattering.
            group = index.get(key) if key else None
            if group is None:
                group = {'job': job, 'postings': 0, 'skills': {}}
                groups.append(group)
                if key:
                    index[key] = group
            group['postings'] += 1
            group['skills'].update(dict.fromkeys(job.get('ai_key_skills') or []))
        return groups

    @classmethod
    def _employer_block(cls, index: int, total: int, group: dict) -> str:
        """One employer rendered from the actor's own extracted fields.

        Not `description_text`: the skills in these postings sit late in the prose (measured on a
        live fetch, the last verbatim skill mention landed between 56% and 99% of the way through),
        so any per-posting truncation that makes ten postings affordable drops real skills — and
        ten untruncated descriptions is ~38k chars, over this tier's per-minute token budget. The
        `ai_*` fields cover the whole posting at ~700 chars each. They are the job board's own
        extraction, so 92% of the skill strings appear verbatim in the description and the rest are
        its paraphrase ("Containerization" for Docker); the summary labels the source for that reason.
        """
        job = group['job']
        skills = list(group['skills'])
        reposts = group['postings']
        platform = job.get('platform')
        platform_info = f" [Source: {platform}]" if platform else ""
        header = f"Employer {index} of {total}{platform_info}"
        if reposts > 1:
            header += (
                f" — this employer posted {reposts} variants of the same role in the sample"
                f" (counted once; skills below are the union across them)"
            )
        return (
            f"{header}\n"
            f"Title: {job.get('title', 'N/A')} | Company: {job.get('organization') or 'not stated'}\n"
            f"Location: {', '.join(job.get('locations_derived') or ['N/A'])} | "
            f"Employment: {', '.join(job.get('employment_type') or ['N/A'])}\n"
            f"Seniority stated: {job.get('seniority') or 'not stated'} | "
            f"Years of experience stated: {job.get('ai_experience_level') or 'not stated'}\n"
            f"Salary: {cls._salary_line(job)}\n"
            f"Work described: {job.get('ai_core_responsibilities') or 'not stated'}\n"
            f"Requirements: {job.get('ai_requirements_summary') or 'not stated'}\n"
            f"Skills named: {', '.join(skills) if skills else 'none named'}\n"
        )

    @classmethod
    def _frequency_table(cls, groups: list) -> str:
        """Counted in Python, not by the model — counting across ten lists is what an LLM gets wrong,
        and a miscounted frequency is worse than no frequency: it reads as evidence.

        The denominator is employers, not rows: a skill named by one employer six times is one
        employer's preference however many times that employer reposted it."""
        counts = Counter(skill for group in groups for skill in group['skills'])
        total = len(groups)
        ranked = [
            f"{skill} - {n}/{total} employers"
            for skill, n in counts.most_common()
            if n >= cls.MIN_FREQUENCY_TO_RANK
        ]
        singletons = sum(1 for n in counts.values() if n < cls.MIN_FREQUENCY_TO_RANK)
        table = "\n".join(ranked) or "No skill is named by more than one employer."
        return f"{table}\n({singletons} further skills are named by exactly 1/{total} employers.)"

    async def summarize_job(self, job_data):
        """Summarize the sampled job postings into one market-evidence block.

        Accepts a single posting dict or a list of them. One LLM call over all of them, not one per
        posting: a frequency claim only exists across postings, so N separate summaries would need a
        second aggregating call to say anything the single-posting version couldn't — at N times the
        cost and N round-trips.
        """
        try:
            jobs = [job_data] if isinstance(job_data, dict) else list(job_data or [])
            if not jobs:
                return None
            jobs = jobs[: self.MAX_POSTINGS]
            n_postings = len(jobs)
            groups = self._group_by_employer(jobs)
            total = len(groups)
            blocks = "\n".join(
                self._employer_block(i, total, g) for i, g in enumerate(groups, start=1)
            )

            prompt = f"""
            You are the Market Agent for the MindMorph learning platform. Downstream, a Consensus
            agent turns your output into skill nodes for a learning path, so the only thing that
            matters here is which skills, tools and technologies these postings actually demand.

            {n_postings} job postings were sampled. They come from {total} distinct employers -
            several postings can be one employer reposting the same role, so every count below is
            over employers, never over postings. Each block is one employer, from the job board's
            own extraction of that employer's posting(s).

            {blocks}
            Skill frequency across all {total} employers, already counted for you:
            {self._frequency_table(groups)}

            Report:
            1. What these roles do, in a few lines, covering the range across the employers.
            2. The skill frequency table above, reproduced exactly. Do not recount, reorder, merge
               or drop rows, and do not add a row that is not in it.
            3. Seniority as a distribution over employers, e.g. "2/{total} employers state 0-2
               years" - from the stated seniority and years-of-experience lines only.
            4. What the employers ask for beyond named tools: degrees, domains, stated requirements.
               Attribute each to a count, e.g. "3/{total} employers".

            Honesty rules, which override everything above:
            - Report only what these postings state. Never add a skill, tool, certification,
              qualification, benefit or company fact because it is typical for the role. If no
              posting names a tool, that tool must not appear in your output at all, not even as an
              example or a parenthetical.
            - If a section has nothing in the postings to fill it, write one line saying the postings
              do not state it, and move on.
            - Every claim about more than one employer carries its count, always with the /{total}
              employers denominator. Never write a count out of {n_postings} postings: that
              denominator counts reposts, not employers. A skill named by 1/{total} employers is
              reported as 1/{total} employers, not dropped and not inflated. Never write "in
              demand", "widely required" or "the market wants" without a count attached - a count
              IS the claim.
            - Give counts only, never employer or posting numbers. Asked to attribute claims to
              specific postings, the model guessed: a live run tagged a healthcare requirement to a
              posting that was about marketing data. Nothing downstream reads the numbers, and a
              wrong one is indistinguishable from evidence.
            - {total} employers from one search is a very small sample, not the market. Say so in
              one line, giving the number of employers - never describe the sample as
              {n_postings} postings.
            - These skill names are the job board's extraction of each posting, not always the
              posting's own wording. Say so in the same line.

            No benefits, perks or company overviews. No career or application advice. Compact
            bullets; every line names a skill, a stated requirement or a count.
            """

            logger.info(
                "Market: summarizing %d job posting(s) from %d distinct employer(s)",
                n_postings, total,
            )
            response = await self.llm.ainvoke(prompt)
            return response.content

        except Exception:
            logger.exception("Market: error summarizing jobs with LLM")
            return None

    async def extract_job_title(self, query: str) -> str:
        """Distill a learning goal / Scout question into a concise job title.

        The LinkedIn actor's `titleSearch` matches job titles, so a verbose question
        (e.g. "What Python skills are in demand?") returns zero results. We reduce it
        to a 2-4 word role title (e.g. "machine learning engineer").
        """
        prompt = (
            "Extract the single most relevant job title (2-4 words) to search a job board, "
            "based on the request below. Reply with ONLY the title text - no quotes, no punctuation, "
            "no explanation.\n\n"
            f"Request: {query}"
        )
        try:
            resp = await self.llm.ainvoke(prompt)
            title = (resp.content or "").strip().strip('"').splitlines()[0].strip()
            return title or query
        except Exception:
            logger.exception("Market: job-title extraction failed, using raw query")
            return query

    async def fetch_multi_platform_jobs(self, search_query: str, location: str = "United States") -> list:
        """Aggregates postings from Apify LinkedIn AND open platforms (Indeed, Naukri, Glassdoor, open web)."""
        combined_jobs = []

        # 1. Apify LinkedIn (if token is available)
        if getattr(self.scraper, "token", None):
            try:
                await self.scraper.initialize()
                dataset_id = await self.scraper.search_jobs(search_query, location)
                if dataset_id:
                    apify_jobs = await self.scraper.fetch_job_results(dataset_id)
                    if apify_jobs:
                        for j in apify_jobs:
                            j.setdefault("platform", "LinkedIn")
                        combined_jobs.extend(apify_jobs)
            except Exception as e:
                logger.warning("Market: Apify LinkedIn fetch failed (%s); continuing with open platforms", e)

        # 2. Multi-Platform open JDs (Indeed, Naukri, Glassdoor, Google search / open web boards)
        if self.multi_scraper:
            try:
                open_jobs = await self.multi_scraper.fetch_open_job_postings(search_query, max_total=8)
                if open_jobs:
                    combined_jobs.extend(open_jobs)
            except Exception as e:
                logger.warning("Market: open multi-platform scraper error (%s)", e)

        return combined_jobs[: self.MAX_POSTINGS]

    async def run_analysis(self, search_query: str, location: str = "United States"):
        jobs_list = await self.fetch_multi_platform_jobs(search_query, location)
        
        print(f"Successfully gathered {len(jobs_list)} job postings across platforms")

        # Process with LLM (one call across the whole sample)
        if jobs_list:
            print(f"\n{'='*60}")
            print(f"ANALYZING {min(len(jobs_list), self.MAX_POSTINGS)} MULTI-PLATFORM JOB POSTINGS")
            print(f"{'='*60}\n")

            for job in jobs_list[: self.MAX_POSTINGS]:
                platform = job.get('platform', 'Web')
                print(f"- [{platform}] {job.get('title')} @ {job.get('organization')} ({job.get('url', 'N/A')})")
            print(f"\n{'='*60}")
            print(f"GENERATING AI SUMMARY...")
            print(f"{'='*60}\n")

            summary = await self.summarize_job(jobs_list)

            if summary:
                print(summary)
                print(f"\n{'='*60}\n")
        else:
            print("No jobs found to analyze.")

async def main():
    agent = MarketAnalysisAgent()
    
    # Input parameters
    query = "Senior Machine Learning Engineer"
    loc = "United States"
    
    await agent.run_analysis(query, loc)

if __name__ == "__main__":
    asyncio.run(main())