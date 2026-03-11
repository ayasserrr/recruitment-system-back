from sqlalchemy import Column, Integer, String, DateTime, Boolean, ForeignKey, func
from sqlalchemy.orm import relationship
from database.connection import Base

class CandidateCV(Base):
    __tablename__ = "candidate_cvs"

    cv_id = Column(Integer, primary_key=True, autoincrement=True)
    candidate_id = Column(Integer, ForeignKey("candidates.candidate_id"), nullable=False)
    file_url = Column(String(500), nullable=False)
    file_name = Column(String(255), nullable=True)
    uploaded_at = Column(DateTime, server_default=func.now())
    is_primary = Column(Boolean, default=False)

    candidate = relationship("Candidate", back_populates="cvs")
    experiences = relationship("CVExperience", back_populates="cv")
    educations = relationship("CVEducation", back_populates="cv")
    projects = relationship("CVProject", back_populates="cv")
    skills = relationship("CVSkill", back_populates="cv")
    applications = relationship("Application", back_populates="cv")
