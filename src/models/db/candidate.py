from sqlalchemy import Column, Integer, String, Text, DateTime, func
from sqlalchemy.orm import relationship
from database.connection import Base

class Candidate(Base):
    __tablename__ = "candidates"

    candidate_id = Column(Integer, primary_key=True, autoincrement=True)
    first_name = Column(String(100), nullable=False)
    last_name = Column(String(100), nullable=False)
    email = Column(String(255), nullable=False, unique=True)
    phone = Column(String(30), nullable=True)
    linkedin_url = Column(String(500), nullable=True)
    portfolio_url = Column(String(500), nullable=True)
    professional_summary = Column(Text, nullable=True)
    years_of_experience = Column(Integer, nullable=True)
    education_level = Column(String(100), nullable=True)
    field_of_study = Column(String(100), nullable=True)
    created_at = Column(DateTime, server_default=func.now())

    cvs = relationship("CandidateCV", back_populates="candidate")
    applications = relationship("Application", back_populates="candidate")
