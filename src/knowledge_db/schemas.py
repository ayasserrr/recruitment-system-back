from typing import Optional, List
from sqlmodel import SQLModel
from knowledge_db.models import ConceptLevel


# ── Category ─────────────────────────────────────────────────────────────────

class CategoryBase(SQLModel):
    name: str
    label: str
    description: Optional[str] = None


class CategoryCreate(CategoryBase):
    pass


class CategoryUpdate(SQLModel):
    name: Optional[str] = None
    label: Optional[str] = None
    description: Optional[str] = None


class CategoryRead(CategoryBase):
    id: int


# ── Tool ──────────────────────────────────────────────────────────────────────

class ToolBase(SQLModel):
    name: str
    slug: str
    description: Optional[str] = None
    category_id: int


class ToolCreate(ToolBase):
    pass


class ToolUpdate(SQLModel):
    name: Optional[str] = None
    slug: Optional[str] = None
    description: Optional[str] = None
    category_id: Optional[int] = None


class ToolRead(ToolBase):
    id: int


class ToolReadWithConcepts(ToolRead):
    concepts: List["ConceptRead"] = []


# ── Concept ───────────────────────────────────────────────────────────────────

class ConceptBase(SQLModel):
    tool_id: int
    level: ConceptLevel
    name: str
    notes: Optional[str] = None


class ConceptCreate(ConceptBase):
    pass


class ConceptUpdate(SQLModel):
    tool_id: Optional[int] = None
    level: Optional[ConceptLevel] = None
    name: Optional[str] = None
    notes: Optional[str] = None


class ConceptRead(ConceptBase):
    id: int


ToolReadWithConcepts.model_rebuild()
