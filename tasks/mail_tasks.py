# tasks/mail_tasks.py
import os
import logging
import smtplib
from email.message import EmailMessage
from celery_app import celery

logger = logging.getLogger(__name__)

SMTP_HOST = os.getenv("SMTP_HOST", "smtp.office365.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", 587))
SMTP_USER = os.getenv("SMTP_USER")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD")
DEFAULT_FROM = SMTP_USER or f"no-reply@{os.getenv('APP_DOMAIN','deloai.com')}"
APP_URL = os.getenv("APP_URL", "https://deloai.com")

# Accept flexible kwargs so callers with different names work
@celery.task(name="send_mail", bind=True, max_retries=3, default_retry_delay=10)
def send_mail(self, *args, **kwargs):
    """
    Flexible email sending task.

    Accepts:
      - to or to_email : str or List[str]
      - subject or title
      - html or html_body
      - text or plain_body or plain
    """
    try:
        # Normalize recipients
        to = kwargs.get("to") or kwargs.get("to_email") or kwargs.get("recipients") or kwargs.get("recipient")
        if not to:
            # sometimes people pass first positional arg as to
            if len(args) >= 1:
                to = args[0]
        if not to:
            raise ValueError("Recipient(s) missing (to / to_email).")

        if isinstance(to, str):
            recipients = [t.strip() for t in to.split(",") if t.strip()]
        elif isinstance(to, (list, tuple, set)):
            recipients = [str(t).strip() for t in to]
        else:
            raise ValueError("Invalid recipient type. Must be str or list.")

        subject = kwargs.get("subject") or kwargs.get("title") or (args[1] if len(args) >= 2 else None)
        html = kwargs.get("html") or kwargs.get("html_body") or kwargs.get("body_html") or (args[2] if len(args) >= 3 else None)
        text = kwargs.get("text") or kwargs.get("plain_body") or kwargs.get("plain") or (args[3] if len(args) >= 4 else None)

        if not subject:
            subject = "(No subject)"

        if text is None:
            text = "Please view this email in an HTML-capable client."

        msg = EmailMessage()
        msg["Subject"] = subject
        msg["From"] = DEFAULT_FROM
        msg["To"] = ", ".join(recipients)
        msg.set_content(str(text))

        if html:
            msg.add_alternative(str(html), subtype="html")

        # connect and send
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=30) as s:
            # optional EHLO for some servers
            try:
                s.ehlo()
            except Exception:
                pass

            # If using STARTTLS
            try:
                s.starttls()
                s.ehlo()
            except Exception as e:
                logger.debug("STARTTLS not available or failed: %s", e)

            if SMTP_USER and SMTP_PASSWORD:
                s.login(SMTP_USER, SMTP_PASSWORD)

            s.send_message(msg)

        logger.info("Email sent to %s (subject=%s)", recipients, subject)
        return {"status": "ok", "recipients": recipients, "subject": subject}

    except smtplib.SMTPAuthenticationError as ae:
        logger.exception("SMTP auth error sending email to %s: %s", locals().get("recipients"), ae)
        # do not retry immediately if credentials are wrong
        raise self.retry(exc=ae, countdown=60)

    except Exception as exc:
        # log and retry a couple of times; Celery settings above control retries
        logger.exception("Failed to send email: %s", exc)
        try:
            raise self.retry(exc=exc)
        except self.MaxRetriesExceededError:
            logger.error("Max retries exceeded for send_mail to %s", locals().get("recipients"))
            return {"status": "failed", "error": str(exc)}
