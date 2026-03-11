from sqlalchemy import Column, Integer, String, Text, DateTime, ForeignKey, func
from sqlalchemy.orm import relationship
from database.connection import Base

class Recruiter(Base):
    __tablename__ = "recruiters"

    recruiter_id = Column(Integer, primary_key=True, autoincrement=True)
    company_id = Column(Integer, ForeignKey("companies.company_id"), nullable=False)
    first_name = Column(String(100), nullable=False)
    last_name = Column(String(100), nullable=False)
    email = Column(String(255), nullable=False, unique=True)
    password_hash = Column(String(255), nullable=False)
    role = Column(String(50), nullable=True)
    bio = Column(Text, nullable=True)
    phone = Column(String(30), nullable=True)
    profile_picture = Column(String(500), nullable=True)
    created_at = Column(DateTime, server_default=func.now())
    last_login = Column(DateTime, nullable=True)

    company = relationship("Company", back_populates="recruiters")
    job_requisitions = relationship("JobRequisition", back_populates="recruiter")
