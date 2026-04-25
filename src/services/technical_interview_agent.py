"""
Technical Interview Agent - Bilingual Voice Interview System
Integrates LiveKit for real-time voice communication with advanced evaluation engines
"""

import asyncio
import json
import logging
import uuid
import io
from datetime import datetime
from typing import Dict, List, Optional, Tuple, Any
from dataclasses import dataclass, asdict
from enum import Enum

import numpy as np
from sqlalchemy.orm import Session
from sentence_transformers import SentenceTransformer
from keybert import KeyBERT
import torch
import whisper
import librosa
from livekit import rtc
from livekit.api import LiveKitAPI, DataPacket

from models.db.semantic_analysis_report import SemanticAnalysisReport
from models.db.job_requisition import JobRequisition
from database.connection import SessionLocal
from .senior_interviewer_persona import senior_interviewer
from .elite_interviewer_persona import elite_interviewer
from .focused_interviewer_persona import focused_interviewer

logger = logging.getLogger(__name__)

# Model configurations
_WHISPER_MODEL = "large-v3"  # For high-accuracy bilingual STT
_SENTENCE_BERT_MODEL = "all-MiniLM-L6-v2"  # For centroid scoring
_KEYBERT_MODEL = "all-MiniLM-L6-v2"  # For deep term detection
_VAD_MODEL = "silero_vad"  # For voice activity detection


class InterviewMode(Enum):
    TECHNICAL = "technical"
    HR = "hr"


@dataclass
class InterviewContext:
    """Context loaded from screening phase for personalized interviews"""
    candidate_id: str
    application_id: int
    match_percentage: float
    hr_explanation_json: Dict[str, Any]
    gaps: List[str]  # Identified skill gaps
    strengths: List[str]  # Candidate strengths
    screening_score: float


@dataclass
class QuestionResponse:
    """Structure for interview Q&A pairs"""
    question_id: str
    question_text: str
    question_type: str  # "gap_probe", "technical_deep", "hr_star"
    audio_url: Optional[str]
    response_text: str
    response_audio_url: Optional[str]
    timestamp: datetime
    
    # Evaluation metrics
    centroid_score: float  # Semantic similarity to ideal answer
    depth_boost: float    # Expert terminology bonus
    llm_qualitative_score: float  # GPT-4o-mini evaluation
    confidence_score: float  # Combined confidence


@dataclass
class InterviewSession:
    """Complete interview session data"""
    session_id: str
    candidate_id: str
    application_id: int
    mode: InterviewMode
    context: InterviewContext
    questions_asked: List[QuestionResponse]
    final_score: float
    transcript: str
    audio_recording_url: Optional[str]
    started_at: datetime
    completed_at: Optional[datetime]


class TechnicalInterviewAgent:
    """
    High-performance bilingual voice interview agent with LiveKit integration
    and dual-evaluation engine (mathematical + qualitative)
    """
    
    def __init__(self):
        self.livekit_api = LiveKitAPI()
        self.whisper_model = None
        self.sentence_bert = None
        self.keybert_model = None
        self.vad_model = None
        
        # Load models asynchronously
        self._models_loaded = False
        
    async def _load_models(self):
        """Lazy-load heavy models to avoid startup delays"""
        if self._models_loaded:
            return
            
        logger.info("Loading interview agent models...")
        
        # Load Whisper for STT
        self.whisper_model = whisper.load_model(_WHISPER_MODEL)
        
        # Load Sentence-BERT for centroid scoring
        self.sentence_bert = SentenceTransformer(_SENTENCE_BERT_MODEL)
        
        # Load KeyBERT for deep term detection
        self.keybert_model = KeyBERT(model=_KEYBERT_MODEL)
        
        # Load Silero VAD
        torch.hub.load_repo_or_dir('snakers4/silero-vad', 'silero_vad')
        self.vad_model = torch.hub.load('snakers4/silero-vad', 'silero_vad')
        
        self._models_loaded = True
        logger.info("All interview agent models loaded successfully")
    
    async def load_interview_context(self, application_id: int) -> InterviewContext:
        """
        Load candidate context from screening phase for personalized interviews
        """
        db = SessionLocal()
        try:
            # Get semantic analysis report
            report = db.query(SemanticAnalysisReport).filter(
                SemanticAnalysisReport.application_id == application_id
            ).first()
            
            if not report:
                raise ValueError(f"No semantic analysis found for application {application_id}")
            
            # Parse HR explanation JSON
            hr_json = {}
            if report.hr_explanation_json:
                try:
                    hr_json = json.loads(report.hr_explanation_json)
                except json.JSONDecodeError:
                    logger.warning(f"Invalid hr_explanation_json for app {application_id}")
            
            # Extract gaps and strengths
            gaps = hr_json.get("gaps", [])
            strengths = hr_json.get("strengths", [])
            
            return InterviewContext(
                candidate_id=str(report.application.candidate_id),
                application_id=application_id,
                match_percentage=report.match_percentage or 0.0,
                hr_explanation_json=hr_json,
                gaps=gaps,
                strengths=strengths,
                screening_score=report.match_percentage or 0.0
            )
            
        finally:
            db.close()
    
    def generate_personalized_questions(self, context: InterviewContext, mode: InterviewMode) -> List[Dict[str, str]]:
        """
        Generate personalized questions based on screening gaps and interview mode
        """
        questions = []
        
        if mode == InterviewMode.TECHNICAL:
            # Priority 1: Gap probing questions
            for gap in context.gaps[:3]:  # Focus on top 3 gaps
                questions.append({
                    "id": str(uuid.uuid4()),
                    "type": "gap_probe",
                    "text": f"I noticed from your CV screening that we should explore your experience with {gap}. Can you walk me through a specific project where you used {gap} and what challenges you faced?"
                })
            
            # Priority 2: Technical deep dives
            questions.extend([
                {
                    "id": str(uuid.uuid4()),
                    "type": "technical_deep",
                    "text": "Describe the most complex technical architecture you've designed. What were the key trade-offs you considered?"
                },
                {
                    "id": str(uuid.uuid4()),
                    "type": "technical_deep", 
                    "text": "Tell me about a time you had to optimize a system for performance. What metrics did you track and what improvements did you achieve?"
                }
            ])
            
        else:  # HR Mode - STAR method
            questions.extend([
                {
                    "id": str(uuid.uuid4()),
                    "type": "hr_star",
                    "text": "Tell me about a time when you had to work with a difficult team member. What was the situation, what did you do, and what was the result?"
                },
                {
                    "id": str(uuid.uuid4()),
                    "type": "hr_star",
                    "text": "Describe a situation where you had to learn a new technology quickly. What was your approach and how did you ensure you became proficient?"
                },
                {
                    "id": str(uuid.uuid4()),
                    "type": "hr_star",
                    "text": "Give me an example of a project that didn't go as planned. What went wrong, what actions did you take, and what did you learn?"
                }
            ])
        
        # Add strength-based questions for confidence building
        if context.strengths:
            strength = context.strengths[0]
            questions.append({
                "id": str(uuid.uuid4()),
                "type": "strength_confirm",
                "text": f"I see you have strong experience with {strength}. Can you share what aspects of {strength} you find most interesting or challenging?"
            })
        
        return questions[:5]  # Limit to 5 questions per interview
    
    async def transcribe_audio(self, audio_data: bytes, language: Optional[str] = None) -> str:
        """
        Transcribe audio using Whisper-v3 with bilingual support
        """
        if not self._models_loaded:
            await self._load_models()
        
        # Save audio to temporary file for Whisper
        import tempfile
        import os
        
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp_file:
            tmp_file.write(audio_data)
            tmp_path = tmp_file.name
        
        try:
            # Use Whisper for transcription
            result = self.whisper_model.transcribe(
                tmp_path,
                language=language,  # None for auto-detection (Arabic/English)
                fp16=False,  # Use FP32 for better compatibility
                verbose=False
            )
            
            transcription = result["text"].strip()
            logger.info(f"Transcribed: {transcription[:100]}...")
            return transcription
            
        finally:
            os.unlink(tmp_path)
    
    def detect_voice_activity(self, audio_data: bytes) -> bool:
        """
        Use Silero VAD to detect if speech is present in audio
        """
        if not self._models_loaded:
            return True  # Fallback to assume speech
        
        try:
            # Convert audio to 16kHz mono for VAD
            audio, sr = librosa.load(io.BytesIO(audio_data), sr=16000)
            
            # Use Silero VAD
            speech_prob = self.vad_model(torch.from_numpy(audio), 16000).item()
            
            return speech_prob > 0.5  # Threshold for speech detection
            
        except Exception as e:
            logger.warning(f"VAD detection failed: {e}")
            return True  # Fallback
    
    async def evaluate_response_centroid(self, response_text: str, ideal_answers: List[str]) -> float:
        """
        Calculate centroid score using Sentence-BERT semantic similarity
        """
        if not self._models_loaded:
            await self._load_models()
        
        # Encode response and ideal answers
        texts = [response_text] + ideal_answers
        embeddings = self.sentence_bert.encode(texts)
        
        response_embedding = embeddings[0]
        ideal_embeddings = embeddings[1:]
        
        # Calculate centroid of ideal answers
        centroid = np.mean(ideal_embeddings, axis=0)
        
        # Calculate cosine similarity
        similarity = np.dot(response_embedding, centroid) / (
            np.linalg.norm(response_embedding) * np.linalg.norm(centroid)
        )
        
        return float(similarity)
    
    async def detect_depth_terms(self, response_text: str) -> float:
        """
        Detect expert-level terminology using KeyBERT for depth boost
        """
        if not self._models_loaded:
            await self._load_models()
        
        # Extract key phrases with high diversity
        keywords = self.keybert_model.extract_keywords(
            response_text,
            keyphrase_ngram_range=(1, 2),
            stop_words='english',
            top_k=10,
            diversity=0.7
        )
        
        # Expert terminology indicators
        expert_terms = {
            'idempotency', 'race condition', 'deadlock', 'mutex', 'semaphore',
            'circuit breaker', 'event sourcing', 'cqrs', 'saga pattern',
            'sharding', 'replication', 'consistency', 'availability',
            'partition tolerance', 'acid', 'base', 'eventual consistency',
            'microservices', 'serverless', 'containerization', 'orchestration',
            'immutable', 'functional programming', 'reactive programming',
            'asynchronous', 'non-blocking', 'concurrency', 'parallelism'
        }
        
        # Count expert terms in keywords
        expert_count = sum(1 for keyword, score in keywords 
                          if any(term in keyword.lower() for term in expert_terms))
        
        # Depth boost based on expert term density
        depth_boost = min(1.0, expert_count / 3.0)  # Max boost for 3+ expert terms
        
        return depth_boost
    
    async def evaluate_llm_qualitative(self, question: str, response: str, mode: InterviewMode) -> float:
        """
        Use GPT-4o-mini for qualitative evaluation
        """
        import openai
        
        if mode == InterviewMode.HR:
            system_prompt = """You are an HR expert evaluating interview responses using the STAR method.
            Rate the response on a scale of 0-100 considering:
            1. Clear Situation description
            2. Specific Task definition  
            3. Action taken by candidate
            4. Measurable Result
            5. Communication clarity
            Respond with only the numerical score."""
        else:
            system_prompt = """You are a technical expert evaluating interview responses.
            Rate the response on a scale of 0-100 considering:
            1. Technical accuracy
            2. Depth of understanding
            3. Problem-solving approach
            4. Communication of technical concepts
            5. Real-world applicability
            Respond with only the numerical score."""
        
        try:
            response = await openai.ChatCompletion.acreate(
                model="gpt-4o-mini",
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": f"Question: {question}\n\nResponse: {response}"}
                ],
                max_tokens=10,
                temperature=0
            )
            
            score_text = response.choices[0].message.content.strip()
            score = float(score_text)
            return min(100.0, max(0.0, score))
            
        except Exception as e:
            logger.warning(f"LLM evaluation failed: {e}")
            return 50.0  # Default middle score
    
    async def stream_real_time_status(self, session_id: str, confidence_score: float, technical_accuracy: float):
        """
        Stream real-time evaluation metrics to HR dashboard via LiveKit data channels
        """
        try:
            # Get LiveKit room for this interview session
            room_name = f"interview_{session_id}"
            
            # Create data packet with current metrics
            status_data = {
                "session_id": session_id,
                "timestamp": datetime.utcnow().isoformat(),
                "confidence_score": confidence_score,
                "technical_accuracy": technical_accuracy,
                "current_question": self._current_question_index if hasattr(self, '_current_question_index') else 0
            }
            
            # Send via LiveKit data channel
            data_packet = DataPacket(
                payload=json.dumps(status_data).encode(),
                kind=rtc.DataPacketKind.RELIABLE
            )
            
            await self.livekit_api.data.send_data(room_name, data_packet)
            
        except Exception as e:
            logger.warning(f"Failed to stream real-time status: {e}")
    
    async def conduct_interview(self, application_id: int, mode: InterviewMode, use_persona: str = "focused") -> InterviewSession:
        """
        Main interview orchestration method with persona selection
        """
        await self._load_models()
        
        # Use Focused Interviewer Persona for 5-round structured interviews (default)
        if use_persona == "focused" and mode == InterviewMode.TECHNICAL:
            logger.info(f"Using Focused Interviewer Persona for application {application_id}")
            return await focused_interviewer.conduct_focused_interview(application_id)
        
        # Use Elite Interviewer Persona for professional English interviews
        elif use_persona == "elite" and mode == InterviewMode.TECHNICAL:
            logger.info(f"Using Elite Interviewer Persona for application {application_id}")
            return await elite_interviewer.conduct_elite_interview(application_id)
        
        # Use Senior Interviewer Persona for bilingual interviews
        elif use_persona == "bilingual" and mode == InterviewMode.TECHNICAL:
            logger.info(f"Using Senior Interviewer Persona for application {application_id}")
            return await senior_interviewer.conduct_interview_conversation(application_id)
        
        # Load context from screening phase
        context = await self.load_interview_context(application_id)
        
        # Generate personalized questions
        questions = self.generate_personalized_questions(context, mode)
        
        # Create interview session
        session = InterviewSession(
            session_id=str(uuid.uuid4()),
            candidate_id=context.candidate_id,
            application_id=application_id,
            mode=mode,
            context=context,
            questions_asked=[],
            final_score=0.0,
            transcript="",
            audio_recording_url=None,
            started_at=datetime.utcnow(),
            completed_at=None
        )
        
        # TODO: Implement actual LiveKit room setup and voice interaction
        # For now, simulate the interview flow
        
        logger.info(f"Starting {mode.value} interview for candidate {context.candidate_id}")
        
        for i, question in enumerate(questions):
            self._current_question_index = i + 1
            
            # Simulate asking question and getting response
            # In production, this would involve:
            # 1. TTS to speak the question
            # 2. VAD to detect when candidate starts speaking
            # 3. STT to transcribe response
            # 4. Turn-taking logic for interruptions
            
            # For demonstration, create a mock response
            mock_response = f"This is a mock response to: {question['text']}"
            
            # Evaluate response
            centroid_score = await self.evaluate_response_centroid(
                mock_response, 
                ["Ideal answer 1", "Ideal answer 2"]  # These would be predefined ideal answers
            )
            
            depth_boost = await self.detect_depth_terms(mock_response)
            llm_score = await self.evaluate_llm_qualitative(question['text'], mock_response, mode)
            
            # Calculate combined confidence score
            confidence_score = (centroid_score * 0.4 + depth_boost * 0.3 + llm_score / 100 * 0.3)
            
            # Stream real-time status
            await self.stream_real_time_status(session.session_id, confidence_score, centroid_score)
            
            # Create Q&A record
            qa_record = QuestionResponse(
                question_id=question['id'],
                question_text=question['text'],
                question_type=question['type'],
                audio_url=None,  # Would be set in production
                response_text=mock_response,
                response_audio_url=None,  # Would be set in production
                timestamp=datetime.utcnow(),
                centroid_score=centroid_score,
                depth_boost=depth_boost,
                llm_qualitative_score=llm_score,
                confidence_score=confidence_score
            )
            
            session.questions_asked.append(qa_record)
            session.transcript += f"Q: {question['text']}\nA: {mock_response}\n\n"
        
        # Calculate final interview score
        if session.questions_asked:
            session.final_score = sum(qa.confidence_score for qa in session.questions_asked) / len(session.questions_asked)
        
        session.completed_at = datetime.utcnow()
        
        # Persist results
        await self.persist_interview_results(session)
        
        logger.info(f"Interview completed for candidate {context.candidate_id}. Final score: {session.final_score:.2f}")
        
        return session
    
    async def persist_interview_results(self, session: InterviewSession):
        """
        Save interview results to SemanticAnalysisReport
        """
        db = SessionLocal()
        try:
            # Get existing report
            report = db.query(SemanticAnalysisReport).filter(
                SemanticAnalysisReport.application_id == session.application_id
            ).first()
            
            if report:
                # Update with interview data
                interview_data = {
                    "session_id": session.session_id,
                    "mode": session.mode.value,
                    "final_score": session.final_score,
                    "screening_score": session.context.screening_score,
                    "combined_score": (session.context.screening_score * 0.6 + session.final_score * 0.4),
                    "questions_asked": [asdict(qa) for qa in session.questions_asked],
                    "transcript": session.transcript,
                    "audio_recording_url": session.audio_recording_url,
                    "completed_at": session.completed_at.isoformat() if session.completed_at else None
                }
                
                # Store interview data in ai_insights (extend existing)
                existing_insights = report.ai_insights or "{}"
                try:
                    insights_data = json.loads(existing_insights)
                except json.JSONDecodeError:
                    insights_data = {}
                
                insights_data["interview_results"] = interview_data
                report.ai_insights = json.dumps(insights_data, ensure_ascii=False)
                
                # Update match_percentage to combined score
                report.match_percentage = insights_data["interview_results"]["combined_score"]
                
                db.commit()
                logger.info(f"Interview results persisted for application {session.application_id}")
            
        except Exception as e:
            db.rollback()
            logger.error(f"Failed to persist interview results: {e}")
            raise
        finally:
            db.close()
    
    def generate_candidate_scorecard(self, session: InterviewSession) -> Dict[str, Any]:
        """
        Generate comprehensive candidate scorecard merging screening + interview performance
        """
        screening_score = session.context.screening_score
        interview_score = session.final_score
        combined_score = screening_score * 0.6 + interview_score * 0.4
        
        scorecard = {
            "candidate_id": session.candidate_id,
            "application_id": session.application_id,
            "session_id": session.session_id,
            "scores": {
                "screening_score": screening_score,
                "interview_score": interview_score,
                "combined_score": combined_score
            },
            "recommendation": self._get_final_recommendation(combined_score),
            "strengths": session.context.strengths,
            "identified_gaps": session.context.gaps,
            "interview_performance": {
                "mode": session.mode.value,
                "questions_asked": len(session.questions_asked),
                "average_confidence": session.final_score,
                "depth_boosts": [qa.depth_boost for qa in session.questions_asked],
                "technical_accuracy": np.mean([qa.centroid_score for qa in session.questions_asked]) if session.questions_asked else 0
            },
            "generated_at": datetime.utcnow().isoformat()
        }
        
        return scorecard
    
    def _get_final_recommendation(self, combined_score: float) -> str:
        """Generate final recommendation based on combined score"""
        if combined_score >= 85:
            return "Strongly Recommend - Hire"
        elif combined_score >= 75:
            return "Recommend - Proceed to Final Round"
        elif combined_score >= 65:
            return "Consider - Additional Technical Assessment"
        else:
            return "Not Recommended - Reject"


# Singleton instance
interview_agent = TechnicalInterviewAgent()
