from sqlalchemy import Column, Integer, String, Text, ForeignKey
from sqlalchemy.orm import relationship
from database.connection import Base

class CVProject(Base):
    __tablename__ = "cv_projects"

    id = Column(Integer, primary_key=True, autoincrement=True)
    cv_id = Column(Integer, ForeignKey("candidate_cvs.cv_id"), nullable=False)
    project_name = Column(String(255), nullable=True)
    description = Column(Text, nullable=True)
    tech_stack = Column(String(500), nullable=True)

    cv = relationship("CandidateCV", back_populates="projects")
