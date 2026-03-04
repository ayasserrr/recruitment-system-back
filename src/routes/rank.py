from fastapi import APIRouter, status
from fastapi.responses import JSONResponse

from controllers import RankController
from .schemas.rank import RankRequest


rank_router = APIRouter(
    prefix="/api/v1/rank",
    tags=["rank"],
)


@rank_router.post("/{company_id}/{job_id}")
async def rank_job(company_id: str, job_id: str, payload: RankRequest):
    controller = RankController()

    try:
        report = await controller.rank_job(
            company_id=company_id,
            job_id=job_id,
            job_requirements=payload.job_requirements,
        )
    except FileNotFoundError as e:
        return JSONResponse(
            status_code=status.HTTP_404_NOT_FOUND,
            content={"signal": str(e)},
        )
    except Exception as e:
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"signal": "ranking_failed", "error": str(e)},
        )

    return JSONResponse(status_code=status.HTTP_200_OK, content=report)
