"""
LiveKit Interview Agent Worker
==============================
Runs as a separate long-lived process that registers with the LiveKit server
and receives job dispatches for each interview room.

Start command (from src/):
    python livekit_agent.py start

Required environment variables (via .env):
    LIVEKIT_URL          wss://...
    LIVEKIT_API_KEY      your LiveKit API key
    LIVEKIT_API_SECRET   your LiveKit API secret
    OPENAI_API_KEY       used by livekit-plugins-openai for STT / LLM / TTS

Room metadata (JSON string set when the room is created by /interviews/start):
    {
        "session_id":      <int>   TechnicalInterviewSession.session_id (DB PK)
        "application_id":  <int>
        "candidate_name":  <str>
        "job_title":       <str>
        "requisition_id":  <int>
    }

Pipeline on connect:
    1.  Greeting  — "Hello <name>, I am your AI interviewer for <role>…"
    2.  Warm-up   — one soft-skill question
    3.  Q1 → Q5   — strict technical questions from FocusedInterviewerPersona
    4.  Closing   — exact farewell phrase → call end_interview()

Silence logic:
    • Tracks the last time any speech (agent or candidate) was committed.
    • If the candidate is silent for > SILENCE_NUDGE_SECONDS, sends a gentle prompt.
    • Timer resets on every speech event so the nudge never fires mid-question.

DB writes:
    • preliminary: overall_score=0.0, status="Active", started_at set at room join.
    • final:       overall_score=<calculated>, summary, transcript, status="Completed".
"""

import asyncio
import json
import logging
import os
import time
from datetime import datetime
from typing import Optional

from livekit.agents import (
    Agent,
    AgentSession,
    AutoSubscribe,
    JobContext,
    WorkerOptions,
    cli,
    function_tool,
)
from livekit.agents.voice.room_io import RoomOptions
from livekit.plugins import openai as lk_openai
from livekit.plugins import silero

from database.connection import SessionLocal
from models.db.hr_interview_session import HRInterviewSession
from models.db.technical_interview_session import TechnicalInterviewSession

logger = logging.getLogger(__name__)

# ── Constants ─────────────────────────────────────────────────────────────────

SILENCE_NUDGE_SECONDS = 25          # candidate silence threshold before nudge
SILENCE_CHECK_INTERVAL = 5         # watchdog tick (seconds)
POST_CLOSE_GRACE_SECONDS = 3       # wait after TTS before disconnecting
STARTUP_GRACE_SECONDS   = 40       # silence watchdog is inactive for this long at startup


# ── System prompt builder ─────────────────────────────────────────────────────

def _build_system_prompt(
    candidate_name: str,
    job_title: str,
    questions: list[str],
) -> str:
    numbered = "\n".join(f"  Q{i+1}: {q}" for i, q in enumerate(questions))
    return f"""You are a professional AI technical interviewer conducting a structured \
LIVE VOICE interview for the {job_title} role at a leading technology company.

IMPORTANT: This is a real-time AUDIO/VOICE conversation. The candidate SPEAKS their \
answers aloud — you will hear their voice through the microphone. Do NOT ask them to \
type, write, or submit anything. Simply ask your question and wait quietly for them to speak.

You are speaking with: {candidate_name}

━━━  STRICT INTERVIEW STRUCTURE  ━━━

STEP 1 — GREETING (once, at the very start):
  "Hello {candidate_name}, I am your AI interviewer for the {job_title} role. \
It's great to have you here today."

STEP 2 — WARM-UP (one question, before any technical questions):
  Ask: "Before we dive in, could you briefly tell me about a project you're \
particularly proud of, and what your specific contribution was?"
  After the candidate answers, acknowledge with a short encouraging remark \
(e.g., "That sounds like a great experience, thank you for sharing."), \
then transition: "Let's move on to the technical part."

STEP 3 — TECHNICAL QUESTIONS (exactly 5, in the order shown, one at a time):
{numbered}

  Protocol for each question:
  - State the question clearly, then wait silently for the full answer.
  - Once the answer is complete, call `record_answer_score` with the question \
number (1-5), your score (0-100), and one-sentence rationale.
  - Then ask the next question.
  - NEVER repeat a question already asked.
  - NEVER skip to a later question before the current one is answered.
  - NEVER ask more than 5 technical questions.

STEP 4 — CLOSING (after Q5 answer + `record_answer_score(5, …)` call):
  Say EXACTLY this phrase (word-for-word):
  "Thank you for your time, {candidate_name}. We have finished the technical \
part. Your interview is now being submitted for final evaluation. \
Have a great day, goodbye!"
  Immediately call `end_interview` after delivering this phrase.

━━━  BEHAVIOURAL RULES  ━━━
• Be professional, warm, and encouraging throughout.
• Do NOT reveal scores, rankings, or comparisons to the candidate.
• Do NOT ask follow-up questions beyond clarifying pauses.
• If the candidate says "end the interview", "stop the interview", "I want to finish", \
or anything that clearly requests ending the session, immediately deliver the closing \
farewell phrase and call end_interview() — do not continue with remaining questions.
• If the candidate is silent, simply wait — an automated nudge will be sent after \
a period of silence. You do not need to handle silence yourself.
• Keep transitions between questions smooth and natural.
"""


# ── Agent class ───────────────────────────────────────────────────────────────

class RecruitmentInterviewAgent(Agent):
    """
    Strict 5-question AI interviewer.
    Manages per-answer mini-scores, silence watchdog, and DB persistence.
    """

    def __init__(
        self,
        db_session_id: int,
        candidate_name: str,
        job_title: str,
        questions: list[str],
        requisition_id: int,
        ctx: JobContext,
        mode: str = "technical",
        job_responsibilities: str = "",
    ) -> None:
        self._db_session_id = db_session_id
        self._candidate_name = candidate_name
        self._job_title = job_title
        self._questions = questions
        self._requisition_id = requisition_id
        self._ctx = ctx
        self._mode = mode
        self._job_responsibilities = job_responsibilities

        self._mini_scores: list[float] = []
        self._transcript_parts: list[str] = []
        self._interview_done = False
        # Pre-offset so the watchdog never fires during the startup greeting.
        # Effective first-nudge window starts STARTUP_GRACE_SECONDS after __init__.
        self._last_speech_time: float = time.monotonic() + STARTUP_GRACE_SECONDS

        prompt_builder = _build_hr_system_prompt if mode == "hr" else _build_system_prompt
        super().__init__(
            instructions=prompt_builder(candidate_name, job_title, questions),
        )

    # ── Function tools (callable by the LLM during conversation) ─────────────

    @function_tool
    async def record_answer_score(
        self,
        question_number: int,
        score: float,
        rationale: str,
    ) -> str:
        """
        Record a per-answer score for a technical question.
        Call this immediately after the candidate finishes answering each
        technical question, before moving on to the next one.

        Args:
            question_number: Integer 1 through 5.
            score: Numeric score from 0.0 (no answer) to 100.0 (perfect).
            rationale: One sentence explaining the score (not shared with candidate).
        """
        clamped = max(0.0, min(100.0, float(score)))
        self._mini_scores.append(clamped)
        logger.info(
            "[agent] Q%d scored %.1f — %s", question_number, clamped, rationale
        )
        self._last_speech_time = time.monotonic()   # prevent spurious nudge

        # Safety net: if all 5 questions are scored and LLM hasn't called
        # end_interview() yet, auto-trigger it after 25 s (enough time for
        # the LLM to deliver the farewell phrase via TTS).
        if len(self._mini_scores) >= 5 and not self._interview_done:
            asyncio.create_task(
                self._auto_end_after_closing(),
                name="auto-end-watchdog",
            )

        return f"Score {clamped:.1f}/100 recorded for Q{question_number}."

    @function_tool
    async def end_interview(self) -> str:
        """
        Finalise the interview: compute overall score, persist results to the
        database, then disconnect from the LiveKit room.
        Call this ONLY after delivering the closing farewell message and after
        calling record_answer_score for all 5 questions.
        """
        if self._interview_done:
            return "Interview already finalised."

        self._interview_done = True

        overall = (
            round(sum(self._mini_scores) / len(self._mini_scores), 2)
            if self._mini_scores
            else 0.0
        )
        transcript = "\n\n".join(self._transcript_parts)
        summary = self._build_summary(overall)

        try:
            if self._mode == "hr":
                await asyncio.to_thread(
                    _persist_completion_hr,
                    db_session_id=self._db_session_id,
                    overall_score=overall,
                    summary=summary,
                    transcript=transcript,
                    requisition_id=self._requisition_id,
                    job_title=self._job_title,
                    job_responsibilities=self._job_responsibilities,
                    candidate_name=self._candidate_name,
                )
            else:
                await asyncio.to_thread(
                    _persist_completion,
                    db_session_id=self._db_session_id,
                    overall_score=overall,
                    summary=summary,
                    transcript=transcript,
                    requisition_id=self._requisition_id,
                    job_title=self._job_title,
                    job_responsibilities=self._job_responsibilities,
                    candidate_name=self._candidate_name,
                )
        except Exception:
            logger.exception(
                "[agent] Persistence failed for session %d — disconnecting anyway.",
                self._db_session_id,
            )

        logger.info(
            "[agent] Interview finalised — session=%d  score=%.2f",
            self._db_session_id,
            overall,
        )

        # Allow TTS to finish delivering the farewell phrase, then close the room.
        await asyncio.sleep(POST_CLOSE_GRACE_SECONDS)
        try:
            await self._ctx.room.disconnect()
        except Exception as exc:
            logger.debug("[agent] Room disconnect after interview: %s", exc)

        return "Interview finalised and results persisted."

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    async def on_enter(self) -> None:
        """
        Called by the framework when this agent enters an AgentSession.
        Only starts the silence watchdog here — greeting is triggered by the
        entrypoint once the candidate's participant is confirmed in the room.
        """
        asyncio.create_task(self._silence_watchdog(), name="silence-watchdog")
        logger.info(
            "[agent] Entered session for candidate '%s' (session_id=%d).",
            self._candidate_name,
            self._db_session_id,
        )

    async def greet_and_start(self) -> None:
        """Deliver the opening greeting then hand off to the LLM."""
        await asyncio.sleep(0.8)  # let the audio track subscription settle
        try:
            await self.session.say(
                f"Hello {self._candidate_name}, I am your AI interviewer for the "
                f"{self._job_title} role. It's great to have you here today.",
                allow_interruptions=False,
            )
            self.session.generate_reply(
                instructions=(
                    "The greeting has been delivered. Now ask the warm-up question "
                    "exactly as specified in your instructions, then proceed through "
                    "the 5 technical questions."
                )
            )
        except Exception as exc:
            logger.error("[agent] Failed to deliver greeting for session %d: %s",
                         self._db_session_id, exc)

    # ── Internal helpers ──────────────────────────────────────────────────────

    async def _auto_end_after_closing(self) -> None:
        """
        Safety net — runs after all 5 answers are scored.
        Gives the LLM 25 seconds to deliver the farewell phrase and call
        end_interview() on its own.  If it hasn't by then, we call it here
        so the room never stays open indefinitely after the last question.
        """
        await asyncio.sleep(25)
        if not self._interview_done:
            logger.warning(
                "[agent] LLM did not call end_interview() within 25 s of Q5 — "
                "auto-terminating session %d.",
                self._db_session_id,
            )
            await self.end_interview()

    async def _silence_watchdog(self) -> None:
        """
        Ticks every SILENCE_CHECK_INTERVAL seconds.
        If the candidate has been silent for > SILENCE_NUDGE_SECONDS
        (and the interview is still active), injects a gentle nudge via TTS.
        """
        while not self._interview_done:
            await asyncio.sleep(SILENCE_CHECK_INTERVAL)
            if self._interview_done:
                break
            elapsed = time.monotonic() - self._last_speech_time
            if elapsed >= SILENCE_NUDGE_SECONDS:
                try:
                    await self.session.say(
                        "Are you still there? Take your time — "
                        "would you like me to repeat the question?",
                        allow_interruptions=True,
                    )
                except Exception as exc:
                    logger.debug("[agent] Silence nudge suppressed: %s", exc)
                # Reset timer so we don't spam every tick
                self._last_speech_time = time.monotonic()

    def _build_summary(self, overall: float) -> str:
        count = len(self._mini_scores)
        tier = (
            "excellent" if overall >= 80
            else "good" if overall >= 65
            else "satisfactory" if overall >= 50
            else "below expectations"
        )
        scores_str = ", ".join(f"Q{i+1}={s:.0f}" for i, s in enumerate(self._mini_scores))
        return (
            f"{self._candidate_name} completed {count}/5 technical questions for "
            f"the {self._job_title} role with an overall score of "
            f"{overall:.1f}/100 ({tier} performance). "
            f"Per-question scores: [{scores_str}]."
        )


# ── Agent worker entrypoint ───────────────────────────────────────────────────

async def entrypoint(ctx: JobContext) -> None:
    """
    Called by livekit-agents when a job is dispatched to this worker.
    Each interview room gets one invocation of this function.
    """
    logger.info("[agent-worker] Job dispatched — room: %s", ctx.room.name)
    await ctx.connect(auto_subscribe=AutoSubscribe.AUDIO_ONLY)

    # ── Parse room metadata ───────────────────────────────────────────────────
    metadata: dict = {}
    if ctx.room.metadata:
        try:
            metadata = json.loads(ctx.room.metadata)
        except json.JSONDecodeError:
            logger.error(
                "[agent-worker] Malformed room metadata JSON for room %s.",
                ctx.room.name,
            )

    db_session_id: Optional[int] = metadata.get("session_id")
    application_id: Optional[int] = metadata.get("application_id")
    candidate_name: str = metadata.get("candidate_name", "Candidate")
    job_title: str = metadata.get("job_title", "the position")
    requisition_id: int = metadata.get("requisition_id", 0)
    mode: str = metadata.get("mode", "technical")
    job_responsibilities: str = metadata.get("job_responsibilities", "")

    if not db_session_id:
        logger.error(
            "[agent-worker] No session_id in room metadata for room %s — aborting.",
            ctx.room.name,
        )
        return

    # ── Load questions based on mode ──────────────────────────────────────────
    if mode == "hr":
        questions = await asyncio.to_thread(_load_hr_questions_sync, application_id, job_title)
    else:
        questions = await asyncio.to_thread(_load_questions_sync, application_id, job_title)

    logger.info("[agent-worker] mode=%s | session=%d | %d questions loaded.", mode, db_session_id, len(questions))

    # ── Build agent + session ─────────────────────────────────────────────────
    agent = RecruitmentInterviewAgent(
        db_session_id=db_session_id,
        candidate_name=candidate_name,
        job_title=job_title,
        questions=questions,
        requisition_id=requisition_id,
        ctx=ctx,
        mode=mode,
        job_responsibilities=job_responsibilities,
    )

    session = AgentSession(
        stt=lk_openai.STT(model="whisper-1"),
        llm=lk_openai.LLM(model="gpt-4o", temperature=0.2),
        tts=lk_openai.TTS(voice="alloy"),
        vad=silero.VAD.load(
            min_silence_duration=0.6,
            activation_threshold=0.5,
        ),
    )

    # ── Track speech for silence watchdog ─────────────────────────────────────
    _END_PHRASES = frozenset({
        "end the interview", "end interview", "stop the interview",
        "stop interview", "finish the interview", "finish interview",
        "i want to end", "i want to stop", "i want to finish",
        "that's all", "thats all",
    })

    @session.on("user_speech_committed")
    def _on_user_speech(event) -> None:
        agent._last_speech_time = time.monotonic()
        text = getattr(event, "transcript", "") or ""
        if not text.strip():
            return
        agent._transcript_parts.append(f"Candidate: {text.strip()}")
        # Programmatic end — if the LLM doesn't react in time, force the close.
        lower = text.lower().strip()
        if any(phrase in lower for phrase in _END_PHRASES) and not agent._interview_done:
            logger.info(
                "[agent] Candidate requested end via speech — force-ending session %d.",
                agent._db_session_id,
            )
            asyncio.create_task(agent.end_interview(), name="force-end-interview")

    @session.on("agent_speech_committed")
    def _on_agent_speech(event) -> None:
        agent._last_speech_time = time.monotonic()
        text = getattr(event, "text", "") or ""
        if text.strip():
            agent._transcript_parts.append(f"Interviewer: {text.strip()}")

    # ── Greeting gate — fires exactly once when the candidate joins ───────────
    # LiveKit audio is not buffered: if the agent speaks before the candidate's
    # subscription is ready, the audio is lost.  We trigger the greeting only
    # when we have confirmed a candidate participant in the room.
    _greeted = False

    def _maybe_greet(participant) -> None:
        nonlocal _greeted
        if _greeted:
            return
        if not participant.identity.startswith("candidate_"):
            return
        _greeted = True
        logger.info(
            "[agent-worker] Candidate '%s' confirmed in room — delivering greeting.",
            participant.identity,
        )
        asyncio.create_task(agent.greet_and_start(), name="greeting")

    # ── Candidate-disconnect handler ──────────────────────────────────────────
    @ctx.room.on("participant_disconnected")
    def _on_participant_disconnected(participant) -> None:
        if not participant.identity.startswith("candidate_"):
            return
        if not agent._interview_done:
            asyncio.create_task(
                _handle_candidate_disconnect(ctx, agent),
                name="candidate-disconnect",
            )

    await session.start(
        agent,
        room=ctx.room,
        room_options=RoomOptions(audio_input=True, audio_output=True),
    )

    # ── Trigger greeting ──────────────────────────────────────────────────────
    # Dump all participants visible right now for diagnostics
    all_remote = list(ctx.room.remote_participants.values())
    logger.info(
        "[agent-worker] After session.start — remote participants: %s",
        [p.identity for p in all_remote],
    )

    # Case 1: candidate already in room when session starts
    for p in all_remote:
        _maybe_greet(p)

    # Case 2: candidate joins after session starts
    ctx.room.on("participant_connected")(_maybe_greet)

    # Fallback: poll every 1 s for up to 30 s in case participant_connected
    # event was missed or candidate was in mid-connect during session.start()
    async def _greeting_watchdog() -> None:
        for _ in range(30):
            await asyncio.sleep(1.0)
            if _greeted:
                return
            current = list(ctx.room.remote_participants.values())
            logger.info(
                "[agent-worker] Greeting watchdog tick — remote participants: %s",
                [p.identity for p in current],
            )
            for p in current:
                _maybe_greet(p)
            if _greeted:
                return
        if not _greeted:
            logger.warning(
                "[agent-worker] No candidate joined after 30 s — forcing greeting for session %d.",
                db_session_id,
            )
            asyncio.create_task(agent.greet_and_start(), name="greeting-forced")

    asyncio.create_task(_greeting_watchdog(), name="greeting-watchdog")

    await session.wait_for_inactive()


async def _handle_candidate_disconnect(ctx: JobContext, agent: "RecruitmentInterviewAgent") -> None:
    """
    Wait 15 s after the candidate drops — long enough for a transient reconnect.
    If they haven't come back and the interview isn't done, mark No-show and close.
    """
    await asyncio.sleep(15)
    if agent._interview_done:
        return

    # Check if any candidate participant is still present
    still_present = any(
        p.identity.startswith("candidate_")
        for p in ctx.room.remote_participants.values()
    )
    if still_present:
        return

    logger.warning(
        "[agent] Candidate did not reconnect within 15 s — marking No-show "
        "for session %d and closing room.",
        agent._db_session_id,
    )
    agent._interview_done = True  # stop silence watchdog and auto-end

    persist_no_show_fn = _persist_no_show_hr if agent._mode == "hr" else _persist_no_show
    await asyncio.to_thread(
        persist_no_show_fn,
        db_session_id=agent._db_session_id,
        requisition_id=agent._requisition_id,
    )
    try:
        await ctx.room.disconnect()
    except Exception as exc:
        logger.debug("[agent] Room disconnect after no-show: %s", exc)


# ── Helper: load personalised questions ──────────────────────────────────────

def _load_questions_sync(
    application_id: Optional[int],
    job_title: str,
) -> list[str]:
    """
    Synchronous helper (runs in a thread via asyncio.to_thread).

    Strategy (zero LLM calls):
    1. Load candidate gaps + JR knowledge gaps + project info from DB
    2. Use knowledge_base_service to build 5 gap-targeted questions via
       DB embeddings + MiniLM cosine retrieval; logs selections to jr_question_selections
    3. Falls back to FocusedInterviewerPersona templates if retrieval fails
    4. Falls back to generic questions if persona fails
    """
    if not application_id:
        return _generic_questions(job_title)

    # ── Attempt 1: knowledge base DB retrieval (no LLM) ──────────────────────
    try:
        from services.knowledge_base_service import (
            load_candidate_gaps_sync,
            get_all_five_questions,
        )
        gap_data  = load_candidate_gaps_sync(application_id)
        questions = get_all_five_questions(
            gaps=gap_data["gaps"],
            jr_knowledge_gaps=gap_data["jr_knowledge_gaps"],
            project_name=gap_data["project_name"],
            main_tech=gap_data["main_tech"],
            job_title=gap_data["job_title"] or job_title,
            application_id=application_id,
            requisition_id=gap_data.get("requisition_id"),
        )
        logger.info(
            "[agent-worker] Loaded %d DB-targeted questions for app %d.",
            len(questions), application_id,
        )
        return questions
    except Exception as exc:
        logger.warning(
            "[agent-worker] knowledge_base_service failed (%s) — falling back to persona.", exc
        )

    # ── Attempt 2: FocusedInterviewerPersona templates ───────────────────────
    try:
        from services.focused_interviewer_persona import FocusedInterviewerPersona
        import asyncio

        persona = FocusedInterviewerPersona()
        loop = asyncio.new_event_loop()
        try:
            meta = loop.run_until_complete(
                persona.load_focused_interview_metadata(application_id)
            )
        finally:
            loop.close()

        return [
            persona.generate_round_1_question(meta),
            persona.generate_round_2_question(meta, ""),
            persona.generate_round_3_question(meta),
            persona.generate_round_4_question(meta),
            persona.generate_round_5_question(meta),
        ]
    except Exception as exc:
        logger.warning(
            "[agent-worker] Persona fallback also failed (%s) — using generics.", exc
        )
        return _generic_questions(job_title)


def _generic_questions(job_title: str) -> list[str]:
    return [
        "Describe the most complex system you have designed. What were the main architectural trade-offs?",
        "How have you identified and resolved performance bottlenecks in a production environment?",
        "Walk me through how you approach debugging an issue that cannot be reproduced locally.",
        "Tell me about a time you disagreed with a technical decision. How did you handle it?",
        "How do you ensure long-term code quality and reliability in a team environment?",
    ]


# ── HR mode: system prompt and questions ─────────────────────────────────────

def _build_hr_system_prompt(
    candidate_name: str,
    job_title: str,
    questions: list[str],
) -> str:
    numbered = "\n".join(f"  Q{i+1}: {q}" for i, q in enumerate(questions))
    return f"""You are a warm, professional AI HR interviewer conducting a structured \
LIVE VOICE behavioral interview for the {job_title} role.

IMPORTANT: This is a real-time AUDIO/VOICE conversation. The candidate SPEAKS their \
answers aloud — you will hear their voice through the microphone. Do NOT ask them to \
type, write, or submit anything. Simply ask your question and wait quietly for them to speak.

You are speaking with: {candidate_name}

━━━  STRICT INTERVIEW STRUCTURE  ━━━

STEP 1 — GREETING (once, at the very start):
  "Hello {candidate_name}, I'm your HR interviewer for the {job_title} position. \
It's wonderful to have you here today. I'll be asking you a few questions about your \
experiences and working style."

STEP 2 — WARM-UP (one question):
  Ask: "To start, could you briefly tell me about yourself and what drew you to this role?"
  Acknowledge their answer warmly (e.g., "That's great to hear, thank you."), \
then transition: "Let's move into some situational questions."

STEP 3 — BEHAVIORAL QUESTIONS (exactly 5, in the order shown, one at a time):
{numbered}

  Protocol for each question:
  - Ask the question clearly and wait for the full answer.
  - Encourage specific examples if the answer is vague: \
"Could you walk me through a specific situation where that happened?"
  - Once they finish, call `record_answer_score` with the question number (1–5), \
your score (0–100), and a one-sentence rationale.
  - Score based on: specificity of the situation, clarity of their actions, \
self-awareness, communication quality, and reflection on outcomes.
  - Then ask the next question.
  - NEVER skip or repeat questions.

STEP 4 — CLOSING (after Q5 answer + `record_answer_score(5, …)` call):
  Say EXACTLY this phrase:
  "Thank you so much for your time, {candidate_name}. That concludes our HR interview. \
We'll review everything and be in touch with next steps very soon. \
Have a wonderful day, goodbye!"
  Immediately call `end_interview` after delivering this phrase.

━━━  BEHAVIOURAL RULES  ━━━
• Be warm, empathetic, and encouraging throughout.
• Focus exclusively on soft skills: communication, teamwork, leadership, \
adaptability, initiative, and problem-solving.
• Do NOT ask any technical questions.
• Do NOT reveal scores, decisions, or comparisons to the candidate.
• If the candidate says "end the interview", "stop the interview", "I want to finish", \
or anything that clearly requests ending the session, immediately deliver the closing \
farewell phrase and call end_interview() — do not continue with remaining questions.
• If the candidate is silent, simply wait — an automated nudge will be sent after \
a period of silence. You do not need to handle silence yourself.
"""


def _hr_generic_questions(job_title: str) -> list[str]:
    return [
        "Tell me about a time you faced a significant challenge at work. \
How did you approach it and what was the outcome?",
        "Describe a situation where you had to collaborate closely with a difficult \
team member. How did you handle it and what did you learn?",
        "Give me an example of a time you had to adapt quickly to a major unexpected \
change. What did you do and what was the result?",
        "Tell me about a time you took initiative to improve a process or solve a \
problem without being asked. What motivated you and what impact did it have?",
        "Describe a situation where you had to manage multiple competing priorities \
under pressure. How did you stay organised and what was the outcome?",
    ]


def _load_hr_questions_sync(application_id: Optional[int], job_title: str) -> list[str]:
    """Returns behavioral HR questions. Extendable to load personalised questions from DB."""
    return _hr_generic_questions(job_title)


# ── Transcript quality assessment ─────────────────────────────────────────────

def _assess_transcript_quality(transcript: str) -> dict:
    """
    Compute simple quality signals from the raw transcript.

    Returns:
        turn_count        — number of non-empty candidate turns detected
        avg_words_per_turn — mean word count across candidate turns
        quality_flag      — 'ok' | 'low_quality' | 'very_low_quality'
        quality_note      — human-readable note appended to session summary
    """
    if not transcript or not transcript.strip():
        return {
            "turn_count": 0,
            "avg_words_per_turn": 0.0,
            "quality_flag": "very_low_quality",
            "quality_note": "[Quality: no transcript captured]",
        }

    import re
    # Try labeled format first ("Candidate: ..." lines)
    candidate_pattern = re.compile(
        r"(?:candidate|applicant|interviewee)\s*:\s*(.+?)(?=\n(?:interviewer|hr|recruiter|agent)\s*:|$)",
        re.IGNORECASE | re.DOTALL,
    )
    turns = [m.strip() for m in candidate_pattern.findall(transcript) if m.strip()]
    if not turns:
        # Fallback: every other non-empty line (assume interviewer speaks first)
        lines = [l.strip() for l in transcript.split("\n") if l.strip()]
        turns = [lines[i] for i in range(1, len(lines), 2)]

    turn_count = len(turns)
    if turn_count == 0:
        avg_words = 0.0
    else:
        avg_words = round(sum(len(t.split()) for t in turns) / turn_count, 1)

    if turn_count < 2 or avg_words < 10:
        flag = "very_low_quality"
        note = (
            f"[Quality: very_low — {turn_count} candidate turns, "
            f"{avg_words:.0f} words/turn avg. Scores may be unreliable.]"
        )
    elif turn_count < 4 or avg_words < 20:
        flag = "low_quality"
        note = (
            f"[Quality: low — {turn_count} candidate turns, "
            f"{avg_words:.0f} words/turn avg. Consider manual review.]"
        )
    else:
        flag = "ok"
        note = f"[Quality: ok — {turn_count} turns, {avg_words:.0f} words/turn avg]"

    return {
        "turn_count": turn_count,
        "avg_words_per_turn": avg_words,
        "quality_flag": flag,
        "quality_note": note,
    }


# ── Helper: persist final results to DB ──────────────────────────────────────

def _persist_completion(
    db_session_id: int,
    overall_score: float,
    summary: str,
    transcript: str,
    requisition_id: int,
    job_title: str = "",
    job_responsibilities: str = "",
    candidate_name: str = "Candidate",
) -> None:
    """
    Updates TechnicalInterviewSession to Completed.
    Runs 4-model ensemble scoring on the transcript; the ensemble composite
    score (0-100) replaces the LLM-reported overall_score so grading is not
    dependent on the LLM's self-assessment.
    """
    db = SessionLocal()
    try:
        row: Optional[TechnicalInterviewSession] = (
            db.query(TechnicalInterviewSession)
            .filter(TechnicalInterviewSession.session_id == db_session_id)
            .first()
        )
        if not row:
            logger.error(
                "[agent-worker] TechnicalInterviewSession %d not found — cannot persist.",
                db_session_id,
            )
            return

        # ── Run 4-model ensemble scoring ──────────────────────────────────────
        final_score = overall_score  # LLM score as fallback
        try:
            from services.tech_analysis_service import score_transcript as tech_score_transcript
            tech_scores = tech_score_transcript(
                transcript=transcript,
                job_title=job_title,
                job_responsibilities=job_responsibilities,
                candidate_name=candidate_name,
            )
            ensemble_score = tech_scores.get("overall_score_100", overall_score)
            final_score = ensemble_score  # ensemble overrides LLM score
            row.codebert_score        = tech_scores.get("codebert_score")
            row.roberta_depth_score   = tech_scores.get("roberta_depth_score")
            row.nli_technical_score   = tech_scores.get("nli_technical_score")
            row.tfidf_technical_score = tech_scores.get("tfidf_technical_score")
            row.shap_json             = tech_scores.get("shap_json")
            row.shap_summary          = tech_scores.get("shap_summary")
            logger.info(
                "[agent-worker] Tech ensemble score=%.2f (LLM was %.2f) for session %d.",
                ensemble_score, overall_score, db_session_id,
            )
        except Exception as exc:
            logger.warning(
                "[agent-worker] Tech ensemble scoring failed — falling back to LLM score: %s", exc
            )

        # ── Transcript quality assessment ─────────────────────────────────────
        quality = _assess_transcript_quality(transcript)
        if quality["quality_flag"] != "ok":
            logger.warning(
                "[agent-worker] Low-quality transcript for session %d: %s",
                db_session_id, quality["quality_note"],
            )
        full_summary = f"{summary}\n{quality['quality_note']}"

        row.status = "Completed"
        row.overall_score = final_score
        row.overall_performance = _tier_label(final_score)
        row.recommendation = _recommendation(final_score)
        row.summary = full_summary
        row.transcript = transcript
        row.ended_at = datetime.utcnow()
        db.commit()

        logger.info(
            "[agent-worker] Persisted Completed for session %d (score=%.2f, quality=%s).",
            db_session_id, final_score, quality["quality_flag"],
        )

    except Exception:
        db.rollback()
        logger.exception(
            "[agent-worker] DB error while persisting session %d.", db_session_id
        )
        return
    finally:
        db.close()

    # Generate full technical report (no LLM)
    try:
        from services.report_generation_service import generate_technical_report
        generate_technical_report(db_session_id)
    except Exception as exc:
        logger.warning("[agent-worker] Technical report generation failed: %s", exc)

    # Fire final-ranking check asynchronously so DB write isn't blocked
    if requisition_id:
        try:
            from tasks.interview_tasks import maybe_dispatch_final_ranking
            maybe_dispatch_final_ranking.delay(requisition_id)
        except Exception as exc:
            logger.warning(
                "[agent-worker] Could not dispatch final ranking for JR %d: %s",
                requisition_id,
                exc,
            )


def _persist_no_show(db_session_id: int, requisition_id: int) -> None:
    """Mark the session No-show when the candidate abandons the room mid-interview."""
    db = SessionLocal()
    try:
        row: Optional[TechnicalInterviewSession] = (
            db.query(TechnicalInterviewSession)
            .filter(TechnicalInterviewSession.session_id == db_session_id)
            .first()
        )
        if not row:
            return
        # Only update if the interview hasn't been completed/cancelled already
        if row.status in ("Completed", "No-show", "Cancelled"):
            return
        row.status = "No-show"
        row.overall_score = 0.0
        row.ended_at = datetime.utcnow()
        row.summary = "Candidate disconnected before completing the interview."
        db.commit()
        logger.info("[agent-worker] Session %d marked No-show.", db_session_id)
    except Exception:
        db.rollback()
        logger.exception("[agent-worker] DB error marking no-show for session %d.", db_session_id)
    finally:
        db.close()

    if requisition_id:
        try:
            from tasks.interview_tasks import maybe_dispatch_final_ranking
            maybe_dispatch_final_ranking.delay(requisition_id)
        except Exception as exc:
            logger.warning(
                "[agent-worker] Could not dispatch final ranking after no-show (JR %d): %s",
                requisition_id, exc,
            )


def _persist_completion_hr(
    db_session_id: int,
    overall_score: float,
    summary: str,
    transcript: str,
    requisition_id: int,
    job_title: str,
    job_responsibilities: str,
    candidate_name: str,
) -> None:
    """Updates HRInterviewSession to Completed, runs ensemble scoring, triggers final ranking."""
    db = SessionLocal()
    try:
        row: Optional[HRInterviewSession] = (
            db.query(HRInterviewSession)
            .filter(HRInterviewSession.session_id == db_session_id)
            .first()
        )
        if not row:
            logger.error("[agent-worker] HRInterviewSession %d not found — cannot persist.", db_session_id)
            return

        # ── Transcript quality assessment ─────────────────────────────────────
        quality = _assess_transcript_quality(transcript)
        if quality["quality_flag"] != "ok":
            logger.warning(
                "[agent-worker] Low-quality HR transcript for session %d: %s",
                db_session_id, quality["quality_note"],
            )
        full_summary = f"{summary}\n{quality['quality_note']}"

        row.status = "Completed"
        row.summary = full_summary
        row.transcript = transcript
        row.ended_at = datetime.utcnow()
        db.commit()

        logger.info(
            "[agent-worker] HR session %d base fields persisted (quality=%s).",
            db_session_id, quality["quality_flag"],
        )

        # ── 4-model ensemble scoring (replaces LLM overall_score) ────────────
        final_hr_score = overall_score  # LLM fallback
        try:
            from services.hr_analysis_service import score_transcript
            scores = score_transcript(
                transcript=transcript,
                job_title=job_title,
                job_responsibilities=job_responsibilities,
                candidate_name=candidate_name,
            )
            ensemble_composite = scores.get("composite_score", 0.0)
            final_hr_score = round(ensemble_composite * 100, 2)  # 0-100

            row.emotion_score        = scores.get("emotion_score")
            row.sentiment_score      = scores.get("sentiment_score")
            row.nli_align_score      = scores.get("nli_align_score")
            row.semantic_depth_score = scores.get("semantic_depth_score")
            row.shap_json            = scores.get("shap_json")
            row.shap_summary         = scores.get("shap_summary")
            logger.info(
                "[agent-worker] HR ensemble score=%.2f (LLM was %.2f) for session %d.",
                final_hr_score, overall_score, db_session_id,
            )
        except Exception as exc:
            logger.warning(
                "[agent-worker] HR ensemble scoring failed for session %d — keeping LLM score: %s",
                db_session_id, exc,
            )

        row.overall_score       = final_hr_score
        row.overall_performance = _tier_label(final_hr_score)
        row.recommendation      = _recommendation(final_hr_score)
        db.commit()
        logger.info(
            "[agent-worker] HR session %d final score=%.2f (%s).",
            db_session_id, final_hr_score, _tier_label(final_hr_score),
        )

    except Exception:
        db.rollback()
        logger.exception("[agent-worker] DB error while persisting HR session %d.", db_session_id)
        return
    finally:
        db.close()

    # Generate full HR report (no LLM)
    try:
        from services.report_generation_service import generate_hr_report
        generate_hr_report(db_session_id)
    except Exception as exc:
        logger.warning("[agent-worker] HR report generation failed: %s", exc)

    if requisition_id:
        try:
            from tasks.interview_tasks import maybe_dispatch_final_ranking_after_hr
            maybe_dispatch_final_ranking_after_hr.delay(requisition_id)
        except Exception as exc:
            logger.warning("[agent-worker] Could not dispatch final ranking after HR (JR %d): %s", requisition_id, exc)


def _persist_no_show_hr(db_session_id: int, requisition_id: int) -> None:
    """Mark the HR session No-show when the candidate abandons mid-interview."""
    db = SessionLocal()
    try:
        row: Optional[HRInterviewSession] = (
            db.query(HRInterviewSession)
            .filter(HRInterviewSession.session_id == db_session_id)
            .first()
        )
        if not row:
            return
        if row.status in ("Completed", "No-show", "Cancelled"):
            return
        row.status = "No-show"
        row.overall_score = 0.0
        row.ended_at = datetime.utcnow()
        row.summary = "Candidate disconnected before completing the HR interview."
        db.commit()
        logger.info("[agent-worker] HR session %d marked No-show.", db_session_id)
    except Exception:
        db.rollback()
        logger.exception("[agent-worker] DB error marking no-show for HR session %d.", db_session_id)
    finally:
        db.close()

    if requisition_id:
        try:
            from tasks.interview_tasks import maybe_dispatch_final_ranking_after_hr
            maybe_dispatch_final_ranking_after_hr.delay(requisition_id)
        except Exception as exc:
            logger.warning("[agent-worker] Could not dispatch final ranking after HR no-show (JR %d): %s", requisition_id, exc)


def _tier_label(score: float) -> str:
    if score >= 80:
        return "Excellent"
    if score >= 65:
        return "Good"
    if score >= 50:
        return "Satisfactory"
    return "Below Expectations"


def _recommendation(score: float) -> str:
    if score >= 80:
        return "Strongly Recommended"
    if score >= 65:
        return "Recommended"
    if score >= 50:
        return "Consider with Reservations"
    return "Not Recommended"


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    cli.run_app(
        WorkerOptions(
            entrypoint_fnc=entrypoint,
            # agent_name must match LIVEKIT_AGENT_NAME in config / dispatch call
            agent_name=os.getenv("LIVEKIT_AGENT_NAME", "interview-agent"),
        )
    )
