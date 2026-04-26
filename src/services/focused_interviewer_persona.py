"""
Focused 5-Round Technical Interviewer
Elite Engineering Lead with strict 5-question structure and project-centric evaluation
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

from models.db.semantic_analysis_report import SemanticAnalysisReport
from models.db.application import Application
from models.db.candidate import Candidate
from models.db.cv_project import CVProject
from models.db.cv_experience import CVExperience
from models.db.job_requisition import JobRequisition
from database.connection import SessionLocal
from services.interview_types import InterviewSession, QuestionResponse, InterviewMode

logger = logging.getLogger(__name__)


class InterviewRound(Enum):
    ICE_BREAKER = "ice_breaker"           # Round 1
    PROJECT_DEEP_DIVE = "project_deep_dive"  # Round 2
    SKILL_GAP_BRIDGE = "skill_gap_bridge"    # Round 3
    PROBLEM_SOLVING = "problem_solving"       # Round 4
    BEHAVIORAL_STAR = "behavioral_star"       # Round 5


@dataclass
class InterviewRoundData:
    """Data for each interview round"""
    round_number: int
    round_type: InterviewRound
    question: str
    answer: str
    follow_up_used: bool
    nudge_used: bool
    technical_correctness: float
    depth_score: float
    star_score: float
    centroid_similarity: float
    confidence_score: float
    timestamp: datetime


@dataclass
class FocusedInterviewMetadata:
    """Focused interview metadata with 5-round structure"""
    candidate_id: str
    application_id: int
    candidate_name: str
    job_title: str
    projects: List[Dict[str, Any]]
    experiences: List[Dict[str, Any]]
    identified_gaps: List[str]
    current_round: InterviewRound
    rounds_completed: int
    interview_start_time: datetime
    last_activity_time: datetime
    follow_ups_used: int
    nudges_used: int
    rounds_data: List[InterviewRoundData]


class FocusedInterviewerPersona:
    """
    Focused 5-Round Technical Interviewer
    Elite Engineering Lead with strict timing and project-centric evaluation
    """
    
    def __init__(self):
        self.knowledge_db = self._build_knowledge_database()
        self.ideal_answers = self._build_ideal_answers()
        self.current_metadata = None
        self.max_follow_ups_per_round = 1
        self.max_nudges_per_round = 1
        self.silence_threshold = timedelta(seconds=8)
        self.max_duration = timedelta(minutes=15)
        self.min_duration = timedelta(minutes=10)
        
    def _build_knowledge_database(self) -> Dict[str, Dict[str, Any]]:
        """
        Build comprehensive knowledge database for technical evaluation
        """
        return {
            "architecture": {
                "concepts": ["microservices", "monolith", "serverless", "event-driven", "api"],
                "ideal_answer": "System architecture should balance scalability, maintainability, and performance. Key considerations include service boundaries, data consistency, communication patterns, and operational complexity. The choice depends on team size, traffic patterns, and business requirements.",
                "expert_terms": ["service boundaries", "data consistency", "communication patterns", "operational complexity"],
                "follow_ups": ["What were the key trade-offs?", "How did you handle service communication?"]
            },
            
            "database": {
                "concepts": ["sql", "nosql", "acid", "base", "sharding", "replication", "indexing"],
                "ideal_answer": "Database selection involves consistency vs availability trade-offs. SQL provides strong consistency and complex queries, while NoSQL offers scalability and flexibility. Key factors include data relationships, query patterns, and scaling requirements.",
                "expert_terms": ["consistency models", "query optimization", "connection pooling", "transaction management"],
                "follow_ups": ["How did you optimize queries?", "What scaling strategies did you use?"]
            },
            
            "scaling": {
                "concepts": ["horizontal", "vertical", "load balancing", "caching", "cdn", "auto-scaling"],
                "ideal_answer": "Scaling requires identifying bottlenecks and applying appropriate strategies. Horizontal scaling distributes load across instances, vertical scaling increases resources, caching reduces database load, and CDN handles static content. Auto-scaling responds to demand changes.",
                "expert_terms": ["bottleneck identification", "resource utilization", "elastic scaling", "performance metrics"],
                "follow_ups": ["What metrics did you track?", "How did you handle stateful services?"]
            },
            
            "security": {
                "concepts": ["authentication", "authorization", "encryption", "owasp", "jwt", "oauth"],
                "ideal_answer": "Security requires defense-in-depth with multiple layers. Authentication verifies identity, authorization controls access, encryption protects data, and secure coding prevents vulnerabilities. Regular security audits and monitoring are essential.",
                "expert_terms": ["defense in depth", "zero trust", "secure coding practices", "vulnerability management"],
                "follow_ups": ["How did you handle secrets?", "What security testing did you perform?"]
            },
            
            "performance": {
                "concepts": ["optimization", "caching", "indexing", "profiling", "monitoring", "metrics"],
                "ideal_answer": "Performance optimization requires measurement first. Profile the application to identify bottlenecks, then apply appropriate optimizations: caching for frequently accessed data, indexing for database queries, and code optimization for CPU-intensive operations.",
                "expert_terms": ["performance profiling", "bottleneck analysis", "optimization strategies", "metric collection"],
                "follow_ups": ["What profiling tools did you use?", "How did you measure improvements?"]
            }
        }
    
    def _build_ideal_answers(self) -> Dict[str, str]:
        """
        Build ideal answer templates for centroid matching
        """
        return {
            "technical_decision": "The decision was based on careful evaluation of requirements, constraints, and trade-offs. We considered factors like team expertise, scalability needs, maintenance overhead, and ecosystem support. The chosen solution provided the best balance of technical and business requirements.",
            
            "problem_solving": "I approached the problem systematically by first understanding the root cause, then exploring multiple solutions, evaluating their pros and cons, and implementing the most promising one. I measured the results and iterated based on feedback and metrics.",
            
            "conflict_resolution": "When my technical decision was challenged, I listened to understand the concerns, presented my reasoning with evidence, and remained open to alternative perspectives. We found common ground by focusing on shared goals and objective criteria.",
            
            "architecture_evolution": "To handle increased load, I would first identify bottlenecks through monitoring and profiling. Then prioritize solutions based on impact and effort: optimize database queries, add caching layers, implement horizontal scaling, and consider architectural changes if needed."
        }
    
    async def load_focused_interview_metadata(self, application_id: int) -> FocusedInterviewMetadata:
        """
        Load comprehensive metadata for focused 5-round interview
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
            
            return FocusedInterviewMetadata(
                candidate_id=str(application.candidate_id),
                application_id=application_id,
                candidate_name=candidate_name,
                job_title=job_title,
                projects=projects,
                experiences=experiences,
                identified_gaps=gaps,
                current_round=InterviewRound.ICE_BREAKER,
                rounds_completed=0,
                interview_start_time=datetime.utcnow(),
                last_activity_time=datetime.utcnow(),
                follow_ups_used=0,
                nudges_used=0,
                rounds_data=[]
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
    
    def generate_round_1_question(self, metadata: FocusedInterviewMetadata) -> str:
        """
        Round 1: Ice-Breaker & Lead
        Greet candidate and ask about most significant project
        """
        if not metadata.projects:
            return f"I've reviewed your background. Can you walk me through the architecture of your most significant project and your specific ownership in it?"
        
        # Select most substantial project
        main_project = max(metadata.projects, key=lambda p: p.get("duration", 0))
        project_name = main_project["project_name"]
        
        return f"I've reviewed your background. Can you walk me through the architecture of {project_name} and your specific ownership in it?"
    
    def generate_round_2_question(self, metadata: FocusedInterviewMetadata, round_1_answer: str) -> str:
        """
        Round 2: Project Deep-Dive
        Follow-up on technical decisions from Round 1
        """
        # Detect technologies mentioned in Round 1
        detected_tech = self._detect_technologies(round_1_answer)
        
        if not detected_tech:
            return "What were the key technical decisions you made in this project, and what were the trade-offs?"
        
        # Select primary technology for deep dive
        tech = detected_tech[0]
        
        # Generate tech-specific follow-up
        if "database" in tech.lower() or "sql" in tech.lower() or "nosql" in tech.lower():
            return f"Why did you choose {tech} over alternative database solutions for this implementation? What were the trade-offs?"
        elif "framework" in tech.lower() or "react" in tech.lower() or "angular" in tech.lower():
            return f"Why did you choose {tech} over other frameworks for this implementation? What were the trade-offs?"
        elif "cloud" in tech.lower() or "aws" in tech.lower() or "azure" in tech.lower():
            return f"Why did you choose {tech} over other cloud providers for this implementation? What were the trade-offs?"
        else:
            return f"Why did you choose {tech} over alternative technologies for this implementation? What were the trade-offs?"
    
    def generate_round_3_question(self, metadata: FocusedInterviewMetadata) -> str:
        """
        Round 3: Skill-Gap Bridge
        Target specific skill gap through project context
        """
        if not metadata.identified_gaps:
            # If no gaps, focus on technical challenge
            return "Looking at your projects, what was the most challenging technical problem you solved, and how did you approach it?"
        
        # Select primary gap
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
            # Map gaps to specific technical areas
            gap_mapping = {
                "docker": "container orchestration",
                "security": "security implementation",
                "scaling": "scaling challenges",
                "performance": "performance optimization",
                "database": "data management"
            }
            
            gap_area = gap_mapping.get(gap.lower(), gap)
            
            return f"I see you used technologies in {project_name}. How did you handle {gap_area} during that phase?"
        else:
            return f"I'd like to explore your experience with {gap}. Can you describe a project where you applied these concepts?"
    
    def generate_round_4_question(self, metadata: FocusedInterviewMetadata) -> str:
        """
        Round 4: Problem-Solving Scenario
        Hypothetical challenge related to their past work
        """
        if not metadata.projects:
            return "If you had to design a system to handle 10x the current traffic, what would be the first bottleneck you'd solve?"
        
        main_project = metadata.projects[0]
        project_name = main_project["project_name"]
        
        return f"If you had to re-architecture {project_name} to handle 10x the current traffic, what would be the first bottleneck you'd solve?"
    
    def generate_round_5_question(self, metadata: FocusedInterviewMetadata) -> str:
        """
        Round 5: Behavioral/STAR Final
        Soft-skills question focused on ownership or conflict
        """
        return "Describe a time a technical decision you made was challenged by a teammate. How did you resolve it?"
    
    def _detect_technologies(self, text: str) -> List[str]:
        """
        Detect technologies mentioned in candidate's response
        """
        tech_patterns = [
            r'\b(Docker|Kubernetes|Container|Pod|Service|Orchestration)\b',
            r'\b(React|Vue|Angular|Frontend|Component|State|Framework)\b',
            r'\b(Python|Django|Flask|FastAPI|Backend|API|Node\.js|Express)\b',
            r'\b(PostgreSQL|MySQL|MongoDB|Redis|Database|SQL|NoSQL|Oracle)\b',
            r'\b(AWS|Azure|GCP|Cloud|Lambda|EC2|S3|Cloud Provider)\b',
            r'\b(Microservices|Architecture|System|Design|Scalable|Monolith)\b',
            r'\b(Security|Authentication|Authorization|JWT|OAuth|Encryption)\b',
            r'\b(Performance|Optimization|Cache|Index|Query|Scaling)\b'
        ]
        
        technologies = []
        for pattern in tech_patterns:
            matches = re.findall(pattern, text, re.IGNORECASE)
            technologies.extend(matches)
        
        return list(set(technologies))
    
    def generate_follow_up(self, metadata: FocusedInterviewMetadata, current_answer: str, round_type: InterviewRound) -> Optional[str]:
        """
        Generate one brief follow-up if answer is vague
        """
        if metadata.follow_ups_used >= self.max_follow_ups_per_round:
            return None
        
        # Check for vagueness indicators
        vague_indicators = [
            "it was good", "it worked well", "we did it", "it was successful",
            "I think", "maybe", "probably", "sort of", "kind of"
        ]
        
        is_vague = any(indicator.lower() in current_answer.lower() for indicator in vague_indicators)
        
        if not is_vague and len(current_answer) > 50:
            return None  # Answer is sufficiently detailed
        
        # Generate context-aware follow-up
        detected_tech = self._detect_technologies(current_answer)
        
        if round_type == InterviewRound.PROJECT_DEEP_DIVE:
            if detected_tech:
                tech = detected_tech[0]
                return f"Can you be more specific about how you implemented {tech}?"
            else:
                return "Can you elaborate on the specific technical challenges you encountered?"
        
        elif round_type == InterviewRound.SKILL_GAP_BRIDGE:
            return "What specific problems did you face, and how did you solve them?"
        
        elif round_type == InterviewRound.PROBLEM_SOLVING:
            return "What metrics would you use to identify that bottleneck?"
        
        elif round_type == InterviewRound.BEHAVIORAL_STAR:
            return "What was the specific technical disagreement, and what data did you use to support your position?"
        
        return "Can you provide more specific details about your approach?"
    
    def generate_nudge(self, metadata: FocusedInterviewMetadata, last_question: str) -> str:
        """
        Generate nudge for prolonged silence
        """
        if metadata.nudges_used >= self.max_nudges_per_round:
            return None
        
        detected_tech = self._detect_technologies(last_question)
        tech_context = detected_tech[0] if detected_tech else "the technical implementation"
        
        return f"Would you like me to clarify the question regarding {tech_context}?"
    
    def evaluate_technical_correctness(self, answer: str, round_type: InterviewRound) -> float:
        """
        Evaluate technical correctness based on round type
        """
        if round_type == InterviewRound.PROJECT_DEEP_DIVE:
            # Check for understanding of trade-offs, decisions, reasoning
            decision_indicators = ["trade-off", "advantage", "disadvantage", "because", "reason", "chose"]
            return min(1.0, sum(1 for ind in decision_indicators if ind.lower() in answer.lower()) / 3.0)
        
        elif round_type == InterviewRound.SKILL_GAP_BRIDGE:
            # Check for problem-solving approach
            problem_indicators = ["problem", "challenge", "solution", "implement", "solve", "approach"]
            return min(1.0, sum(1 for ind in problem_indicators if ind.lower() in answer.lower()) / 3.0)
        
        elif round_type == InterviewRound.PROBLEM_SOLVING:
            # Check for scalability/bottleneck understanding
            scaling_indicators = ["bottleneck", "scale", "traffic", "performance", "optimize", "load"]
            return min(1.0, sum(1 for ind in scaling_indicators if ind.lower() in answer.lower()) / 3.0)
        
        elif round_type == InterviewRound.BEHAVIORAL_STAR:
            # Check for STAR methodology
            star_indicators = ["situation", "task", "action", "result", "when", "i did", "we achieved"]
            return min(1.0, sum(1 for ind in star_indicators if ind.lower() in answer.lower()) / 4.0)
        
        return 0.5  # Neutral for ice-breaker
    
    def evaluate_depth_of_explanation(self, answer: str) -> float:
        """
        Evaluate depth based on expert terminology and detailed explanations
        """
        expert_terms = {
            "scalability", "performance", "security", "maintainability", "architecture",
            "design pattern", "best practice", "optimization", "concurrency", "distributed",
            "microservices", "serverless", "containerization", "orchestration", "devops"
        }
        
        explanatory_phrases = [
            "because", "therefore", "however", "specifically", "for example", "in particular",
            "the reason", "trade-off", "advantage", "disadvantage", "consideration"
        ]
        
        depth_score = 0.0
        
        # Count expert terms
        for term in expert_terms:
            if term.lower() in answer.lower():
                depth_score += 0.1
        
        # Count explanatory phrases
        for phrase in explanatory_phrases:
            if phrase.lower() in answer.lower():
                depth_score += 0.1
        
        # Length and complexity bonus
        if len(answer) > 150:
            depth_score += 0.2
        
        return min(1.0, depth_score)
    
    def evaluate_star_methodology(self, answer: str) -> float:
        """
        Evaluate STAR method usage for behavioral questions
        """
        star_components = {
            "situation": ["situation", "context", "background", "when", "at", "in"],
            "task": ["task", "goal", "objective", "needed to", "responsible for", "my role"],
            "action": ["i did", "i implemented", "i created", "i developed", "my approach", "we decided"],
            "result": ["result", "outcome", "achieved", "improved", "reduced", "increased", "solved"]
        }
        
        star_score = 0.0
        for component, indicators in star_components.items():
            if any(indicator.lower() in answer.lower() for indicator in indicators):
                star_score += 0.25
        
        return min(1.0, star_score)
    
    def calculate_centroid_similarity(self, answer: str, round_type: InterviewRound) -> float:
        """
        Calculate semantic similarity with ideal answers
        """
        ideal_key = {
            InterviewRound.PROJECT_DEEP_DIVE: "technical_decision",
            InterviewRound.PROBLEM_SOLVING: "problem_solving", 
            InterviewRound.BEHAVIORAL_STAR: "conflict_resolution",
            InterviewRound.SKILL_GAP_BRIDGE: "problem_solving"
        }.get(round_type, "technical_decision")
        
        ideal_answer = self.ideal_answers.get(ideal_key, "")
        
        # Simple keyword-based similarity (in production, use sentence transformers)
        answer_words = set(answer.lower().split())
        ideal_words = set(ideal_answer.lower().split())
        
        if not answer_words or not ideal_words:
            return 0.0
        
        intersection = answer_words.intersection(ideal_words)
        union = answer_words.union(ideal_words)
        
        return len(intersection) / len(union) if union else 0.0
    
    def calculate_round_score(self, round_data: InterviewRoundData) -> float:
        """
        Calculate comprehensive score for a round
        """
        weights = {
            "technical_correctness": 0.4,
            "depth_score": 0.3,
            "star_score": 0.2,
            "centroid_similarity": 0.1
        }
        
        return (
            round_data.technical_correctness * weights["technical_correctness"] +
            round_data.depth_score * weights["depth_score"] +
            round_data.star_score * weights["star_score"] +
            round_data.centroid_similarity * weights["centroid_similarity"]
        )
    
    def get_closing_phrase(self) -> str:
        """
        Return the exact closing phrase as required
        """
        return "Thank you for your time. Your detailed evaluation will be processed and shared with HR shortly."
    
    async def conduct_focused_interview(self, application_id: int) -> InterviewSession:
        """
        Conduct focused 5-round technical interview
        """
        # Load comprehensive metadata
        metadata = await self.load_focused_interview_metadata(application_id)
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
        
        logger.info(f"Starting focused 5-round interview for {metadata.candidate_name}")
        
        # Round 1: Ice-Breaker & Lead
        round_1_question = self.generate_round_1_question(metadata)
        round_1_answer = self._simulate_round_answer(1, round_1_question, metadata)
        
        # Evaluate Round 1
        round_1_data = InterviewRoundData(
            round_number=1,
            round_type=InterviewRound.ICE_BREAKER,
            question=round_1_question,
            answer=round_1_answer,
            follow_up_used=False,
            nudge_used=False,
            technical_correctness=self.evaluate_technical_correctness(round_1_answer, InterviewRound.ICE_BREAKER),
            depth_score=self.evaluate_depth_of_explanation(round_1_answer),
            star_score=self.evaluate_star_methodology(round_1_answer),
            centroid_similarity=self.calculate_centroid_similarity(round_1_answer, InterviewRound.ICE_BREAKER),
            confidence_score=0.0,
            timestamp=datetime.utcnow()
        )
        round_1_data.confidence_score = self.calculate_round_score(round_1_data)
        
        metadata.rounds_data.append(round_1_data)
        metadata.rounds_completed = 1
        metadata.current_round = InterviewRound.PROJECT_DEEP_DIVE
        
        # Create Q&A record
        qa_1 = QuestionResponse(
            question_id=str(uuid.uuid4()),
            question_text=round_1_question,
            question_type="ice_breaker",
            audio_url=None,
            response_text=round_1_answer,
            response_audio_url=None,
            timestamp=datetime.utcnow(),
            centroid_score=round_1_data.centroid_similarity,
            depth_boost=round_1_data.depth_score,
            llm_qualitative_score=round_1_data.technical_correctness,
            confidence_score=round_1_data.confidence_score
        )
        
        session.questions_asked.append(qa_1)
        session.transcript += f"Q1: {round_1_question}\nA1: {round_1_answer}\n\n"
        
        # Round 2: Project Deep-Dive
        round_2_question = self.generate_round_2_question(metadata, round_1_answer)
        round_2_answer = self._simulate_round_answer(2, round_2_question, metadata)
        
        # Check if follow-up needed
        follow_up_2 = self.generate_follow_up(metadata, round_2_answer, InterviewRound.PROJECT_DEEP_DIVE)
        if follow_up_2:
            round_2_follow_up_answer = self._simulate_round_answer(2, follow_up_2, metadata)
            round_2_answer += f" {round_2_follow_up_answer}"
            metadata.follow_ups_used += 1
        
        # Evaluate Round 2
        round_2_data = InterviewRoundData(
            round_number=2,
            round_type=InterviewRound.PROJECT_DEEP_DIVE,
            question=round_2_question,
            answer=round_2_answer,
            follow_up_used=bool(follow_up_2),
            nudge_used=False,
            technical_correctness=self.evaluate_technical_correctness(round_2_answer, InterviewRound.PROJECT_DEEP_DIVE),
            depth_score=self.evaluate_depth_of_explanation(round_2_answer),
            star_score=self.evaluate_star_methodology(round_2_answer),
            centroid_similarity=self.calculate_centroid_similarity(round_2_answer, InterviewRound.PROJECT_DEEP_DIVE),
            confidence_score=0.0,
            timestamp=datetime.utcnow()
        )
        round_2_data.confidence_score = self.calculate_round_score(round_2_data)
        
        metadata.rounds_data.append(round_2_data)
        metadata.rounds_completed = 2
        metadata.current_round = InterviewRound.SKILL_GAP_BRIDGE
        
        qa_2 = QuestionResponse(
            question_id=str(uuid.uuid4()),
            question_text=round_2_question,
            question_type="project_deep_dive",
            audio_url=None,
            response_text=round_2_answer,
            response_audio_url=None,
            timestamp=datetime.utcnow(),
            centroid_score=round_2_data.centroid_similarity,
            depth_boost=round_2_data.depth_score,
            llm_qualitative_score=round_2_data.technical_correctness,
            confidence_score=round_2_data.confidence_score
        )
        
        session.questions_asked.append(qa_2)
        session.transcript += f"Q2: {round_2_question}\nA2: {round_2_answer}\n\n"
        
        # Round 3: Skill-Gap Bridge
        round_3_question = self.generate_round_3_question(metadata)
        round_3_answer = self._simulate_round_answer(3, round_3_question, metadata)
        
        # Evaluate Round 3
        round_3_data = InterviewRoundData(
            round_number=3,
            round_type=InterviewRound.SKILL_GAP_BRIDGE,
            question=round_3_question,
            answer=round_3_answer,
            follow_up_used=False,
            nudge_used=False,
            technical_correctness=self.evaluate_technical_correctness(round_3_answer, InterviewRound.SKILL_GAP_BRIDGE),
            depth_score=self.evaluate_depth_of_explanation(round_3_answer),
            star_score=self.evaluate_star_methodology(round_3_answer),
            centroid_similarity=self.calculate_centroid_similarity(round_3_answer, InterviewRound.SKILL_GAP_BRIDGE),
            confidence_score=0.0,
            timestamp=datetime.utcnow()
        )
        round_3_data.confidence_score = self.calculate_round_score(round_3_data)
        
        metadata.rounds_data.append(round_3_data)
        metadata.rounds_completed = 3
        metadata.current_round = InterviewRound.PROBLEM_SOLVING
        
        qa_3 = QuestionResponse(
            question_id=str(uuid.uuid4()),
            question_text=round_3_question,
            question_type="skill_gap_bridge",
            audio_url=None,
            response_text=round_3_answer,
            response_audio_url=None,
            timestamp=datetime.utcnow(),
            centroid_score=round_3_data.centroid_similarity,
            depth_boost=round_3_data.depth_score,
            llm_qualitative_score=round_3_data.technical_correctness,
            confidence_score=round_3_data.confidence_score
        )
        
        session.questions_asked.append(qa_3)
        session.transcript += f"Q3: {round_3_question}\nA3: {round_3_answer}\n\n"
        
        # Round 4: Problem-Solving Scenario
        round_4_question = self.generate_round_4_question(metadata)
        round_4_answer = self._simulate_round_answer(4, round_4_question, metadata)
        
        # Evaluate Round 4
        round_4_data = InterviewRoundData(
            round_number=4,
            round_type=InterviewRound.PROBLEM_SOLVING,
            question=round_4_question,
            answer=round_4_answer,
            follow_up_used=False,
            nudge_used=False,
            technical_correctness=self.evaluate_technical_correctness(round_4_answer, InterviewRound.PROBLEM_SOLVING),
            depth_score=self.evaluate_depth_of_explanation(round_4_answer),
            star_score=self.evaluate_star_methodology(round_4_answer),
            centroid_similarity=self.calculate_centroid_similarity(round_4_answer, InterviewRound.PROBLEM_SOLVING),
            confidence_score=0.0,
            timestamp=datetime.utcnow()
        )
        round_4_data.confidence_score = self.calculate_round_score(round_4_data)
        
        metadata.rounds_data.append(round_4_data)
        metadata.rounds_completed = 4
        metadata.current_round = InterviewRound.BEHAVIORAL_STAR
        
        qa_4 = QuestionResponse(
            question_id=str(uuid.uuid4()),
            question_text=round_4_question,
            question_type="problem_solving",
            audio_url=None,
            response_text=round_4_answer,
            response_audio_url=None,
            timestamp=datetime.utcnow(),
            centroid_score=round_4_data.centroid_similarity,
            depth_boost=round_4_data.depth_score,
            llm_qualitative_score=round_4_data.technical_correctness,
            confidence_score=round_4_data.confidence_score
        )
        
        session.questions_asked.append(qa_4)
        session.transcript += f"Q4: {round_4_question}\nA4: {round_4_answer}\n\n"
        
        # Round 5: Behavioral/STAR Final
        round_5_question = self.generate_round_5_question(metadata)
        round_5_answer = self._simulate_round_answer(5, round_5_question, metadata)
        
        # Evaluate Round 5
        round_5_data = InterviewRoundData(
            round_number=5,
            round_type=InterviewRound.BEHAVIORAL_STAR,
            question=round_5_question,
            answer=round_5_answer,
            follow_up_used=False,
            nudge_used=False,
            technical_correctness=self.evaluate_technical_correctness(round_5_answer, InterviewRound.BEHAVIORAL_STAR),
            depth_score=self.evaluate_depth_of_explanation(round_5_answer),
            star_score=self.evaluate_star_methodology(round_5_answer),
            centroid_similarity=self.calculate_centroid_similarity(round_5_answer, InterviewRound.BEHAVIORAL_STAR),
            confidence_score=0.0,
            timestamp=datetime.utcnow()
        )
        round_5_data.confidence_score = self.calculate_round_score(round_5_data)
        
        metadata.rounds_data.append(round_5_data)
        metadata.rounds_completed = 5
        
        qa_5 = QuestionResponse(
            question_id=str(uuid.uuid4()),
            question_text=round_5_question,
            question_type="behavioral_star",
            audio_url=None,
            response_text=round_5_answer,
            response_audio_url=None,
            timestamp=datetime.utcnow(),
            centroid_score=round_5_data.centroid_similarity,
            depth_boost=round_5_data.depth_score,
            llm_qualitative_score=round_5_data.technical_correctness,
            confidence_score=round_5_data.confidence_score
        )
        
        session.questions_asked.append(qa_5)
        session.transcript += f"Q5: {round_5_question}\nA5: {round_5_answer}\n\n"
        
        # Add exact closing phrase
        closing_phrase = self.get_closing_phrase()
        session.transcript += f"\n{closing_phrase}\n"
        
        # Calculate final score (average of all rounds)
        if metadata.rounds_data:
            session.final_score = sum(round_data.confidence_score for round_data in metadata.rounds_data) / len(metadata.rounds_data)
        
        session.completed_at = datetime.utcnow()
        
        logger.info(f"Focused 5-round interview completed for {metadata.candidate_name}. Final score: {session.final_score:.2f}")
        
        return session
    
    def _simulate_round_answer(self, round_number: int, question: str, metadata: FocusedInterviewMetadata) -> str:
        """Simulate candidate responses for demonstration"""
        if round_number == 1:
            if metadata.projects:
                project = metadata.projects[0]
                return (
                    f"I was the lead developer on {project['project_name']}. "
                    f"The system used a microservices architecture with React frontend and Python backend. "
                    f"I owned the API design and implementation, handling authentication and core business logic. "
                    f"We used PostgreSQL for data storage and Redis for caching."
                )
            return "I led the development of a scalable microservices system..."
        
        elif round_number == 2:
            return "We chose PostgreSQL because we needed strong consistency and complex queries. The trade-off was limited horizontal scaling compared to NoSQL, but the data relationships were critical for our business logic."
        
        elif round_number == 3:
            return "We faced significant scaling challenges during peak traffic. I implemented database connection pooling, query optimization, and added Redis caching layers. This reduced database load by 60% and improved response times."
        
        elif round_number == 4:
            return "The first bottleneck would be the database. I'd implement read replicas to distribute query load, add aggressive caching for frequently accessed data, and optimize slow queries through proper indexing."
        
        elif round_number == 5:
            return "A teammate challenged my decision to use microservices, arguing it added complexity. I presented benchmarks showing our monolith's limitations, proposed a phased migration, and we compromised by starting with non-critical services. The approach proved successful."
        
        return "I have extensive experience in this area and have successfully implemented similar solutions."


# Singleton instance
focused_interviewer = FocusedInterviewerPersona()
