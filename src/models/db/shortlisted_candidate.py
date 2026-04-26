from sqlalchemy import Column, Integer, String, Text, DateTime, ForeignKey, func
from database.connection import Base


class ShortlistedCandidate(Base):
    __tablename__ = "shortlisted_candidates"

    id = Column(Integer, primary_key=True, autoincrement=True)
    application_id = Column(
        Integer,
        ForeignKey("applications.application_id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    posting_id = Column(
        Integer,
        ForeignKey("job_postings.posting_id", ondelete="CASCADE"),
        nullable=False,
    )
    candidate_id = Column(
        Integer,
        ForeignKey("candidates.candidate_id", ondelete="CASCADE"),
        nullable=False,
    )
    # e.g. "Semantic Analysis" | "Technical Assessment" | "Final Ranking"
    shortlisted_from = Column(String(100), nullable=False, default="Final Ranking")
    shortlist_note = Column(Text, nullable=True)
    created_at = Column(DateTime, server_default=func.now())
