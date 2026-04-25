from typing import Annotated, List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlmodel import Session, select
from sqlalchemy.exc import IntegrityError

from knowledge_db.database import get_knowledge_session
from knowledge_db.models import Concept, ConceptLevel
from knowledge_db.schemas import ConceptCreate, ConceptRead, ConceptUpdate

router = APIRouter(prefix="/concepts", tags=["Knowledge — Concepts"])

SessionDep = Annotated[Session, Depends(get_knowledge_session)]


@router.post("/", response_model=ConceptRead, status_code=status.HTTP_201_CREATED)
def create_concept(data: ConceptCreate, session: SessionDep):
    concept = Concept.model_validate(data)
    session.add(concept)
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        raise HTTPException(status_code=409, detail=f"Concept '{data.name}' already exists for this tool.")
    session.refresh(concept)
    return concept


@router.get("/", response_model=List[ConceptRead])
def list_concepts(
    session: SessionDep,
    tool_id: Optional[int] = Query(None),
    level: Optional[ConceptLevel] = Query(None),
):
    """
    Primary endpoint used by the assessment engine.
    Filter by tool_id and/or level to pull concepts before sending them to the LLM.
    """
    query = select(Concept)
    if tool_id is not None:
        query = query.where(Concept.tool_id == tool_id)
    if level is not None:
        query = query.where(Concept.level == level)
    return session.exec(query).all()


@router.get("/{concept_id}", response_model=ConceptRead)
def get_concept(concept_id: int, session: SessionDep):
    concept = session.get(Concept, concept_id)
    if not concept:
        raise HTTPException(status_code=404, detail="Concept not found")
    return concept


@router.put("/{concept_id}", response_model=ConceptRead)
def update_concept(concept_id: int, data: ConceptUpdate, session: SessionDep):
    concept = session.get(Concept, concept_id)
    if not concept:
        raise HTTPException(status_code=404, detail="Concept not found")
    for field, value in data.model_dump(exclude_unset=True).items():
        setattr(concept, field, value)
    session.add(concept)
    session.commit()
    session.refresh(concept)
    return concept


@router.delete("/{concept_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_concept(concept_id: int, session: SessionDep):
    concept = session.get(Concept, concept_id)
    if not concept:
        raise HTTPException(status_code=404, detail="Concept not found")
    session.delete(concept)
    session.commit()
