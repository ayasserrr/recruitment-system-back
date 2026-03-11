from sqlalchemy import Column, Integer, String, Text, DateTime, Boolean, ForeignKey
from sqlalchemy.orm import relationship
from database.connection import Base

class PostingPlatform(Base):
    __tablename__ = "posting_platforms"

    id = Column(Integer, primary_key=True, autoincrement=True)
    requisition_id = Column(Integer, ForeignKey("job_requisitions.requisition_id"), nullable=False)
    platform_name = Column(String(100), nullable=False)
    platform_url = Column(String(500), nullable=True)
    status = Column(String(50), nullable=True)
    posted_at = Column(DateTime, nullable=True)
    platform_post_title = Column(Text, nullable=True)
    platform_post_content = Column(Text, nullable=True)
    platform_post_format = Column(String(50), nullable=True)
    ai_generated = Column(Boolean, default=False)
    ai_model_version = Column(String(100), nullable=True)
    generated_at = Column(DateTime, nullable=True)
    language = Column(String(20), nullable=True)
    tone = Column(String(50), nullable=True)
    target_audience = Column(String(100), nullable=True)

    requisition = relationship("JobRequisition", back_populates="posting_platforms")
