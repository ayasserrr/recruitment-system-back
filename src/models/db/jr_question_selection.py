from sqlalchemy import Column, Integer, String, Text, DateTime, ForeignKey, func
from sqlalchemy.orm import relationship
from database.connection import Base


class JrQuestionSelection(Base):
    __tablename__ = "jr_question_selections"

    id = Column(Integer, primary_key=True, autoincrement=True)
    requisition_id = Column(
        Integer, ForeignKey("job_requisitions.requisition_id"), nullable=False, index=True,
    )
    application_id = Column(
        Integer, ForeignKey("applications.application_id"), nullable=False, index=True,
    )
    question_id = Column(
        Integer, ForeignKey("kb_questions.id"), nullable=False,
    )
    round_number = Column(Integer, nullable=False)                # 1-5
    selection_reason = Column(Text, nullable=True)               # "gap:docker, sim:0.87"
    selected_at = Column(DateTime, server_default=func.now())

    question = relationship("KbQuestion", back_populates="selections")
