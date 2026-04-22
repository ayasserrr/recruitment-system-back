_SCHEMA = """{
  "first_name": "<string, REQUIRED>",
  "last_name": "<string, REQUIRED>",
  "email": "<string, REQUIRED>",
  "phone": "<string | null>",
  "linkedin_url": "<string | null>",
  "portfolio_url": "<string | null>",
  "professional_summary": "<string | null>",
  "years_of_experience": "<integer | null>",
  "education_level": "<highest degree, e.g. Bachelor, Master, PhD | null>",
  "field_of_study": "<string | null>",
  "experiences": [
    {
      "job_title": "<string | null>",
      "company": "<string | null>",
      "start_date": "<YYYY-MM-DD | null>",
      "end_date": "<YYYY-MM-DD | null>",
      "responsibilities": ["<string>", "..."]
    }
  ],
  "educations": [
    {
      "institution": "<string | null>",
      "degree": "<string | null>",
      "field": "<string | null>",
      "graduation_date": "<YYYY-MM-DD | null>"
    }
  ],
  "projects": [
    {
      "project_name": "<string | null>",
      "description": "<string | null>",
      "tech_stack": ["<string>", "..."]
    }
  ],
  "skills": [
    {
      "skill_name": "<string, REQUIRED>",
      "proficiency_level": "<string | null>"
    }
  ]
}"""


def cv_system_prompt() -> str:
    return (
        "You are a specialized ATS Parser. "
        "Extract structured candidate data from CV text and return ONLY a valid JSON object. "
        "No markdown fences, no explanations, no trailing commas."
    )


def cv_user_prompt(cv_text: str) -> str:
    return (
        f"CV Text:\n{cv_text}\n\n"
        "Extract and return a JSON object that matches this exact schema:\n"
        f"{_SCHEMA}\n\n"
        "Strict Rules:\n"
        "1. MANDATORY: first_name, last_name, email (top-level) and skill_name (per skill) "
        "are NOT NULL in the database — you MUST extract them.\n"
        "2. DATA TYPES: years_of_experience must be an integer. "
        "All date fields must use YYYY-MM-DD format; use null if unknown or ongoing.\n"
        "3. NO HALLUCINATIONS: set any non-mandatory missing field to null.\n"
        "4. CLEAN OUTPUT: remove emojis and fix encoding artifacts.\n"
        "5. SKILLS: return each distinct skill as a separate object with skill_name "
        "and an optional proficiency_level.\n"
        "6. RESPONSIBILITIES: return as a JSON array of concise bullet strings.\n"
        "7. TECH STACK: return as a JSON array of technology names."
    )
