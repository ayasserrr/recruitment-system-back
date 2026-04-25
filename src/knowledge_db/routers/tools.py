from typing import Annotated, List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlmodel import Session, select
from sqlalchemy.orm import selectinload
from sqlalchemy.exc import IntegrityError

from knowledge_db.database import get_knowledge_session
from knowledge_db.models import Tool, Concept
from knowledge_db.schemas import ToolCreate, ToolRead, ToolReadWithConcepts, ToolUpdate

router = APIRouter(prefix="/tools", tags=["Knowledge — Tools"])

SessionDep = Annotated[Session, Depends(get_knowledge_session)]


@router.post("/", response_model=ToolRead, status_code=status.HTTP_201_CREATED)
def create_tool(data: ToolCreate, session: SessionDep):
    tool = Tool.model_validate(data)
    session.add(tool)
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        raise HTTPException(status_code=409, detail=f"Tool '{data.name}' or slug '{data.slug}' already exists.")
    session.refresh(tool)
    return tool


@router.get("/", response_model=List[ToolRead])
def list_tools(
    session: SessionDep,
    category_id: Optional[int] = Query(None),
):
    query = select(Tool)
    if category_id is not None:
        query = query.where(Tool.category_id == category_id)
    return session.exec(query).all()


@router.get("/{tool_id}", response_model=ToolReadWithConcepts)
def get_tool(tool_id: int, session: SessionDep):
    tool = session.exec(
        select(Tool)
        .where(Tool.id == tool_id)
        .options(selectinload(Tool.concepts))
    ).first()
    if not tool:
        raise HTTPException(status_code=404, detail="Tool not found")
    return tool


@router.put("/{tool_id}", response_model=ToolRead)
def update_tool(tool_id: int, data: ToolUpdate, session: SessionDep):
    tool = session.get(Tool, tool_id)
    if not tool:
        raise HTTPException(status_code=404, detail="Tool not found")
    for field, value in data.model_dump(exclude_unset=True).items():
        setattr(tool, field, value)
    session.add(tool)
    session.commit()
    session.refresh(tool)
    return tool


@router.delete("/{tool_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_tool(tool_id: int, session: SessionDep):
    tool = session.get(Tool, tool_id)
    if not tool:
        raise HTTPException(status_code=404, detail="Tool not found")
    # remove child concepts first to avoid FK violation
    concepts = session.exec(select(Concept).where(Concept.tool_id == tool_id)).all()
    for concept in concepts:
        session.delete(concept)
    session.delete(tool)
    session.commit()
