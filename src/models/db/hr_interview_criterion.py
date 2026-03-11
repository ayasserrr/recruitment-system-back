from sqlalchemy import Column, Integer, String, Text, ForeignKey
from sqlalchemy.orm import relationship
from database.connection import Base

class HRInterviewCriterion(Base):
    __tablename__ = "hr_interview_criteria"

    criterion_id = Column(Integer, primary_key=True, autoincrement=True)
    config_id = Column(Integer, ForeignKey("hr_interview_configs.config_id"), nullable=False)
    criterion_name = Column(String(100), nullable=False)
    description = Column(Text, nullable=True)
    max_score = Column(Integer, nullable=False)
    weight_percentage = Column(Integer, nullable=True)

    config = relationship("HRInterviewConfig", back_populates="criteria")
    scores = relationship("HRInterviewScore", back_populates="criterion")
