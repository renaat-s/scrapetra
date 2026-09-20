#!/usr/bin/env python3
"""
Batch scrape lead packages for all configured cities.

Usage:
    python batch_scrape.py                    # Scrape all UK + US cities
    python batch_scrape.py --region uk        # Scrape UK cities only
    python batch_scrape.py --region us        # Scrape US cities only
    python batch_scrape.py --city "London"    # Scrape a single city
    python batch_scrape.py --dry-run          # Preview without scraping

Requires SCRAPETRA_API_KEY env var (or uses default demo key).
Set SCRAPETRA_BASE_URL env var to target a non-local instance.
"""

import os
import sys
import time
import json
import argparse
import logging
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError

sys.path.insert(0, os.path.dirname(__file__))
from config import REGIONS, SCRAPE_TARGETS, PACKAGE_LEAD_COUNT, API_KEY

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("batch_scrape")

BASE_URL = os.getenv("SCRAPETRA_BASE_URL", "http://127.0.0.1:8000")
API_KEY_OVERRIDE = os.getenv("SCRAPETRA_API_KEY", API_KEY)
SCRAPE_DELAY = 30  # seconds between scrapes to avoid rate limits


def api_post(path: str, data: dict) -> dict:
    """Make a POST request to the ScrapeTra API."""
    url = f"{BASE_URL}{path}"
    boundary = "----BatchScrapeBoundary"
    body = ""
    for key, value in data.items():
        body += f"--{boundary}\r\n"
        body += f'Content-Disposition: form-data; name="{key}"\r\n\r\n'
        body += f"{value}\r\n"
    body += f"--{boundary}--\r\n"

    req = Request(
        url,
        data=body.encode("utf-8"),
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        method="POST",
    )

    try:
        with urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except HTTPError as e:
        error_body = e.read().decode("utf-8", errors="replace")
        logger.error("API error %s: %s", e.code, error_body)
        return {"error": error_body}
    except URLError as e:
        logger.error("Connection error: %s", e.reason)
        return {"error": str(e.reason)}


def check_package_status(package_id: str) -> dict:
    """Check if a package is ready."""
    url = f"{BASE_URL}/api/packages/{package_id}"
    try:
        with urlopen(url, timeout=15) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception:
        return {}


def scrape_city(region: str, city: str, dry_run: bool = False) -> str | None:
    """Trigger a scrape for a single city. Returns package_id or None."""
    region_cfg = REGIONS[region]
    keyword = f"businesses in {city}"

    logger.info(
        "Scraping %s, %s (%s) — %s%s for %d leads",
        city,
        "United Kingdom" if region == "uk" else "United States",
        region.upper(),
        region_cfg["symbol"],
        region_cfg["price"],
        PACKAGE_LEAD_COUNT,
    )

    if dry_run:
        logger.info("  [DRY RUN] Would scrape: %s", keyword)
        return None

    result = api_post("/api/packages/scrape", {
        "api_key": API_KEY_OVERRIDE,
        "region": region,
        "city": city,
    })

    if "error" in result:
        logger.error("  Failed: %s", result["error"])
        return None

    package_id = result.get("package_id", "")
    logger.info("  Started! Package ID: %s", package_id[:8])
    return package_id


def main():
    parser = argparse.ArgumentParser(description="Batch scrape ScrapeTra lead packages")
    parser.add_argument("--region", choices=["uk", "us"], help="Scrape only this region")
    parser.add_argument("--city", help="Scrape only this city")
    parser.add_argument("--dry-run", action="store_true", help="Preview without scraping")
    parser.add_argument("--delay", type=int, default=SCRAPE_DELAY, help="Seconds between scrapes")
    args = parser.parse_args()

    logger.info("ScrapeTra Batch Scraper")
    logger.info("Target: %s", BASE_URL)
    logger.info("API Key: %s...%s", API_KEY_OVERRIDE[:8], API_KEY_OVERRIDE[-4:])
    logger.info("Package size: %d leads", PACKAGE_LEAD_COUNT)
    logger.info("")

    cities_to_scrape = []

    if args.city:
        # Single city — find its region
        found = False
        for region, cities in SCRAPE_TARGETS.items():
            if args.city in cities:
                cities_to_scrape.append((region, args.city))
                found = True
                break
        if not found:
            logger.error("City '%s' not found in SCRAPE_TARGETS", args.city)
            sys.exit(1)
    else:
        # All cities in target region(s)
        regions = [args.region] if args.region else ["uk", "us"]
        for region in regions:
            for city in SCRAPE_TARGETS.get(region, []):
                cities_to_scrape.append((region, city))

    total = len(cities_to_scrape)
    logger.info("Cities to scrape: %d", total)
    logger.info("Estimated time: ~%d minutes", (total * args.delay) // 60)
    logger.info("")

    package_ids = []
    completed = 0
    failed = 0

    for i, (region, city) in enumerate(cities_to_scrape, 1):
        logger.info("[%d/%d] %s, %s", i, total, city, region.upper())

        pkg_id = scrape_city(region, city, dry_run=args.dry_run)
        if pkg_id:
            package_ids.append(pkg_id)
            completed += 1
        else:
            failed += 1

        # Delay between requests (skip on last item or dry run)
        if i < total and not args.dry_run:
            logger.info("  Waiting %ds before next scrape...", args.delay)
            time.sleep(args.delay)

    logger.info("")
    logger.info("=== BATCH COMPLETE ===")
    logger.info("Started: %d | Failed: %d", completed, failed)

    if package_ids and not args.dry_run:
        logger.info("")
        logger.info("Package IDs:")
        for pid in package_ids:
            logger.info("  %s", pid)

        # Save package IDs to file
        output_file = os.path.join(os.path.dirname(__file__), "exports", "batch_packages.json")
        os.makedirs(os.path.dirname(output_file), exist_ok=True)
        with open(output_file, "w") as f:
            json.dump({
                "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "base_url": BASE_URL,
                "total": len(package_ids),
                "packages": [{"id": pid} for pid in package_ids],
            }, f, indent=2)
        logger.info("Saved package IDs to %s", output_file)


if __name__ == "__main__":
    main()
