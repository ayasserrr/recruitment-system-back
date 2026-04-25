import os
from sqlmodel import SQLModel, Session, create_engine
from dotenv import load_dotenv

load_dotenv()

KNOWLEDGE_DB_URL = os.getenv("knowledge_db_URL", "postgresql://postgres:12345@localhost:5432/knowledge_db")

knowledge_engine = create_engine(KNOWLEDGE_DB_URL)


def get_knowledge_session():
    with Session(knowledge_engine) as session:
        yield session


def create_knowledge_tables():
    import knowledge_db.models  # noqa: registers tables into SQLModel.metadata
    SQLModel.metadata.create_all(knowledge_engine)
