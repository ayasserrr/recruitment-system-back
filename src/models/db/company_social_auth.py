from sqlalchemy import Boolean, Column, Integer, String, DateTime, ForeignKey, func
from sqlalchemy.orm import relationship
from database.connection import Base


class CompanySocialAuth(Base):
    __tablename__ = "company_social_auth"

    id = Column(Integer, primary_key=True, autoincrement=True)
    company_id = Column(Integer, ForeignKey("companies.company_id", ondelete="CASCADE"), nullable=False, unique=True)
    linkedin_access_token = Column(String(2048), nullable=True)
    linkedin_organization_id = Column(String(255), nullable=True)
    provider_user_id = Column(String(255), nullable=True)   # LinkedIn Person ID (e.g. "abc123XYZ")
    expires_at = Column(DateTime, nullable=True)
    is_active = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

    company = relationship("Company", back_populates="social_auth")
