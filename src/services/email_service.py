"""
Async email notification service.

Uses Python's built-in smtplib via anyio.to_thread so the blocking SMTP
call never holds up the LangGraph execution or the Celery worker thread.

Usage
─────
    from services.email_service import send_post_live_notification
    send_post_live_notification(
        recipient_email="hr@company.com",
        recipient_name="Sara",
        job_title="Senior Python Engineer",
        apply_url="http://localhost:3000/apply?cid=1&jid=5",
    )

The function is fire-and-forget — it spawns a daemon thread and returns
immediately.  Any SMTP failure is logged as a warning, never raised.
"""

import logging
import smtplib
import threading
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from helpers.config import get_settings

logger = logging.getLogger(__name__)


# ── Email builder ─────────────────────────────────────────────────────────────

def _build_message(
    recipient_email: str,
    recipient_name: str,
    job_title: str,
    apply_url: str,
) -> MIMEMultipart:
    cfg = get_settings()
    sender = f"{cfg.SMTP_FROM_NAME} <{cfg.SMTP_USER}>"

    msg = MIMEMultipart("alternative")
    msg["Subject"] = "Your Job Requisition is now LIVE!"
    msg["From"] = sender
    msg["To"] = recipient_email

    plain = (
        f"Hello {recipient_name},\n\n"
        f"We are happy to inform you that your job post for \"{job_title}\" "
        f"has been successfully published to LinkedIn.\n\n"
        f"Candidates can now apply via the link below, and the AI will begin "
        f"processing incoming CVs immediately. You can track the progress in "
        f"your dashboard.\n\n"
        f"Apply link: {apply_url}\n\n"
        f"Best regards,\n"
        f"{cfg.SMTP_FROM_NAME}"
    )

    html = f"""
    <html>
      <body style="font-family: Arial, sans-serif; color: #333; max-width: 600px; margin: auto;">
        <h2 style="color: #2563eb;">Your Job Post is LIVE!</h2>
        <p>Hello <strong>{recipient_name}</strong>,</p>
        <p>
          We are happy to inform you that your job post for
          <strong>"{job_title}"</strong> has been successfully published to
          <strong>LinkedIn</strong>.
        </p>
        <p>
          Candidates can now apply via the link below, and the AI pipeline will
          begin processing incoming CVs immediately. You can track the progress
          in your dashboard.
        </p>
        <p style="margin: 24px 0;">
          <a href="{apply_url}"
             style="background:#2563eb;color:#fff;padding:12px 24px;
                    border-radius:6px;text-decoration:none;font-weight:bold;">
            View Application Link
          </a>
        </p>
        <p style="color:#666;font-size:13px;">
          This is an automated message from {cfg.SMTP_FROM_NAME}.
        </p>
      </body>
    </html>
    """

    msg.attach(MIMEText(plain, "plain"))
    msg.attach(MIMEText(html, "html"))
    return msg


# ── SMTP sender (blocking — runs in a background thread) ──────────────────────

def _send(
    recipient_email: str,
    recipient_name: str,
    job_title: str,
    apply_url: str,
) -> None:
    cfg = get_settings()

    if not cfg.SMTP_USER or not cfg.SMTP_PASSWORD:
        logger.warning(
            "[email] SMTP_USER or SMTP_PASSWORD not configured — "
            "skipping notification for '%s' → %s",
            job_title, recipient_email,
        )
        return

    try:
        msg = _build_message(
            recipient_email=recipient_email,
            recipient_name=recipient_name,
            job_title=job_title,
            apply_url=apply_url,
        )
        with smtplib.SMTP(cfg.SMTP_HOST, cfg.SMTP_PORT) as server:
            server.ehlo()
            server.starttls()
            server.login(cfg.SMTP_USER, cfg.SMTP_PASSWORD)
            server.sendmail(cfg.SMTP_USER, recipient_email, msg.as_string())

        logger.info(
            "[email] Post-live notification sent to %s for job '%s'.",
            recipient_email, job_title,
        )
    except Exception as exc:
        logger.warning(
            "[email] Failed to send notification to %s: %s",
            recipient_email, exc,
        )


# ── Shortlist notification ─────────────────────────────────────────────────────

def _build_shortlist_message(
    recipient_email: str,
    first_name: str,
    job_title: str,
    company_name: str,
) -> MIMEMultipart:
    cfg = get_settings()
    sender = f"{cfg.SMTP_FROM_NAME} <{cfg.SMTP_USER}>"

    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"Congratulations! You've passed the first screening for {job_title}"
    msg["From"] = sender
    msg["To"] = recipient_email

    plain = (
        f"Dear {first_name},\n\n"
        f"We are pleased to inform you that after an initial AI-driven semantic "
        f"analysis of your application, you have been shortlisted for the "
        f"{job_title} position at {company_name}.\n\n"
        f"You are among the top candidates in our applicant pool. Please stay "
        f"tuned as our team prepares the technical assessment stage. You will "
        f"receive further instructions shortly.\n\n"
        f"Best regards,\n"
        f"TalentPilot AI Recruitment Team"
    )

    html = f"""
    <html>
      <body style="font-family: Arial, sans-serif; color: #333; max-width: 600px; margin: auto;">
        <h2 style="color: #16a34a;">You've been shortlisted!</h2>
        <p>Dear <strong>{first_name}</strong>,</p>
        <p>
          We are pleased to inform you that after an initial AI-driven semantic
          analysis of your application, you have been <strong>shortlisted</strong>
          for the <strong>{job_title}</strong> position at
          <strong>{company_name}</strong>.
        </p>
        <p>
          You are among the <strong>top candidates</strong> in our applicant pool.
          Please stay tuned as our team prepares the technical assessment stage.
          You will receive further instructions shortly.
        </p>
        <p style="color:#666;font-size:13px;">
          This is an automated message from TalentPilot AI Recruitment Team.
        </p>
      </body>
    </html>
    """

    msg.attach(MIMEText(plain, "plain"))
    msg.attach(MIMEText(html, "html"))
    return msg


def send_shortlist_notification_sync(
    recipient_email: str,
    first_name: str,
    job_title: str,
    company_name: str,
) -> bool:
    """
    Send a shortlist notification email synchronously (blocking).

    Intended to be called from a Celery worker where blocking is acceptable.
    Returns True on success, False on failure.
    """
    cfg = get_settings()

    if not cfg.SMTP_USER or not cfg.SMTP_PASSWORD:
        logger.warning(
            "[email] SMTP_USER or SMTP_PASSWORD not configured — "
            "skipping shortlist notification for '%s' → %s",
            job_title, recipient_email,
        )
        return False

    try:
        msg = _build_shortlist_message(
            recipient_email=recipient_email,
            first_name=first_name,
            job_title=job_title,
            company_name=company_name,
        )
        with smtplib.SMTP(cfg.SMTP_HOST, cfg.SMTP_PORT) as server:
            server.ehlo()
            server.starttls()
            server.login(cfg.SMTP_USER, cfg.SMTP_PASSWORD)
            server.sendmail(cfg.SMTP_USER, recipient_email, msg.as_string())

        logger.info(
            "[email] Shortlist notification sent to %s for job '%s'.",
            recipient_email, job_title,
        )
        return True
    except Exception as exc:
        logger.warning(
            "[email] Failed to send shortlist notification to %s: %s",
            recipient_email, exc,
        )
        return False


# ── Assessment invitation ─────────────────────────────────────────────────────

def _build_assessment_invitation_message(
    recipient_email: str,
    first_name: str,
    job_title: str,
    assessment_url: str,
) -> MIMEMultipart:
    cfg = get_settings()
    sender = f"{cfg.SMTP_FROM_NAME} <{cfg.SMTP_USER}>"

    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"Technical Assessment Invitation — {job_title}"
    msg["From"] = sender
    msg["To"] = recipient_email

    plain = (
        f"Dear {first_name},\n\n"
        f"Congratulations on advancing to the Technical Assessment stage for the "
        f"{job_title} position!\n\n"
        f"Please complete your technical assessment using the secure link below. "
        f"Answer all questions carefully — your responses will be evaluated by our "
        f"AI assessment system.\n\n"
        f"Assessment Link: {assessment_url}\n\n"
        f"Important notes:\n"
        f"  • The link is unique to you — do not share it.\n"
        f"  • Complete the assessment in one sitting.\n"
        f"  • Ensure you have a stable internet connection.\n\n"
        f"Good luck!\n\n"
        f"Best regards,\n"
        f"TalentPilot AI Recruitment Team"
    )

    html = f"""
    <html>
      <body style="font-family: Arial, sans-serif; color: #333; max-width: 600px; margin: auto;">
        <h2 style="color: #7c3aed;">Technical Assessment Invitation</h2>
        <p>Dear <strong>{first_name}</strong>,</p>
        <p>
          Congratulations on advancing to the <strong>Technical Assessment</strong> stage
          for the <strong>{job_title}</strong> position!
        </p>
        <p>
          Please complete your technical assessment using the secure link below.
          Your responses will be evaluated by our AI assessment system.
        </p>
        <p style="margin: 24px 0;">
          <a href="{assessment_url}"
             style="background:#7c3aed;color:#fff;padding:12px 28px;
                    border-radius:6px;text-decoration:none;font-weight:bold;">
            Start Assessment
          </a>
        </p>
        <ul style="color:#555;font-size:14px;">
          <li>The link is unique to you — do not share it.</li>
          <li>Complete the assessment in one sitting.</li>
          <li>Ensure you have a stable internet connection.</li>
        </ul>
        <p><strong>Good luck!</strong></p>
        <p style="color:#666;font-size:13px;">
          This is an automated message from TalentPilot AI Recruitment Team.
        </p>
      </body>
    </html>
    """

    msg.attach(MIMEText(plain, "plain"))
    msg.attach(MIMEText(html, "html"))
    return msg


def send_assessment_invitation_sync(
    recipient_email: str,
    first_name: str,
    job_title: str,
    assessment_url: str,
) -> bool:
    """
    Send a technical assessment invitation email synchronously (blocking).
    Intended for use in Celery workers. Returns True on success, False on failure.
    """
    cfg = get_settings()

    if not cfg.SMTP_USER or not cfg.SMTP_PASSWORD:
        logger.warning(
            "[email] SMTP not configured — skipping assessment invitation for %s.",
            recipient_email,
        )
        return False

    try:
        msg = _build_assessment_invitation_message(
            recipient_email=recipient_email,
            first_name=first_name,
            job_title=job_title,
            assessment_url=assessment_url,
        )
        with smtplib.SMTP(cfg.SMTP_HOST, cfg.SMTP_PORT) as server:
            server.ehlo()
            server.starttls()
            server.login(cfg.SMTP_USER, cfg.SMTP_PASSWORD)
            server.sendmail(cfg.SMTP_USER, recipient_email, msg.as_string())

        logger.info(
            "[email] Assessment invitation sent to %s for job '%s'.",
            recipient_email, job_title,
        )
        return True
    except Exception as exc:
        logger.warning(
            "[email] Failed to send assessment invitation to %s: %s",
            recipient_email, exc,
        )
        return False


# ── Interview invitation ──────────────────────────────────────────────────────

def _build_interview_invitation_message(
    recipient_email: str,
    first_name: str,
    job_title: str,
    interview_url: str,
    interview_deadline: str,
) -> MIMEMultipart:
    cfg = get_settings()
    sender = f"{cfg.SMTP_FROM_NAME} <{cfg.SMTP_USER}>"

    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"Interview Invitation — {job_title}"
    msg["From"] = sender
    msg["To"] = recipient_email

    plain = (
        f"Dear {first_name},\n\n"
        f"Congratulations! You have successfully passed the technical assessment stage "
        f"and have been selected for a live AI-conducted technical interview for the "
        f"{job_title} position.\n\n"
        f"Please join your interview session using the link below before the deadline:\n"
        f"Interview Link: {interview_url}\n\n"
        f"Interview Deadline: {interview_deadline}\n\n"
        f"Important notes:\n"
        f"  • The interview is AI-conducted and takes approximately 20-30 minutes.\n"
        f"  • Ensure you have a working microphone and a quiet environment.\n"
        f"  • The link is unique to you — do not share it.\n\n"
        f"Good luck!\n\n"
        f"Best regards,\n"
        f"TalentPilot AI Recruitment Team"
    )

    html = f"""
    <html>
      <body style="font-family: Arial, sans-serif; color: #333; max-width: 600px; margin: auto;">
        <h2 style="color: #0f172a;">You're Invited to a Live Technical Interview!</h2>
        <p>Dear <strong>{first_name}</strong>,</p>
        <p>
          Congratulations! You have successfully passed the technical assessment and have
          been selected for a <strong>live AI-conducted technical interview</strong> for the
          <strong>{job_title}</strong> position.
        </p>
        <p>Please complete your interview before: <strong>{interview_deadline}</strong></p>
        <p style="margin: 24px 0;">
          <a href="{interview_url}"
             style="background:#0f172a;color:#fff;padding:12px 28px;
                    border-radius:6px;text-decoration:none;font-weight:bold;">
            Join Interview
          </a>
        </p>
        <ul style="color:#555;font-size:14px;">
          <li>The interview is AI-conducted and takes approximately 20-30 minutes.</li>
          <li>Ensure you have a working microphone and a quiet environment.</li>
          <li>The link is unique to you — do not share it.</li>
        </ul>
        <p><strong>Good luck!</strong></p>
        <p style="color:#666;font-size:13px;">
          This is an automated message from TalentPilot AI Recruitment Team.
        </p>
      </body>
    </html>
    """

    msg.attach(MIMEText(plain, "plain"))
    msg.attach(MIMEText(html, "html"))
    return msg


def send_interview_invitation_sync(
    recipient_email: str,
    first_name: str,
    job_title: str,
    interview_url: str,
    interview_deadline: str,
) -> bool:
    """
    Send a live interview invitation email synchronously (blocking).
    Intended for use in Celery workers. Returns True on success, False on failure.
    """
    cfg = get_settings()

    if not cfg.SMTP_USER or not cfg.SMTP_PASSWORD:
        logger.warning(
            "[email] SMTP not configured — skipping interview invitation for %s.",
            recipient_email,
        )
        return False

    try:
        msg = _build_interview_invitation_message(
            recipient_email=recipient_email,
            first_name=first_name,
            job_title=job_title,
            interview_url=interview_url,
            interview_deadline=interview_deadline,
        )
        with smtplib.SMTP(cfg.SMTP_HOST, cfg.SMTP_PORT) as server:
            server.ehlo()
            server.starttls()
            server.login(cfg.SMTP_USER, cfg.SMTP_PASSWORD)
            server.sendmail(cfg.SMTP_USER, recipient_email, msg.as_string())

        logger.info(
            "[email] Interview invitation sent to %s for job '%s'.",
            recipient_email, job_title,
        )
        return True
    except Exception as exc:
        logger.warning(
            "[email] Failed to send interview invitation to %s: %s",
            recipient_email, exc,
        )
        return False


# ── HR interview invitation ───────────────────────────────────────────────────

def _build_hr_interview_invitation_message(
    recipient_email: str,
    first_name: str,
    job_title: str,
    interview_deadline: str,
) -> MIMEMultipart:
    cfg = get_settings()
    sender = f"{cfg.SMTP_FROM_NAME} <{cfg.SMTP_USER}>"

    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"HR Interview Invitation — {job_title}"
    msg["From"] = sender
    msg["To"] = recipient_email

    plain = (
        f"Dear {first_name},\n\n"
        f"Congratulations! You have successfully completed the technical interview stage "
        f"and have been selected to move forward to the HR Interview for the "
        f"{job_title} position.\n\n"
        f"Our HR team will be in touch shortly to schedule your interview session.\n"
        f"Please ensure you are available before: {interview_deadline}\n\n"
        f"What to expect:\n"
        f"  • The HR interview typically takes 30–45 minutes.\n"
        f"  • Topics include your background, motivation, culture fit, and leadership style.\n"
        f"  • Be prepared to discuss your previous experiences and career goals.\n\n"
        f"We look forward to speaking with you!\n\n"
        f"Best regards,\n"
        f"TalentPilot AI Recruitment Team"
    )

    html = f"""
    <html>
      <body style="font-family: Arial, sans-serif; color: #333; max-width: 600px; margin: auto;">
        <h2 style="color: #0f172a;">You're Invited to an HR Interview!</h2>
        <p>Dear <strong>{first_name}</strong>,</p>
        <p>
          Congratulations! You have successfully passed the <strong>technical interview</strong>
          and have been selected to advance to the <strong>HR Interview</strong> for the
          <strong>{job_title}</strong> position.
        </p>
        <p>Our HR team will contact you to schedule your session before: <strong>{interview_deadline}</strong></p>
        <ul style="color:#555;font-size:14px;">
          <li>The HR interview typically takes 30–45 minutes.</li>
          <li>Topics include your background, motivation, culture fit, and leadership style.</li>
          <li>Be prepared to discuss your previous experiences and career goals.</li>
        </ul>
        <p>We look forward to speaking with you!</p>
        <p style="color:#666;font-size:13px;">
          This is an automated message from TalentPilot AI Recruitment Team.
        </p>
      </body>
    </html>
    """

    msg.attach(MIMEText(plain, "plain"))
    msg.attach(MIMEText(html, "html"))
    return msg


def send_hr_interview_invitation_sync(
    recipient_email: str,
    first_name: str,
    job_title: str,
    interview_deadline: str,
) -> bool:
    """
    Send an HR interview invitation email synchronously (blocking).
    Intended for use in Celery workers. Returns True on success, False on failure.
    """
    cfg = get_settings()

    if not cfg.SMTP_USER or not cfg.SMTP_PASSWORD:
        logger.warning(
            "[email] SMTP not configured — skipping HR invitation for %s.",
            recipient_email,
        )
        return False

    try:
        msg = _build_hr_interview_invitation_message(
            recipient_email=recipient_email,
            first_name=first_name,
            job_title=job_title,
            interview_deadline=interview_deadline,
        )
        with smtplib.SMTP(cfg.SMTP_HOST, cfg.SMTP_PORT) as server:
            server.ehlo()
            server.starttls()
            server.login(cfg.SMTP_USER, cfg.SMTP_PASSWORD)
            server.sendmail(cfg.SMTP_USER, recipient_email, msg.as_string())

        logger.info(
            "[email] HR interview invitation sent to %s for job '%s'.",
            recipient_email, job_title,
        )
        return True
    except Exception as exc:
        logger.warning(
            "[email] Failed to send HR interview invitation to %s: %s",
            recipient_email, exc,
        )
        return False


# ── Final hire/no-hire decision notification ──────────────────────────────────

def _build_final_decision_message(
    recipient_email: str,
    first_name: str,
    job_title: str,
    company_name: str,
    decision: str,  # "hire" | "reject"
    final_rank: int | None,
) -> MIMEMultipart:
    cfg = get_settings()
    sender = f"{cfg.SMTP_FROM_NAME} <{cfg.SMTP_USER}>"

    msg = MIMEMultipart("alternative")
    is_hire = decision == "hire"
    subject = (
        f"Congratulations! Final Selection — {job_title}"
        if is_hire
        else f"Update on Your Application — {job_title}"
    )
    msg["Subject"] = subject
    msg["From"] = sender
    msg["To"] = recipient_email

    if is_hire:
        rank_text = f" (ranked #{final_rank} overall)" if final_rank else ""
        plain = (
            f"Dear {first_name},\n\n"
            f"We are thrilled to inform you that you have been selected{rank_text} "
            f"for the {job_title} position at {company_name}!\n\n"
            f"Our recruiting team will be in touch shortly with the next steps.\n\n"
            f"Congratulations and welcome aboard!\n\n"
            f"Best regards,\n"
            f"TalentPilot AI Recruitment Team"
        )
        html = f"""
        <html>
          <body style="font-family: Arial, sans-serif; color: #333; max-width: 600px; margin: auto;">
            <h2 style="color: #16a34a;">Congratulations — You've Been Selected!</h2>
            <p>Dear <strong>{first_name}</strong>,</p>
            <p>
              We are thrilled to inform you that you have been
              <strong>selected{rank_text}</strong> for the
              <strong>{job_title}</strong> position at <strong>{company_name}</strong>!
            </p>
            <p>Our recruiting team will be in touch shortly with the next steps.</p>
            <p style="color:#666;font-size:13px;">
              This is an automated message from TalentPilot AI Recruitment Team.
            </p>
          </body>
        </html>
        """
    else:
        plain = (
            f"Dear {first_name},\n\n"
            f"Thank you for your time and effort throughout our recruitment process "
            f"for the {job_title} position at {company_name}.\n\n"
            f"After careful evaluation, we have decided to move forward with other "
            f"candidates at this time. We appreciate your interest and encourage you "
            f"to apply for future openings.\n\n"
            f"Best regards,\n"
            f"TalentPilot AI Recruitment Team"
        )
        html = f"""
        <html>
          <body style="font-family: Arial, sans-serif; color: #333; max-width: 600px; margin: auto;">
            <h2 style="color: #64748b;">Application Status Update</h2>
            <p>Dear <strong>{first_name}</strong>,</p>
            <p>
              Thank you for your time and effort throughout our recruitment process
              for the <strong>{job_title}</strong> position at <strong>{company_name}</strong>.
            </p>
            <p>
              After careful evaluation, we have decided to move forward with other
              candidates at this time. We appreciate your interest and encourage you
              to apply for future openings.
            </p>
            <p style="color:#666;font-size:13px;">
              This is an automated message from TalentPilot AI Recruitment Team.
            </p>
          </body>
        </html>
        """

    msg.attach(MIMEText(plain, "plain"))
    msg.attach(MIMEText(html, "html"))
    return msg


def send_final_decision_sync(
    recipient_email: str,
    first_name: str,
    job_title: str,
    company_name: str,
    decision: str,
    final_rank: int | None = None,
) -> bool:
    """
    Send a hire/no-hire decision email synchronously. Returns True on success.
    decision: "hire" | "reject"
    """
    cfg = get_settings()

    if not cfg.SMTP_USER or not cfg.SMTP_PASSWORD:
        logger.warning(
            "[email] SMTP not configured — skipping final decision email for %s.",
            recipient_email,
        )
        return False

    try:
        msg = _build_final_decision_message(
            recipient_email=recipient_email,
            first_name=first_name,
            job_title=job_title,
            company_name=company_name,
            decision=decision,
            final_rank=final_rank,
        )
        with smtplib.SMTP(cfg.SMTP_HOST, cfg.SMTP_PORT) as server:
            server.ehlo()
            server.starttls()
            server.login(cfg.SMTP_USER, cfg.SMTP_PASSWORD)
            server.sendmail(cfg.SMTP_USER, recipient_email, msg.as_string())

        logger.info(
            "[email] Final decision (%s) sent to %s for job '%s'.",
            decision, recipient_email, job_title,
        )
        return True
    except Exception as exc:
        logger.warning(
            "[email] Failed to send final decision email to %s: %s",
            recipient_email, exc,
        )
        return False


# ── Public API — fire-and-forget ──────────────────────────────────────────────

def send_post_live_notification(
    recipient_email: str,
    recipient_name: str,
    job_title: str,
    apply_url: str,
) -> None:
    """
    Send a 'your job is live' email without blocking the caller.

    Spawns a daemon thread so LangGraph / Celery execution continues
    immediately.  SMTP errors are caught and logged — never raised.
    """
    threading.Thread(
        target=_send,
        args=(recipient_email, recipient_name, job_title, apply_url),
        daemon=True,
        name=f"email-{recipient_email[:20]}",
    ).start()
