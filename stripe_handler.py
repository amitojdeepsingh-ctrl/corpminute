"""
Stripe webhook handler + subscription management.
Handles payment events and triggers document generation.
"""
import io
import json
import logging
import time
from datetime import datetime

import stripe

from config import settings
from schema import Corporation, Director, Officer, Shareholder, save_corp, load_corp, list_active_corps
from documents.generator import generate_full_minute_book, docs_to_zip
from documents.pdf import generate_minute_book_pdf
from email_sender import send_welcome_minute_book, send_creator_alert

logger = logging.getLogger("corpminute.stripe")


def init_stripe():
    stripe.api_key = settings.stripe_secret_key


def verify_webhook(payload: bytes, sig_header: str) -> dict:
    """Verify Stripe webhook signature and return event dict."""
    init_stripe()
    event = stripe.Webhook.construct_event(
        payload, sig_header, settings.stripe_webhook_secret
    )
    return event


def handle_payment_succeeded(event: dict) -> None:
    """
    Fires on payment_intent.succeeded or checkout.session.completed.
    Generates full minute book and emails it to the customer.
    """
    init_stripe()
    obj = event["data"]["object"]
    event_type = event["type"]

    customer_id = None
    subscription_id = None
    customer_email = None
    plan = "solo"

    if event_type == "checkout.session.completed":
        customer_id = obj.get("customer")
        subscription_id = obj.get("subscription")
        customer_email = obj.get("customer_details", {}).get("email")
        metadata = obj.get("metadata", {})
        plan = metadata.get("plan", "solo")

        # Build corp from metadata submitted in Stripe checkout
        corp = _build_corp_from_checkout(obj, customer_id, subscription_id, plan)

    elif event_type == "invoice.payment_succeeded":
        customer_id = obj.get("customer")
        subscription_id = obj.get("subscription")
        # Load existing corp — renewal payment
        corp = load_corp(customer_id)
        if not corp:
            logger.warning(f"Received renewal for unknown customer {customer_id}")
            return
        # Regenerate documents on renewal
        _generate_and_deliver(customer_id, corp)
        return
    else:
        logger.info(f"Unhandled payment event type: {event_type}")
        return

    if not corp:
        logger.error(f"Could not build corporation for customer {customer_id}")
        return

    save_corp(customer_id, corp)
    _generate_and_deliver(customer_id, corp)
    logger.info(f"Onboarding complete for {corp.corp_name} ({customer_id})")


def handle_subscription_deleted(event: dict) -> None:
    """Mark corporation as cancelled when subscription ends."""
    obj = event["data"]["object"]
    customer_id = obj.get("customer")
    if not customer_id:
        return
    corp = load_corp(customer_id)
    if corp:
        corp.status = "cancelled"
        save_corp(customer_id, corp)
        logger.info(f"Cancelled subscription for {corp.corp_name}")


def _build_corp_from_checkout(obj: dict, customer_id: str, subscription_id: str, plan: str) -> Corporation:
    """
    Build a Corporation object from Stripe checkout session metadata.
    The checkout session must include metadata fields set via Stripe Checkout + intake form.
    """
    meta = obj.get("metadata", {})
    customer_email = obj.get("customer_details", {}).get("email", "")

    directors_raw = json.loads(meta.get("directors", "[]"))
    directors = [Director(**d) for d in directors_raw] if directors_raw else []

    officers_raw = json.loads(meta.get("officers", "[]"))
    officers = [Officer(**o) for o in officers_raw] if officers_raw else []

    shareholders_raw = json.loads(meta.get("shareholders", "[]"))
    shareholders = [Shareholder(**s) for s in shareholders_raw] if shareholders_raw else []

    return Corporation(
        corp_name=meta.get("corp_name", "Unknown Corporation"),
        corp_number=meta.get("corp_number", ""),
        province=meta.get("province", "ontario"),
        incorporation_date=meta.get("incorporation_date", ""),
        fiscal_year_end=meta.get("fiscal_year_end", ""),
        business_type=meta.get("business_type", "Corporation"),
        directors=directors,
        officers=officers,
        shareholders=shareholders,
        stripe_customer_id=customer_id,
        stripe_subscription_id=subscription_id or "",
        plan=plan,
        customer_email=customer_email,
        status="active",
        last_generated=datetime.utcnow().isoformat(),
    )


def _generate_and_deliver(customer_id: str, corp: Corporation) -> None:
    """Generate minute book and email it to the customer."""
    logger.info(f"Generating minute book for {corp.corp_name}")

    docs = generate_full_minute_book(corp)
    zip_bytes = docs_to_zip(docs)
    pdf_bytes = generate_minute_book_pdf(docs, corp=corp)

    corp.last_generated = datetime.utcnow().isoformat()
    save_corp(customer_id, corp)

    success = send_welcome_minute_book(
        to=corp.customer_email,
        corp_name=corp.corp_name,
        pdf_bytes=pdf_bytes,
        zip_bytes=zip_bytes,
    )

    if not success:
        send_creator_alert(
            to=settings.creator_email,
            subject="Email delivery failed",
            message=f"Failed to deliver minute book to {corp.customer_email} for {corp.corp_name}."
        )


def get_mrr() -> tuple[float, int]:
    """Return (monthly_recurring_revenue, subscriber_count) from Stripe."""
    init_stripe()
    try:
        subscriptions = stripe.Subscription.list(status="active", limit=100)
        count = 0
        mrr = 0.0
        for sub in subscriptions.auto_paging_iter():
            count += 1
            for item in sub["items"]["data"]:
                price = item["price"]
                amount = price.get("unit_amount", 0) / 100
                interval = price.get("recurring", {}).get("interval", "month")
                if interval == "year":
                    mrr += amount / 12
                else:
                    mrr += amount
        return mrr, count
    except Exception as e:
        logger.error(f"Stripe MRR check failed: {e}")
        return 0.0, 0
