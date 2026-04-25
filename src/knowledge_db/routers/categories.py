from typing import Annotated, List
from fastapi import APIRouter, Depends, HTTPException, status
from sqlmodel import Session, select
from sqlalchemy.exc import IntegrityError

from knowledge_db.database import get_knowledge_session
from knowledge_db.models import Category
from knowledge_db.schemas import CategoryCreate, CategoryRead, CategoryUpdate

router = APIRouter(prefix="/categories", tags=["Knowledge — Categories"])

SessionDep = Annotated[Session, Depends(get_knowledge_session)]


@router.post("/", response_model=CategoryRead, status_code=status.HTTP_201_CREATED)
def create_category(data: CategoryCreate, session: SessionDep):
    category = Category.model_validate(data)
    session.add(category)
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        raise HTTPException(status_code=409, detail=f"Category '{data.name}' already exists.")
    session.refresh(category)
    return category


@router.get("/", response_model=List[CategoryRead])
def list_categories(session: SessionDep):
    return session.exec(select(Category)).all()


@router.get("/{category_id}", response_model=CategoryRead)
def get_category(category_id: int, session: SessionDep):
    category = session.get(Category, category_id)
    if not category:
        raise HTTPException(status_code=404, detail="Category not found")
    return category


@router.put("/{category_id}", response_model=CategoryRead)
def update_category(category_id: int, data: CategoryUpdate, session: SessionDep):
    category = session.get(Category, category_id)
    if not category:
        raise HTTPException(status_code=404, detail="Category not found")
    for field, value in data.model_dump(exclude_unset=True).items():
        setattr(category, field, value)
    session.add(category)
    session.commit()
    session.refresh(category)
    return category


@router.delete("/{category_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_category(category_id: int, session: SessionDep):
    category = session.get(Category, category_id)
    if not category:
        raise HTTPException(status_code=404, detail="Category not found")
    if category.tools:
        raise HTTPException(
            status_code=400,
            detail="Cannot delete category that still has tools. Remove tools first."
        )
    session.delete(category)
    session.commit()
