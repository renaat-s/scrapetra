import csv
import io
import asyncio
import time
import hmac
import hashlib
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
    REGIONS, SCRAPE_TARGETS, PACKAGE_LEAD_COUNT,
    ADMIN_PASSWORD, SESSION_SECRET, SESSION_MAX_AGE,
)
from database import (
    init_db, create_search, update_search_status, insert_leads,
    get_leads, get_search, create_payment, mark_payment_paid_by_session,
    mark_payment_paid_by_paypal, create_download_token,
    validate_download_token, is_search_paid,
    create_campaign, update_campaign, get_campaign, get_all_campaigns,
    log_outreach, update_outreach_status, get_outreach_logs,
    create_package, update_package, get_package, get_packages, get_ready_packages,
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


# --- AUTH HELPERS ---

def _create_session_token(password: str) -> str:
    """Create a signed session token from password + timestamp."""
    ts = str(int(time.time()))
    payload = f"{password}:{ts}"
    sig = hmac.new(SESSION_SECRET.encode(), payload.encode(), hashlib.sha256).hexdigest()
    return f"{ts}:{sig}"


def _verify_session_token(token: str) -> bool:
    """Verify a session token is valid and not expired."""
    try:
        ts_str, sig = token.split(":", 1)
        ts = int(ts_str)
        age = time.time() - ts
        if age > SESSION_MAX_AGE or age < 0:
            return False
        payload = f"{ADMIN_PASSWORD}:{ts_str}"
        expected = hmac.new(SESSION_SECRET.encode(), payload.encode(), hashlib.sha256).hexdigest()
        return hmac.compare_digest(sig, expected)
    except Exception:
        return False


def _require_admin(request: Request) -> bool:
    """Check if request has a valid admin session. Returns True if authenticated."""
    token = request.cookies.get("session_token", "")
    return _verify_session_token(token)


@app.on_event("startup")
async def startup():
    await init_db()


@app.get("/", response_class=HTMLResponse)
async def landing(request: Request):
    return templates.TemplateResponse("landing.html", {"request": request})


@app.get("/leads/{region}/{city_slug}", response_class=HTMLResponse)
async def lead_package_page(request: Request, region: str, city_slug: str):
    if region not in REGIONS:
        raise HTTPException(status_code=404, detail="Region not found")

    region_cfg = REGIONS[region]
    city_name = city_slug.replace("-", " ").title()

    country = "United Kingdom" if region == "uk" else "United States"

    return templates.TemplateResponse("lead-package.html", {
        "request": request,
        "region": region,
        "region_upper": region.upper(),
        "city_slug": city_slug,
        "city": city_name,
        "country": country,
        "currency": region_cfg["currency"],
        "symbol": region_cfg["symbol"],
        "price": f"{region_cfg['price']:.0f}",
        "lead_count": PACKAGE_LEAD_COUNT,
    })


@app.get("/privacy", response_class=HTMLResponse)
async def privacy(request: Request):
    return templates.TemplateResponse("privacy.html", {"request": request})


@app.get("/terms", response_class=HTMLResponse)
async def terms(request: Request):
    return templates.TemplateResponse("terms.html", {"request": request})


@app.get("/robots.txt", response_class=HTMLResponse)
async def robots():
    content = open(os.path.join(__dirname, "static", "robots.txt"), "r", encoding="utf-8").read()
    return HTMLResponse(content, media_type="text/plain")


@app.get("/sitemap.xml", response_class=HTMLResponse)
async def sitemap():
    content = open(os.path.join(__dirname, "static", "sitemap.xml"), "r", encoding="utf-8").read()
    return HTMLResponse(content, media_type="application/xml")


@app.exception_handler(404)
async def not_found_handler(request: Request, exc):
    return templates.TemplateResponse("404.html", {"request": request}, status_code=404)


@app.get("/tools/mx-check", response_class=HTMLResponse)
async def mx_check_page(request: Request):
    return templates.TemplateResponse("mx-check.html", {"request": request})


@app.get("/api/mx-check")
async def mx_check_api(domain: str = Query(...)):
    import re as _re
    import dns.resolver
    domain = domain.strip().lower()
    domain = _re.sub(r'^https?://', '', domain).replace('/', '').lstrip('www.')
    if not _re.match(r'^[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$', domain):
        raise HTTPException(status_code=400, detail="Invalid domain")

    try:
        answers = dns.resolver.resolve(domain, "MX")
        mx_records = [str(r.exchange).rstrip('.') for r in answers]
        return {"domain": domain, "valid": len(mx_records) > 0, "mx_records": mx_records}
    except Exception:
        return {"domain": domain, "valid": False, "mx_records": []}


@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request, next: str = "/dashboard"):
    if _require_admin(request):
        return RedirectResponse(next)
    return templates.TemplateResponse("login.html", {"request": request, "next": next})


@app.post("/login", response_class=HTMLResponse)
async def login_submit(request: Request, password: str = Form(...), next: str = Form("/dashboard")):
    if password != ADMIN_PASSWORD:
        return templates.TemplateResponse("login.html", {
            "request": request, "next": next, "error": "Invalid password"
        }, status_code=401)

    token = _create_session_token(ADMIN_PASSWORD)
    response = RedirectResponse(next, status_code=303)
    response.set_cookie(
        "session_token", token,
        max_age=SESSION_MAX_AGE, httponly=True, samesite="lax",
    )
    return response


@app.get("/logout")
async def logout():
    response = RedirectResponse("/", status_code=303)
    response.delete_cookie("session_token")
    return response


@app.get("/browse", response_class=HTMLResponse)
async def browse_packages(request: Request, region: str = Query("")):
    packages = await get_packages(region=region, status="ready")
    return templates.TemplateResponse("browse.html", {
        "request": request,
        "packages": packages,
        "regions": REGIONS,
        "selected_region": region,
    })


@app.get("/browse/{package_id}", response_class=HTMLResponse)
async def browse_package_detail(request: Request, package_id: str):
    package = await get_package(package_id)
    if not package or package["status"] != "ready":
        raise HTTPException(status_code=404, detail="Package not found")
    leads = await get_leads(package_id)
    region_cfg = REGIONS.get(package["region"], REGIONS["uk"])
    return templates.TemplateResponse("browse-package.html", {
        "request": request,
        "package": package,
        "leads": leads,
        "lead_count": len(leads),
        "symbol": region_cfg["symbol"],
        "currency": region_cfg["currency"],
    })


@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard(request: Request):
    if not _require_admin(request):
        return RedirectResponse("/login?next=/dashboard", status_code=303)
    return templates.TemplateResponse("dashboard.html", {
        "request": request,
        "stripe_publishable_key": STRIPE_PUBLISHABLE_KEY,
        "paypal_client_id": PAYPAL_CLIENT_ID,
    })


@app.get("/api/campaigns")
async def list_campaigns(request: Request):
    if not _require_admin(request):
        raise HTTPException(status_code=401, detail="Admin access required")
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
        import traceback
        tb = traceback.format_exc()
        logger.error("Campaign %s failed: %s\n%s", campaign_id, e, tb)
        await update_campaign(campaign_id, status=f"error: {str(e)[:200]}")


@app.get("/api/campaigns/{campaign_id}")
async def get_campaign_detail(request: Request, campaign_id: str):
    if not _require_admin(request):
        raise HTTPException(status_code=401, detail="Admin access required")
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

    region = campaign.get("region", "uk")
    region_cfg = REGIONS.get(region, REGIONS["uk"])
    browse_base = origin.replace("http://", "https://").replace("127.0.0.1", "www.scrapetra.com")

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
            currency=region_cfg["currency"],
            symbol=region_cfg["symbol"],
            browse_url=f"{browse_base}/browse?region={region}",
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

    region = campaign.get("region", "uk")
    region_cfg = REGIONS.get(region, REGIONS["uk"])
    currency = region_cfg["currency"].lower()
    amount = int(campaign["price"] * 100)

    result = create_checkout_session(
        campaign_id, origin,
        currency=currency,
        amount=amount,
        product_name=f"ScrapeTra: {campaign['name']} - {campaign['valid_emails']} verified leads",
    )
    if "error" in result:
        raise HTTPException(status_code=400, detail=result["error"])

    await create_payment(campaign_id, result["session_id"], amount, region_cfg["currency"])
    return {"url": result["url"], "session_id": result["session_id"]}


@app.post("/api/campaigns/{campaign_id}/checkout-paypal")
async def campaign_paypal_checkout(campaign_id: str, request: Request):
    campaign = await get_campaign(campaign_id)
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")

    origin = str(request.base_url).rstrip("/")

    region = campaign.get("region", "uk")
    region_cfg = REGIONS.get(region, REGIONS["uk"])
    currency = region_cfg["currency"]
    amount = f"{campaign['price']:.2f}"

    result = create_paypal_order(
        amount=amount,
        currency=currency,
        search_id=campaign_id,
        origin=origin,
        description=f"ScrapeTra: {campaign['name']} - {campaign['valid_emails']} verified leads",
    )

    if "error" in result:
        raise HTTPException(status_code=400, detail=result["error"])

    await create_payment(campaign_id, "", int(campaign["price"] * 100), currency)
    return {"approve_url": result["approve_url"], "order_id": result["order_id"]}


@app.post("/api/search")
async def start_search(
    request: Request,
    keyword: str = Form(...),
    count: int = Form(10),
    region: str = Form("uk-en"),
    api_key: str = Form(""),
):
    client_ip = request.client.host if request.client else "unknown"
    if not _check_rate_limit(f"search:{client_ip}"):
        raise HTTPException(status_code=429, detail="Rate limit exceeded. Try again in 1 minute.")
    if api_key != API_KEY:
        raise HTTPException(status_code=401, detail="Invalid API key")

    count = max(1, min(count, 100))
    search_id = await create_search(keyword, count)
    asyncio.create_task(_run_search_agent(search_id, keyword, count, region))
    return JSONResponse({"search_id": search_id, "status": "pending"})


async def _run_search_agent(search_id: str, keyword: str, count: int, region: str = "uk-en"):
    try:
        await update_search_status(search_id, "running")
        leads = await run_agent(keyword, count, region=region)
        await insert_leads(search_id, leads)
        await update_search_status(search_id, "completed")
    except Exception as e:
        await update_search_status(search_id, f"error: {str(e)[:200]}")


@app.get("/api/status/{search_id}")
async def search_status(request: Request, search_id: str):
    if not _require_admin(request):
        raise HTTPException(status_code=401, detail="Admin access required")
    search = await get_search(search_id)
    if not search:
        raise HTTPException(status_code=404, detail="Search not found")
    return search


@app.get("/api/leads/{search_id}")
async def search_leads(request: Request, search_id: str):
    if not _require_admin(request):
        raise HTTPException(status_code=401, detail="Admin access required")
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
    return RedirectResponse("/dashboard")


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
    return RedirectResponse("/dashboard")


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


@app.get("/api/packages")
async def list_packages(region: str = Query(""), status: str = Query("")):
    packages = await get_packages(region=region, status=status)
    return {"packages": packages, "count": len(packages)}


@app.get("/api/packages/{package_id}")
async def package_detail(package_id: str):
    package = await get_package(package_id)
    if not package:
        raise HTTPException(status_code=404, detail="Package not found")
    leads = await get_leads(package_id)
    return {"package": package, "leads": leads, "lead_count": len(leads)}


@app.post("/api/packages/scrape")
async def scrape_package(
    request: Request,
    api_key: str = Form(...),
    region: str = Form(...),
    city: str = Form(...),
):
    if api_key != API_KEY:
        raise HTTPException(status_code=401, detail="Invalid API key")
    if region not in REGIONS:
        raise HTTPException(status_code=400, detail=f"Invalid region. Use: {', '.join(REGIONS.keys())}")

    client_ip = request.client.host if request.client else "unknown"
    if not _check_rate_limit(f"package:{client_ip}"):
        raise HTTPException(status_code=429, detail="Rate limit exceeded")

    region_cfg = REGIONS[region]
    keyword = f"businesses in {city}"
    package_id = await create_package(
        region=region,
        city=city,
        country="United Kingdom" if region == "uk" else "United States",
        currency=region_cfg["currency"],
        price=region_cfg["price"],
        keyword=keyword,
    )

    asyncio.create_task(_run_package_scrape(
        package_id, keyword, PACKAGE_LEAD_COUNT,
        region_cfg["region_code"], region_cfg["currency"], region_cfg["price"],
    ))

    return JSONResponse({"package_id": package_id, "status": "scraping"})


async def _run_package_scrape(
    package_id: str, keyword: str, target_count: int,
    region_code: str, currency: str, price: float,
):
    try:
        leads = await run_agent(keyword, target_count, region=region_code)

        valid_leads = [l for l in leads if l.get("email") and l.get("email_valid")]

        await insert_leads(package_id, valid_leads)

        csv_data = _generate_package_csv(valid_leads)
        csv_path = os.path.join(__dirname, "exports", f"package_{package_id[:8]}.csv")
        os.makedirs(os.path.dirname(csv_path), exist_ok=True)
        with open(csv_path, "w", newline="", encoding="utf-8") as f:
            f.write(csv_data)

        await update_package(
            package_id,
            status="ready",
            lead_count=len(valid_leads),
            csv_path=csv_path,
            completed_at=time.strftime("%Y-%m-%dT%H:%M:%S"),
        )
    except Exception as e:
        import traceback
        tb = traceback.format_exc()
        logger.error("Package %s failed: %s\n%s", package_id, e, tb)
        await update_package(package_id, status=f"error: {str(e)[:200]}")


def _generate_package_csv(leads: list[dict]) -> str:
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


@app.post("/api/packages/{package_id}/checkout-stripe")
async def package_stripe_checkout(package_id: str, request: Request):
    package = await get_package(package_id)
    if not package:
        raise HTTPException(status_code=404, detail="Package not found")
    if package["status"] != "ready":
        raise HTTPException(status_code=400, detail="Package not ready yet")

    origin = str(request.base_url).rstrip("/")
    currency = package["currency"].lower()
    amount = int(package["price"] * 100)

    result = create_checkout_session(
        package_id, origin,
        currency=currency,
        amount=amount,
        product_name=f"ScrapeTra: {package['city']} {package['country']} Lead Package ({package['lead_count']} leads)",
    )
    if "error" in result:
        raise HTTPException(status_code=400, detail=result["error"])

    await create_payment(package_id, result["session_id"], amount, package["currency"])
    return {"url": result["url"], "session_id": result["session_id"]}


@app.post("/api/packages/{package_id}/checkout-paypal")
async def package_paypal_checkout(package_id: str, request: Request):
    package = await get_package(package_id)
    if not package:
        raise HTTPException(status_code=404, detail="Package not found")
    if package["status"] != "ready":
        raise HTTPException(status_code=400, detail="Package not ready yet")

    origin = str(request.base_url).rstrip("/")
    currency = package["currency"]
    amount = f"{package['price']:.2f}"

    result = create_paypal_order(
        amount=amount,
        currency=currency,
        search_id=package_id,
        origin=origin,
        description=f"ScrapeTra: {package['city']} {package['country']} Lead Package ({package['lead_count']} leads)",
    )
    if "error" in result:
        raise HTTPException(status_code=400, detail=result["error"])

    await create_payment(package_id, "", int(package["price"] * 100), currency)
    return {"approve_url": result["approve_url"], "order_id": result["order_id"]}


@app.get("/api/scrape-targets")
async def scrape_targets(request: Request):
    if not _require_admin(request):
        raise HTTPException(status_code=401, detail="Admin access required")
    return {"targets": SCRAPE_TARGETS, "regions": REGIONS}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
