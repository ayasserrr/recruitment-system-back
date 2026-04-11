from sqlalchemy import Column, Integer, String, Text, DateTime, func
from sqlalchemy.orm import relationship
from database.connection import Base

class Company(Base):
    __tablename__ = "companies"

    company_id = Column(Integer, primary_key=True, autoincrement=True)
    
    # Company information
    name = Column(String(255), nullable=False)
    industry = Column(String(100), nullable=True)
    website = Column(String(255), nullable=True)
    logo_url = Column(String(500), nullable=True)
    address = Column(Text, nullable=True)
    
    # Authentication and profile fields (from Recruiter model)
    email = Column(String(255), nullable=False, unique=True)
    password_hash = Column(String(255), nullable=False)
    first_name = Column(String(100), nullable=False)
    last_name = Column(String(100), nullable=False)
    phone = Column(String(30), nullable=True)
    profile_picture = Column(String(500), nullable=True)
    role = Column(String(50), nullable=True)
    bio = Column(Text, nullable=True)
    last_login = Column(DateTime, nullable=True)
    
    # Timestamps
    created_at = Column(DateTime, server_default=func.now())

    # Relationships
    job_requisitions = relationship("JobRequisition", back_populates="company")
    assessment_templates = relationship("AssessmentTemplate", back_populates="company")
    recruiters = relationship("Recruiter", back_populates="company")
    social_auth = relationship("CompanySocialAuth", back_populates="company", uselist=False)
