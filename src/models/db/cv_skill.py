from sqlalchemy import Column, Integer, String, ForeignKey
from sqlalchemy.orm import relationship
from database.connection import Base

class CVSkill(Base):
    __tablename__ = "cv_skills"

    id = Column(Integer, primary_key=True, autoincrement=True)
    cv_id = Column(Integer, ForeignKey("candidate_cvs.cv_id"), nullable=False)
    skill_name = Column(String(100), nullable=False)
    proficiency_level = Column(String(50), nullable=True)

    cv = relationship("CandidateCV", back_populates="skills")
