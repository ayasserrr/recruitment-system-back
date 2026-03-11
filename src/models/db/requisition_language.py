from sqlalchemy import Column, Integer, String, ForeignKey
from sqlalchemy.orm import relationship
from database.connection import Base

class RequisitionLanguage(Base):
    __tablename__ = "requisition_languages"

    id = Column(Integer, primary_key=True, autoincrement=True)
    requisition_id = Column(Integer, ForeignKey("job_requisitions.requisition_id"), nullable=False)
    language = Column(String(50), nullable=False)
    proficiency_level = Column(String(50), nullable=True)

    requisition = relationship("JobRequisition", back_populates="languages")
