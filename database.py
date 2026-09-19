import asyncpg
import os
import uuid
import secrets
import ssl
from datetime import datetime, timedelta

DATABASE_URL = os.getenv("DATABASE_URL", "")

_pool: asyncpg.Pool | None = None


async def _get_pool() -> asyncpg.Pool:
    global _pool
    if _pool is None or _pool.is_closing():
        ssl_ctx = ssl.create_default_context()
        ssl_ctx.check_hostname = False
        ssl_ctx.verify_mode = ssl.CERT_NONE
        _pool = await asyncpg.create_pool(
            DATABASE_URL,
            min_size=1,
            max_size=5,
            ssl=ssl_ctx,
        )
    return _pool


async def init_db():
    import logging
    logger = logging.getLogger("scrapetra.db")
    try:
        pool = await _get_pool()
        async with pool.acquire() as db:
            await db.execute("""
                CREATE TABLE IF NOT EXISTS searches (
                    id TEXT PRIMARY KEY,
                    keyword TEXT NOT NULL,
                    count INTEGER NOT NULL,
                    status TEXT DEFAULT 'pending',
                    created_at TEXT,
                    completed_at TEXT
                )
            """)
            await db.execute("""
                CREATE TABLE IF NOT EXISTS leads (
                    id TEXT PRIMARY KEY,
                    search_id TEXT NOT NULL,
                    company_name TEXT,
                    company_url TEXT,
                    email TEXT,
                    email_valid INTEGER DEFAULT 0,
                    domain_valid INTEGER DEFAULT 0
                )
            """)
            await db.execute("""
                DO $$
                BEGIN
                    IF EXISTS (
                        SELECT 1 FROM information_schema.table_constraints
                        WHERE constraint_name = 'leads_search_id_fkey'
                    ) THEN
                        ALTER TABLE leads DROP CONSTRAINT leads_search_id_fkey;
                    END IF;
                END $$;
            """)
            await db.execute("""
                CREATE TABLE IF NOT EXISTS payments (
                    id TEXT PRIMARY KEY,
                    search_id TEXT NOT NULL,
                    stripe_session_id TEXT,
                    paypal_order_id TEXT,
                    amount INTEGER DEFAULT 0,
                    currency TEXT DEFAULT 'GBP',
                    status TEXT DEFAULT 'pending',
                    created_at TEXT
                )
            """)
            await db.execute("""
                CREATE TABLE IF NOT EXISTS download_tokens (
                    token TEXT PRIMARY KEY,
                    search_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    used INTEGER DEFAULT 0
                )
            """)
            await db.execute("""
                CREATE TABLE IF NOT EXISTS campaigns (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    category TEXT NOT NULL,
                    city TEXT NOT NULL,
                    target_count INTEGER DEFAULT 20,
                    price REAL DEFAULT 35.00,
                    template_style TEXT DEFAULT 'standard',
                    status TEXT DEFAULT 'pending',
                    leads_found INTEGER DEFAULT 0,
                    valid_emails INTEGER DEFAULT 0,
                    csv_path TEXT,
                    buyer_email TEXT,
                    stripe_url TEXT,
                    paypal_url TEXT,
                    bank_sort TEXT,
                    bank_account TEXT,
                    bank_ref TEXT,
                    created_at TEXT,
                    completed_at TEXT,
                    delivered_at TEXT
                )
            """)
            await db.execute("""
                CREATE TABLE IF NOT EXISTS outreach_log (
                    id TEXT PRIMARY KEY,
                    campaign_id TEXT NOT NULL,
                    lead_email TEXT NOT NULL,
                    subject TEXT,
                    status TEXT DEFAULT 'queued',
                    sent_at TEXT,
                    opened_at TEXT,
                    clicked_at TEXT,
                    FOREIGN KEY (campaign_id) REFERENCES campaigns(id)
                )
            """)
            await db.execute("""
                CREATE TABLE IF NOT EXISTS packages (
                    id TEXT PRIMARY KEY,
                    region TEXT NOT NULL,
                    city TEXT NOT NULL,
                    country TEXT NOT NULL,
                    currency TEXT NOT NULL,
                    price REAL NOT NULL,
                    lead_count INTEGER DEFAULT 0,
                    status TEXT DEFAULT 'scraping',
                    csv_path TEXT,
                    keyword TEXT,
                    created_at TEXT,
                    completed_at TEXT,
                    sold_at TEXT
                )
            """)
    except Exception as e:
        logger.error("Database initialization failed: %s", e)


async def create_search(keyword: str, count: int) -> str:
    search_id = str(uuid.uuid4())
    now = datetime.utcnow().isoformat()
    pool = await _get_pool()
    async with pool.acquire() as db:
        await db.execute(
            "INSERT INTO searches (id, keyword, count, status, created_at) VALUES ($1, $2, $3, 'pending', $4)",
            search_id, keyword, count, now,
        )
    return search_id


async def update_search_status(search_id: str, status: str):
    pool = await _get_pool()
    async with pool.acquire() as db:
        now = datetime.utcnow().isoformat()
        if status == "completed":
            await db.execute(
                "UPDATE searches SET status = $1, completed_at = $2 WHERE id = $3",
                status, now, search_id,
            )
        else:
            await db.execute("UPDATE searches SET status = $1 WHERE id = $2", status, search_id)


async def insert_leads(search_id: str, leads: list[dict]):
    pool = await _get_pool()
    async with pool.acquire() as db:
        for lead in leads:
            lead_id = str(uuid.uuid4())
            await db.execute(
                """INSERT INTO leads (id, search_id, company_name, company_url, email, email_valid, domain_valid)
                   VALUES ($1, $2, $3, $4, $5, $6, $7)""",
                lead_id,
                search_id,
                lead.get("company_name", ""),
                lead.get("company_url", ""),
                lead.get("email", ""),
                1 if lead.get("email_valid") else 0,
                1 if lead.get("domain_valid") else 0,
            )


async def get_leads(search_id: str) -> list[dict]:
    pool = await _get_pool()
    async with pool.acquire() as db:
        rows = await db.fetch("SELECT * FROM leads WHERE search_id = $1", search_id)
        return [dict(row) for row in rows]


async def get_search(search_id: str) -> dict | None:
    pool = await _get_pool()
    async with pool.acquire() as db:
        row = await db.fetchrow("SELECT * FROM searches WHERE id = $1", search_id)
        return dict(row) if row else None


async def create_payment(search_id: str, stripe_session_id: str = "", amount: int = 0, currency: str = "GBP") -> str:
    payment_id = str(uuid.uuid4())
    now = datetime.utcnow().isoformat()
    pool = await _get_pool()
    async with pool.acquire() as db:
        await db.execute(
            "INSERT INTO payments (id, search_id, stripe_session_id, amount, currency, status, created_at) VALUES ($1, $2, $3, $4, $5, 'pending', $6)",
            payment_id, search_id, stripe_session_id, amount, currency, now,
        )
    return payment_id


async def update_payment_status(payment_id: str, status: str):
    pool = await _get_pool()
    async with pool.acquire() as db:
        await db.execute("UPDATE payments SET status = $1 WHERE id = $2", status, payment_id)


async def mark_payment_paid_by_session(stripe_session_id: str):
    pool = await _get_pool()
    async with pool.acquire() as db:
        await db.execute(
            "UPDATE payments SET status = 'paid' WHERE stripe_session_id = $1",
            stripe_session_id,
        )


async def mark_payment_paid_by_paypal(order_id: str):
    pool = await _get_pool()
    async with pool.acquire() as db:
        await db.execute(
            "UPDATE payments SET status = 'paid' WHERE paypal_order_id = $1",
            order_id,
        )


async def create_download_token(search_id: str, ttl_seconds: int = 3600) -> str:
    token = secrets.token_urlsafe(32)
    now = datetime.utcnow()
    expires = now + timedelta(seconds=ttl_seconds)
    pool = await _get_pool()
    async with pool.acquire() as db:
        await db.execute(
            "INSERT INTO download_tokens (token, search_id, created_at, expires_at, used) VALUES ($1, $2, $3, $4, 0)",
            token, search_id, now.isoformat(), expires.isoformat(),
        )
    return token


async def validate_download_token(token: str, search_id: str) -> bool:
    pool = await _get_pool()
    async with pool.acquire() as db:
        row = await db.fetchrow(
            "SELECT * FROM download_tokens WHERE token = $1 AND search_id = $2 AND used = 0",
            token, search_id,
        )
        if not row:
            return False
        row = dict(row)
        expires = datetime.fromisoformat(row["expires_at"])
        if datetime.utcnow() > expires:
            return False
        await db.execute(
            "UPDATE download_tokens SET used = 1 WHERE token = $1", token
        )
        return True


async def is_search_paid(search_id: str) -> bool:
    pool = await _get_pool()
    async with pool.acquire() as db:
        row = await db.fetchval(
            "SELECT COUNT(*) FROM payments WHERE search_id = $1 AND status = 'paid'",
            search_id,
        )
        return row > 0


async def create_campaign(
    name: str,
    category: str,
    city: str,
    target_count: int = 20,
    price: float = 35.0,
    template_style: str = "standard",
) -> str:
    campaign_id = str(uuid.uuid4())
    now = datetime.utcnow().isoformat()
    pool = await _get_pool()
    async with pool.acquire() as db:
        await db.execute(
            """INSERT INTO campaigns
               (id, name, category, city, target_count, price, template_style, status, created_at)
               VALUES ($1, $2, $3, $4, $5, $6, $7, 'pending', $8)""",
            campaign_id, name, category, city, target_count, price, template_style, now,
        )
    return campaign_id


async def update_campaign(campaign_id: str, **kwargs):
    allowed = {
        "status", "leads_found", "valid_emails", "csv_path",
        "buyer_email", "stripe_url", "paypal_url",
        "bank_sort", "bank_account", "bank_ref",
        "completed_at", "delivered_at",
    }
    updates = {k: v for k, v in kwargs.items() if k in allowed}
    if not updates:
        return
    keys = list(updates.keys())
    values = list(updates.values())
    set_clause = ", ".join(f"{k} = ${i+1}" for i, k in enumerate(keys))
    pool = await _get_pool()
    async with pool.acquire() as db:
        await db.execute(f"UPDATE campaigns SET {set_clause} WHERE id = ${len(keys)+1}", *values, campaign_id)


async def get_campaign(campaign_id: str) -> dict | None:
    pool = await _get_pool()
    async with pool.acquire() as db:
        row = await db.fetchrow("SELECT * FROM campaigns WHERE id = $1", campaign_id)
        return dict(row) if row else None


async def get_all_campaigns(limit: int = 50) -> list[dict]:
    pool = await _get_pool()
    async with pool.acquire() as db:
        rows = await db.fetch("SELECT * FROM campaigns ORDER BY created_at DESC LIMIT $1", limit)
        return [dict(row) for row in rows]


async def log_outreach(campaign_id: str, lead_email: str, subject: str) -> str:
    log_id = str(uuid.uuid4())
    pool = await _get_pool()
    async with pool.acquire() as db:
        await db.execute(
            "INSERT INTO outreach_log (id, campaign_id, lead_email, subject, status) VALUES ($1, $2, $3, $4, 'queued')",
            log_id, campaign_id, lead_email, subject,
        )
    return log_id


async def update_outreach_status(log_id: str, status: str):
    pool = await _get_pool()
    async with pool.acquire() as db:
        now = datetime.utcnow().isoformat()
        await db.execute(
            "UPDATE outreach_log SET status = $1, sent_at = $2 WHERE id = $3",
            status, now, log_id,
        )


async def get_outreach_logs(campaign_id: str) -> list[dict]:
    pool = await _get_pool()
    async with pool.acquire() as db:
        rows = await db.fetch(
            "SELECT * FROM outreach_log WHERE campaign_id = $1 ORDER BY sent_at DESC",
            campaign_id,
        )
        return [dict(row) for row in rows]


async def create_package(
    region: str, city: str, country: str, currency: str,
    price: float, keyword: str = "",
) -> str:
    package_id = str(uuid.uuid4())
    now = datetime.utcnow().isoformat()
    pool = await _get_pool()
    async with pool.acquire() as db:
        await db.execute(
            """INSERT INTO packages
               (id, region, city, country, currency, price, status, keyword, created_at)
               VALUES ($1, $2, $3, $4, $5, $6, 'scraping', $7, $8)""",
            package_id, region, city, country, currency, price, keyword, now,
        )
    return package_id


async def update_package(package_id: str, **kwargs):
    allowed = {
        "status", "lead_count", "csv_path", "completed_at", "sold_at",
    }
    updates = {k: v for k, v in kwargs.items() if k in allowed}
    if not updates:
        return
    keys = list(updates.keys())
    values = list(updates.values())
    set_clause = ", ".join(f"{k} = ${i+1}" for i, k in enumerate(keys))
    pool = await _get_pool()
    async with pool.acquire() as db:
        await db.execute(
            f"UPDATE packages SET {set_clause} WHERE id = ${len(keys)+1}",
            *values, package_id,
        )


async def get_package(package_id: str) -> dict | None:
    pool = await _get_pool()
    async with pool.acquire() as db:
        row = await db.fetchrow("SELECT * FROM packages WHERE id = $1", package_id)
        return dict(row) if row else None


async def get_packages(
    region: str = "", status: str = "", limit: int = 50
) -> list[dict]:
    pool = await _get_pool()
    async with pool.acquire() as db:
        query = "SELECT * FROM packages WHERE 1=1"
        params = []
        if region:
            params.append(region)
            query += f" AND region = ${len(params)}"
        if status:
            params.append(status)
            query += f" AND status = ${len(params)}"
        params.append(limit)
        query += f" ORDER BY created_at DESC LIMIT ${len(params)}"
        rows = await db.fetch(query, *params)
        return [dict(row) for row in rows]


async def get_ready_packages(region: str = "") -> list[dict]:
    return await get_packages(region=region, status="ready")
