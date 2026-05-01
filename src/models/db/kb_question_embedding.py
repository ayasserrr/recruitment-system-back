from sqlalchemy import Column, Integer, String, DateTime, ForeignKey, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import relationship
from database.connection import Base


class KbQuestionEmbedding(Base):
    __tablename__ = "kb_question_embeddings"

    id = Column(Integer, primary_key=True, autoincrement=True)
    question_id = Column(
        Integer, ForeignKey("kb_questions.id", ondelete="CASCADE"),
        nullable=False, unique=True, index=True,
    )
    model_id = Column(String(100), nullable=False, default="all-MiniLM-L6-v2")
    # 384-dimensional float vector stored as JSON array
    embedding = Column(JSONB, nullable=False)
    computed_at = Column(DateTime, server_default=func.now())

    question = relationship("KbQuestion", back_populates="embedding")
