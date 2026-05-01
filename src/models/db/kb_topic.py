from sqlalchemy import Column, Integer, String, Text, DateTime, ForeignKey, func
from sqlalchemy.orm import relationship
from database.connection import Base


class KbTopic(Base):
    __tablename__ = "kb_topics"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(100), nullable=False, unique=True)          # slug: "database", "security"
    display_name = Column(String(200), nullable=False)               # "Database & Storage"
    parent_id = Column(Integer, ForeignKey("kb_topics.id"), nullable=True)
    description = Column(Text, nullable=True)
    created_at = Column(DateTime, server_default=func.now())

    parent = relationship("KbTopic", remote_side=[id], back_populates="subtopics")
    subtopics = relationship("KbTopic", back_populates="parent")
    questions = relationship("KbQuestion", back_populates="topic")
