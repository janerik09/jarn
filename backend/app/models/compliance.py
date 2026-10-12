from sqlalchemy import Column, String, DateTime, Text
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.sql import func
from app.database import Base
import uuid


class ComplianceAssessment(Base):
    __tablename__ = "compliance_assessments"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    framework = Column(String(50), nullable=False, default="all")
    status = Column(String(20), nullable=False, default="running", index=True)
    results = Column(JSONB, nullable=True)
    error = Column(Text, nullable=True)
    created_at = Column(DateTime, server_default=func.now(), nullable=False)
    completed_at = Column(DateTime, nullable=True)
