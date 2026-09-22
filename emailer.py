import os
import logging
import requests

from config import RESEND_API_KEY, SENDER_EMAIL, SENDER_NAME

logger = logging.getLogger("scrapetra.emailer")

RESEND_API_URL = "https://api.resend.com/emails"


def _send_via_resend(to: str, subject: str, body: str, html: str = "") -> bool:
    api_key = os.getenv("RESEND_API_KEY", "")
    if not api_key:
        logger.warning("RESEND_API_KEY not configured")
        return False
    try:
        payload = {
            "from": f"{SENDER_NAME} <{SENDER_EMAIL}>",
            "to": to,
            "subject": subject,
            "html": html if html else body,
            "text": body,
        }
        resp = requests.post(
            RESEND_API_URL,
            json=payload,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            timeout=15,
        )
        if resp.status_code == 200:
            logger.info("Email sent via Resend to %s", to)
            return True
        else:
            logger.error("Resend failed for %s: %s", to, resp.text[:200])
            return False
    except Exception as e:
        logger.error("Resend failed for %s: %s", to, e)
        return False


def send_email(to: str, subject: str, body: str, html: str = "") -> bool:
    if _send_via_resend(to, subject, body, html):
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