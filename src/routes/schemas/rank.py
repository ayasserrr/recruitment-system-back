from pydantic import BaseModel


class RankRequest(BaseModel):
    job_requirements: str
