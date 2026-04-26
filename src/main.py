from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from dotenv import load_dotenv
from routes import base_router, data_router, rank_router, job_requisitions_router, candidates_router, jobs_router, assessment_router, admin_router
from routes.auth import router as auth_router
from routes.recruiter_auth import router as recruiter_auth_router
from routes.linkedin_auth import router as linkedin_auth_router
from routes.interview_session import router as interview_session_router
from routes.frontend_jobs import router as frontend_jobs_router
from routes.pipeline import router as pipeline_router
from routes.apply import router as apply_router
from routes.semantic_frontend import router as semantic_router
from routes.assessment_frontend import router as assessment_frontend_router
from routes.technical_interview_frontend import router as tech_interview_router
from routes.hr_interview_frontend import router as hr_interview_router
from routes.final_ranking_frontend import router as final_ranking_router
from routes.shortlist import router as shortlist_router
from routes.analytics import router as analytics_router
from database.connection import engine, Base
import models.db
from knowledge_db.routers.categories import router as categories_router
from knowledge_db.routers.tools import router as tools_router
from knowledge_db.routers.concepts import router as concepts_router
from knowledge_db.database import create_knowledge_tables

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
    create_knowledge_tables()

# Include routers
app.include_router(auth_router)
app.include_router(recruiter_auth_router)
from routes.recruiter_auth import recruiter_alias_router
app.include_router(recruiter_alias_router)
app.include_router(linkedin_auth_router)
app.include_router(base_router)
app.include_router(data_router)
app.include_router(rank_router)
app.include_router(job_requisitions_router)
app.include_router(candidates_router)
app.include_router(assessment_router)
app.include_router(admin_router)
app.include_router(interview_session_router)

# ── Frontend / Dashboard routers ───────────────────────────────────────────────
# frontend_jobs_router must come BEFORE the legacy jobs_router so that
# GET /api/v1/jobs/{id} resolves to the protected dashboard endpoint.
app.include_router(frontend_jobs_router)
app.include_router(pipeline_router)
app.include_router(semantic_router)
app.include_router(assessment_frontend_router)
app.include_router(tech_interview_router)
app.include_router(hr_interview_router)
app.include_router(final_ranking_router)
app.include_router(shortlist_router)
app.include_router(analytics_router)
# apply_router registers its own full paths (no APIRouter prefix)
app.include_router(apply_router)

# Legacy public jobs router (kept for POST /{jid}/rank-candidates)
app.include_router(jobs_router)

# Knowledge DB routers
app.include_router(categories_router, prefix="/api/v1/knowledge")
app.include_router(tools_router, prefix="/api/v1/knowledge")
app.include_router(concepts_router, prefix="/api/v1/knowledge")

@app.get("/")
def root():
    return {"message": "Recruitment System API is running"}