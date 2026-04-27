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
    JobContext,
    WorkerOptions,
    cli,
    function_tool,
)
from livekit.plugins import openai as lk_openai
from livekit.plugins import silero

from database.connection import SessionLocal
from models.db.technical_interview_session import TechnicalInterviewSession

logger = logging.getLogger(__name__)

# ── Constants ─────────────────────────────────────────────────────────────────

SILENCE_NUDGE_SECONDS = 10          # candidate silence threshold before nudge
SILENCE_CHECK_INTERVAL = 3         # watchdog tick (seconds)
POST_CLOSE_GRACE_SECONDS = 3       # wait after TTS before disconnecting


# ── System prompt builder ─────────────────────────────────────────────────────

def _build_system_prompt(
    candidate_name: str,
    job_title: str,
    questions: list[str],
) -> str:
    numbered = "\n".join(f"  Q{i+1}: {q}" for i, q in enumerate(questions))
    return f"""You are a professional AI technical interviewer conducting a structured \
interview for the {job_title} role at a leading technology company.

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
• If the candidate is silent for more than 10 seconds, they will receive an \
automated prompt — you do not need to handle silence yourself.
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
    ) -> None:
        self._db_session_id = db_session_id
        self._candidate_name = candidate_name
        self._job_title = job_title
        self._questions = questions
        self._requisition_id = requisition_id
        self._ctx = ctx                          # used for server-side disconnect

        self._mini_scores: list[float] = []
        self._transcript_parts: list[str] = []   # ["Agent: ...", "Candidate: ..."]
        self._interview_done = False
        self._last_speech_time: float = time.monotonic()

        super().__init__(
            instructions=_build_system_prompt(candidate_name, job_title, questions),
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

        # Run the DB write in a thread so the event loop stays responsive
        # while SQLAlchemy commits.  Disconnect still fires even if persist fails.
        try:
            await asyncio.to_thread(
                _persist_completion,
                db_session_id=self._db_session_id,
                overall_score=overall,
                summary=summary,
                transcript=transcript,
                requisition_id=self._requisition_id,
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

        # Allow TTS to finish delivering the farewell phrase, then close the
        # room from the server side via ctx.disconnect().
        await asyncio.sleep(POST_CLOSE_GRACE_SECONDS)
        await self._ctx.disconnect()

        return "Interview finalised and results persisted."

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    async def on_enter(self) -> None:
        """
        Called by the framework when this agent enters an AgentSession.
        Starts the silence watchdog in the background.
        The LLM will immediately produce the greeting from the system prompt.
        """
        asyncio.create_task(self._silence_watchdog(), name="silence-watchdog")
        logger.info(
            "[agent] Entered session for candidate '%s' (session_id=%d).",
            self._candidate_name,
            self._db_session_id,
        )

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
    await ctx.connect()

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

    if not db_session_id:
        logger.error(
            "[agent-worker] No session_id in room metadata for room %s — aborting.",
            ctx.room.name,
        )
        return

    # ── Load personalised questions ───────────────────────────────────────────
    questions = await asyncio.to_thread(
        _load_questions_sync, application_id, job_title
    )

    # ── Build agent + session ─────────────────────────────────────────────────
    agent = RecruitmentInterviewAgent(
        db_session_id=db_session_id,
        candidate_name=candidate_name,
        job_title=job_title,
        questions=questions,
        requisition_id=requisition_id,
        ctx=ctx,
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
    @session.on("user_speech_committed")
    def _on_user_speech(event) -> None:
        agent._last_speech_time = time.monotonic()
        text = getattr(event, "transcript", "") or ""
        if text.strip():
            agent._transcript_parts.append(f"Candidate: {text.strip()}")

    @session.on("agent_speech_committed")
    def _on_agent_speech(event) -> None:
        agent._last_speech_time = time.monotonic()
        text = getattr(event, "text", "") or ""
        if text.strip():
            agent._transcript_parts.append(f"Interviewer: {text.strip()}")

    # ── Candidate-disconnect handler ──────────────────────────────────────────
    # If the candidate closes their browser mid-interview, give them a 15-second
    # grace period (transient network drop) then mark No-show and close the room.
    @ctx.room.on("participant_disconnected")
    def _on_participant_disconnected(participant) -> None:
        if not participant.identity.startswith("candidate_"):
            return  # ignore agent or other system participants
        if not agent._interview_done:
            asyncio.create_task(
                _handle_candidate_disconnect(ctx, agent),
                name="candidate-disconnect",
            )

    await session.start(ctx.room, agent=agent)
    await session.wait_for_shutdown()


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

    await asyncio.to_thread(
        _persist_no_show,
        db_session_id=agent._db_session_id,
        requisition_id=agent._requisition_id,
    )
    await ctx.disconnect()


# ── Helper: load personalised questions ──────────────────────────────────────

def _load_questions_sync(
    application_id: Optional[int],
    job_title: str,
) -> list[str]:
    """
    Synchronous helper (runs in a thread via asyncio.to_thread).
    Loads personalised questions from FocusedInterviewerPersona.
    Falls back to generic questions on any error.
    """
    if not application_id:
        return _generic_questions(job_title)

    try:
        from services.focused_interviewer_persona import FocusedInterviewerPersona
        import asyncio

        persona = FocusedInterviewerPersona()

        # load_focused_interview_metadata is declared async but only does sync
        # DB work — run it in a new event loop inside the thread.
        loop = asyncio.new_event_loop()
        try:
            meta = loop.run_until_complete(
                persona.load_focused_interview_metadata(application_id)
            )
        finally:
            loop.close()

        return [
            persona.generate_round_1_question(meta),
            persona.generate_round_2_question(meta, ""),   # LLM adapts naturally in context
            persona.generate_round_3_question(meta),
            persona.generate_round_4_question(meta),
            persona.generate_round_5_question(meta),
        ]
    except Exception as exc:
        logger.warning(
            "[agent-worker] Could not load personalised questions (%s) — using generics.",
            exc,
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


# ── Helper: persist final results to DB ──────────────────────────────────────

def _persist_completion(
    db_session_id: int,
    overall_score: float,
    summary: str,
    transcript: str,
    requisition_id: int,
) -> None:
    """
    Updates TechnicalInterviewSession to Completed and triggers final ranking.
    Runs synchronously (called from an async context via direct call;
    DB operations are fast so blocking is acceptable here).
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

        row.status = "Completed"
        row.overall_score = overall_score
        row.overall_performance = _tier_label(overall_score)
        row.recommendation = _recommendation(overall_score)
        row.summary = summary
        row.transcript = transcript          # column added in migration h2i3j4k5l6m7
        row.ended_at = datetime.utcnow()
        db.commit()

        logger.info(
            "[agent-worker] Persisted Completed for session %d (score=%.2f).",
            db_session_id,
            overall_score,
        )

    except Exception:
        db.rollback()
        logger.exception(
            "[agent-worker] DB error while persisting session %d.", db_session_id
        )
    finally:
        db.close()

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
