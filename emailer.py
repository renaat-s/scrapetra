import os
import smtplib
import logging
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

from config import SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASS, SENDER_EMAIL, SENDER_NAME

logger = logging.getLogger("scrapetra.emailer")

SENDGRID_API_KEY = os.getenv("SENDGRID_API_KEY", "")


def _send_via_sendgrid(to: str, subject: str, body: str, html: str = "") -> bool:
    if not SENDGRID_API_KEY:
        return False
    try:
        from sendgrid import SendGridAPIClient
        from sendgrid.helpers.mail import Mail, Email, To, Content

        message = Mail(
            from_email=Email(SENDER_EMAIL),
            to_emails=To(to),
            subject=subject,
            plain_text_content=Content("text/plain", body),
            html_content=Content("text/html", html) if html else None,
        )
        message.reply_to = Email(SENDER_EMAIL)

        sg = SendGridAPIClient(SENDGRID_API_KEY)
        sg.send(message)
        logger.info("Email sent via SendGrid to %s", to)
        return True
    except Exception as e:
        logger.error("SendGrid failed for %s: %s", to, e)
        return False


def _send_via_smtp(to: str, subject: str, body: str, html: str = "") -> bool:
    if not SMTP_USER or not SMTP_PASS:
        return False

    msg = MIMEMultipart("alternative")
    msg["From"] = f"{SENDER_NAME} <{SENDER_EMAIL}>"
    msg["To"] = to
    msg["Subject"] = subject
    msg["Reply-To"] = SENDER_EMAIL

    msg.attach(MIMEText(body, "plain"))
    if html:
        msg.attach(MIMEText(html, "html"))

    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=15) as server:
            server.ehlo()
            server.starttls()
            server.ehlo()
            server.login(SMTP_USER, SMTP_PASS)
            server.sendmail(SENDER_EMAIL, to, msg.as_string())
        logger.info("Email sent via SMTP to %s", to)
        return True
    except Exception as e:
        logger.error("SMTP failed for %s: %s", to, e)
        return False


def send_email(to: str, subject: str, body: str, html: str = "") -> bool:
    if _send_via_sendgrid(to, subject, body, html):
        return True
    if _send_via_smtp(to, subject, body, html):
        return True
    logger.error("All email backends failed for %s", to)
    return False


def send_csv_delivery(
    to: str,
    campaign_name: str,
    csv_content: str,
    category: str = "",
    city: str = "",
) -> bool:
    subject = f"Your ScrapeTra Lead Data: {campaign_name}"

    body = f"""Hi there,

Your lead data from ScrapeTra is attached.

Campaign: {campaign_name}
Category: {category}
Location: {city}

The CSV contains verified company names, URLs, and MX-validated email addresses.

If you have any questions, reply to this email.

Best,
ScrapeTra - Autonomous Lead Intelligence

--
ScrapeTra | London, UK
You received this email because you purchased a lead package from ScrapeTra.
To opt out of future marketing emails, email unsubscribe@scrapetra.com with your email address.
"""

    html = f"""<!DOCTYPE html>
<html>
<body style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto; padding: 20px;">
  <h2 style="color: #3b82f6;">ScrapeTra Lead Data Delivery</h2>
  <p>Your lead data from <strong>{campaign_name}</strong> is ready.</p>
  <div style="background: #f3f4f6; padding: 16px; border-radius: 8px; margin: 16px 0;">
    <p><strong>Campaign:</strong> {campaign_name}</p>
    <p><strong>Category:</strong> {category}</p>
    <p><strong>Location:</strong> {city}</p>
  </div>
  <p>The CSV contains verified company names, URLs, and MX-validated email addresses.</p>
  <hr style="border: 1px solid #e5e7eb; margin: 20px 0;">
  <p style="color: #6b7280; font-size: 12px;">Generated autonomously in 60 seconds by <a href="https://www.scrapetra.com" style="color: #3b82f6;">ScrapeTra</a>. Build your own list at <a href="https://www.scrapetra.com/dashboard" style="color: #3b82f6;">scrapetra.com</a></p>
  <p style="color: #9ca3af; font-size: 11px; margin-top: 12px;">ScrapeTra | London, UK | To opt out of future marketing emails, email <a href="mailto:unsubscribe@scrapetra.com" style="color: #9ca3af;">unsubscribe@scrapetra.com</a></p>
</body>
</html>"""

    return send_email(to, subject, body, html)


def send_pitch_email(to: str, subject: str, body: str) -> bool:
    return send_email(to, subject, body)
