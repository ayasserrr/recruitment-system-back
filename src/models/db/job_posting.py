from sqlalchemy import Column, Integer, String, DateTime, ForeignKey, UniqueConstraint, func
from sqlalchemy.orm import relationship
from database.connection import Base

class JobPosting(Base):
    __tablename__ = "job_postings"

    posting_id = Column(Integer, primary_key=True, autoincrement=True)
    requisition_id = Column(Integer, ForeignKey("job_requisitions.requisition_id"), nullable=False, unique=True)
    state = Column(String(50), nullable=False, default="Open")
    posted_date = Column(DateTime, nullable=True)
    closed_date = Column(DateTime, nullable=True)
    total_applications = Column(Integer, default=0)
    views_count = Column(Integer, default=0)
    created_at = Column(DateTime, server_default=func.now())

    requisition = relationship("JobRequisition", back_populates="job_posting")
    applications = relationship("Application", back_populates="posting")
    final_rankings = relationship("FinalRanking", back_populates="posting")
