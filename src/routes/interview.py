"""
Interview Management Routes
API endpoints for managing technical interviews with LiveKit integration
"""

import asyncio
import json
import logging
import uuid
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, HTTPException, Depends, BackgroundTasks
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..database import get_db
from ..services.technical_interview_agent import interview_agent, InterviewMode
from ..models.db.application import Application
from ..models.db.job_requisition import JobRequisition
from ..models.db.semantic_analysis_report import SemanticAnalysisReport

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/interviews", tags=["interviews"])


class InterviewRequest(BaseModel):
    """Request model for starting an interview"""
    application_id: int = Field(..., description="Application ID to interview")
    mode: str = Field(default="technical", description="Interview mode: 'technical' or 'hr'")
    language: Optional[str] = Field(default="en", description="Preferred language: 'en', 'ar', or 'auto'")
    persona: str = Field(default="focused", description="Interview persona: 'focused' (5-round), 'elite' (professional English), or 'bilingual' (Arabic/English)")


class InterviewResponse(BaseModel):
    """Response model for interview initiation"""
    session_id: str
    livekit_room_name: str
    livekit_token: str
    status: str
    message: str


class InterviewStatus(BaseModel):
    """Model for interview status updates"""
    session_id: str
    current_question: int
    total_questions: int
    confidence_score: float
    technical_accuracy: float
    status: str


class CandidateScorecard(BaseModel):
    """Model for final candidate scorecard"""
    candidate_id: str
    application_id: int
    session_id: str
    scores: dict
    recommendation: str
    strengths: List[str]
    identified_gaps: List[str]
    interview_performance: dict
    generated_at: str


@router.post("/start", response_model=InterviewResponse)
async def start_interview(
    request: InterviewRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db)
):
    """
    Start a technical interview for a candidate
    
    - Validates application exists and has screening results
    - Creates LiveKit room for voice communication
    - Generates access token for candidate
    - Starts interview process in background
    """
    try:
        # Validate application exists
        application = db.query(Application).filter(
            Application.application_id == request.application_id
        ).first()
        
        if not application:
            raise HTTPException(status_code=404, detail="Application not found")
        
        # Check if screening results exist
        report = db.query(SemanticAnalysisReport).filter(
            SemanticAnalysisReport.application_id == request.application_id
        ).first()
        
        if not report:
            raise HTTPException(
                status_code=400, 
                detail="Screening results not found. Please complete CV screening first."
            )
        
        # Check if interview already completed
        if report.ai_insights and "interview_results" in report.ai_insights:
            interview_data = json.loads(report.ai_insights)["interview_results"]
            if interview_data.get("completed_at"):
                raise HTTPException(
                    status_code=400,
                    detail="Interview already completed for this application"
                )
        
        # Validate interview mode
        try:
            mode = InterviewMode(request.mode.lower())
        except ValueError:
            raise HTTPException(
                status_code=400,
                detail="Invalid interview mode. Must be 'technical' or 'hr'"
            )
        
        # Generate LiveKit room and token
        session_id = str(uuid.uuid4())
        room_name = f"interview_{session_id}"
        
        # TODO: Generate actual LiveKit token
        # For now, return mock token
        livekit_token = f"mock_token_{session_id}"
        
        # Start interview in background
        background_tasks.add_task(
            conduct_interview_background,
            request.application_id,
            mode,
            session_id,
            room_name,
            request.persona
        )
        
        return InterviewResponse(
            session_id=session_id,
            livekit_room_name=room_name,
            livekit_token=livekit_token,
            status="started",
            message=f"Interview started for application {request.application_id}"
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to start interview: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


async def conduct_interview_background(
    application_id: int,
    mode: InterviewMode,
    session_id: str,
    room_name: str,
    persona: str = "elite"
):
    """
    Background task to conduct the actual interview
    """
    try:
        logger.info(f"Starting background interview for application {application_id} with persona: {persona}")
        
        # Conduct interview
        session = await interview_agent.conduct_interview(application_id, mode, use_persona=persona)
        
        logger.info(f"Interview completed for session {session_id}")
        
    except Exception as e:
        logger.error(f"Background interview failed for session {session_id}: {e}")
        # TODO: Update interview status to failed in database


@router.get("/status/{session_id}", response_model=InterviewStatus)
async def get_interview_status(session_id: str):
    """
    Get real-time interview status
    """
    # TODO: Implement status tracking from LiveKit data channels
    # For now, return mock status
    return InterviewStatus(
        session_id=session_id,
        current_question=2,
        total_questions=5,
        confidence_score=0.75,
        technical_accuracy=0.82,
        status="in_progress"
    )


@router.get("/scorecard/{application_id}", response_model=CandidateScorecard)
async def get_candidate_scorecard(
    application_id: int,
    db: Session = Depends(get_db)
):
    """
    Get comprehensive candidate scorecard including screening and interview results
    """
    try:
        # Get semantic analysis report
        report = db.query(SemanticAnalysisReport).filter(
            SemanticAnalysisReport.application_id == application_id
        ).first()
        
        if not report:
            raise HTTPException(status_code=404, detail="No results found for application")
        
        # Parse interview results from ai_insights
        interview_results = None
        if report.ai_insights:
            try:
                insights_data = json.loads(report.ai_insights)
                interview_results = insights_data.get("interview_results")
            except json.JSONDecodeError:
                pass
        
        if not interview_results:
            raise HTTPException(
                status_code=404,
                detail="Interview results not available"
            )
        
        # Parse HR explanation for gaps and strengths
        hr_data = {}
        if report.hr_explanation_json:
            try:
                hr_data = json.loads(report.hr_explanation_json)
            except json.JSONDecodeError:
                pass
        
        # Build scorecard
        scorecard = CandidateScorecard(
            candidate_id=str(report.application.candidate_id),
            application_id=application_id,
            session_id=interview_results.get("session_id", ""),
            scores=interview_results.get("scores", {}),
            recommendation=interview_results.get("recommendation", "Not evaluated"),
            strengths=hr_data.get("strengths", []),
            identified_gaps=hr_data.get("gaps", []),
            interview_performance=interview_results.get("interview_performance", {}),
            generated_at=interview_results.get("completed_at", datetime.utcnow().isoformat())
        )
        
        return scorecard
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get scorecard: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.get("/eligible/{requisition_id}")
async def get_interview_eligible_candidates(
    requisition_id: int,
    db: Session = Depends(get_db)
):
    """
    Get list of candidates eligible for interview based on screening results
    """
    try:
        # Get job requisition
        job_req = db.query(JobRequisition).filter(
            JobRequisition.requisition_id == requisition_id
        ).first()
        
        if not job_req:
            raise HTTPException(status_code=404, detail="Job requisition not found")
        
        # Get applications with screening results
        applications = db.query(Application).filter(
            Application.requisition_id == requisition_id,
            Application.status.in_(["screened", "shortlisted"])
        ).all()
        
        eligible_candidates = []
        for app in applications:
            report = db.query(SemanticAnalysisReport).filter(
                SemanticAnalysisReport.application_id == app.application_id
            ).first()
            
            if report and report.match_percentage:
                # Check if eligible for interview (score >= 65)
                if report.match_percentage >= 65:
                    eligible_candidates.append({
                        "application_id": app.application_id,
                        "candidate_id": app.candidate_id,
                        "candidate_name": f"{app.candidate.first_name} {app.candidate.last_name}",
                        "screening_score": report.match_percentage,
                        "recommendation": report.recommendation_summary or "Not evaluated",
                        "email": app.candidate.email
                    })
        
        # Sort by screening score
        eligible_candidates.sort(key=lambda x: x["screening_score"], reverse=True)
        
        return {
            "requisition_id": requisition_id,
            "job_title": job_req.job_title,
            "eligible_candidates": eligible_candidates,
            "total_eligible": len(eligible_candidates)
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get eligible candidates: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.post("/complete/{session_id}")
async def mark_interview_completed(
    session_id: str,
    db: Session = Depends(get_db)
):
    """
    Mark interview as completed and trigger final evaluation.
    Called by LiveKit webhook or candidate end-session action.
    """
    from ..models.db.technical_interview_session import TechnicalInterviewSession
    from ..models.db.job_requisition import JobRequisition
    from ..models.db.job_posting import JobPosting

    session = db.query(TechnicalInterviewSession).filter(
        TechnicalInterviewSession.session_id == int(session_id)
        if session_id.isdigit() else None
    ).first()

    if not session:
        raise HTTPException(status_code=404, detail="Interview session not found")

    if session.status == "Completed":
        return {"status": "already_completed", "session_id": session_id}

    session.status = "Completed"
    session.ended_at = datetime.utcnow()
    db.commit()

    # Check if all sessions for this JR are done → trigger final ranking
    application = session.application
    posting = application.posting if application else None
    if posting:
        from tasks.interview_tasks import maybe_dispatch_final_ranking
        maybe_dispatch_final_ranking.delay(posting.requisition_id)

    return {"status": "completed", "session_id": session_id}
