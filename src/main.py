from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv
from routes import base_router, data_router, rank_router
from routes.auth import router as auth_router
from database.connection import engine, Base
import models.db

load_dotenv()

app = FastAPI(title="Recruitment System API", version="1.0.0")

# CORS Configuration for Frontend Integration
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # In production, replace with specific frontend domain
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.on_event("startup")
def startup():
    Base.metadata.create_all(bind=engine)

# Include routers
app.include_router(auth_router)
app.include_router(base_router)
app.include_router(data_router)
app.include_router(rank_router)

@app.get("/")
def root():
    return {"message": "Recruitment System API is running"}