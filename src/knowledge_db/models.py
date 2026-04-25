from enum import Enum as PyEnum
from typing import Optional, List
from sqlmodel import Field, Relationship, SQLModel


class ConceptLevel(str, PyEnum):
    beginner = "beginner"
    mid = "mid"
    high = "high"


class Category(SQLModel, table=True):
    __tablename__ = "categories"

    id: Optional[int] = Field(default=None, primary_key=True)
    name: str = Field(unique=True, index=True, max_length=100)
    label: str = Field(max_length=200)
    description: Optional[str] = None

    tools: List["Tool"] = Relationship(back_populates="category")


class Tool(SQLModel, table=True):
    __tablename__ = "tools"

    id: Optional[int] = Field(default=None, primary_key=True)
    name: str = Field(unique=True, index=True, max_length=100)
    slug: str = Field(unique=True, index=True, max_length=100)
    description: Optional[str] = None
    category_id: int = Field(foreign_key="categories.id")

    category: Optional[Category] = Relationship(back_populates="tools")
    concepts: List["Concept"] = Relationship(back_populates="tool")


class Concept(SQLModel, table=True):
    __tablename__ = "concepts"

    id: Optional[int] = Field(default=None, primary_key=True)
    tool_id: int = Field(foreign_key="tools.id")
    level: ConceptLevel
    name: str = Field(max_length=200)
    notes: Optional[str] = None

    tool: Optional[Tool] = Relationship(back_populates="concepts")
