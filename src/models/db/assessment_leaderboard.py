from sqlalchemy import Column, Integer, String, Text, Numeric, Boolean, DateTime, ForeignKey, JSON, func
from sqlalchemy.orm import relationship
from database.connection import Base


class AssessmentLeaderboard(Base):
    __tablename__ = "assessment_leaderboards"

    id = Column(Integer, primary_key=True, autoincrement=True)
    jr_id = Column(Integer, ForeignKey("job_requisitions.requisition_id"), nullable=False)
    candidate_id = Column(Integer, ForeignKey("candidates.candidate_id"), nullable=False)
    application_id = Column(Integer, ForeignKey("applications.application_id"), nullable=False)
    assessment_id = Column(Integer, ForeignKey("candidate_assessments.assessment_id"), nullable=False)
    rank = Column(Integer, nullable=True)
    final_score = Column(Numeric(8, 4), nullable=True)
    segment = Column(String(100), nullable=True)
    reject = Column(Boolean, default=False)
    reject_reason = Column(Text, nullable=True)
    avg_depth = Column(Numeric(8, 4), nullable=True)
    per_question_detail = Column(JSON, nullable=True)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

    job_requisition = relationship("JobRequisition", back_populates="leaderboard_entries")
    candidate = relationship("Candidate")
    application = relationship("Application")
    candidate_assessment = relationship("CandidateAssessment")
