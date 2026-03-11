from sqlalchemy import Column, Integer, String, Text, DateTime, ForeignKey, UniqueConstraint, func
from sqlalchemy.orm import relationship
from database.connection import Base

class Application(Base):
    __tablename__ = "applications"
    __table_args__ = (
        UniqueConstraint("posting_id", "candidate_id", name="uq_application_posting_candidate"),
    )

    application_id = Column(Integer, primary_key=True, autoincrement=True)
    posting_id = Column(Integer, ForeignKey("job_postings.posting_id"), nullable=False)
    candidate_id = Column(Integer, ForeignKey("candidates.candidate_id"), nullable=False)
    cv_id = Column(Integer, ForeignKey("candidate_cvs.cv_id"), nullable=False)
    status = Column(String(50), nullable=False, default="Applied")
    applied_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())
    cover_letter = Column(Text, nullable=True)
    current_pipeline_stage = Column(String(100), nullable=True)

    posting = relationship("JobPosting", back_populates="applications")
    candidate = relationship("Candidate", back_populates="applications")
    cv = relationship("CandidateCV", back_populates="applications")
    semantic_report = relationship("SemanticAnalysisReport", back_populates="application", uselist=False)
    candidate_assessment = relationship("CandidateAssessment", back_populates="application", uselist=False)
    technical_interview_session = relationship("TechnicalInterviewSession", back_populates="application", uselist=False)
    hr_interview_session = relationship("HRInterviewSession", back_populates="application", uselist=False)
    final_ranking = relationship("FinalRanking", back_populates="application", uselist=False)
    pipeline_logs = relationship("PipelineStageLog", back_populates="application")
