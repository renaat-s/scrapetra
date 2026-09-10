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

OUTREACH_TEMPLATES = {
    "standard": Template("""Subject: ${lead_count} verified ${category} leads in ${city} ready for you

Hey ${company_name},

I'm ScrapeTra - an automated B2B lead generation agent. Today I scraped and verified ${lead_count} active ${category} leads in ${city}.

Every email is syntax-verified and MX-record confirmed - no bounced leads, no waste.

The full CSV includes:
- Company name & URL
- Verified email address
- MX record validation status

Price: £${price}

Pay instantly and receive the CSV within 60 seconds:

Stripe (Card): ${stripe_url}
PayPal: ${paypal_url}
Bank Transfer: Sort ${sort_code} | Acc ${account_number} | Ref: ${ref}

Once payment clears, the CSV arrives in your inbox automatically.

${sender_name}
ScrapeTra - Autonomous Lead Intelligence
"""),

    "premium": Template("""Subject: ${lead_count} exclusive ${category} contacts in ${city} - verified today

Hi ${company_name},

I'm reaching out because ScrapeTra (an autonomous B2B scraping agent) just completed a fresh batch of ${lead_count} verified ${category} leads in ${city}.

This isn't recycled data. Every single record was:
- Scraped live from public directories & websites
- Cross-referenced against MX DNS records for deliverability
- Syntax-validated for email accuracy

Individually, these leads would cost you 3-5x through traditional data brokers.

ScrapeTra's price: £${price} (one-time, no subscription)

Get the full export now:
- Stripe (instant): ${stripe_url}
- PayPal: ${paypal_url}
- UK Bank Transfer: Sort ${sort_code} | Acc ${account_number} | Ref: ${ref}

The CSV is delivered to your email within seconds of payment.

Best,
${sender_name}
ScrapeTra - Autonomous Lead Intelligence
"""),
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
) -> dict:
    tmpl = OUTREACH_TEMPLATES.get(template_style, OUTREACH_TEMPLATES["standard"])

    ref = f"ST-{datetime.utcnow().strftime('%Y%m%d')}-{lead.get('company_name', 'BIZ')[:8].upper()}"

    body = tmpl.safe_substitute(
        company_name=lead.get("company_name", "there"),
        lead_count=lead_count,
        category=category,
        city=city,
        price=f"{price:.2f}",
        stripe_url=stripe_url or "#",
        paypal_url=paypal_url or "#",
        sort_code=sort_code or "XXXXXX",
        account_number=account_number or "XXXXXXXX",
        ref=ref,
        sender_name=sender_name,
    )

    subject_line = body.split("\n")[0].replace("Subject: ", "")
    body_lines = body.split("\n")[1:]
    body_text = "\n".join(body_lines).strip()

    return {
        "to": lead.get("email", ""),
        "subject": subject_line,
        "body": body_text,
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
    async with db.DB_PATH.__class__.__init__:
        pass
    async with __import__("aiosqlite").connect(db.DB_PATH) as database:
        for lead in leads:
            lead_id = __import__("uuid").uuid4().hex
            await database.execute(
                """INSERT INTO leads (id, search_id, company_name, company_url, email, email_valid, domain_valid)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    lead_id,
                    campaign_id,
                    lead.get("company_name", ""),
                    lead.get("company_url", ""),
                    lead.get("email", ""),
                    1 if lead.get("email_valid") else 0,
                    1 if lead.get("domain_valid") else 0,
                ),
            )
        await database.commit()


async def _mark_campaign_delivered(campaign_id: str, buyer_email: str):
    import database as db
    import aiosqlite
    async with aiosqlite.connect(db.DB_PATH) as database:
        await database.execute(
            "UPDATE campaigns SET status = 'delivered', buyer_email = ?, delivered_at = ? WHERE id = ?",
            (buyer_email, datetime.utcnow().isoformat(), campaign_id),
        )
        await database.commit()
