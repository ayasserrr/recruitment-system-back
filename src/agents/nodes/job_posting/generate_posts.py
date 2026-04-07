import logging
from openai import OpenAI

from helpers.config import get_settings
from models.schemas.post_schema import GeneratedPosts
from agents.state import PipelineState

logger   = logging.getLogger(__name__)
settings = get_settings()


def _build_prompt(jr: dict) -> str:
    skills_text    = ", ".join(jr["required_skills"])  if jr["required_skills"]  else "Not specified"
    preferred_text = ", ".join(jr["preferred_skills"]) if jr["preferred_skills"] else "None"
    remote_text    = "Remote-friendly" if jr["remote_available"] else "On-site"
    salary_text    = (
        f"{jr['currency']} {jr['min_salary']:,.0f} – {jr['max_salary']:,.0f}/month"
        if jr["min_salary"] and jr["max_salary"] else "Competitive"
    )
    exp_text = (
        f"{jr['min_experience']}–{jr['max_experience']} years"
        if jr["min_experience"] and jr["max_experience"]
        else f"{jr['min_experience']} years" if jr["min_experience"]
        else "Not specified"
    )

    return f"""
Generate job posts for the following position for TWO platforms: LinkedIn and Indeed.

JOB DETAILS:
- Title: {jr['job_title']}
- Department: {jr['department'] or 'Not specified'}
- Seniority: {jr['seniority_level'] or 'Not specified'}
- Employment Type: {jr['employment_type'] or 'Full-time'}
- Location: {jr['location'] or 'Not specified'} ({remote_text})
- Experience Required: {exp_text}
- Education: {jr['min_education'] or 'Not specified'}
- Required Skills: {skills_text}
- Preferred Skills: {preferred_text}
- Salary: {salary_text}
- Application Deadline: {jr['deadline'] or 'Open'}
- Key Responsibilities: {jr['key_responsibilities'] or 'Not specified'}
- Full Description: {jr['full_description'] or 'Not specified'}

PLATFORM REQUIREMENTS:

LINKEDIN POST:
- Tone: Professional, engaging, inspiring
- Length: 150-250 words
- Include: growth opportunity, 3-5 relevant hashtags at the end
- Format: Paragraphs (no bullet points)
- Title: Eye-catching hiring announcement

INDEED POST:
- Tone: Direct, clear, benefit-focused
- Length: 120-180 words
- Include: Clear requirements, salary range, bullet points
- Format: Use bullet points
- Title: Standard job title format

Return ONLY this JSON, no extra text:
{{
  "linkedin": {{ "title": "...", "content": "..." }},
  "indeed":   {{ "title": "...", "content": "..." }}
}}
"""


def generate_posts(state: PipelineState) -> PipelineState:
    """
    Phase 1 — Node 2
    Call gpt-4o-mini via OpenAI SDK using the OPENAI_API_KEY from .env.
    """
    try:
        client = OpenAI(api_key=settings.OPENAI_API_KEY)

        response = client.chat.completions.create(
            model="gpt-4o-mini",
            response_format={"type": "json_object"},
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are an expert HR copywriter who crafts compelling job posts "
                        "optimized for each platform's audience and tone. "
                        "Always respond with valid JSON matching the exact schema provided."
                    ),
                },
                {"role": "user", "content": _build_prompt(state["jr_data"])},
            ],
            temperature=0.7,
        )

        raw_json = response.choices[0].message.content
        parsed   = GeneratedPosts.model_validate_json(raw_json)

        generated_posts = {
            "linkedin": {"title": parsed.linkedin.title, "content": parsed.linkedin.content},
            "indeed":   {"title": parsed.indeed.title,   "content": parsed.indeed.content},
        }

        logger.info(f"[generate_posts] Posts generated for requisition {state['requisition_id']}")
        return {**state, "generated_posts": generated_posts}

    except Exception as e:
        logger.error(f"[generate_posts] Error: {e}")
        return {**state, "current_phase": "error", "error": str(e)}
