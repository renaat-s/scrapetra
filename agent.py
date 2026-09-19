import re
import asyncio
import random
import time
from urllib.parse import urlparse, urljoin

import aiodns
import httpx
from bs4 import BeautifulSoup
from ddgs import DDGS


EMAIL_REGEX = re.compile(
    r"^[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}$"
)

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64; rv:127.0) Gecko/20100101 Firefox/127.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36 Edg/124.0.0.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
]

def _get_dns_resolver() -> aiodns.DNSResolver:
    return aiodns.DNSResolver(timeout=5, tries=2)


def _random_ua() -> str:
    return random.choice(USER_AGENTS)


def is_valid_email_syntax(email: str) -> bool:
    return bool(EMAIL_REGEX.match(email))


async def check_mx_record(domain: str) -> bool:
    try:
        resolver = _get_dns_resolver()
        result = await resolver.query_dns(domain, "MX")
        return len(result.answer) > 0
    except Exception:
        return False


def extract_emails_from_text(text: str) -> list[str]:
    raw = re.findall(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}", text)
    seen = set()
    result = []
    junk_suffixes = (".png", ".jpg", ".gif", ".svg", ".css", ".js", ".ico", ".webp")
    junk_prefixes = ("you@", "test@", "example@", "noreply@", "no-reply@", "donotreply@")
    for e in raw:
        e_lower = e.lower()
        if e_lower not in seen and not e_lower.endswith(junk_suffixes) and not e_lower.startswith(junk_prefixes):
            seen.add(e_lower)
            result.append(e_lower)
    return result


def _ddg_search_sync(keyword: str, max_results: int, region: str = "uk-en") -> list[dict]:
    results = []
    with DDGS() as ddgs:
        for r in ddgs.text(keyword, max_results=max_results, region=region):
            results.append({
                "title": r.get("title", ""),
                "url": r.get("href", ""),
                "snippet": r.get("body", ""),
            })
    return results


async def search_web(keyword: str, max_results: int = 10, region: str = "uk-en") -> list[dict]:
    return await asyncio.to_thread(_ddg_search_sync, keyword, max_results, region)


async def scrape_company_page(url: str, client: httpx.AsyncClient) -> dict:
    company_name = ""
    emails = []
    try:
        resp = await client.get(url, follow_redirects=True)
        resp.raise_for_status()
        html = resp.text

        soup = BeautifulSoup(html, "lxml")

        title_tag = soup.find("title")
        if title_tag and title_tag.string:
            company_name = title_tag.string.strip().split(" - ")[0].split(" | ")[0].strip()

        emails = extract_emails_from_text(html)

        contact_links = [
            a["href"] for a in soup.find_all("a", href=True)
            if "contact" in a["href"].lower()
        ]
        for cp in contact_links[:2]:
            try:
                if cp.startswith("/"):
                    cp = urljoin(url, cp)
                r2 = await client.get(cp, follow_redirects=True)
                emails.extend(extract_emails_from_text(r2.text))
            except Exception:
                continue

    except Exception:
        pass

    return {"company_name": company_name, "emails": list(dict.fromkeys(emails))}


async def verify_batch_emails(emails: list[str]) -> list[dict]:
    tasks = []
    for email in emails:
        tasks.append(_verify_single(email))
    return await asyncio.gather(*tasks)


async def _verify_single(email: str) -> dict:
    syntax_ok = is_valid_email_syntax(email)
    mx_ok = False
    if syntax_ok:
        domain = email.split("@")[1]
        mx_ok = await check_mx_record(domain)
    return {"email": email, "syntax": syntax_ok, "mx_valid": mx_ok}


async def run_agent(keyword: str, desired_count: int, region: str = "uk-en") -> list[dict]:
    search_results = await search_web(keyword, max_results=min(desired_count * 2, 30), region=region)

    leads = []
    seen_domains = set()
    all_emails_to_verify = []

    async with httpx.AsyncClient(
        timeout=httpx.Timeout(10.0),
        headers={"User-Agent": _random_ua()},
        limits=httpx.Limits(max_connections=5, max_keepalive_connections=3),
    ) as client:
        for item in search_results:
            if len(leads) >= desired_count:
                break

            url = item["url"]
            parsed = urlparse(url)
            domain = parsed.netloc.lower().replace("www.", "")

            if domain in seen_domains:
                continue
            seen_domains.add(domain)

            scraped = await scrape_company_page(url, client)

            emails_found = scraped["emails"][:3]
            company = scraped["company_name"] or item["title"]

            if emails_found:
                for email in emails_found:
                    all_emails_to_verify.append({
                        "company_name": company,
                        "company_url": url,
                        "email": email,
                    })
                    if len(all_emails_to_verify) >= desired_count:
                        break
            else:
                leads.append({
                    "company_name": company,
                    "company_url": url,
                    "email": "",
                    "email_valid": False,
                    "domain_valid": False,
                })

            await asyncio.sleep(random.uniform(2.0, 7.0))

    emails_to_check = [e["email"] for e in all_emails_to_verify]
    if emails_to_check:
        verified = await verify_batch_emails(emails_to_check)
        verify_map = {v["email"]: v for v in verified}

        for entry in all_emails_to_verify:
            v = verify_map.get(entry["email"], {})
            leads.append({
                "company_name": entry["company_name"],
                "company_url": entry["company_url"],
                "email": entry["email"],
                "email_valid": v.get("syntax", False),
                "domain_valid": v.get("mx_valid", False),
            })

    return leads[:desired_count]
