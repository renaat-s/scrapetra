import csv
import io
import asyncio
import time
import logging
import os
from collections import defaultdict
from fastapi import FastAPI, Request, Form, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import (
    HTMLResponse, StreamingResponse, RedirectResponse,
    JSONResponse, FileResponse,
)
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from config import (
    API_KEY, EXPORT_DIR, STRIPE_PUBLISHABLE_KEY,
    STRIPE_WEBHOOK_SECRET, ALLOWED_ORIGINS,
    PAYPAL_CLIENT_ID, BANK_SORT_CODE, BANK_ACCOUNT,
    DEFAULT_LEAD_PRICE, SENDER_NAME,
)
from database import (
    init_db, create_search, update_search_status, insert_leads,
    get_leads, get_search, create_payment, mark_payment_paid_by_session,
    mark_payment_paid_by_paypal, create_download_token,
    validate_download_token, is_search_paid,
    create_campaign, update_campaign, get_campaign, get_all_campaigns,
    log_outreach, update_outreach_status, get_outreach_logs,
)
from agent import run_agent
from stripe_pay import create_checkout_session, verify_webhook
from paypal_pay import create_paypal_order, capture_paypal_order
from outreach import (
    run_outreach_campaign, generate_pitch_email, fulfill_delivery,
)
from emailer import send_pitch_email, send_csv_delivery

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("scrapetra.main")

__dirname = os.path.dirname(os.path.abspath(__file__))

app = FastAPI(title="ScrapeTra - B2B Lead Scraping Agent")
app.mount("/static", StaticFiles(directory=os.path.join(__dirname, "static")), name="static")
templates = Jinja2Templates(directory=os.path.join(__dirname, "templates"))

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

_rate_limit_store: dict[str, list[float]] = defaultdict(list)
RATE_LIMIT_WINDOW = 60.0
RATE_LIMIT_MAX = 10


def _check_rate_limit(key: str) -> bool:
    now = time.time()
    _rate_limit_store[key] = [t for t in _rate_limit_store[key] if now - t < RATE_LIMIT_WINDOW]
    if len(_rate_limit_store[key]) >= RATE_LIMIT_MAX:
        return False
    _rate_limit_store[key].append(now)
    return True


@app.on_event("startup")
async def startup():
    await init_db()


@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    return templates.TemplateResponse("dashboard.html", {
        "request": request,
        "stripe_publishable_key": STRIPE_PUBLISHABLE_KEY,
        "paypal_client_id": PAYPAL_CLIENT_ID,
    })


@app.get("/api/campaigns")
async def list_campaigns():
    campaigns = await get_all_campaigns()
    return {"campaigns": campaigns, "count": len(campaigns)}


@app.post("/api/campaigns")
async def create_new_campaign(
    request: Request,
    api_key: str = Form(...),
    name: str = Form(...),
    category: str = Form(...),
    city: str = Form(...),
    target_count: int = Form(20),
    price: float = Form(35.0),
    template_style: str = Form("standard"),
):
    client_ip = request.client.host if request.client else "unknown"
    if not _check_rate_limit(f"campaign:{client_ip}"):
        raise HTTPException(status_code=429, detail="Rate limit exceeded")
    if api_key != API_KEY:
        raise HTTPException(status_code=401, detail="Invalid API key")

    campaign_id = await create_campaign(
        name=name,
        category=category,
        city=city,
        target_count=target_count,
        price=price,
        template_style=template_style,
    )

    asyncio.create_task(_run_campaign(campaign_id, category, city, target_count, price, SENDER_NAME, template_style))

    return JSONResponse({"campaign_id": campaign_id, "status": "pending"})


async def _run_campaign(
    campaign_id: str, category: str, city: str,
    target_count: int, price: float, sender_name: str,
    template_style: str,
):
    try:
        await update_campaign(campaign_id, status="running")
        result = await run_outreach_campaign(
            campaign_id, category, city, target_count, price, sender_name, template_style
        )

        origin = "http://127.0.0.1:8000"
        stripe_url = f"{origin}/checkout/{campaign_id}"
        paypal_url = f"{origin}/paypal/create/{campaign_id}"

        await update_campaign(
            campaign_id,
            status="scraping_done",
            leads_found=result["total_found"],
            valid_emails=result["valid_emails"],
            csv_path=result["csv_path"],
            completed_at=time.strftime("%Y-%m-%dT%H:%M:%S"),
            stripe_url=stripe_url,
            paypal_url=paypal_url,
            bank_sort=BANK_SORT_CODE,
            bank_account=BANK_ACCOUNT,
            bank_ref=f"ST-{campaign_id[:8].upper()}",
        )
    except Exception as e:
        logger.error("Campaign %s failed: %s", campaign_id, e)
        await update_campaign(campaign_id, status=f"error: {str(e)[:200]}")


@app.get("/api/campaigns/{campaign_id}")
async def get_campaign_detail(campaign_id: str):
    campaign = await get_campaign(campaign_id)
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")
    leads = await get_leads(campaign_id)
    logs = await get_outreach_logs(campaign_id)
    return {"campaign": campaign, "leads": leads, "logs": logs, "lead_count": len(leads)}


@app.post("/api/campaigns/{campaign_id}/pitch")
async def send_campaign_pitch(
    campaign_id: str,
    request: Request,
    api_key: str = Form(...),
):
    if api_key != API_KEY:
        raise HTTPException(status_code=401, detail="Invalid API key")

    campaign = await get_campaign(campaign_id)
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")

    leads = await get_leads(campaign_id)
    valid_leads = [l for l in leads if l.get("email") and l.get("email_valid")]

    if not valid_leads:
        raise HTTPException(status_code=400, detail="No valid leads to pitch")

    origin = str(request.base_url).rstrip("/")
    emails_sent = 0

    for lead in valid_leads:
        pitch = generate_pitch_email(
            lead=lead,
            lead_count=campaign["valid_emails"],
            category=campaign["category"],
            city=campaign["city"],
            price=campaign["price"],
            sender_name=SENDER_NAME,
            stripe_url=f"{origin}/checkout/{campaign_id}",
            paypal_url=f"{origin}/paypal/create/{campaign_id}",
            sort_code=BANK_SORT_CODE,
            account_number=BANK_ACCOUNT,
            template_style=campaign.get("template_style", "standard"),
        )

        log_id = await log_outreach(campaign_id, pitch["to"], pitch["subject"])

        sent = send_pitch_email(pitch["to"], pitch["subject"], pitch["body"])
        await update_outreach_status(log_id, "sent" if sent else "failed")
        if sent:
            emails_sent += 1

        await asyncio.sleep(1)

    await update_campaign(campaign_id, status="pitched")
    return {"emails_sent": emails_sent, "total": len(valid_leads)}


@app.post("/api/campaigns/{campaign_id}/fulfill")
async def fulfill_campaign(campaign_id: str, buyer_email: str = Form(...), api_key: str = Form(...)):
    if api_key != API_KEY:
        raise HTTPException(status_code=401, detail="Invalid API key")

    campaign = await get_campaign(campaign_id)
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")

    csv_path = campaign.get("csv_path", "")
    if not csv_path:
        raise HTTPException(status_code=400, detail="CSV not ready yet")

    result = await fulfill_delivery(campaign_id, buyer_email, csv_path)
    if not result["success"]:
        raise HTTPException(status_code=500, detail=result.get("error", "Delivery failed"))

    with open(csv_path, "r", encoding="utf-8") as f:
        csv_content = f.read()

    send_csv_delivery(
        to=buyer_email,
        campaign_name=campaign["name"],
        csv_content=csv_content,
        category=campaign["category"],
        city=campaign["city"],
    )

    return {"delivered": True, "buyer_email": buyer_email}


@app.post("/api/campaigns/{campaign_id}/checkout-stripe")
async def campaign_stripe_checkout(campaign_id: str, request: Request):
    campaign = await get_campaign(campaign_id)
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")

    origin = str(request.base_url).rstrip("/")
    result = create_checkout_session(campaign_id, origin)
    if "error" in result:
        raise HTTPException(status_code=400, detail=result["error"])

    await create_payment(campaign_id, result["session_id"], int(campaign["price"] * 100))
    return {"url": result["url"], "session_id": result["session_id"]}


@app.post("/api/campaigns/{campaign_id}/checkout-paypal")
async def campaign_paypal_checkout(campaign_id: str, request: Request):
    campaign = await get_campaign(campaign_id)
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")

    origin = str(request.base_url).rstrip("/")
    amount = f"{campaign['price']:.2f}"

    result = create_paypal_order(
        amount=amount,
        currency="GBP",
        search_id=campaign_id,
        origin=origin,
        description=f"ScrapeTra: {campaign['name']} - {campaign['valid_emails']} verified leads",
    )

    if "error" in result:
        raise HTTPException(status_code=400, detail=result["error"])

    await create_payment(campaign_id, "", int(campaign["price"] * 100), "GBP")
    return {"approve_url": result["approve_url"], "order_id": result["order_id"]}


@app.post("/api/search")
async def start_search(
    request: Request,
    keyword: str = Form(...),
    count: int = Form(10),
    api_key: str = Form(""),
):
    client_ip = request.client.host if request.client else "unknown"
    if not _check_rate_limit(f"search:{client_ip}"):
        raise HTTPException(status_code=429, detail="Rate limit exceeded. Try again in 1 minute.")
    if api_key != API_KEY:
        raise HTTPException(status_code=401, detail="Invalid API key")

    count = max(1, min(count, 100))
    search_id = await create_search(keyword, count)
    asyncio.create_task(_run_search_agent(search_id, keyword, count))
    return JSONResponse({"search_id": search_id, "status": "pending"})


async def _run_search_agent(search_id: str, keyword: str, count: int):
    try:
        await update_search_status(search_id, "running")
        leads = await run_agent(keyword, count)
        await insert_leads(search_id, leads)
        await update_search_status(search_id, "completed")
    except Exception as e:
        await update_search_status(search_id, f"error: {str(e)[:200]}")


@app.get("/api/status/{search_id}")
async def search_status(search_id: str):
    search = await get_search(search_id)
    if not search:
        raise HTTPException(status_code=404, detail="Search not found")
    return search


@app.get("/api/leads/{search_id}")
async def search_leads(search_id: str):
    search = await get_search(search_id)
    if not search:
        raise HTTPException(status_code=404, detail="Search not found")
    leads = await get_leads(search_id)
    return {"search": search, "leads": leads, "count": len(leads)}


@app.get("/api/download-token/{search_id}")
async def get_download_token(
    search_id: str,
    api_key: str = Query(None),
    session_id: str = Query(None),
):
    if api_key and api_key == API_KEY:
        token = await create_download_token(search_id, ttl_seconds=3600)
        return {"token": token, "expires_in": 3600}

    if session_id:
        paid = await is_search_paid(search_id)
        if paid:
            token = await create_download_token(search_id, ttl_seconds=3600)
            return {"token": token, "expires_in": 3600}

    raise HTTPException(status_code=401, detail="Payment or valid API key required")


@app.get("/api/export/{search_id}")
async def export_csv(search_id: str, token: str = Query(...)):
    valid = await validate_download_token(token, search_id)
    if not valid:
        raise HTTPException(status_code=403, detail="Invalid or expired download token")

    leads = await get_leads(search_id)
    if not leads:
        raise HTTPException(status_code=404, detail="No leads found")

    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=["company_name", "company_url", "email", "email_valid", "domain_valid"])
    writer.writeheader()
    for lead in leads:
        writer.writerow({
            "company_name": lead["company_name"],
            "company_url": lead["company_url"],
            "email": lead["email"],
            "email_valid": "Yes" if lead["email_valid"] else "No",
            "domain_valid": "Yes" if lead["domain_valid"] else "No",
        })

    output.seek(0)
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename=scrapetra_leads_{search_id[:8]}.csv"},
    )


@app.get("/payment/success")
async def payment_success(request: Request, search_id: str = ""):
    return templates.TemplateResponse("dashboard.html", {
        "request": request,
        "stripe_publishable_key": STRIPE_PUBLISHABLE_KEY,
        "paypal_client_id": PAYPAL_CLIENT_ID,
        "payment_success": True,
        "paid_search_id": search_id,
    })


@app.get("/payment/cancel")
async def payment_cancel(request: Request):
    return RedirectResponse("/")


@app.get("/paypal/success")
async def paypal_success(request: Request, search_id: str = "", token: str = ""):
    if search_id and token:
        result = capture_paypal_order(token)
        if result.get("status") == "COMPLETED":
            await mark_payment_paid_by_paypal(token)
            await create_download_token(search_id, ttl_seconds=86400)

    return templates.TemplateResponse("dashboard.html", {
        "request": request,
        "stripe_publishable_key": STRIPE_PUBLISHABLE_KEY,
        "paypal_client_id": PAYPAL_CLIENT_ID,
        "payment_success": True,
        "paid_search_id": search_id,
    })


@app.get("/paypal/cancel")
async def paypal_cancel(request: Request):
    return RedirectResponse("/")


@app.post("/webhook/stripe")
async def stripe_webhook(request: Request):
    payload = await request.body()
    sig_header = request.headers.get("stripe-signature", "")

    if not STRIPE_WEBHOOK_SECRET:
        return {"status": "ok", "note": "webhook secret not configured"}

    event = verify_webhook(payload, sig_header, STRIPE_WEBHOOK_SECRET)
    if not event:
        raise HTTPException(status_code=400, detail="Invalid webhook signature")

    if event["type"] == "checkout.session.completed":
        session = event["data"]["object"]
        stripe_session_id = session.get("id", "")
        search_id = session.get("metadata", {}).get("search_id", "")
        buyer_email = session.get("customer_details", {}).get("email", "")

        if stripe_session_id:
            await mark_payment_paid_by_session(stripe_session_id)

        if search_id:
            campaign = await get_campaign(search_id)
            if campaign:
                if buyer_email and not campaign.get("buyer_email"):
                    await update_campaign(search_id, buyer_email=buyer_email)

                csv_path = campaign.get("csv_path", "")
                if csv_path:
                    target_email = buyer_email or campaign.get("buyer_email", "")
                    if target_email:
                        await fulfill_delivery(search_id, target_email, csv_path)
                        send_csv_delivery(
                            to=target_email,
                            campaign_name=campaign["name"],
                            csv_content=open(csv_path, "r", encoding="utf-8").read(),
                            category=campaign.get("category", ""),
                            city=campaign.get("city", ""),
                        )
                        logger.info("Auto-fulfilled campaign %s to %s", search_id, target_email)

    return {"status": "ok"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
