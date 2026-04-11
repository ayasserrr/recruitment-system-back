import json
import logging
import re
from openai import OpenAI

from helpers.config import get_settings
from models.schemas.post_schema import GeneratedPosts
from agents.state import PipelineState

logger   = logging.getLogger(__name__)
settings = get_settings()

# Hard limit on how much of long free-text fields we feed into the prompt.
# LinkedIn posts are 150-250 words; the model only needs the gist.
_MAX_DESC_CHARS = 500


def _truncate(text: str | None, limit: int = _MAX_DESC_CHARS) -> str:
    if not text:
        return "Not specified"
    return text[:limit] + "…" if len(text) > limit else text


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

    return f"""Generate TWO short job posts (LinkedIn and Indeed) for the position below.

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
- Key Responsibilities: {_truncate(jr['key_responsibilities'])}
- Description: {_truncate(jr['full_description'])}

RULES — you MUST follow these exactly:
1. LinkedIn post: 150-200 words, professional tone, paragraphs only (no bullet points), end with 3-5 hashtags on one line.
2. Indeed post: 120-160 words, direct tone, use bullet points for requirements.
3. Both "content" values must be plain text strings — no nested JSON, no code blocks.
4. Return ONLY the JSON object below, nothing else before or after it.

{{"linkedin": {{"title": "...", "content": "..."}}, "indeed": {{"title": "...", "content": "..."}}}}"""


def _extract_json(raw: str) -> dict:
    """
    Robustly extract the JSON object from the model response.

    Strategy (tried in order):
      1. Direct json.loads on the stripped string.
      2. Find the outermost {{ }} pair and parse that slice — handles
         cases where the model adds preamble/postamble text.
      3. If the object is truncated, close all open braces/brackets and
         retry — handles max_tokens cut-off.
    """
    text = raw.strip()

    # Strategy 1 — clean response
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Strategy 2 — extract outermost { }
    start = text.find("{")
    end   = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        try:
            return json.loads(text[start:end + 1])
        except json.JSONDecodeError:
            pass

    # Strategy 3 — truncated JSON: close unclosed braces/strings
    if start != -1:
        fragment = text[start:]
        # Close any open string by appending a quote if we're mid-string
        open_strings = fragment.count('"') % 2
        if open_strings:
            fragment += '"'
        # Count unclosed braces
        depth = fragment.count("{") - fragment.count("}")
        fragment += "}" * max(depth, 0)
        try:
            return json.loads(fragment)
        except json.JSONDecodeError:
            pass

    raise ValueError(f"Cannot extract valid JSON from model response. Raw (first 300 chars): {raw[:300]}")


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
                        "You are an expert HR copywriter. "
                        "Always respond with a single compact JSON object. "
                        "Keep posts concise: LinkedIn 150-200 words, Indeed 120-160 words. "
                        "Never add text outside the JSON."
                    ),
                },
                {"role": "user", "content": _build_prompt(state["jr_data"])},
            ],
            temperature=0.7,
            max_tokens=2048,
        )

        raw_json = response.choices[0].message.content

        # Log finish reason so truncation is visible in the logs
        finish_reason = response.choices[0].finish_reason
        if finish_reason != "stop":
            logger.warning(
                "[generate_posts] Unexpected finish_reason='%s' for requisition %s — "
                "response may be truncated.",
                finish_reason, state["requisition_id"],
            )

        data   = _extract_json(raw_json)
        parsed = GeneratedPosts.model_validate(data)

        generated_posts = {
            "linkedin": {"title": parsed.linkedin.title, "content": parsed.linkedin.content},
            "indeed":   {"title": parsed.indeed.title,   "content": parsed.indeed.content},
        }

        logger.info("[generate_posts] Posts generated for requisition %s", state["requisition_id"])
        return {**state, "generated_posts": generated_posts}

    except Exception as e:
        logger.error("[generate_posts] Error: %s", e)
        return {**state, "current_phase": "error", "error": str(e)}
