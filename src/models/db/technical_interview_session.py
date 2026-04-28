from sqlalchemy import Column, Integer, String, Text, DateTime, Numeric, ForeignKey, func
from sqlalchemy.orm import relationship
from database.connection import Base

class TechnicalInterviewSession(Base):
    __tablename__ = "technical_interview_sessions"

    session_id = Column(Integer, primary_key=True, autoincrement=True)
    application_id = Column(Integer, ForeignKey("applications.application_id"), nullable=False, unique=True)
    config_id = Column(Integer, ForeignKey("technical_interview_configs.config_id"), nullable=False)
    status = Column(String(50), nullable=False, default="Scheduled")
    room_name = Column(String(120), nullable=True, unique=True, index=True)
    language = Column(String(10), nullable=True, default="en")
    mode = Column(String(20), nullable=True, default="technical")
    scheduled_at = Column(DateTime, nullable=True)
    started_at = Column(DateTime, nullable=True)
    ended_at = Column(DateTime, nullable=True)
    interviewer_name = Column(String(255), nullable=True)
    overall_score = Column(Numeric(6, 2), nullable=True)
    overall_performance = Column(String(100), nullable=True)
    summary = Column(Text, nullable=True)
    recommendation = Column(String(100), nullable=True)
    # Full verbatim transcript of the voice interview, populated by livekit_agent.py
    transcript = Column(Text, nullable=True)
    # ── Technical ensemble model scores (populated by tech_interview_node) ────
    codebert_score       = Column(Numeric(6, 4), nullable=True)  # CodeBERT semantic depth
    roberta_depth_score  = Column(Numeric(6, 4), nullable=True)  # RoBERTa-QA extraction score
    nli_technical_score  = Column(Numeric(6, 4), nullable=True)  # DeBERTa NLI technical alignment
    tfidf_technical_score = Column(Numeric(6, 4), nullable=True) # TF-IDF keyword coverage
    # ── SHAP ─────────────────────────────────────────────────────────────────
    shap_json    = Column(Text, nullable=True)   # JSON per-model SHAP values
    shap_summary = Column(Text, nullable=True)   # human-readable narrative
    created_at = Column(DateTime, server_default=func.now())

    application = relationship("Application", back_populates="technical_interview_session")
    config = relationship("TechnicalInterviewConfig", back_populates="sessions")
    scores = relationship("TechnicalInterviewScore", back_populates="session")
    report = relationship("TechnicalInterviewReport", back_populates="session", uselist=False)
