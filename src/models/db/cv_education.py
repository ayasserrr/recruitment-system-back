from sqlalchemy import Column, Integer, String, Date, ForeignKey
from sqlalchemy.orm import relationship
from database.connection import Base

class CVEducation(Base):
    __tablename__ = "cv_educations"

    id = Column(Integer, primary_key=True, autoincrement=True)
    cv_id = Column(Integer, ForeignKey("candidate_cvs.cv_id"), nullable=False)
    institution = Column(String(255), nullable=True)
    degree = Column(String(100), nullable=True)
    field = Column(String(100), nullable=True)
    graduation_date = Column(Date, nullable=True)

    cv = relationship("CandidateCV", back_populates="educations")
