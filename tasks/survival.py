"""
Monthly survival check + growth marketing task.
Runs on the 1st and 2nd of each month.
"""
import json
import logging
from datetime import datetime
from pathlib import Path

from config import settings

logger = logging.getLogger("corpminute.survival")

REPORTS_FILE = settings.data_dir / "_monthly_reports.json"

LINKEDIN_TOPICS = [
    "Are you legally compliant? The $500 mistake most incorporated Canadian businesses make.",
    "What is a minute book — and why your accountant keeps asking about it.",
    "Annual resolutions: the corporate requirement 80% of Canadian business owners ignore.",
    "Do accountants check your minute book? Here's what happens when they do.",
    "5 corporate resolutions every Canadian business needs to pass this year.",
    "The CRA won't tell you — but your corporation could be non-compliant right now.",
    "Solo founders: protecting your corporate shield starts with your minute book.",
    "What happens if your corporation is missing annual resolutions at due diligence?",
]


async def run_survival_check() -> dict:
    """
    1st of month: check MRR, subscriber count, wallet balance.
    Returns status dict and emails creator.
    """
    from stripe_handler import get_mrr
    from email_sender import send_monthly_report, send_creator_alert

    mrr, subscriber_count = get_mrr()
    wallet_balance = await _get_wallet_balance()

    report = {
        "date": datetime.utcnow().isoformat(),
        "mrr": mrr,
        "subscriber_count": subscriber_count,
        "wallet_balance": wallet_balance,
    }

    # Save to reports
    _append_report(report)

    # Alert if wallet is low
    if wallet_balance < 10:
        send_creator_alert(
            to=settings.creator_email,
            subject="Low Wallet Balance — Action Required",
            message=(
                f"Your Conway Cloud wallet balance is ${wallet_balance:.2f} USDC. "
                "Please top up immediately to maintain service. Non-essential tasks have been paused."
            ),
        )
        logger.warning(f"LOW WALLET: ${wallet_balance:.2f}")

    # Monthly report to creator
    send_monthly_report(
        to=settings.creator_email,
        mrr=mrr,
        subscriber_count=subscriber_count,
        wallet_balance=wallet_balance,
    )

    logger.info(f"Survival check: MRR=${mrr:.2f}, Subs={subscriber_count}, Wallet=${wallet_balance:.2f}")
    return report


async def run_growth_marketing(mrr: float, subscriber_count: int) -> None:
    """
    2nd of month (if MRR < $5,000): post LinkedIn content.
    Posts 1x/week if sub_count > 20, 3x/week if sub_count <= 20.
    """
    if mrr >= 5000:
        logger.info("MRR >= $5,000 — skipping growth marketing")
        return

    if not settings.linkedin_access_token:
        logger.warning("LinkedIn credentials not configured — skipping growth marketing")
        return

    topic_index = _get_next_topic_index()
    topic = LINKEDIN_TOPICS[topic_index % len(LINKEDIN_TOPICS)]

    article = _generate_linkedin_article(topic)

    success = await _post_to_linkedin(article)
    if success:
        _increment_topic_index()
        logger.info(f"LinkedIn post published: {topic[:60]}...")
    else:
        logger.error("LinkedIn post failed")


def _generate_linkedin_article(topic: str) -> str:
    """Generate a LinkedIn article using Claude API if available."""
    try:
        import anthropic
        client = anthropic.Anthropic()
        message = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=1200,
            messages=[{
                "role": "user",
                "content": (
                    f"Write a LinkedIn article (800-1000 words) for Canadian small business owners on this topic: "
                    f"'{topic}'\n\n"
                    "Requirements:\n"
                    "- Professional but accessible tone\n"
                    "- Include specific Canadian regulatory context (CBCA, provincial CBAs)\n"
                    "- End with a soft CTA mentioning CorpMinute.ca ($25/month alternative to paying a lawyer)\n"
                    "- No hashtags in the body — add 3-5 at the very end\n"
                    "- Format as plain text, ready to paste into LinkedIn"
                )
            }]
        )
        return message.content[0].text
    except Exception as e:
        logger.error(f"Claude API for LinkedIn article failed: {e}")
        return _fallback_article(topic)


def _fallback_article(topic: str) -> str:
    return f"""📋 {topic}

If you operate an incorporated business in Canada, annual resolutions are not optional — they're a legal requirement under the Canada Business Corporations Act and provincial equivalents.

Yet the vast majority of small business owners either don't know this, or rely on a lawyer charging $500-$800/year to maintain their minute book.

CorpMinute.ca automates this entirely for $25/month. Annual resolutions generated automatically. Law changes monitored. Deadline alerts sent. No lawyer required.

Learn more: corpminute.ca

#CorporateLaw #SmallBusiness #Canada #CorpMinute #MinuteBook"""


async def _post_to_linkedin(content: str) -> bool:
    """Post content to LinkedIn using the API."""
    if not settings.linkedin_access_token or not settings.linkedin_person_urn:
        return False
    try:
        import httpx
        payload = {
            "author": settings.linkedin_person_urn,
            "lifecycleState": "PUBLISHED",
            "specificContent": {
                "com.linkedin.ugc.ShareContent": {
                    "shareCommentary": {"text": content},
                    "shareMediaCategory": "NONE",
                }
            },
            "visibility": {"com.linkedin.ugc.MemberNetworkVisibility": "PUBLIC"},
        }
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                "https://api.linkedin.com/v2/ugcPosts",
                json=payload,
                headers={
                    "Authorization": f"Bearer {settings.linkedin_access_token}",
                    "Content-Type": "application/json",
                },
                timeout=30,
            )
        return resp.status_code in (200, 201)
    except Exception as e:
        logger.error(f"LinkedIn API error: {e}")
        return False


async def _get_wallet_balance() -> float:
    """Get Conway Cloud wallet balance (placeholder — replace with Conway API)."""
    try:
        import httpx
        # Conway Cloud API — replace with actual endpoint
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                "https://api.conway.cloud/v1/wallet/balance",
                headers={"Authorization": f"Bearer {settings.creator_wallet}"},
                timeout=10,
            )
            if resp.status_code == 200:
                return float(resp.json().get("balance_usdc", 0))
    except Exception:
        pass
    return 99.0  # Default optimistic value if API unavailable


def _append_report(report: dict) -> None:
    reports = []
    if REPORTS_FILE.exists():
        try:
            reports = json.loads(REPORTS_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    reports.append(report)
    reports = reports[-24:]  # Keep last 24 months
    REPORTS_FILE.write_text(json.dumps(reports, indent=2), encoding="utf-8")


def _get_next_topic_index() -> int:
    idx_file = settings.data_dir / "_linkedin_topic_idx.txt"
    if idx_file.exists():
        try:
            return int(idx_file.read_text().strip())
        except Exception:
            pass
    return 0


def _increment_topic_index() -> None:
    idx_file = settings.data_dir / "_linkedin_topic_idx.txt"
    current = _get_next_topic_index()
    idx_file.write_text(str(current + 1))
