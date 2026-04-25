"""
Elite Engineering Lead Interviewer
Strictly English-speaking Senior Technical Interviewer with project-centric evaluation
"""

import asyncio
import json
import logging
import uuid
import re
import time
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple, Any
from dataclasses import dataclass, asdict
from enum import Enum

import numpy as np
from sqlalchemy.orm import Session

from ..models.db.semantic_analysis_report import SemanticAnalysisReport
from ..models.db.application import Application
from ..models.db.candidate import Candidate
from ..models.db.cv_project import CVProject
from ..models.db.cv_experience import CVExperience
from ..models.db.job_requisition import JobRequisition
from ..database import SessionLocal
from ..services.technical_interview_agent import InterviewSession, QuestionResponse, InterviewMode

logger = logging.getLogger(__name__)


class InterviewPhase(Enum):
    INTRODUCTION = "introduction"           # 1 min
    PROJECT_DEEP_DIVE = "project_deep_dive" # 6-8 mins
    TARGETED_PROBING = "targeted_probing"   # 4-5 mins
    CLOSING = "closing"                     # 1 min


class ConversationState(Enum):
    WAITING = "waiting"
    SPEAKING = "speaking"
    LISTENING = "listening"
    SILENCE_NUDGE = "silence_nudge"


@dataclass
class InterviewTiming:
    """Timing controls for interview phases"""
    introduction_duration: timedelta = timedelta(minutes=1)
    project_deep_dive_duration: timedelta = timedelta(minutes=7)
    targeted_probing_duration: timedelta = timedelta(minutes=4)
    closing_duration: timedelta = timedelta(minutes=1)
    total_duration: timedelta = timedelta(minutes=13)
    silence_threshold: timedelta = timedelta(seconds=8)


@dataclass
class EvaluationCriteria:
    """Real-time evaluation metrics"""
    technical_correctness: float = 0.0
    depth_of_explanation: float = 0.0
    star_methodology: float = 0.0
    evidence_quality: float = 0.0
    communication_clarity: float = 0.0


@dataclass
class InterviewMetadata:
    """Enhanced interview context with timing and evaluation"""
    candidate_id: str
    application_id: int
    candidate_name: str
    job_title: str
    projects: List[Dict[str, Any]]
    experiences: List[Dict[str, Any]]
    identified_gaps: List[str]
    current_phase: InterviewPhase
    conversation_state: ConversationState
    phase_start_time: datetime
    interview_start_time: datetime
    last_speaker_activity: datetime
    questions_asked: int
    evaluation: EvaluationCriteria


class EliteInterviewerPersona:
    """
    Elite Engineering Lead Interviewer
    Strictly English-speaking with project-centric evaluation and conversational intelligence
    """
    
    def __init__(self):
        self.knowledge_db = self._build_knowledge_database()
        self.current_metadata = None
        self.conversation_history = []
        self.timing = InterviewTiming()
        self.ideal_answers = self._build_ideal_answers()
        
    def _build_knowledge_database(self) -> Dict[str, Dict[str, Any]]:
        """
        Build comprehensive knowledge database with ideal answers and evaluation criteria
        """
        return {
            "docker": {
                "concepts": ["containers", "images", "dockerfile", "orchestration"],
                "ideal_answer": "Docker provides containerization with isolated environments. Key benefits include consistency across environments, resource efficiency, and scalability. Important considerations are image optimization, multi-stage builds, and orchestration with Kubernetes.",
                "expert_terms": ["multi-stage builds", "layer caching", "security scanning", "orchestration"],
                "follow_ups": ["How did you handle image optimization?", "What orchestration challenges did you face?"]
            },
            
            "react": {
                "concepts": ["components", "state management", "hooks", "virtual dom"],
                "ideal_answer": "React is a component-based library using Virtual DOM for efficient updates. Key concepts include component lifecycle, state management, hooks for side effects, and performance optimization through memoization and code splitting.",
                "expert_terms": ["reconciliation", "hooks lifecycle", "context api", "concurrent features"],
                "follow_ups": ["How did you manage state in large applications?", "What performance optimizations did you implement?"]
            },
            
            "python": {
                "concepts": ["oop", "async programming", "decorators", "generators"],
                "ideal_answer": "Python offers multiple paradigms with strong OOP support. Advanced features include decorators for metaprogramming, generators for memory efficiency, and asyncio for concurrent programming. The GIL is a consideration for CPU-bound tasks.",
                "expert_terms": ["gil", "asyncio", "metaclasses", "context managers", "descriptors"],
                "follow_ups": ["How did you handle the GIL limitations?", "What async patterns did you use?"]
            },
            
            "database": {
                "concepts": ["acid properties", "normalization", "indexing", "transactions"],
                "ideal_answer": "Database design requires balancing normalization with performance. ACID properties ensure data integrity, while proper indexing optimizes query performance. Transactions handle concurrent access and maintain consistency.",
                "expert_terms": ["acid", "normalization", "query optimization", "connection pooling", "sharding"],
                "follow_ups": ["How did you optimize query performance?", "What indexing strategies did you use?"]
            },
            
            "aws": {
                "concepts": ["cloud architecture", "scalability", "security", "cost optimization"],
                "ideal_answer": "AWS provides comprehensive cloud services with focus on scalability, reliability, and security. Key considerations include VPC design, IAM policies, auto-scaling groups, and cost optimization through reserved instances and serverless architectures.",
                "expert_terms": ["vpc", "iam roles", "cloudformation", "auto scaling", "serverless"],
                "follow_ups": ["How did you design your VPC architecture?", "What cost optimization strategies did you implement?"]
            }
        }
    
    def _build_ideal_answers(self) -> Dict[str, str]:
        """
        Build ideal answer templates for centroid matching
        """
        return {
            "architecture": "The system follows a microservices architecture with separate services for different business domains. We used event-driven communication with message queues for async processing. The database layer uses read replicas for performance optimization.",
            
            "performance": "We implemented multiple performance optimizations including database indexing, query optimization, caching strategies with Redis, and asynchronous processing for long-running tasks. This resulted in a 60% improvement in response times.",
            
            "scalability": "The system was designed for horizontal scalability using load balancers, auto-scaling groups, and stateless services. We implemented circuit breakers and rate limiting to handle traffic spikes.",
            
            "security": "Security was implemented through multiple layers including authentication with JWT, authorization with RBAC, data encryption at rest and in transit, and regular security audits. We followed OWASP guidelines for web security."
        }
    
    async def load_interview_metadata(self, application_id: int) -> InterviewMetadata:
        """
        Load comprehensive interview metadata including candidate info and job details
        """
        db = SessionLocal()
        try:
            # Get application with candidate and job data
            application = db.query(Application).filter(
                Application.application_id == application_id
            ).first()
            
            if not application:
                raise ValueError(f"Application {application_id} not found")
            
            # Get job requisition for role information
            job_req = db.query(JobRequisition).filter(
                JobRequisition.requisition_id == application.requisition_id
            ).first()
            
            # Get candidate projects
            projects = []
            cv_id = None
            for cv in application.candidate.cvs:
                if cv.is_primary:
                    cv_id = cv.cv_id
                    break
            
            if cv_id:
                cv_projects = db.query(CVProject).filter(CVProject.cv_id == cv_id).all()
                for proj in cv_projects:
                    projects.append({
                        "project_name": proj.project_name,
                        "description": proj.description,
                        "tech_stack": proj.tech_stack.split(", ") if proj.tech_stack else [],
                        "role": proj.role or "Developer",
                        "duration": proj.duration_months or 0
                    })
            
            # Get candidate experiences
            experiences = []
            if cv_id:
                cv_experiences = db.query(CVExperience).filter(CVExperience.cv_id == cv_id).all()
                for exp in cv_experiences:
                    experiences.append({
                        "company": exp.company_name,
                        "position": exp.job_title,
                        "description": exp.description,
                        "duration": self._calculate_duration(exp.start_date, exp.end_date)
                    })
            
            # Get semantic analysis for gaps
            report = db.query(SemanticAnalysisReport).filter(
                SemanticAnalysisReport.application_id == application_id
            ).first()
            
            gaps = []
            if report and report.hr_explanation_json:
                try:
                    hr_data = json.loads(report.hr_explanation_json)
                    gaps = hr_data.get("gaps", [])
                except json.JSONDecodeError:
                    pass
            
            candidate_name = f"{application.candidate.first_name} {application.candidate.last_name}"
            job_title = job_req.job_title if job_req else "Technical Position"
            
            return InterviewMetadata(
                candidate_id=str(application.candidate_id),
                application_id=application_id,
                candidate_name=candidate_name,
                job_title=job_title,
                projects=projects,
                experiences=experiences,
                identified_gaps=gaps,
                current_phase=InterviewPhase.INTRODUCTION,
                conversation_state=ConversationState.WAITING,
                phase_start_time=datetime.utcnow(),
                interview_start_time=datetime.utcnow(),
                last_speaker_activity=datetime.utcnow(),
                questions_asked=0,
                evaluation=EvaluationCriteria()
            )
            
        finally:
            db.close()
    
    def _calculate_duration(self, start_date, end_date) -> str:
        """Calculate duration from dates"""
        if not start_date:
            return "Unknown"
        
        try:
            start = datetime.strptime(start_date, "%Y-%m-%d") if isinstance(start_date, str) else start_date
            end = datetime.strptime(end_date, "%Y-%m-%d") if end_date else datetime.now()
            
            months = (end.year - start.year) * 12 + (end.month - start.month)
            return f"{months} months"
        except:
            return "Unknown"
    
    def generate_introduction(self, metadata: InterviewMetadata) -> str:
        """
        Generate professional English introduction
        """
        return (
            f"Good {self._get_time_greeting()}, {metadata.candidate_name}. "
            f"I'm interviewing you for the {metadata.job_title} position. "
            f"Today we'll discuss your technical background and explore some of your specific projects in detail. "
            f"This should take about 10-15 minutes. Are you ready to begin?"
        )
    
    def _get_time_greeting(self) -> str:
        """Get appropriate time-based greeting"""
        hour = datetime.utcnow().hour
        if 5 <= hour < 12:
            return "morning"
        elif 12 <= hour < 17:
            return "afternoon"
        else:
            return "evening"
    
    def generate_project_deep_dive_question(self, metadata: InterviewMetadata) -> str:
        """
        Generate project-specific question for deep dive phase
        """
        if not metadata.projects:
            return "Could you tell me about the most challenging technical project you've worked on recently?"
        
        # Select the most substantial project
        main_project = max(metadata.projects, key=lambda p: p.get("duration", 0))
        project_name = main_project["project_name"]
        tech_stack = main_project.get("tech_stack", [])
        
        # Generate architecture-focused question
        question = f"I saw your work on {project_name}. Can you walk me through the technical architecture and your specific contribution?"
        
        # Add tech-specific follow-up hint
        if tech_stack:
            main_tech = tech_stack[0]
            question += f" I'm particularly interested in how you utilized {main_tech} in this system."
        
        return question
    
    def generate_follow_up_question(self, metadata: InterviewMetadata, previous_answer: str) -> Optional[str]:
        """
        Generate intelligent follow-up based on previous answer
        """
        # Detect technologies mentioned
        detected_tech = self._detect_technologies(previous_answer)
        
        if not detected_tech:
            return None
        
        # Select most relevant technology for follow-up
        tech = detected_tech[0].lower()
        
        # Generate context-aware follow-up
        if "database" in tech or "sql" in tech or "nosql" in tech:
            return "Why did you choose this particular database solution over alternatives for this use case?"
        elif "docker" in tech or "container" in tech:
            return "How did you handle container orchestration and deployment scaling in this architecture?"
        elif "react" in tech or "frontend" in tech:
            return "What state management strategies did you implement for handling complex application state?"
        elif "api" in tech or "rest" in tech:
            return "How did you approach API design and versioning in this system?"
        elif "performance" in tech or "optimization" in tech:
            return "What specific performance metrics did you track, and what optimizations yielded the best results?"
        
        return None
    
    def generate_targeted_probing_question(self, metadata: InterviewMetadata) -> str:
        """
        Generate targeted question based on identified gaps and project context
        """
        if not metadata.identified_gaps:
            # Focus on technical depth if no gaps identified
            return "Looking at your projects, what technical challenge required the most innovative solution from your side?"
        
        # Select primary gap and link to project context
        gap = metadata.identified_gaps[0]
        
        # Find relevant project
        relevant_project = None
        for proj in metadata.projects:
            tech_stack = [tech.lower() for tech in proj.get("tech_stack", [])]
            if any(gap_term in " ".join(tech_stack) for gap_term in gap.lower().split()):
                relevant_project = proj
                break
        
        if relevant_project:
            project_name = relevant_project["project_name"]
            return f"In your {project_name} project, I noticed you worked with technologies related to {gap}. Could you explain your decision-making process and the trade-offs you considered?"
        else:
            return f"I'd like to explore your experience with {gap}. Can you describe a scenario where you applied these concepts in a practical setting?"
    
    def generate_closing(self, metadata: InterviewMetadata) -> str:
        """
        Generate professional closing statement
        """
        return "Thank you for your time. Your detailed evaluation will be processed and shared with HR shortly."
    
    def _detect_technologies(self, text: str) -> List[str]:
        """
        Detect technologies mentioned in candidate's response
        """
        tech_patterns = [
            r'\b(Docker|Kubernetes|Container|Pod|Service)\b',
            r'\b(React|Vue|Angular|Frontend|Component|State)\b',
            r'\b(Python|Django|Flask|FastAPI|Backend|API)\b',
            r'\b(PostgreSQL|MySQL|MongoDB|Redis|Database|SQL|NoSQL)\b',
            r'\b(AWS|Azure|GCP|Cloud|Lambda|EC2|S3)\b',
            r'\b(Microservices|Architecture|System|Design|Scalable)\b',
            r'\b(Performance|Optimization|Cache|Index|Query)\b'
        ]
        
        technologies = []
        for pattern in tech_patterns:
            matches = re.findall(pattern, text, re.IGNORECASE)
            technologies.extend(matches)
        
        return list(set(technologies))
    
    def evaluate_technical_correctness(self, answer: str, expected_concepts: List[str]) -> float:
        """
        Evaluate technical correctness based on concept coverage
        """
        if not expected_concepts:
            return 0.5  # Neutral score if no expectations
        
        concept_count = 0
        for concept in expected_concepts:
            if concept.lower() in answer.lower():
                concept_count += 1
        
        return min(1.0, concept_count / len(expected_concepts))
    
    def evaluate_depth_of_explanation(self, answer: str) -> float:
        """
        Evaluate depth based on expert terminology and detailed explanations
        """
        expert_indicators = [
            "because", "therefore", "however", "although", "specifically",
            "for example", "in particular", "the reason", "trade-off",
            "advantage", "disadvantage", "consideration", "challenge"
        ]
        
        expert_terms = {
            "scalability", "performance", "security", "maintainability",
            "architecture", "design pattern", "best practice", "optimization",
            "concurrency", "asynchronous", "synchronous", "distributed",
            "microservices", "monolith", "serverless", "containerization"
        }
        
        depth_score = 0.0
        
        # Count explanatory phrases
        for indicator in expert_indicators:
            if indicator.lower() in answer.lower():
                depth_score += 0.1
        
        # Count expert terms
        for term in expert_terms:
            if term.lower() in answer.lower():
                depth_score += 0.15
        
        # Check for detailed explanations (length and complexity)
        if len(answer) > 200:
            depth_score += 0.2
        
        return min(1.0, depth_score)
    
    def evaluate_star_methodology(self, answer: str) -> float:
        """
        Evaluate STAR method usage in behavioral questions
        """
        star_indicators = {
            "situation": ["situation", "context", "background", "when", "at"],
            "task": ["task", "goal", "objective", "needed to", "responsible for"],
            "action": ["i did", "i implemented", "i created", "i developed", "my approach"],
            "result": ["result", "outcome", "achieved", "improved", "reduced", "increased"]
        }
        
        star_score = 0.0
        for component, indicators in star_indicators.items():
            for indicator in indicators:
                if indicator.lower() in answer.lower():
                    star_score += 0.25
                    break
        
        return min(1.0, star_score)
    
    def evaluate_evidence_quality(self, answer: str, metadata: InterviewMetadata) -> float:
        """
        Evaluate quality of evidence provided (project references, metrics, etc.)
        """
        evidence_indicators = [
            "in my project", "on my project", "we implemented", "i built",
            "the result was", "we achieved", "improved by", "reduced by",
            "increased by", "decreased by", "optimized", "performance"
        ]
        
        project_references = 0
        for proj in metadata.projects:
            if proj["project_name"].lower() in answer.lower():
                project_references += 2
        
        evidence_count = 0
        for indicator in evidence_indicators:
            if indicator.lower() in answer.lower():
                evidence_count += 1
        
        # Look for specific metrics
        metric_pattern = r'\b\d+%|\b\d+x|\b\d+\s*(percent|times|seconds|minutes|hours|days)\b'
        if re.search(metric_pattern, answer, re.IGNORECASE):
            evidence_count += 2
        
        return min(1.0, (evidence_count + project_references) / 5.0)
    
    def handle_silence(self, metadata: InterviewMetadata, last_question: str) -> str:
        """
        Generate appropriate nudge for prolonged silence
        """
        detected_tech = self._detect_technologies(last_question)
        tech_context = detected_tech[0] if detected_tech else "your implementation"
        
        return f"Would you like me to rephrase the question about your implementation of {tech_context}?"
    
    def handle_vague_answer(self, metadata: InterviewMetadata) -> str:
        """
        Generate deep-dive prompt for vague answers
        """
        return "That's a good start, but can you explain the low-level logic behind that decision?"
    
    def check_phase_timing(self, metadata: InterviewMetadata) -> bool:
        """
        Check if current phase has exceeded its time allocation
        """
        current_time = datetime.utcnow()
        phase_duration = current_time - metadata.phase_start_time
        
        if metadata.current_phase == InterviewPhase.INTRODUCTION:
            return phase_duration >= self.timing.introduction_duration
        elif metadata.current_phase == InterviewPhase.PROJECT_DEEP_DIVE:
            return phase_duration >= self.timing.project_deep_dive_duration
        elif metadata.current_phase == InterviewPhase.TARGETED_PROBING:
            return phase_duration >= self.timing.targeted_probing_duration
        elif metadata.current_phase == InterviewPhase.CLOSING:
            return phase_duration >= self.timing.closing_duration
        
        return False
    
    def advance_phase(self, metadata: InterviewMetadata) -> InterviewPhase:
        """
        Advance to the next interview phase
        """
        phase_order = [
            InterviewPhase.INTRODUCTION,
            InterviewPhase.PROJECT_DEEP_DIVE,
            InterviewPhase.TARGETED_PROBING,
            InterviewPhase.CLOSING
        ]
        
        current_index = phase_order.index(metadata.current_phase)
        if current_index < len(phase_order) - 1:
            return phase_order[current_index + 1]
        
        return InterviewPhase.CLOSING
    
    def calculate_centroid_similarity(self, answer: str, ideal_answer: str) -> float:
        """
        Calculate semantic similarity between answer and ideal response
        """
        # Simple keyword-based similarity (in production, use actual sentence transformers)
        answer_words = set(answer.lower().split())
        ideal_words = set(ideal_answer.lower().split())
        
        if not answer_words or not ideal_words:
            return 0.0
        
        intersection = answer_words.intersection(ideal_words)
        union = answer_words.union(ideal_words)
        
        return len(intersection) / len(union) if union else 0.0
    
    async def conduct_elite_interview(self, application_id: int) -> InterviewSession:
        """
        Conduct elite engineering lead interview with strict timing and evaluation
        """
        # Load comprehensive metadata
        metadata = await self.load_interview_metadata(application_id)
        self.current_metadata = metadata
        
        # Initialize session
        session = InterviewSession(
            session_id=str(uuid.uuid4()),
            candidate_id=metadata.candidate_id,
            application_id=application_id,
            mode=InterviewMode.TECHNICAL,
            context=metadata,  # Will be converted to proper type
            questions_asked=[],
            final_score=0.0,
            transcript="",
            audio_recording_url=None,
            started_at=datetime.utcnow(),
            completed_at=None
        )
        
        logger.info(f"Starting elite interview for {metadata.candidate_name} - {metadata.job_title}")
        
        # Phase 1: Introduction (1 minute)
        intro_question = self.generate_introduction(metadata)
        metadata.phase_start_time = datetime.utcnow()
        
        # Simulate candidate response
        intro_response = "Yes, I'm ready to begin. I'm excited to discuss my technical experience."
        
        # Create Q&A record
        qa_record = QuestionResponse(
            question_id=str(uuid.uuid4()),
            question_text=intro_question,
            question_type="introduction",
            audio_url=None,
            response_text=intro_response,
            response_audio_url=None,
            timestamp=datetime.utcnow(),
            centroid_score=0.0,
            depth_boost=0.0,
            llm_qualitative_score=0.0,
            confidence_score=0.8  # High confidence for readiness response
        )
        
        session.questions_asked.append(qa_record)
        session.transcript += f"Q: {intro_question}\nA: {intro_response}\n\n"
        
        metadata.current_phase = InterviewPhase.PROJECT_DEEP_DIVE
        metadata.phase_start_time = datetime.utcnow()
        
        # Phase 2: Project Deep-Dive (6-8 minutes)
        project_question = self.generate_project_deep_dive_question(metadata)
        
        # Simulate detailed technical response
        project_response = self._simulate_project_response(metadata)
        
        # Evaluate response comprehensively
        detected_tech = self._detect_technologies(project_response)
        tech_correctness = self.evaluate_technical_correctness(project_response, detected_tech)
        depth_score = self.evaluate_depth_of_explanation(project_response)
        evidence_score = self.evaluate_evidence_quality(project_response, metadata)
        
        # Calculate centroid similarity with ideal answer
        ideal_architecture = self.ideal_answers.get("architecture", "")
        centroid_score = self.calculate_centroid_similarity(project_response, ideal_architecture)
        
        # Create Q&A record
        project_qa = QuestionResponse(
            question_id=str(uuid.uuid4()),
            question_text=project_question,
            question_type="project_deep_dive",
            audio_url=None,
            response_text=project_response,
            response_audio_url=None,
            timestamp=datetime.utcnow(),
            centroid_score=centroid_score,
            depth_boost=depth_score,
            llm_qualitative_score=tech_correctness,
            confidence_score=(tech_correctness + depth_score + evidence_score) / 3.0
        )
        
        session.questions_asked.append(project_qa)
        session.transcript += f"Q: {project_question}\nA: {project_response}\n\n"
        
        # Generate follow-up question
        follow_up = self.generate_follow_up_question(metadata, project_response)
        if follow_up:
            follow_up_response = self._simulate_follow_up_response(follow_up)
            
            # Evaluate follow-up
            follow_up_correctness = self.evaluate_technical_correctness(follow_up_response, self._detect_technologies(follow_up))
            follow_up_depth = self.evaluate_depth_of_explanation(follow_up_response)
            
            follow_up_qa = QuestionResponse(
                question_id=str(uuid.uuid4()),
                question_text=follow_up,
                question_type="follow_up",
                audio_url=None,
                response_text=follow_up_response,
                response_audio_url=None,
                timestamp=datetime.utcnow(),
                centroid_score=self.calculate_centroid_similarity(follow_up_response, self.ideal_answers.get("performance", "")),
                depth_boost=follow_up_depth,
                llm_qualitative_score=follow_up_correctness,
                confidence_score=(follow_up_correctness + follow_up_depth) / 2.0
            )
            
            session.questions_asked.append(follow_up_qa)
            session.transcript += f"Q: {follow_up}\nA: {follow_up_response}\n\n"
        
        metadata.current_phase = InterviewPhase.TARGETED_PROBING
        metadata.phase_start_time = datetime.utcnow()
        
        # Phase 3: Targeted Probing (4-5 minutes)
        probing_question = self.generate_targeted_probing_question(metadata)
        probing_response = self._simulate_probing_response(metadata)
        
        # Evaluate probing response
        probing_correctness = self.evaluate_technical_correctness(probing_response, metadata.identified_gaps)
        probing_depth = self.evaluate_depth_of_explanation(probing_response)
        probing_evidence = self.evaluate_evidence_quality(probing_response, metadata)
        
        probing_qa = QuestionResponse(
            question_id=str(uuid.uuid4()),
            question_text=probing_question,
            question_type="targeted_probing",
            audio_url=None,
            response_text=probing_response,
            response_audio_url=None,
            timestamp=datetime.utcnow(),
            centroid_score=self.calculate_centroid_similarity(probing_response, self.ideal_answers.get("scalability", "")),
            depth_boost=probing_depth,
            llm_qualitative_score=probing_correctness,
            confidence_score=(probing_correctness + probing_depth + probing_evidence) / 3.0
        )
        
        session.questions_asked.append(probing_qa)
        session.transcript += f"Q: {probing_question}\nA: {probing_response}\n\n"
        
        metadata.current_phase = InterviewPhase.CLOSING
        metadata.phase_start_time = datetime.utcnow()
        
        # Phase 4: Closing (1 minute)
        closing_statement = self.generate_closing(metadata)
        session.transcript += f"\n{closing_statement}\n"
        
        # Calculate final comprehensive score
        if session.questions_asked:
            session.final_score = sum(qa.confidence_score for qa in session.questions_asked) / len(session.questions_asked)
        
        session.completed_at = datetime.utcnow()
        
        logger.info(f"Elite interview completed for {metadata.candidate_name}. Final score: {session.final_score:.2f}")
        
        return session
    
    def _simulate_project_response(self, metadata: InterviewMetadata) -> str:
        """Simulate detailed project response"""
        if metadata.projects:
            project = metadata.projects[0]
            return (
                f"I worked as the lead developer on {project['project_name']}. "
                f"The system was built using a microservices architecture with React frontend and Python backend. "
                f"We used PostgreSQL for the main database with Redis for caching. The architecture supported "
                f"horizontal scaling through Docker containers and Kubernetes orchestration. I specifically implemented "
                f"the API gateway and authentication system using JWT tokens. We achieved 99.9% uptime and "
                f"handled 10,000 concurrent users through proper load balancing and database optimization."
            )
        return "I led the development of a scalable microservices system..."
    
    def _simulate_follow_up_response(self, question: str) -> str:
        """Simulate follow-up response"""
        if "database" in question.lower():
            return "We chose PostgreSQL for its ACID compliance and strong consistency. The main challenge was optimizing complex queries, which we solved through proper indexing and query optimization, resulting in 70% faster response times."
        elif "docker" in question.lower():
            return "We implemented Kubernetes for orchestration with auto-scaling based on CPU metrics. The main challenge was managing stateful applications, which we solved using persistent volumes and proper service discovery."
        return "We addressed this through careful architecture design and performance monitoring..."
    
    def _simulate_probing_response(self, metadata: InterviewMetadata) -> str:
        """Simulate targeted probing response"""
        if metadata.identified_gaps:
            gap = metadata.identified_gaps[0]
            return (
                f"In my experience with {gap}, I implemented a comprehensive solution that addressed "
                f"the key requirements while maintaining code quality and performance. The decision-making "
                f"process involved evaluating multiple approaches and selecting the optimal balance of "
                f"complexity and maintainability. We achieved significant improvements in system reliability "
                f"and developer productivity through this implementation."
            )
        return "I have extensive experience in this area and have successfully implemented similar solutions in multiple projects..."


# Singleton instance
elite_interviewer = EliteInterviewerPersona()
