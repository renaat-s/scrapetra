import os
import csv
import io
import asyncio
import random
import logging
from datetime import datetime
from string import Template

from agent import run_agent

logger = logging.getLogger("scrapetra.outreach")

SCRAPTRA_LOGO_URL = "https://www.scrapetra.com/static/scrapetra-logo.png"
NANOCLONE_LOGO_URL = "https://www.scrapetra.com/static/nanoclone-footer.png"

BASE_CSS = """
<body style="margin:0;padding:0;background:#0a0a0a;font-family:'Inter',-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;color:#ffffff;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#0a0a0a;padding:40px 0;">
<tr><td align="center">
<table role="presentation" width="600" cellpadding="0" cellspacing="0" style="background:rgba(255,255,255,0.06);backdrop-filter:blur(20px);-webkit-backdrop-filter:blur(20px);border:1px solid rgba(59,130,246,0.4);border-radius:20px;overflow:hidden;">
<tr><td style="padding:0;">"""

FOOTER_HTML = """
</td></tr>
<tr><td style="padding:30px 40px;background:rgba(0,0,0,0.3);border-top:1px solid rgba(59,130,246,0.2);text-align:center;">
<table role="presentation" cellpadding="0" cellspacing="0" style="margin:0 auto;">
<tr>
<td colspan="2" style="text-align:center;padding:0 0 16px 0;"><img src="%s" alt="ScrapeTra Logo" width="140" style="display:block;margin:0 auto;"></td>
</tr>
<tr>
<td colspan="2" style="text-align:center;"><img src="%s" alt="NanoClone Logo" width="180" style="display:block;margin:0 auto;"></td>
</tr>
</table>
<p style="color:#9ca3af;font-size:11px;margin:16px 0 8px 0;">ScrapeTra — Autonomous Lead Intelligence</p>
<p style="color:#6b7280;font-size:10px;margin:0;">You received this email because your business matches our active industry tracking.<br>
<a href="%s" style="color:#60a5fa;text-decoration:underline;">Unsubscribe</a> &nbsp;|&nbsp; <a href="https://www.scrapetra.com/privacy" style="color:#60a5fa;text-decoration:underline;">Privacy Policy</a> &nbsp;|&nbsp; <a href="https://www.scrapetra.com/terms" style="color:#60a5fa;text-decoration:underline;">Terms</a><br>
ScrapeTra Ltd, London, UK<br>
This is an automated message from ScrapeTra. No human intervention was required.</p>
</td></tr>
</table>
</td></tr>
</table>
</body>
</html>"""


def _build_html(subject, body_html, unsubscribe_url):
    return BASE_CSS + body_html + FOOTER_HTML % (SCRAPTRA_LOGO_URL, NANOCLONE_LOGO_URL, unsubscribe_url)


OUTREACH_TEMPLATES = {
    "standard": {
        "subject": Template("Fresh ${lead_count} verified ${category} leads in ${city} — ready to buy"),
        "body": Template("""Hi,

ScrapeTra just completed a fresh scrape of ${category} businesses in ${city}. ${lead_count} companies, each with a syntax-verified, MX-validated email address.

This is live data — scraped today, not recycled from a stale database.

What's in the CSV:
- Company name & website URL
- Verified email address
- MX record validation status

Price: ${symbol}${price} (one-time, no subscription)

View package & buy now: ${browse_url}

The CSV is delivered to your email within 60 seconds of payment. Every email was checked against DNS MX records before inclusion. Zero bounces guaranteed.

${sender_name}
ScrapeTra — Autonomous Lead Intelligence
scrapetra.com"""),
        "html": Template("""<img src="%s" alt="ScrapeTra Logo" width="100" style="display:block;margin:0 auto 20px auto;">
<h1 style="color:#3b82f6;font-size:32px;margin:0 0 4px 0;text-align:center;font-weight:800;letter-spacing:-0.5px;">ScrapeTra</h1>
<p style="color:#60a5fa;font-size:14px;margin:0 0 24px 0;text-align:center;font-weight:400;letter-spacing:1px;">Autonomous Lead Intelligence</p>
<div style="background:rgba(59,130,246,0.1);border:1px solid rgba(59,130,246,0.4);border-radius:12px;padding:20px;margin:20px 0;">
<p style="font-size:16px;margin:0 0 12px 0;">ScrapeTra just completed a fresh scrape of <strong>${category}</strong> businesses in <strong>${city}</strong>.</p>
<p style="font-size:15px;margin:0;">${lead_count} companies, each with a syntax-verified, MX-validated email address.</p>
</div>
<div style="margin:20px 0;">
<p style="font-size:14px;margin:0 0 8px 0;">This is live data — scraped today, not recycled from a stale database.</p>
<p style="font-size:14px;margin:0 0 8px 0;"><strong>What's in the CSV:</strong></p>
<ul style="font-size:14px;margin:0 0 20px 0;padding-left:20px;">
<li>Company name & website URL</li>
<li>Verified email address</li>
<li>MX record validation status</li>
</ul>
</div>
<div style="background:rgba(59,130,246,0.15);border:1px solid rgba(59,130,246,0.3);border-radius:12px;padding:20px;margin:20px 0;text-align:center;">
<p style="font-size:20px;margin:0 0 10px 0;"><strong>Price: ${symbol}${price}</strong> <span style="font-size:13px;color:#9ca3af;">(one-time, no subscription)</span></p>
<p style="font-size:14px;margin:0;">${lead_count} verified ${category} leads in ${city}</p>
</div>
<div style="text-align:center;"><a href="${browse_url}" style="display:inline-block;background:#3b82f6;color:#ffffff;padding:14px 32px;border-radius:10px;text-decoration:none;font-weight:600;font-size:15px;margin:20px 0;">View Package & Buy Now</a></div>
<p style="font-size:13px;margin:20px 0 0 0;color:#9ca3af;">The CSV is delivered to your email within 60 seconds of payment. Every email was checked against DNS MX records before inclusion. Zero bounces guaranteed.</p>
<p style="font-size:13px;margin:20px 0 0 0;color:#9ca3af;">${sender_name}<br>ScrapeTra — Autonomous Lead Intelligence<br>scrapetra.com</p>"""),
    },

    "premium": {
        "subject": Template("${lead_count} exclusive ${category} contacts in ${city} — verified today"),
        "body": Template("""Hi,

I run ScrapeTra, an autonomous B2B lead generation agent. I just scraped and verified ${lead_count} ${category} leads in ${city}.

Here's what makes this different from bought lists:

1. Scraped live from public business directories and company websites
2. Every email syntax-checked and MX-verified against DNS records
3. Zero recycled data — this was harvested today

Traditional data brokers charge 3-5x more for worse data.

ScrapeTra's price: ${symbol}${price} (one-time, no subscription)

View package & buy now: ${browse_url}

The CSV is delivered to your email within seconds of payment.

If you sell to ${category} companies in ${city}, this saves you hours of manual prospecting.

Best,
${sender_name}
ScrapeTra — Autonomous Lead Intelligence"""),
        "html": Template("""<img src="%s" alt="ScrapeTra Logo" width="100" style="display:block;margin:0 auto 20px auto;">
<h1 style="color:#3b82f6;font-size:32px;margin:0 0 4px 0;text-align:center;font-weight:800;letter-spacing:-0.5px;">ScrapeTra</h1>
<p style="color:#60a5fa;font-size:14px;margin:0 0 24px 0;text-align:center;font-weight:400;letter-spacing:1px;">Autonomous Lead Intelligence</p>
<div style="background:rgba(59,130,246,0.1);border:1px solid rgba(59,130,246,0.4);border-radius:12px;padding:20px;margin:20px 0;">
<p style="font-size:16px;margin:0 0 12px 0;">I run ScrapeTra, an autonomous B2B lead generation agent.</p>
<p style="font-size:15px;margin:0;">I just scraped and verified <strong>${lead_count}</strong> ${category} leads in <strong>${city}</strong>.</p>
</div>
<div style="margin:20px 0;">
<h3 style="color:#60a5fa;font-size:15px;margin:0 0 10px 0;">What makes this different from bought lists:</h3>
<ol style="font-size:14px;margin:0 0 20px 0;padding-left:20px;color:#d1d5db;">
<li>Scraped live from public business directories and company websites</li>
<li>Every email syntax-checked and MX-verified against DNS records</li>
<li>Zero recycled data — this was harvested today</li>
</ol>
<p style="font-size:14px;margin:0;color:#d1d5db;">Traditional data brokers charge <strong>3-5x more</strong> for worse data.</p>
</div>
<div style="background:rgba(59,130,246,0.15);border:1px solid rgba(59,130,246,0.3);border-radius:12px;padding:20px;margin:20px 0;text-align:center;">
<p style="font-size:20px;margin:0 0 10px 0;"><strong>Price: ${symbol}${price}</strong> <span style="font-size:13px;color:#9ca3af;">(one-time, no subscription)</span></p>
</div>
<div style="text-align:center;"><a href="${browse_url}" style="display:inline-block;background:#3b82f6;color:#ffffff;padding:14px 32px;border-radius:10px;text-decoration:none;font-weight:600;font-size:15px;margin:20px 0;">View Package & Buy Now</a></div>
<p style="font-size:13px;margin:20px 0 0 0;color:#9ca3af;">The CSV is delivered to your email within seconds of payment. If you sell to ${category} companies in ${city}, this saves you hours of manual prospecting.</p>
<p style="font-size:13px;margin:20px 0 0 0;color:#9ca3af;">Best,<br>${sender_name}<br>ScrapeTra — Autonomous Lead Intelligence</p>"""),
    },

    "buyer-outreach": {
        "subject": Template("Fresh ${city} ${category} lead list — ${lead_count} verified contacts"),
        "body": Template("""Hey,

I noticed you work in B2B sales / lead generation. I wanted to share something that might help.

ScrapeTra (my autonomous lead agent) just scraped ${lead_count} verified ${category} contacts in ${city}.

Every record includes:
- Company name & URL
- Verified business email (MX-validated)
- Deliverability status

Price: ${symbol}${price} — instant CSV delivery.

View package & buy now: ${browse_url}

If you're prospecting in ${city}, this is the fastest way to get a clean list.

${sender_name}
ScrapeTra — Autonomous Lead Intelligence"""),
        "html": Template("""<img src="%s" alt="ScrapeTra Logo" width="100" style="display:block;margin:0 auto 20px auto;">
<h1 style="color:#3b82f6;font-size:32px;margin:0 0 4px 0;text-align:center;font-weight:800;letter-spacing:-0.5px;">ScrapeTra</h1>
<p style="color:#60a5fa;font-size:14px;margin:0 0 24px 0;text-align:center;font-weight:400;letter-spacing:1px;">Autonomous Lead Intelligence</p>
<div style="background:rgba(59,130,246,0.1);border:1px solid rgba(59,130,246,0.4);border-radius:12px;padding:20px;margin:20px 0;">
<p style="font-size:16px;margin:0 0 12px 0;">I noticed you work in <strong>B2B sales / lead generation</strong>. I wanted to share something that might help.</p>
</div>
<div style="margin:20px 0;">
<p style="font-size:15px;margin:0 0 12px 0;">ScrapeTra (my autonomous lead agent) just scraped <strong>${lead_count}</strong> verified ${category} contacts in <strong>${city}</strong>.</p>
<h3 style="color:#60a5fa;font-size:14px;margin:0 0 10px 0;">Every record includes:</h3>
<ul style="font-size:14px;margin:0 0 20px 0;padding-left:20px;color:#d1d5db;">
<li>Company name & URL</li>
<li>Verified business email (MX-validated)</li>
<li>Deliverability status</li>
</ul>
</div>
<div style="background:rgba(59,130,246,0.15);border:1px solid rgba(59,130,246,0.3);border-radius:12px;padding:20px;margin:20px 0;text-align:center;">
<p style="font-size:20px;margin:0;"><strong>Price: ${symbol}${price}</strong> — instant CSV delivery</p>
</div>
<div style="text-align:center;"><a href="${browse_url}" style="display:inline-block;background:#3b82f6;color:#ffffff;padding:14px 32px;border-radius:10px;text-decoration:none;font-weight:600;font-size:15px;margin:20px 0;">View Package & Buy Now</a></div>
<p style="font-size:13px;margin:20px 0 0 0;color:#9ca3af;">If you're prospecting in ${city}, this is the fastest way to get a clean list.</p>
<p style="font-size:13px;margin:20px 0 0 0;color:#9ca3af;">${sender_name}<br>ScrapeTra — Autonomous Lead Intelligence</p>"""),
    },

    "step2-followup": {
        "subject": Template("Re: ${category} leads in ${city} — still available"),
        "body": Template("""Hi,

Quick follow-up on my earlier email. The ${lead_count} verified ${category} leads I scraped for ${city} are still sitting in your queue.

I know inboxes get buried. Here's the quick version:

- ${lead_count} ${category} businesses in ${city}
- Every email MX-verified against DNS records (zero bounces)
- CSV delivered instantly after payment
- One-time price: ${symbol}${price} — no subscription

This data was scraped fresh on the day you receive this email. It's not recycled from a broker database.

View package & buy now: ${browse_url}

If you've already purchased, disregard this — your CSV was delivered instantly.

Best regards,
The ScrapeTra Agent
ScrapeTra — Autonomous Lead Intelligence"""),
        "html": Template("""<img src="%s" alt="ScrapeTra Logo" width="100" style="display:block;margin:0 auto 20px auto;">
<h1 style="color:#3b82f6;font-size:32px;margin:0 0 4px 0;text-align:center;font-weight:800;letter-spacing:-0.5px;">ScrapeTra</h1>
<p style="color:#60a5fa;font-size:14px;margin:0 0 24px 0;text-align:center;font-weight:400;letter-spacing:1px;">Autonomous Lead Intelligence</p>
<div style="background:rgba(59,130,246,0.1);border:1px solid rgba(59,130,246,0.4);border-radius:12px;padding:20px;margin:20px 0;">
<p style="font-size:16px;margin:0 0 12px 0;">Quick follow-up on my earlier email.</p>
<p style="font-size:15px;margin:0;">The <strong>${lead_count}</strong> verified ${category} leads I scraped for <strong>${city}</strong> are still sitting in your queue.</p>
</div>
<div style="margin:20px 0;">
<p style="font-size:14px;margin:0 0 8px 0;">I know inboxes get buried. Here's the quick version:</p>
<ul style="font-size:14px;margin:0 0 20px 0;padding-left:20px;color:#d1d5db;">
<li>${lead_count} ${category} businesses in ${city}</li>
<li>Every email MX-verified against DNS records (zero bounces)</li>
<li>CSV delivered instantly after payment</li>
<li>One-time price: ${symbol}${price} — no subscription</li>
</ul>
<p style="font-size:13px;margin:0;color:#9ca3af;">This data was scraped fresh on the day you receive this email. It's not recycled from a broker database.</p>
</div>
<div style="text-align:center;"><a href="${browse_url}" style="display:inline-block;background:#3b82f6;color:#ffffff;padding:14px 32px;border-radius:10px;text-decoration:none;font-weight:600;font-size:15px;margin:20px 0;">View Package & Buy Now</a></div>
<p style="font-size:13px;margin:20px 0 0 0;color:#9ca3af;">If you've already purchased, disregard this — your CSV was delivered instantly.</p>
<p style="font-size:13px;margin:20px 0 0 0;color:#9ca3af;">Best regards,<br>The ScrapeTra Agent<br>ScrapeTra — Autonomous Lead Intelligence</p>"""),
    },
}


async def run_outreach_campaign(
    campaign_id: str,
    category: str,
    city: str,
    target_count: int,
    price: float,
    sender_name: str,
    template_style: str = "standard",
) -> dict:
    keyword = f"{category} in {city}"
    leads = await run_agent(keyword, target_count)

    valid_leads = [l for l in leads if l.get("email") and l.get("email_valid")]

    await _update_campaign_leads(campaign_id, valid_leads)

    csv_data = _generate_csv(valid_leads)

    csv_path = os.path.join(
        os.path.dirname(__file__), "exports", f"campaign_{campaign_id[:8]}.csv"
    )
    os.makedirs(os.path.dirname(csv_path), exist_ok=True)
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        f.write(csv_data)

    return {
        "total_found": len(leads),
        "valid_emails": len(valid_leads),
        "csv_path": csv_path,
        "leads": valid_leads,
    }


def generate_pitch_email(
    lead: dict,
    lead_count: int,
    category: str,
    city: str,
    price: float,
    sender_name: str,
    stripe_url: str = "",
    paypal_url: str = "",
    sort_code: str = "",
    account_number: str = "",
    template_style: str = "standard",
    currency: str = "GBP",
    symbol: str = "\u00a3",
    browse_url: str = "",
    unsubscribe_url: str = "",
) -> dict:
    tmpl_data = OUTREACH_TEMPLATES.get(template_style, OUTREACH_TEMPLATES["standard"])

    ref = f"ST-{datetime.utcnow().strftime('%Y%m%d')}-{city[:8].upper()}"

    bank_details = ""
    if sort_code and account_number:
        bank_details = f"Bank Transfer: Sort {sort_code} | Acc {account_number} | Ref: {ref}"

    sub = {
        "lead_count": str(lead_count),
        "category": category,
        "city": city,
        "price": f"{price:.2f}",
        "symbol": symbol,
        "stripe_url": stripe_url or "#",
        "paypal_url": paypal_url or "#",
        "bank_details": bank_details,
        "ref": ref,
        "sender_name": sender_name,
        "browse_url": browse_url or "#",
        "unsubscribe_url": unsubscribe_url or "#",
    }

    body_text = tmpl_data["body"].safe_substitute(sub)
    body_html = tmpl_data["html"].safe_substitute(sub)

    subject_line = body_text.split("\n")[0]
    if subject_line.startswith("Subject: "):
        subject_line = subject_line.replace("Subject: ", "")
    body_text = "\n".join(body_text.split("\n")[1:]).strip()

    html_content = _build_html(subject_line, body_html, unsubscribe_url)

    return {
        "to": lead.get("email", ""),
        "subject": subject_line,
        "body": body_text,
        "html": html_content,
        "ref": ref,
    }


def generate_drip_email(
    lead: dict,
    lead_count: int,
    category: str,
    city: str,
    price: float,
    sender_name: str,
    symbol: str = "£",
    browse_url: str = "",
    unsubscribe_url: str = "",
) -> dict:
    tmpl_data = OUTREACH_TEMPLATES["step2-followup"]

    ref = f"ST-{datetime.utcnow().strftime('%Y%m%d')}-{city[:8].upper()}"

    sub = {
        "lead_count": str(lead_count),
        "category": category,
        "city": city,
        "price": f"{price:.2f}",
        "symbol": symbol,
        "sender_name": sender_name,
        "browse_url": browse_url or "#",
        "unsubscribe_url": unsubscribe_url or "#",
    }

    body_text = tmpl_data["body"].safe_substitute(sub)
    body_html = tmpl_data["html"].safe_substitute(sub)

    subject_line = body_text.split("\n")[0]
    if subject_line.startswith("Subject: "):
        subject_line = subject_line.replace("Subject: ", "")
    body_text = "\n".join(body_text.split("\n")[1:]).strip()

    html_content = _build_html(subject_line, body_html, unsubscribe_url)

    return {
        "to": lead.get("email", ""),
        "subject": subject_line,
        "body": body_text,
        "html": html_content,
        "ref": ref,
    }


async def fulfill_delivery(campaign_id: str, buyer_email: str, csv_path: str) -> dict:
    if not os.path.exists(csv_path):
        return {"success": False, "error": "CSV file not found"}

    with open(csv_path, "r", encoding="utf-8") as f:
        csv_content = f.read()

    await _mark_campaign_delivered(campaign_id, buyer_email)

    return {
        "success": True,
        "buyer_email": buyer_email,
        "csv_size": len(csv_content),
        "csv_path": csv_path,
    }


def _generate_csv(leads: list[dict]) -> str:
    output = io.StringIO()
    writer = csv.DictWriter(
        output,
        fieldnames=["company_name", "company_url", "email", "email_valid", "domain_valid"],
    )
    writer.writeheader()
    for lead in leads:
        writer.writerow({
            "company_name": lead.get("company_name", ""),
            "company_url": lead.get("company_url", ""),
            "email": lead.get("email", ""),
            "email_valid": "Yes" if lead.get("email_valid") else "No",
            "domain_valid": "Yes" if lead.get("domain_valid") else "No",
        })
    return output.getvalue()


async def _update_campaign_leads(campaign_id: str, leads: list[dict]):
    import database as db
    import uuid as _uuid
    pool = await db._get_pool()
    async with pool.acquire() as database:
        for lead in leads:
            lead_id = _uuid.uuid4().hex
            await database.execute(
                """INSERT INTO leads (id, search_id, company_name, company_url, email, email_valid, domain_valid)
                   VALUES ($1, $2, $3, $4, $5, $6, $7)""",
                lead_id,
                campaign_id,
                lead.get("company_name", ""),
                lead.get("company_url", ""),
                lead.get("email", ""),
                1 if lead.get("email_valid") else 0,
                1 if lead.get("domain_valid") else 0,
            )


async def _mark_campaign_delivered(campaign_id: str, buyer_email: str):
    import database as db
    pool = await db._get_pool()
    async with pool.acquire() as database:
        await database.execute(
            "UPDATE campaigns SET status = 'delivered', buyer_email = $1, delivered_at = $2 WHERE id = $3",
            buyer_email, datetime.utcnow().isoformat(), campaign_id,
        )