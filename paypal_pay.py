import requests
import os

PAYPAL_CLIENT_ID = os.getenv("PAYPAL_CLIENT_ID", "")
PAYPAL_SECRET = os.getenv("PAYPAL_SECRET", "")
PAYPAL_MODE = os.getenv("PAYPAL_MODE", "sandbox")
PAYPAL_API = (
    "https://api-m.sandbox.paypal.com"
    if PAYPAL_MODE == "sandbox"
    else "https://api-m.paypal.com"
)


def get_paypal_access_token() -> str | None:
    if not PAYPAL_CLIENT_ID or not PAYPAL_SECRET:
        return None
    try:
        response = requests.post(
            f"{PAYPAL_API}/v1/oauth2/token",
            auth=(PAYPAL_CLIENT_ID, PAYPAL_SECRET),
            data={"grant_type": "client_credentials"},
            timeout=10,
        )
        response.raise_for_status()
        return response.json().get("access_token")
    except Exception:
        return None


def create_paypal_order(
    amount: str,
    currency: str,
    search_id: str,
    origin: str,
    description: str = "ScrapeTra Lead Data CSV",
) -> dict:
    token = get_paypal_access_token()
    if not token:
        return {"error": "PayPal credentials not configured"}

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    payload = {
        "intent": "CAPTURE",
        "purchase_units": [
            {
                "reference_id": search_id[:12],
                "description": description,
                "amount": {
                    "currency_code": currency,
                    "value": amount,
                },
            }
        ],
        "application_context": {
            "return_url": f"{origin}/paypal/success?search_id={search_id}",
            "cancel_url": f"{origin}/paypal/cancel?search_id={search_id}",
            "brand_name": "ScrapeTra Lead Agent",
            "landing_page": "BILLING",
            "user_action": "PAY_NOW",
        },
    }

    try:
        res = requests.post(
            f"{PAYPAL_API}/v2/checkout/orders",
            json=payload,
            headers=headers,
            timeout=10,
        )
        res.raise_for_status()
        data = res.json()

        approve_url = None
        for link in data.get("links", []):
            if link.get("rel") == "approve":
                approve_url = link.get("href")
                break

        return {
            "order_id": data.get("id", ""),
            "approve_url": approve_url,
            "status": data.get("status", ""),
        }
    except Exception as e:
        return {"error": str(e)}


def capture_paypal_order(order_id: str) -> dict:
    token = get_paypal_access_token()
    if not token:
        return {"error": "PayPal credentials not configured"}

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }

    try:
        res = requests.post(
            f"{PAYPAL_API}/v2/checkout/orders/{order_id}/capture",
            headers=headers,
            timeout=10,
        )
        res.raise_for_status()
        data = res.json()

        capture = {}
        for pu in data.get("purchase_units", []):
            for cap in pu.get("payments", {}).get("captures", []):
                capture = cap
                break

        return {
            "order_id": data.get("id", ""),
            "status": data.get("status", ""),
            "capture_id": capture.get("id", ""),
            "amount": capture.get("amount", {}).get("value", "0"),
            "currency": capture.get("amount", {}).get("currency_code", ""),
        }
    except Exception as e:
        return {"error": str(e)}
