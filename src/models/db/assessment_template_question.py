from sqlalchemy import Column, Integer, String, Text, ForeignKey
from sqlalchemy.orm import relationship
from database.connection import Base

class AssessmentTemplateQuestion(Base):
    __tablename__ = "assessment_template_questions"

    question_id = Column(Integer, primary_key=True, autoincrement=True)
    template_id = Column(Integer, ForeignKey("assessment_templates.template_id"), nullable=False)
    question_text = Column(Text, nullable=False)
    question_type = Column(String(50), nullable=True)
    points = Column(Integer, default=1)
    correct_answer = Column(Text, nullable=True)
    options = Column(Text, nullable=True)

    template = relationship("AssessmentTemplate", back_populates="questions")
    answers = relationship("AssessmentAnswer", back_populates="question")
