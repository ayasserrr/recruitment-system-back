from sqlalchemy import Column, Integer, DateTime, ForeignKey, func
from sqlalchemy.orm import relationship
from database.connection import Base


class AssessmentQuestionSet(Base):
    __tablename__ = "assessment_question_sets"

    id = Column(Integer, primary_key=True, autoincrement=True)
    jr_id = Column(Integer, ForeignKey("job_requisitions.requisition_id"), nullable=False)
    question_id = Column(Integer, ForeignKey("generated_assessment_questions.id"), nullable=False)
    position = Column(Integer, nullable=False)
    created_at = Column(DateTime, server_default=func.now())

    job_requisition = relationship("JobRequisition", back_populates="question_sets")
    question = relationship("GeneratedAssessmentQuestion", back_populates="question_sets")
