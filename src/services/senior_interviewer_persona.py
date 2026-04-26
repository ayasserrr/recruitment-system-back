"""
Senior Technical Interviewer & Career Architect
Bilingual (Arabic/English) conversational AI with deep project analysis capabilities
"""

import asyncio
import json
import logging
import uuid
import re
from datetime import datetime
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
from database.connection import SessionLocal
from services.interview_types import InterviewSession, QuestionResponse, InterviewMode

logger = logging.getLogger(__name__)


class InterviewPhase(Enum):
    ICE_BREAKING = "ice_breaking"      # 20% - Project acknowledgment
    TARGETED_PROBING = "targeted_probing"  # 60% - Gap-focused deep dive
    REAL_TIME_REASONING = "real_time_reasoning"  # 20% - Technical verification


@dataclass
class KnowledgeAnchor:
    """Knowledge-base concept with grading guide"""
    concept: str
    keywords: List[str]
    expert_terms: List[str]
    grading_guide: Dict[str, Any]  # Expected answers, common mistakes, etc.


@dataclass
class InterviewContext:
    """Enhanced context with project and knowledge data"""
    candidate_id: str
    application_id: int
    projects: List[Dict[str, Any]]
    experiences: List[Dict[str, Any]]
    identified_gaps: List[str]
    knowledge_anchors: List[KnowledgeAnchor]
    current_phase: InterviewPhase
    questions_asked: int
    total_questions: int = 5


@dataclass
class ConversationTurn:
    """Single conversation turn with evaluation"""
    turn_id: str
    phase: InterviewPhase
    question_ar: str
    question_en: str
    answer_text: str
    detected_technical_terms: List[str]
    evidence_score: float
    depth_score: float
    follow_up_needed: bool
    timestamp: datetime


class SeniorInterviewerPersona:
    """
    Senior Technical Interviewer with bilingual capabilities and deep project analysis
    Implements the 3-phase conversational strategy with evidence-based evaluation
    """
    
    def __init__(self):
        self.knowledge_db = self._build_knowledge_database()
        self.current_context = None
        self.conversation_history = []
        self.questions_asked = 0
        
    def _build_knowledge_database(self) -> Dict[str, KnowledgeAnchor]:
        """
        Build comprehensive knowledge database with grading guides
        """
        return {
            "docker": KnowledgeAnchor(
                concept="Docker Containerization",
                keywords=["docker", "container", "image", "dockerfile", "compose"],
                expert_terms=["multi-stage builds", "layer caching", "image optimization", "security scanning"],
                grading_guide={
                    "expected_concepts": ["containers vs VMs", "Dockerfile optimization", "image layers"],
                    "common_mistakes": ["not using .dockerignore", "large image sizes", "running as root"],
                    "expert_indicators": ["multi-stage builds", "layer optimization", "security practices"]
                }
            ),
            
            "react": KnowledgeAnchor(
                concept="React Framework",
                keywords=["react", "jsx", "component", "hooks", "state"],
                expert_terms=["virtual DOM", "reconciliation", "hooks lifecycle", "context API"],
                grading_guide={
                    "expected_concepts": ["components", "state management", "lifecycle"],
                    "common_mistakes": ["direct state mutation", "not using keys properly", "memory leaks"],
                    "expert_indicators": ["custom hooks", "performance optimization", "advanced patterns"]
                }
            ),
            
            "python": KnowledgeAnchor(
                concept="Python Programming",
                keywords=["python", "django", "flask", "async", "decorator"],
                expert_terms=["GIL", "asyncio", "metaclasses", "decorators", "generators"],
                grading_guide={
                    "expected_concepts": ["data types", "functions", "OOP", "modules"],
                    "common_mistakes": ["mutable default arguments", "global variables", "exception handling"],
                    "expert_indicators": ["async programming", "metaprogramming", "performance optimization"]
                }
            ),
            
            "database": KnowledgeAnchor(
                concept="Database Systems",
                keywords=["sql", "nosql", "postgres", "mongodb", "query"],
                expert_terms=["acid", "normalization", "indexing", "transactions", "sharding"],
                grading_guide={
                    "expected_concepts": ["CRUD operations", "basic queries", "relationships"],
                    "common_mistakes": ["N+1 queries", "missing indexes", "no transactions"],
                    "expert_indicators": ["query optimization", "database design", "scaling strategies"]
                }
            ),
            
            "aws": KnowledgeAnchor(
                concept="Amazon Web Services",
                keywords=["aws", "ec2", "s3", "lambda", "api gateway"],
                expert_terms=["VPC", "IAM roles", "CloudFormation", "auto-scaling", "CDN"],
                grading_guide={
                    "expected_concepts": ["basic services", "security groups", "storage"],
                    "common_mistakes": ["hardcoded credentials", "over-provisioning", "no monitoring"],
                    "expert_indicators": ["infrastructure as code", "cost optimization", "security best practices"]
                }
            )
        }
    
    async def load_interview_context(self, application_id: int) -> InterviewContext:
        """
        Load comprehensive context including projects, experiences, gaps, and knowledge anchors
        """
        db = SessionLocal()
        try:
            # Get application with candidate data
            application = db.query(Application).filter(
                Application.application_id == application_id
            ).first()
            
            if not application:
                raise ValueError(f"Application {application_id} not found")
            
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
            
            # Select relevant knowledge anchors based on gaps and projects
            relevant_anchors = []
            all_tech = set()
            
            # Extract tech from projects
            for proj in projects:
                all_tech.update(tech.lower() for tech in proj.get("tech_stack", []))
            
            # Add gaps to tech set
            all_tech.update(gap.lower() for gap in gaps)
            
            # Match with knowledge database
            for tech in all_tech:
                for key, anchor in self.knowledge_db.items():
                    if any(tech_term in tech for tech_term in anchor.keywords):
                        relevant_anchors.append(anchor)
                        break
            
            return InterviewContext(
                candidate_id=str(application.candidate_id),
                application_id=application_id,
                projects=projects,
                experiences=experiences,
                identified_gaps=gaps,
                knowledge_anchors=relevant_anchors,
                current_phase=InterviewPhase.ICE_BREAKING,
                questions_asked=0
            )
            
        finally:
            db.close()
    
    def _calculate_duration(self, start_date, end_date) -> str:
        """Calculate duration from dates"""
        if not start_date:
            return "Unknown"
        
        try:
            from datetime import datetime
            start = datetime.strptime(start_date, "%Y-%m-%d") if isinstance(start_date, str) else start_date
            end = datetime.strptime(end_date, "%Y-%m-%d") if end_date else datetime.now()
            
            months = (end.year - start.year) * 12 + (end.month - start.month)
            return f"{months} months"
        except:
            return "Unknown"
    
    def generate_phase_1_question(self, context: InterviewContext) -> Tuple[str, str]:
        """
        Phase 1: Project Ice-Breaking (20%)
        Acknowledge a major project and ask about technical responsibilities
        """
        if not context.projects:
            # Fallback if no projects
            return (
                "أرى أن لديك خلفية تقنية قوية. هل يمكنك أن تخبرني عن أبرز مشروع عملت عليه مؤخراً؟",
                "I see you have a strong technical background. Can you tell me about the most significant project you've worked on recently?"
            )
        
        # Select the most substantial project
        main_project = max(context.projects, key=lambda p: p.get("duration", 0))
        
        project_name = main_project["project_name"]
        tech_stack = main_project.get("tech_stack", [])
        
        # Acknowledge project and ask about technical role
        question_ar = f"لقد أثار اهتمامي مشروعك '{project_name}'، هل يمكنك شرح الجزء التقني الذي كنت مسؤولاً عنه تحديداً؟"
        question_en = f"I'm impressed by your '{project_name}' project. Could you explain specifically which technical components you were responsible for?"
        
        # Add tech stack reference if available
        if tech_stack:
            main_tech = tech_stack[0]
            question_ar += f" خصوصاً في جانب الـ {main_tech}؟"
            question_en += f" especially regarding the {main_tech} aspects?"
        
        return question_ar, question_en
    
    def generate_phase_2_question(self, context: InterviewContext) -> Tuple[str, str]:
        """
        Phase 2: Targeted Probing (60%)
        Ask about specific gaps in the context of their projects
        """
        if not context.identified_gaps:
            # If no gaps, ask about technical challenges
            return (
                "في مشاريعك السابقة، ما هو التحدي التقني الأكثر صعوبة الذي واجهتك وكيف تغلبت عليه؟",
                "In your previous projects, what was the most difficult technical challenge you faced and how did you overcome it?"
            )
        
        # Select a gap and find relevant project
        gap = context.identified_gaps[0]  # Focus on primary gap
        
        # Find project that might relate to this gap
        relevant_project = None
        for proj in context.projects:
            tech_stack = [tech.lower() for tech in proj.get("tech_stack", [])]
            if any(gap_term in " ".join(tech_stack) for gap_term in gap.lower().split()):
                relevant_project = proj
                break
        
        if relevant_project:
            project_name = relevant_project["project_name"]
            question_ar = f"شفت إنك عملت على مشروع '{project_name}'، ليه قررت تستخدم تقنيات مرتبطة بـ {gap}؟ وإيه المشاكل اللي واجهتك في التنفيذ؟"
            question_en = f"I see you worked on '{project_name}'. Why did you choose to use technologies related to {gap}, and what challenges did you face during implementation?"
        else:
            # General gap question
            question_ar = f"لاحظنا إنك لم تذكر خبرة كثيرة في {gap}. هل يمكنك شرح أي مشروع طبقت فيه مفاهيم مشابهة؟"
            question_en = f"I notice you haven't mentioned much experience with {gap}. Can you explain any project where you implemented similar concepts?"
        
        return question_ar, question_en
    
    def generate_phase_3_question(self, context: InterviewContext, previous_answer: str) -> Tuple[str, str]:
        """
        Phase 3: Real-time Reasoning (20%)
        Follow-up based on technical terms detected in previous answer
        """
        # Detect technical terms in previous answer
        detected_terms = self._detect_technical_terms(previous_answer)
        
        if not detected_terms:
            # Generic follow-up
            return (
                "جميل، بس تعمق معايا شوية في الـ Logic ده، إزاي ضمنت الـ Data Consistency هنا؟",
                "That's interesting, but let's dive deeper into the logic. How did you ensure data consistency in this case?"
            )
        
        # Select most relevant term for follow-up
        term = detected_terms[0]
        
        # Check if we have knowledge anchor for this term
        anchor = None
        for knowledge_anchor in context.knowledge_anchors:
            if term.lower() in [k.lower() for k in knowledge_anchor.keywords]:
                anchor = knowledge_anchor
                break
        
        if anchor:
            # Use knowledge guide for targeted follow-up
            if "consistency" in term.lower() or "transaction" in term.lower():
                question_ar = f"استخدمت {term} في إجابتك. إزاي تعاملت مع حالات الـ Concurrency و Race Conditions؟"
                question_en = f"You mentioned {term} in your answer. How did you handle concurrency and race conditions?"
            elif "performance" in term.lower() or "optimization" in term.lower():
                question_ar = f"تحسين الأداء مهم. إيه المقاييس (metrics) استخدمتها لتقييم الأداء؟ وإيه كانت النتايج؟"
                question_en = f"Performance optimization is crucial. What metrics did you use to evaluate performance? What were the results?"
            else:
                question_ar = f"استخدمت مصطلح {term}. هل يمكنك تعطي مثال عملي من مشروعك يوضح الفكرة؟"
                question_en = f"You used the term {term}. Can you give a practical example from your project that illustrates this concept?"
        else:
            # Generic technical follow-up
            question_ar = f"مصطلح {term} ده مهم في المجال. إزاي تطور خبرتك فيه؟"
            question_en = f"The term {term} is important in this field. How have you developed your expertise in it?"
        
        return question_ar, question_en
    
    def _detect_technical_terms(self, text: str) -> List[str]:
        """
        Detect technical terms in candidate's answer
        """
        technical_terms = []
        
        # Common technical patterns
        patterns = [
            r'\b(API|REST|GraphQL|SQL|NoSQL|AWS|Azure|GCP)\b',
            r'\b(React|Vue|Angular|Node\.js|Django|Flask|Spring)\b',
            r'\b(Docker|Kubernetes|Jenkins|CI\/CD|DevOps)\b',
            r'\b(Microservices|Serverless|Lambda|Functions)\b',
            r'\b(Agile|Scrum|Kanban|Waterfall)\b',
            r'\b(Git|GitHub|GitLab|Version Control)\b',
            r'\b(Test|Testing|Unit|Integration|E2E)\b',
            r'\b(Security|Authentication|Authorization|OAuth|JWT)\b',
            r'\b(Database|PostgreSQL|MongoDB|Redis|Elasticsearch)\b',
            r'\b(Python|JavaScript|Java|C\+\+|Go|Rust)\b',
            r'\b(Machine Learning|AI|Deep Learning|Neural Networks)\b'
        ]
        
        for pattern in patterns:
            matches = re.findall(pattern, text, re.IGNORECASE)
            technical_terms.extend(matches)
        
        # Remove duplicates and return
        return list(set(technical_terms))
    
    def evaluate_answer_evidence(self, answer: str, context: InterviewContext) -> float:
        """
        Evidence-based evaluation: Look for concrete examples from projects
        """
        evidence_indicators = [
            "in my project", "في مشروعي", "we implemented", "قمنا بتنفيذ",
            "I worked on", "عملت على", "we built", "بنينا",
            "the result was", "النتيجة كانت", "we achieved", "حققنا",
            "for example", "على سبيل المثال", "specifically", "تحديداً"
        ]
        
        evidence_count = 0
        for indicator in evidence_indicators:
            if indicator.lower() in answer.lower():
                evidence_count += 1
        
        # Check for project references
        for proj in context.projects:
            if proj["project_name"].lower() in answer.lower():
                evidence_count += 2
        
        # Normalize to 0-1 scale
        return min(1.0, evidence_count / 3.0)
    
    def evaluate_answer_depth(self, answer: str, detected_terms: List[str]) -> float:
        """
        Depth evaluation: Look for expert terminology and detailed explanations
        """
        expert_terms = {
            "race conditions", "deadlock", "mutex", "semaphore",
            "normalization", "acid", "base", "eventual consistency",
            "sharding", "replication", "caching", "cdn",
            "microservices", "serverless", "containerization",
            "async", "await", "promise", "callback hell",
            "virtual dom", "reconciliation", "hooks", "context",
            "middleware", "pipeline", "event sourcing", "cqrs"
        }
        
        depth_indicators = 0
        
        # Count expert terms
        for term in expert_terms:
            if term.lower() in answer.lower():
                depth_indicators += 1
        
        # Look for detailed explanations
        detail_patterns = [
            r"because .{10,}", r"since .{10,}", r"السبب .{10,}",
            r"the reason .{10,}", r"we chose .{10,}", r"اخترنا .{10,}",
            r"advantage .{10,}", r"disadvantage .{10,}"
        ]
        
        for pattern in detail_patterns:
            if re.search(pattern, answer, re.IGNORECASE):
                depth_indicators += 1
        
        # Normalize to 0-1 scale
        return min(1.0, depth_indicators / 4.0)
    
    def should_ask_follow_up(self, answer: str, evidence_score: float, depth_score: float) -> bool:
        """
        Determine if follow-up question is needed based on answer quality
        """
        # Need follow-up if answer is vague or lacks evidence
        if evidence_score < 0.5 or depth_score < 0.3:
            return True
        
        # Check for vague indicators
        vague_indicators = [
            "it was good", "كان جيداً", "I think", "أعتقد",
            "maybe", "ربما", "probably", "على الأرجح",
            "kind of", "نوعاً ما", "sort of", "بشكل ما"
        ]
        
        for indicator in vague_indicators:
            if indicator.lower() in answer.lower():
                return True
        
        return False
    
    def generate_acknowledgment(self, answer: str, phase: InterviewPhase) -> str:
        """
        Generate appropriate acknowledgment without direct feedback
        """
        acknowledgments_ar = [
            "فهمت، وجهة نظر مثيرة للاهتمام.",
            "جميل، هذا يقودنا لنقطة مهمة.",
            "ممتاز، أرى أن لديك خبرة في هذا المجال.",
            "طيب، دعنا نستكشف هذا أكثر."
        ]
        
        acknowledgments_en = [
            "I understand, that's an interesting perspective.",
            "Good, that leads us to an important point.",
            "Excellent, I can see you have experience in this area.",
            "Alright, let's explore this further."
        ]
        
        # Select acknowledgment based on phase and answer length
        if len(answer) < 100:  # Short answer
            return acknowledgments_ar[0] if phase != InterviewPhase.ICE_BREAKING else acknowledgments_en[0]
        else:
            import random
            return random.choice(acknowledgments_ar) if phase != InterviewPhase.ICE_BREAKING else random.choice(acknowledgments_en)
    
    def get_closing_phrase(self) -> str:
        """
        Return the exact closing phrase as required
        """
        return "شكراً لوقتك، انتهت المقابلة وسيتم إرسال التقييم النهائي للـ HR."
    
    async def conduct_interview_conversation(self, application_id: int) -> InterviewSession:
        """
        Main interview orchestration with senior interviewer persona
        """
        # Load comprehensive context
        context = await self.load_interview_context(application_id)
        self.current_context = context
        
        # Initialize session
        session = InterviewSession(
            session_id=str(uuid.uuid4()),
            candidate_id=context.candidate_id,
            application_id=application_id,
            mode=InterviewMode.TECHNICAL,
            context=context,  # Will be converted to proper type
            questions_asked=[],
            final_score=0.0,
            transcript="",
            audio_recording_url=None,
            started_at=datetime.utcnow(),
            completed_at=None
        )
        
        logger.info(f"Starting senior interviewer conversation for candidate {context.candidate_id}")
        
        # Phase 1: Ice-Breaking (1 question)
        if context.projects:
            question_ar, question_en = self.generate_phase_1_question(context)
            
            # Simulate candidate response (in production, this would be from LiveKit)
            mock_response = self._simulate_candidate_response(question_ar, context.projects[0])
            
            # Evaluate response
            detected_terms = self._detect_technical_terms(mock_response)
            evidence_score = self.evaluate_answer_evidence(mock_response, context)
            depth_score = self.evaluate_answer_depth(mock_response, detected_terms)
            
            # Create Q&A record
            qa_record = QuestionResponse(
                question_id=str(uuid.uuid4()),
                question_text=question_ar,
                question_type="ice_breaking",
                audio_url=None,
                response_text=mock_response,
                response_audio_url=None,
                timestamp=datetime.utcnow(),
                centroid_score=0.0,  # Not used in persona evaluation
                depth_boost=depth_score,
                llm_qualitative_score=0.0,  # Not used in persona evaluation
                confidence_score=(evidence_score + depth_score) / 2.0
            )
            
            session.questions_asked.append(qa_record)
            session.transcript += f"Q: {question_ar}\nA: {mock_response}\n\n"
            
            context.questions_asked += 1
            context.current_phase = InterviewPhase.TARGETED_PROBING
            
            logger.info(f"Phase 1 completed. Evidence: {evidence_score:.2f}, Depth: {depth_score:.2f}")
        
        # Phase 2: Targeted Probing (2-3 questions)
        while context.questions_asked < 4 and context.current_phase == InterviewPhase.TARGETED_PROBING:
            question_ar, question_en = self.generate_phase_2_question(context)
            
            # Simulate candidate response
            mock_response = self._simulate_candidate_response(question_ar, context.identified_gaps)
            
            # Evaluate response
            detected_terms = self._detect_technical_terms(mock_response)
            evidence_score = self.evaluate_answer_evidence(mock_response, context)
            depth_score = self.evaluate_answer_depth(mock_response, detected_terms)
            
            # Create Q&A record
            qa_record = QuestionResponse(
                question_id=str(uuid.uuid4()),
                question_text=question_ar,
                question_type="targeted_probing",
                audio_url=None,
                response_text=mock_response,
                response_audio_url=None,
                timestamp=datetime.utcnow(),
                centroid_score=0.0,
                depth_boost=depth_score,
                llm_qualitative_score=0.0,
                confidence_score=(evidence_score + depth_score) / 2.0
            )
            
            session.questions_asked.append(qa_record)
            session.transcript += f"Q: {question_ar}\nA: {mock_response}\n\n"
            
            context.questions_asked += 1
            
            # Check if we should move to Phase 3
            if context.questions_asked >= 3:
                context.current_phase = InterviewPhase.REAL_TIME_REASONING
            
            logger.info(f"Phase 2 question {context.questions_asked}. Evidence: {evidence_score:.2f}, Depth: {depth_score:.2f}")
        
        # Phase 3: Real-time Reasoning (1-2 questions)
        while context.questions_asked < 5 and context.current_phase == InterviewPhase.REAL_TIME_REASONING:
            # Get last answer for follow-up
            last_answer = session.questions_asked[-1].response_text if session.questions_asked else ""
            question_ar, question_en = self.generate_phase_3_question(context, last_answer)
            
            # Simulate candidate response
            mock_response = self._simulate_candidate_response(question_ar, detected_terms)
            
            # Evaluate response
            detected_terms = self._detect_technical_terms(mock_response)
            evidence_score = self.evaluate_answer_evidence(mock_response, context)
            depth_score = self.evaluate_answer_depth(mock_response, detected_terms)
            
            # Create Q&A record
            qa_record = QuestionResponse(
                question_id=str(uuid.uuid4()),
                question_text=question_ar,
                question_type="real_time_reasoning",
                audio_url=None,
                response_text=mock_response,
                response_audio_url=None,
                timestamp=datetime.utcnow(),
                centroid_score=0.0,
                depth_boost=depth_score,
                llm_qualitative_score=0.0,
                confidence_score=(evidence_score + depth_score) / 2.0
            )
            
            session.questions_asked.append(qa_record)
            session.transcript += f"Q: {question_ar}\nA: {mock_response}\n\n"
            
            context.questions_asked += 1
            
            logger.info(f"Phase 3 question {context.questions_asked}. Evidence: {evidence_score:.2f}, Depth: {depth_score:.2f}")
        
        # Calculate final score
        if session.questions_asked:
            session.final_score = sum(qa.confidence_score for qa in session.questions_asked) / len(session.questions_asked)
        
        # Add closing phrase to transcript
        closing_phrase = self.get_closing_phrase()
        session.transcript += f"\n{closing_phrase}\n"
        
        session.completed_at = datetime.utcnow()
        
        logger.info(f"Senior interviewer conversation completed. Final score: {session.final_score:.2f}")
        
        return session
    
    def _simulate_candidate_response(self, question: str, context_data: Any) -> str:
        """
        Simulate candidate responses for demonstration
        In production, this would be replaced with actual LiveKit transcription
        """
        # Simple simulation based on question content
        if "مشروع" in question or "project" in question.lower():
            return "في مشروعي السابق، كنت مسؤولاً عن تطوير الـ backend باستخدام Django REST Framework. قمنا ببناء API للتعامل مع البيانات واستخدمنا PostgreSQL كقاعدة بيانات. واجهنا تحديات في الأداء وقمنا بتحسين الـ queries باستخدام الـ indexing."
        
        elif "docker" in question.lower():
            return "استخدمنا Docker في مشروعنا لتسهيل الـ deployment. واجهنا مشكلة في حجم الـ images، فقمنا باستخدام multi-stage builds لتقليل الحجم. أيضاً استخدمنا .dockerignore لتجنب الملفات غير الضرورية."
        
        elif "تحدي" in question or "challenge" in question.lower():
            return "أكبر تحدي واجهته كان في التعامل مع concurrent requests. استخدمنا Redis caching و connection pooling لتحسين الأداء. النتيجة كانت تحسن استجابة النظام بنسبة 40%."
        
        else:
            return "لدي خبرة واسعة في هذا المجال. قمت بتطبيق عدة مشاريع باستخدام أفضل الممارسات والتقنيات الحديثة. أركز دائماً على جودة الكود وأداء النظام."


# Singleton instance
senior_interviewer = SeniorInterviewerPersona()
