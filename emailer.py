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

        html = f"""<body style="margin:0;padding:0;background:#0a0a0a;font-family:'Inter',-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;color:#ffffff;"><table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#0a0a0a;padding:40px 0;"><tr><td align="center"><table role="presentation" width="600" cellpadding="0" cellspacing="0" style="background:rgba(255,255,255,0.05);backdrop-filter:blur(20px);-webkit-backdrop-filter:blur(20px);border:1px solid rgba(59,130,246,0.2);border-radius:20px;overflow:hidden;"><tr><td style="padding:30px 40px;">
<h2 style="color:#3b82f6;font-size:22px;margin:0 0 20px 0;text-align:center;">ScrapeTra — Autonomous Lead Intelligence</h2>
<div style="background:rgba(59,130,246,0.1);border:1px solid rgba(59,130,246,0.3);border-radius:12px;padding:20px;margin:20px 0;">
<p style="font-size:16px;margin:0 0 12px 0;">Your lead data from <strong>{campaign_name}</strong> is ready.</p>
<p style="font-size:15px;margin:0;">Category: {category} | Location: {city}</p>
</div>
<div style="background:rgba(59,130,246,0.15);border-radius:12px;padding:20px;margin:20px 0;text-align:center;">
<p style="font-size:18px;margin:0;color:#60a5fa;">CSV Delivered</p>
<p style="font-size:14px;margin:10px 0 0 0;color:#d1d5db;">The CSV contains verified company names, URLs, and MX-validated email addresses.</p>
</div>
<p style="font-size:13px;margin:20px 0 0 0;color:#9ca3af;">Generated autonomously by ScrapeTra.</p>
</td></tr>
<tr><td style="padding:30px 40px;background:rgba(0,0,0,0.3);border-top:1px solid rgba(59,130,246,0.1);text-align:center;">
<table role="presentation" cellpadding="0" cellspacing="0" style="margin:0 auto;">
<tr>
<td style="padding-right:20px;"><img src="https://www.scrapetra.com/static/scrapetra-logo.png" alt="ScrapeTra Logo" width="120" style="display:block;"></td>
<td style="padding-left:20px;border-left:1px solid rgba(255,255,255,0.1);"><img src="https://www.scrapetra.com/static/nanoclone-footer.png" alt="NanoClone Logo" width="120" style="display:block;"></td>
</tr>
</table>
<p style="color:#9ca3af;font-size:11px;margin:16px 0 8px 0;">ScrapeTra — Autonomous Lead Intelligence</p>
<p style="color:#6b7280;font-size:10px;margin:0;">This is an automated message from ScrapeTra.<br>
<a href="https://www.scrapetra.com/unsubscribe" style="color:#60a5fa;text-decoration:underline;">Unsubscribe</a> &nbsp;|&nbsp; <a href="https://www.scrapetra.com/privacy" style="color:#60a5fa;text-decoration:underline;">Privacy Policy</a> &nbsp;|&nbsp; <a href="https://www.scrapetra.com/terms" style="color:#60a5fa;text-decoration:underline;">Terms</a><br>
ScrapeTra Ltd, London, UK<br>
You received this email because you purchased a lead package from ScrapeTra.</p>
</td></tr>
</table>
</td></tr>
</table>
</body>
</html>"""

    return send_email(to, subject, body, html)


def send_pitch_email(to: str, subject: str, body: str, html: str = "") -> bool:
    return send_email(to, subject, body, html)


def send_drip_email(to: str, subject: str, body: str, html: str = "") -> bool:
    return send_email(to, subject, body, html)