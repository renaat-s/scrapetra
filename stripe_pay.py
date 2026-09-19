import stripe
from config import STRIPE_SECRET_KEY

stripe.api_key = STRIPE_SECRET_KEY


def create_checkout_session(
    search_id: str,
    origin: str,
    currency: str = "gbp",
    amount: int = 3500,
    product_name: str = "ScrapeTra Lead Package",
) -> dict:
    """Create a Stripe Checkout session for one-time payment.

    Args:
        search_id: The campaign or search ID to associate with payment.
        origin: The base URL for success/cancel redirects.
        currency: Three-letter currency code (gbp, usd). Defaults to gbp.
        amount: Price in minor units (pence/cents). 3500 = £35/$35.
        product_name: Description shown on Stripe checkout.
    """
    try:
        session = stripe.checkout.Session.create(
            payment_method_types=["card"],
            line_items=[
                {
                    "price_data": {
                        "currency": currency,
                        "product_data": {
                            "name": product_name,
                        },
                        "unit_amount": amount,
                    },
                    "quantity": 1,
                }
            ],
            mode="payment",
            success_url=f"{origin}/payment/success?search_id={search_id}",
            cancel_url=f"{origin}/payment/cancel?search_id={search_id}",
            metadata={"search_id": search_id},
        )
        return {"session_id": session.id, "url": session.url}
    except Exception as e:
        return {"error": str(e)}


def verify_webhook(payload: bytes, sig_header: str, endpoint_secret: str) -> dict | None:
    """Verify a Stripe webhook event."""
    try:
        event = stripe.Webhook.construct_event(payload, sig_header, endpoint_secret)
        return event
    except (stripe.error.SignatureVerificationError, ValueError):
        return None
