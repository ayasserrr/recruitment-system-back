from sqlalchemy import Column, Integer, String, Text, DateTime, Numeric, ForeignKey, func
from sqlalchemy.orm import relationship
from database.connection import Base

class HRInterviewSession(Base):
    __tablename__ = "hr_interview_sessions"

    session_id = Column(Integer, primary_key=True, autoincrement=True)
    application_id = Column(Integer, ForeignKey("applications.application_id"), nullable=False, unique=True)
    config_id = Column(Integer, ForeignKey("hr_interview_configs.config_id"), nullable=False)
    status = Column(String(50), nullable=False, default="Scheduled")
    room_name = Column(String(255), nullable=True, unique=True, index=True)
    language = Column(String(10), nullable=True, default="en")
    mode = Column(String(50), nullable=True, default="hr")
    scheduled_at = Column(DateTime, nullable=True)
    started_at = Column(DateTime, nullable=True)
    ended_at = Column(DateTime, nullable=True)
    interviewer_name = Column(String(255), nullable=True)
    overall_score       = Column(Numeric(6, 2), nullable=True)
    overall_performance = Column(String(100),  nullable=True)
    summary             = Column(Text,         nullable=True)
    recommendation      = Column(String(100),  nullable=True)
    # ── Full verbatim transcript (populated after interview ends) ─────────────
    transcript          = Column(Text,         nullable=True)
    # ── HR ensemble model scores ──────────────────────────────────────────────
    emotion_score        = Column(Numeric(6, 4), nullable=True)  # Go-Emotions positive aggregate
    sentiment_score      = Column(Numeric(6, 4), nullable=True)  # RoBERTa tone/professionalism
    nli_align_score      = Column(Numeric(6, 4), nullable=True)  # DeBERTa responsibility alignment
    semantic_depth_score = Column(Numeric(6, 4), nullable=True)  # BGE depth vs JD
    # ── SHAP ─────────────────────────────────────────────────────────────────
    shap_json            = Column(Text,          nullable=True)  # JSON dict of per-feature SHAP
    shap_summary         = Column(Text,          nullable=True)  # human-readable SHAP narrative
    created_at           = Column(DateTime, server_default=func.now())

    application = relationship("Application", back_populates="hr_interview_session")
    config = relationship("HRInterviewConfig", back_populates="sessions")
    scores = relationship("HRInterviewScore", back_populates="session")
    report = relationship("HRInterviewReport", back_populates="session", uselist=False)
