from sqlalchemy import Column, Integer, String, ForeignKey
from sqlalchemy.orm import relationship
from database.connection import Base

class RequisitionRequiredSkill(Base):
    __tablename__ = "requisition_required_skills"

    id = Column(Integer, primary_key=True, autoincrement=True)
    requisition_id = Column(Integer, ForeignKey("job_requisitions.requisition_id"), nullable=False)
    skill_name = Column(String(100), nullable=False)
    skill_type = Column(String(50), nullable=True)

    requisition = relationship("JobRequisition", back_populates="required_skills")
