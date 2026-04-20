import logging
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from config import (
    SENDGRID_API_KEY,
    EMAIL_FROM,
    EMAIL_TO,
    SMTP_HOST,
    SMTP_PORT,
    SMTP_USER,
    SMTP_PASSWORD,
)

logger = logging.getLogger(__name__)

EVENT_TYPE_COLORS = {
    "funding": "#22c55e",
    "fda": "#3b82f6",
    "launch": "#f59e0b",
    "research": "#8b5cf6",
    "news": "#6b7280",
}


def _build_html(briefing_text: str, notable_events: list[dict], run_date: str) -> str:
    briefing_html = briefing_text.replace("\n", "<br>")

    alerts_html = ""
    for e in notable_events:
        color = EVENT_TYPE_COLORS.get(e.get("event_type", "news"), "#6b7280")
        badge = f'<span style="background:{color};color:white;padding:2px 8px;border-radius:4px;font-size:12px;font-weight:bold;">{e.get("event_type","").upper()}</span>'
        company = e.get("company") or "Unknown"
        summary = e.get("summary", "")
        url = e.get("source_url", "#")
        alerts_html += f"""
        <div style="border-left:4px solid {color};padding:10px 16px;margin:12px 0;background:#f8fafc;">
            {badge}
            <p style="margin:6px 0;font-size:15px;"><strong>{company}</strong> — {summary}</p>
            <a href="{url}" style="font-size:13px;color:#3b82f6;">Read more →</a>
        </div>"""

    return f"""
<!DOCTYPE html>
<html>
<body style="font-family:Georgia,serif;max-width:680px;margin:0 auto;padding:24px;color:#1e293b;">
  <h1 style="font-size:24px;border-bottom:2px solid #0f172a;padding-bottom:8px;">
    MedRobo Radar — {run_date}
  </h1>

  <h2 style="font-size:18px;margin-top:28px;">Daily Briefing</h2>
  <div style="font-size:15px;line-height:1.7;">{briefing_html}</div>

  <h2 style="font-size:18px;margin-top:32px;">Notable Events</h2>
  {alerts_html if alerts_html else '<p style="color:#64748b;">No notable events flagged this run.</p>'}

  <hr style="margin-top:40px;border:none;border-top:1px solid #e2e8f0;">
  <p style="font-size:12px;color:#94a3b8;">
    Sent by MedRobo Radar · <a href="http://localhost:8501" style="color:#94a3b8;">Open Dashboard</a>
  </p>
</body>
</html>"""


def _send_via_sendgrid(subject: str, html: str) -> None:
    import sendgrid
    from sendgrid.helpers.mail import Mail

    sg = sendgrid.SendGridAPIClient(api_key=SENDGRID_API_KEY)
    message = Mail(
        from_email=EMAIL_FROM,
        to_emails=EMAIL_TO,
        subject=subject,
        html_content=html,
    )
    response = sg.send(message)
    logger.info(f"SendGrid response: {response.status_code}")


def _send_via_smtp(subject: str, html: str) -> None:
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = EMAIL_FROM
    msg["To"] = EMAIL_TO
    msg.attach(MIMEText(html, "html"))

    with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
        server.ehlo()
        server.starttls()
        server.login(SMTP_USER, SMTP_PASSWORD)
        server.sendmail(EMAIL_FROM, [EMAIL_TO], msg.as_string())
    logger.info("Email sent via SMTP")


def send_digest(briefing_text: str, notable_events: list[dict], run_date: str) -> None:
    if not EMAIL_FROM or not EMAIL_TO:
        logger.info("Email not configured — skipping digest")
        return

    subject = f"MedRobo Radar — {run_date}"
    html = _build_html(briefing_text, notable_events, run_date)

    if SENDGRID_API_KEY:
        _send_via_sendgrid(subject, html)
    elif SMTP_USER and SMTP_PASSWORD:
        _send_via_smtp(subject, html)
    else:
        logger.warning("No email transport configured (set SENDGRID_API_KEY or SMTP credentials)")
