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
    "standard": Template("""Subject: Fresh ${lead_count} verified ${category} leads in ${city} — ready to buy

Hi,

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
scrapetra.com

--
ScrapeTra | London, UK
You received this targeted B2B introductory email because your business matches our active industry tracking.
To opt out of future updates, click here: ${unsubscribe_url}
"""),

    "premium": Template("""Subject: ${lead_count} exclusive ${category} contacts in ${city} — verified today

Hi,

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
ScrapeTra — Autonomous Lead Intelligence
scrapetra.com

--
ScrapeTra | London, UK
You received this targeted B2B introductory email because your business matches our active industry tracking.
To opt out of future updates, click here: ${unsubscribe_url}
"""),

    "buyer-outreach": Template("""Subject: Fresh ${city} ${category} lead list — ${lead_count} verified contacts

Hey,

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
ScrapeTra — Autonomous Lead Intelligence
scrapetra.com

--
ScrapeTra | London, UK
You received this targeted B2B introductory email because your business matches our active industry tracking.
To opt out of future updates, click here: ${unsubscribe_url}
"""),

    "step2-followup": Template("""Subject: Re: Fresh ${category} leads in ${city} — still available

Hi,

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
ScrapeTra — Autonomous Lead Intelligence
scrapetra.com

--
ScrapeTra | London, UK
To opt out of future updates, click here: ${unsubscribe_url}
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
    currency: str = "GBP",
    symbol: str = "\u00a3",
    browse_url: str = "",
    unsubscribe_url: str = "",
) -> dict:
    tmpl = OUTREACH_TEMPLATES.get(template_style, OUTREACH_TEMPLATES["standard"])

    ref = f"ST-{datetime.utcnow().strftime('%Y%m%d')}-{city[:8].upper()}"

    bank_details = ""
    if sort_code and account_number:
        bank_details = f"Bank Transfer: Sort {sort_code} | Acc {account_number} | Ref: {ref}"

    body = tmpl.safe_substitute(
        lead_count=lead_count,
        category=category,
        city=city,
        price=f"{price:.2f}",
        symbol=symbol,
        stripe_url=stripe_url or "#",
        paypal_url=paypal_url or "#",
        bank_details=bank_details,
        ref=ref,
        sender_name=sender_name,
        browse_url=browse_url or "#",
        unsubscribe_url=unsubscribe_url or "#",
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
    tmpl = OUTREACH_TEMPLATES["step2-followup"]
    body = tmpl.safe_substitute(
        lead_count=lead_count,
        category=category,
        city=city,
        price=f"{price:.2f}",
        symbol=symbol,
        sender_name=sender_name,
        browse_url=browse_url or "#",
        unsubscribe_url=unsubscribe_url or "#",
    )
    subject_line = body.split("\n")[0].replace("Subject: ", "")
    body_lines = body.split("\n")[1:]
    body_text = "\n".join(body_lines).strip()
    return {
        "to": lead.get("email", ""),
        "subject": subject_line,
        "body": body_text,
    }
