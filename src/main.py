from fastapi import FastAPI
from dotenv import load_dotenv
from routes import base_router, data_router, rank_router

load_dotenv()

app = FastAPI()

app.include_router(base_router)
app.include_router(data_router)
app.include_router(rank_router)