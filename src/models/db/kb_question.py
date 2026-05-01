from sqlalchemy import Column, Integer, String, Text, Boolean, DateTime, ForeignKey, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import relationship
from database.connection import Base


class KbQuestion(Base):
    __tablename__ = "kb_questions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    topic_id = Column(Integer, ForeignKey("kb_topics.id"), nullable=False, index=True)
    question_text = Column(Text, nullable=False)
    difficulty = Column(String(20), nullable=False, default="mid")   # junior | mid | senior
    round_hint = Column(Integer, nullable=True)                      # preferred round 1-5, null = any
    # keyword tags for fast pre-filter before embedding search
    context_tags = Column(JSONB, nullable=True)                      # ["docker", "kubernetes"]
    # what a strong answer should cover (used in grading feedback)
    ideal_points = Column(JSONB, nullable=True)                      # ["mentions CAP theorem", ...]
    # terms that reveal expert-level knowledge
    expert_terms = Column(JSONB, nullable=True)                      # ["sharding key", "hot spot"]
    is_active = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

    topic = relationship("KbTopic", back_populates="questions")
    embedding = relationship("KbQuestionEmbedding", back_populates="question", uselist=False)
    selections = relationship("JrQuestionSelection", back_populates="question")
