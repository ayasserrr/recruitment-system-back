from sqlalchemy import Column, Integer, Text, Numeric, ForeignKey
from sqlalchemy.orm import relationship
from database.connection import Base

class HRInterviewScore(Base):
    __tablename__ = "hr_interview_scores"

    score_id = Column(Integer, primary_key=True, autoincrement=True)
    session_id = Column(Integer, ForeignKey("hr_interview_sessions.session_id"), nullable=False)
    criterion_id = Column(Integer, ForeignKey("hr_interview_criteria.criterion_id"), nullable=False)
    score = Column(Numeric(6, 2), nullable=False)
    notes = Column(Text, nullable=True)

    session = relationship("HRInterviewSession", back_populates="scores")
    criterion = relationship("HRInterviewCriterion", back_populates="scores")
