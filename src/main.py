from fastapi import FastAPI
from dotenv import load_dotenv
from routes import base_router, data_router, rank_router
from database.connection import engine, Base
import models.db

load_dotenv()

app = FastAPI(title="Recruitment System API", version="1.0.0")

@app.on_event("startup")
def startup():
    Base.metadata.create_all(bind=engine)

app.include_router(base_router)
app.include_router(data_router)
app.include_router(rank_router)

@app.get("/")
def root():
    return {"message": "Recruitment System API is running"}