from pydantic import BaseModel


class PlatformPost(BaseModel):
    """A single generated post for one platform."""
    title: str        # e.g. "We're Hiring: Senior Frontend Developer at Acme Corp"
    content: str      # the full post body


class GeneratedPosts(BaseModel):
    """
    Structured output returned by GPT-4o-mini.
    One PlatformPost per platform — LinkedIn and Indeed.
    """
    linkedin: PlatformPost
    indeed: PlatformPost
