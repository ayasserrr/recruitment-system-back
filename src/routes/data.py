from fastapi import APIRouter, Depends, UploadFile, status
from fastapi.responses import JSONResponse
import aiofiles
import logging
import json
from pathlib import Path
from sqlalchemy.orm import Session
from helpers import get_settings, settings
from controllers import CompanyController, DataController, ProcessController
from database.connection import get_db
from models import ResponseSignal
from models.db.application import Application
from models.db.job_posting import JobPosting
from services import CVPersistenceService
from routes.jobs import check_job_deadline

logger = logging.getLogger("uvicorn.error")

data_router = APIRouter(
    prefix="/api/v1/data",
    tags=["data"],
)

@data_router.post("/upload/{company_id}/{job_id}/{candidate_id}")
async def upload_data(
    company_id: str,
    job_id: str,
    candidate_id: str,
    file: UploadFile,
    app_settings: settings = Depends(get_settings),
    db: Session = Depends(get_db),
):
    
    # Reject submissions past the cv_collection_end_date
    try:
        requisition_id = int(job_id)
        check_job_deadline(requisition_id, db)
    except ValueError:
        pass  # job_id is not a DB requisition_id — skip deadline check

    # Initialize Controller
    data_controller = DataController()
    
    # 1. Validate file (type and size)
    is_valid, result_signal = await data_controller.validate_uploaded_file(file=file)

    if not is_valid:
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={"signal": result_signal}
        )

    if data_controller.candidate_has_cv(company_id=company_id, job_id=job_id, candidate_id=candidate_id):
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content={"signal": ResponseSignal.FILE_ALREADY_EXISTS.value},
        )
    
    # 2. Generate the unique file path (now includes timestamp + clean name)
    file_path, file_id = data_controller.generate_unique_filePath(
        original_filename=file.filename,
        company_id=company_id,
        job_id=job_id,
        candidate_id=candidate_id,
    )
    
    try:
        # Save file using chunks
        async with aiofiles.open(file_path, 'wb') as f:
            while chunk := await file.read(app_settings.FILE_DEFAULT_CHUNK_SIZE):
                await f.write(chunk)
                
    except Exception as e:
        logger.error(f"Error saving file for {company_id}/{job_id}/{candidate_id}: {e}")
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"signal": ResponseSignal.FILE_UPLOADED_FAILED.value}
        )

    process_controller = ProcessController()
    try:
        cv_json = await process_controller.extract_candidate_cv_json(
            company_id=company_id,
            job_id=job_id,
            candidate_id=candidate_id,
        )

        candidate_dir = CompanyController().get_candidate_path(
            company_id=company_id,
            job_id=job_id,
            candidate_id=candidate_id,
            create=False,
        )
        cv_json_path: Path = candidate_dir / f"{candidate_id}_cv.json"

        async with aiofiles.open(cv_json_path, "w", encoding="utf-8") as f:
            await f.write(json.dumps(cv_json, ensure_ascii=False, indent=2))

        # Persist parsed CV data to database.
        # candidate_id path param is the DB integer assigned at registration.
        try:
            registered_id = int(candidate_id)
        except (ValueError, TypeError):
            registered_id = None

        db_candidate_id, db_cv_id = CVPersistenceService().persist(
            cv_json=cv_json,
            db=db,
            file_url=str(file_path),
            registered_candidate_id=registered_id,
        )

        # Create / upsert Application record so the ranking pipeline can find this CV.
        # context_gatherer_node queries exclusively via the Application table.
        try:
            requisition_id_int = int(job_id)
            posting = db.query(JobPosting).filter(
                JobPosting.requisition_id == requisition_id_int
            ).first()
            if posting and db_candidate_id:
                existing_app = db.query(Application).filter(
                    Application.posting_id == posting.posting_id,
                    Application.candidate_id == db_candidate_id,
                ).first()
                if existing_app:
                    existing_app.cv_id = db_cv_id
                else:
                    db.add(Application(
                        posting_id=posting.posting_id,
                        candidate_id=db_candidate_id,
                        cv_id=db_cv_id,
                        status="Applied",
                    ))
                db.commit()
        except (ValueError, TypeError):
            pass  # job_id is not a DB requisition_id — skip application creation
        except Exception as e:
            db.rollback()
            logger.error(f"Could not create application record for {company_id}/{job_id}/{candidate_id}: {e}")
            return JSONResponse(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                content={"signal": "APPLICATION_LINK_FAILED", "detail": str(e)},
            )

    except Exception as e:
        logger.error(f"Error processing CV JSON for {company_id}/{job_id}/{candidate_id}: {e}")
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={
                "signal": ResponseSignal.PROCESSING_FAILED.value,
                "file_id": file_id,
            },
        )

    # 5. Auto-trigger text extraction (Processing API)
    try:
        await process_controller.extract_candidate_text(
            company_id=company_id,
            job_id=job_id,
            candidate_id=candidate_id,
        )
    except Exception as e:
        logger.warning(
            f"Auto-processing trigger failed for {company_id}/{job_id}/{candidate_id}: {e}"
        )
        # Non-fatal: CV was uploaded and parsed; processing can be retried manually.

    # 6. Success Response
    return JSONResponse(
        status_code=status.HTTP_201_CREATED,
        content={
            "signal": ResponseSignal.FILE_UPLOADED_SUCCESSFULLY.value,
            "file_id": file_id,
            "cv_json_path": str(cv_json_path),
            "cv_json": cv_json,
            "db_candidate_id": db_candidate_id,
            "db_cv_id": db_cv_id,
        }
    )


@data_router.post("/process/{company_id}/{job_id}/{candidate_id}")
async def process_candidate(company_id: str, job_id: str, candidate_id: str):
    process_controller = ProcessController()
    try:
        text = await process_controller.extract_candidate_text(
            company_id=company_id,
            job_id=job_id,
            candidate_id=candidate_id,
        )
    except FileNotFoundError as e:
        return JSONResponse(
            status_code=status.HTTP_404_NOT_FOUND,
            content={"signal": str(e)},
        )

    return JSONResponse(
        status_code=status.HTTP_200_OK,
        content={"text": text},
    )


@data_router.post("/process/{company_id}/{job_id}")
async def process_job(company_id: str, job_id: str):
    process_controller = ProcessController()
    text = await process_controller.extract_job_text(company_id=company_id, job_id=job_id)
    return JSONResponse(
        status_code=status.HTTP_200_OK,
        content={"text": text},
    )


@data_router.post("/process/{company_id}")
async def process_company(company_id: str):
    process_controller = ProcessController()
    text = await process_controller.extract_company_text(company_id=company_id)
    return JSONResponse(
        status_code=status.HTTP_200_OK,
        content={"text": text},
    )


@data_router.get("/files/")
async def list_companies():
    company_controller = CompanyController()
    roots = [company_controller.uploads_dir, company_controller.legacy_uploads_dir]
    companies_set: set[str] = set()
    for base in roots:
        if not base.exists():
            continue
        for p in base.iterdir():
            if p.is_dir():
                companies_set.add(p.name)

    companies = sorted(companies_set)
    return JSONResponse(status_code=status.HTTP_200_OK, content={"companies": companies})


@data_router.get("/files/{company_id}")
async def list_jobs(company_id: str):
    company_controller = CompanyController()
    company_dir = company_controller.get_company_path(company_id=company_id, create=False)
    if not company_dir.exists():
        return JSONResponse(status_code=status.HTTP_200_OK, content={"company_id": company_id, "jobs": []})
    jobs = [p.name for p in company_dir.iterdir() if p.is_dir()]
    jobs.sort()
    return JSONResponse(status_code=status.HTTP_200_OK, content={"company_id": company_id, "jobs": jobs})


@data_router.get("/files/{company_id}/{job_id}")
async def list_candidates(company_id: str, job_id: str):
    company_controller = CompanyController()
    job_dir = company_controller.get_job_path(company_id=company_id, job_id=job_id, create=False)
    if not job_dir.exists():
        return JSONResponse(
            status_code=status.HTTP_200_OK,
            content={"company_id": company_id, "job_id": job_id, "candidates": []},
        )
    candidates = [p.name for p in job_dir.iterdir() if p.is_dir()]
    candidates.sort()
    return JSONResponse(
        status_code=status.HTTP_200_OK,
        content={"company_id": company_id, "job_id": job_id, "candidates": candidates},
    )


@data_router.get("/files/{company_id}/{job_id}/{candidate_id}")
async def list_candidate_cv(company_id: str, job_id: str, candidate_id: str):
    company_controller = CompanyController()
    candidate_dir = company_controller.get_candidate_path(
        company_id=company_id,
        job_id=job_id,
        candidate_id=candidate_id,
        create=False,
    )

    if not candidate_dir.exists():
        return JSONResponse(
            status_code=status.HTTP_200_OK,
            content={
                "company_id": company_id,
                "job_id": job_id,
                "candidate_id": candidate_id,
                "files": [],
            },
        )

    cvs = [p.name for p in candidate_dir.iterdir() if p.is_file() and p.suffix.lower() in {".pdf", ".txt"}]
    cvs.sort(reverse=True)
    return JSONResponse(
        status_code=status.HTTP_200_OK,
        content={
            "company_id": company_id,
            "job_id": job_id,
            "candidate_id": candidate_id,
            "files": cvs,
        },
    )