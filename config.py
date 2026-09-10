import os

STRIPE_SECRET_KEY = os.getenv("STRIPE_SECRET_KEY", "sk_test_XXXX")
STRIPE_PUBLISHABLE_KEY = os.getenv("STRIPE_PUBLISHABLE_KEY", "pk_test_XXXX")
STRIPE_PRICE_ID = os.getenv("STRIPE_PRICE_ID", "price_XXXX")
STRIPE_WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET", "")

PAYPAL_CLIENT_ID = os.getenv("PAYPAL_CLIENT_ID", "")
PAYPAL_SECRET = os.getenv("PAYPAL_SECRET", "")
PAYPAL_MODE = os.getenv("PAYPAL_MODE", "sandbox")

API_KEY = os.getenv("SCRAPETRA_API_KEY", "scrapetra-demo-key-2026")

EXPORT_DIR = os.path.join(os.path.dirname(__file__), "exports")
os.makedirs(EXPORT_DIR, exist_ok=True)

ALLOWED_ORIGINS = os.getenv(
    "SCRAPETRA_ALLOWED_ORIGINS", "http://127.0.0.1:8000,http://localhost:8000"
).split(",")

SMTP_HOST = os.getenv("SCRAPETRA_SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(os.getenv("SCRAPETRA_SMTP_PORT", "587"))
SMTP_USER = os.getenv("SCRAPETRA_SMTP_USER", "")
SMTP_PASS = os.getenv("SCRAPETRA_SMTP_PASS", "")
SENDER_EMAIL = os.getenv("SCRAPETRA_SENDER_EMAIL", "noreply@scrapetra.com")
SENDER_NAME = os.getenv("SCRAPETRA_SENDER_NAME", "ScrapeTra Lead Agent")

BANK_SORT_CODE = os.getenv("SCRAPETRA_BANK_SORT_CODE", "04-00-04")
BANK_ACCOUNT = os.getenv("SCRAPETRA_BANK_ACCOUNT", "12345678")
BANK_NAME = os.getenv("SCRAPETRA_BANK_NAME", "Monzo Business")

DEFAULT_LEAD_PRICE = float(os.getenv("SCRAPETRA_LEAD_PRICE", "35.00"))
