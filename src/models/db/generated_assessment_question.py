from sqlalchemy import Column, Integer, String, Text, Boolean, DateTime, ForeignKey, JSON, func
from sqlalchemy.orm import relationship
from database.connection import Base


class GeneratedAssessmentQuestion(Base):
    __tablename__ = "generated_assessment_questions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    jr_id = Column(Integer, ForeignKey("job_requisitions.requisition_id"), nullable=False)

    # Soft references to knowledge_db (different database — no DB-level FK)
    concept_id = Column(Integer, nullable=True)
    tool_id = Column(Integer, nullable=False)

    tool_name = Column(String(100), nullable=False)
    level = Column(String(20), nullable=False)       # beginner | mid | high
    concept_name = Column(String(200), nullable=False)
    question_text = Column(Text, nullable=False)
    question_type = Column(String(20), nullable=False, default="open_ended")  # mcq | open_ended
    required_keywords = Column(JSON, nullable=False)  # list[str] — used by grading engine
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, server_default=func.now())

    # Links to AssessmentTemplateQuestion for backward-compatible grading
    template_question_id = Column(
        Integer,
        ForeignKey("assessment_template_questions.question_id"),
        nullable=True,
    )

    job_requisition = relationship("JobRequisition", back_populates="generated_questions")
    question_sets = relationship("AssessmentQuestionSet", back_populates="question")
    template_question = relationship("AssessmentTemplateQuestion", back_populates="generated_question")
