from sqlalchemy import Column, Integer, String, Text, DateTime, ForeignKey
from sqlalchemy.orm import relationship
from database.connection import Base

class PipelineStageLog(Base):
    __tablename__ = "pipeline_stage_logs"

    log_id = Column(Integer, primary_key=True, autoincrement=True)
    application_id = Column(Integer, ForeignKey("applications.application_id"), nullable=False)
    stage_name = Column(String(100), nullable=False)
    status = Column(String(50), nullable=False)
    entered_at = Column(DateTime, nullable=False)
    exited_at = Column(DateTime, nullable=True)
    notes = Column(Text, nullable=True)

    application = relationship("Application", back_populates="pipeline_logs")
