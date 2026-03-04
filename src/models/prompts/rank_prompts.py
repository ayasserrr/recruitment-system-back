def rank_system_prompt() -> str:
    return (
        "You are an expert recruitment assistant.\n"
        "Your job is to evaluate candidates based on\n"
        "their CV and the job requirements provided.\n"
        "Always respond in valid JSON only."
    )


def rank_user_prompt(job_requirements: str, cv_text: str) -> str:
    return (
        "Job Requirements:\n"
        f"{job_requirements}\n\n"
        "Candidate CV:\n"
        f"{cv_text}\n\n"
        "Evaluate this candidate and return ONLY valid JSON (double quotes only).\n"
        "Do not include markdown, comments, or trailing commas.\n"
        "Schema:\n"
        "{\n"
        "  \"score\": 0,\n"
        "  \"strengths\": [\"\"],\n"
        "  \"weaknesses\": [\"\"],\n"
        "  \"summary\": \"\",\n"
        "  \"recommendation\": \"Highly Recommended\"\n"
        "}"
    )
