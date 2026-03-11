from sqlalchemy import Column, Integer, Text, Numeric, ForeignKey
from sqlalchemy.orm import relationship
from database.connection import Base

class AssessmentAnswer(Base):
    __tablename__ = "assessment_answers"

    answer_id = Column(Integer, primary_key=True, autoincrement=True)
    assessment_id = Column(Integer, ForeignKey("candidate_assessments.assessment_id"), nullable=False)
    question_id = Column(Integer, ForeignKey("assessment_template_questions.question_id"), nullable=False)
    candidate_answer = Column(Text, nullable=True)
    score_awarded = Column(Numeric(6, 2), nullable=True)
    ai_feedback = Column(Text, nullable=True)

    assessment = relationship("CandidateAssessment", back_populates="answers")
    question = relationship("AssessmentTemplateQuestion", back_populates="answers")
