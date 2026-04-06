from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from dotenv import load_dotenv
from routes import base_router, data_router, rank_router, job_requisitions_router
from routes.auth import router as auth_router
from routes.recruiter_auth import router as recruiter_auth_router
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

def _friendly_message(field: str, msg: str) -> str:
    msg_lower = msg.lower()
    if "field required" in msg_lower or "missing" in msg_lower:
        return f"'{field}' is required."
    if "valid date" in msg_lower or "too short" in msg_lower:
        return f"'{field}' must be a valid date in YYYY-MM-DD format (e.g. 2024-05-06)."
    if "valid string" in msg_lower:
        return f"'{field}' must be a text value, not a number or null."
    if "valid integer" in msg_lower or "valid number" in msg_lower:
        return f"'{field}' must be a number."
    if "valid boolean" in msg_lower:
        return f"'{field}' must be true or false."
    if "valid list" in msg_lower or "valid array" in msg_lower:
        return f"'{field}' must be an array (e.g. [\"value1\", \"value2\"])."
    return f"'{field}': {msg}."


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    error_messages = []
    for err in exc.errors():
        field = " -> ".join(str(loc) for loc in err["loc"] if loc != "body")
        message = _friendly_message(field, err["msg"])
        error_messages.append(message)

    print("⚠️  422 Validation Error:")
    for msg in error_messages:
        print(f"   {msg}")

    return JSONResponse(
        status_code=422,
        content={"detail": error_messages}
    )


@app.on_event("startup")
def startup():
    Base.metadata.create_all(bind=engine)

# Include routers
app.include_router(auth_router)
app.include_router(recruiter_auth_router)
app.include_router(base_router)
app.include_router(data_router)
app.include_router(rank_router)
app.include_router(job_requisitions_router)

@app.get("/")
def root():
    return {"message": "Recruitment System API is running"}