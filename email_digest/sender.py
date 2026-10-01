import html
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
    DIGEST_MODE,
)
from pipeline.event_styles import event_color, event_label
from pipeline.text_utils import markdown_to_email_html

logger = logging.getLogger(__name__)


def _section_html(section: dict, heading_level: int) -> str:
    """One industry: briefing + notable events. `section` is a per-industry run summary."""
    briefing_html = markdown_to_email_html(section.get("briefing_text", ""))

    alerts_html = ""
    for e in section.get("notable_events", []):
        event_type = e.get("event_type", "news")
        color = event_color(event_type)
        badge = f'<span style="background:{color};color:white;padding:2px 8px;border-radius:4px;font-size:12px;font-weight:bold;">{html.escape(event_label(event_type))}</span>'
        company = html.escape(e.get("company") or "Unknown")
        summary = html.escape(e.get("summary", ""))
        url = html.escape(e.get("source_url", "#"), quote=True)
        alerts_html += f"""
        <div style="border-left:4px solid {color};padding:10px 16px;margin:12px 0;background:#f8fafc;">
            {badge}
            <p style="margin:6px 0;font-size:15px;"><strong>{company}</strong> — {summary}</p>
            <a href="{url}" style="font-size:13px;color:#3b82f6;">Read more →</a>
        </div>"""

    h = f"h{heading_level}"
    h_sub = f"h{heading_level + 1}"
    title = html.escape(section.get("industry_name", ""))
    return f"""
  <{h} style="font-size:20px;margin-top:32px;">{title}</{h}>
  <{h_sub} style="font-size:16px;margin-top:16px;">Daily Briefing</{h_sub}>
  <div style="font-size:15px;line-height:1.7;">{briefing_html}</div>
  <{h_sub} style="font-size:16px;margin-top:24px;">Notable Events</{h_sub}>
  {alerts_html if alerts_html else '<p style="color:#64748b;">No notable events flagged this run.</p>'}"""


def _build_html(title: str, sections: list[dict]) -> str:
    body = "".join(_section_html(s, 2) for s in sections)
    return f"""
<!DOCTYPE html>
<html>
<body style="font-family:Georgia,serif;max-width:680px;margin:0 auto;padding:24px;color:#1e293b;">
  <h1 style="font-size:24px;border-bottom:2px solid #0f172a;padding-bottom:8px;">
    {html.escape(title)}
  </h1>
  {body}
  <hr style="margin-top:40px;border:none;border-top:1px solid #e2e8f0;">
  <p style="font-size:12px;color:#94a3b8;">
    Sent by Industry Radar · <a href="http://localhost:8501" style="color:#94a3b8;">Open Dashboard</a>
  </p>
</body>
</html>"""


def _send_via_sendgrid(subject: str, html_body: str) -> None:
    import sendgrid
    from sendgrid.helpers.mail import Mail

    sg = sendgrid.SendGridAPIClient(api_key=SENDGRID_API_KEY)
    message = Mail(
        from_email=EMAIL_FROM,
        to_emails=EMAIL_TO,
        subject=subject,
        html_content=html_body,
    )
    response = sg.send(message)
    logger.info(f"SendGrid response: {response.status_code}")


def _send_via_smtp(subject: str, html_body: str) -> None:
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = EMAIL_FROM
    msg["To"] = EMAIL_TO
    msg.attach(MIMEText(html_body, "html"))

    with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
        server.ehlo()
        server.starttls()
        server.login(SMTP_USER, SMTP_PASSWORD)
        server.sendmail(EMAIL_FROM, [EMAIL_TO], msg.as_string())
    logger.info("Email sent via SMTP")


def _send(subject: str, html_body: str) -> None:
    if SENDGRID_API_KEY:
        _send_via_sendgrid(subject, html_body)
    elif SMTP_USER and SMTP_PASSWORD:
        _send_via_smtp(subject, html_body)
    else:
        logger.warning("No email transport configured (set SENDGRID_API_KEY or SMTP credentials)")


def build_digests(sections: list[dict], run_date: str, mode: str = DIGEST_MODE) -> list[tuple[str, str]]:
    """Return (subject, html) pairs: one combined digest, or one per industry."""
    if mode == "per_industry" or len(sections) == 1:
        return [
            (f"{s['industry_name']} — {run_date}", _build_html(f"{s['industry_name']} — {run_date}", [s]))
            for s in sections
        ]
    title = f"Industry Radar — {run_date}"
    return [(title, _build_html(title, sections))]


def send_digests(sections: list[dict], run_date: str) -> None:
    """Send the digest(s) for a pipeline run. Each section is a per-industry run
    summary with industry_name, briefing_text and notable_events."""
    if not EMAIL_FROM or not EMAIL_TO:
        logger.info("Email not configured — skipping digest")
        return
    for subject, html_body in build_digests(sections, run_date):
        _send(subject, html_body)


def send_failure_alert(failures: list[tuple[str, str]]) -> None:
    """Email that some industries failed even after a retry. failures: [(industry_id, error)]."""
    if not EMAIL_FROM or not EMAIL_TO:
        logger.error(f"Pipeline failures (email not configured, no alert sent): {failures}")
        return
    items = "".join(
        f"<li><strong>{html.escape(industry_id)}</strong>: {html.escape(error or 'unknown error')}</li>"
        for industry_id, error in failures
    )
    body = f"""<!DOCTYPE html><html><body style="font-family:sans-serif;color:#1e293b;">
<h2>Industry Radar pipeline failed</h2>
<p>These industries failed on the scheduled run and again on retry:</p><ul>{items}</ul>
<p>Check the logs, then re-run with <code>python main.py run &lt;industry&gt;</code>.</p>
</body></html>"""
    _send(f"Industry Radar — pipeline failed ({len(failures)} industr{'y' if len(failures) == 1 else 'ies'})", body)
