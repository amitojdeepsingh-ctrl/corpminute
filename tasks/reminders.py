"""
Deadline reminder scheduler.
Runs daily — checks fiscal year-end dates and sends alerts at 60 and 30 days.
"""
import logging
from datetime import datetime, timedelta

from config import settings

logger = logging.getLogger("corpminute.reminders")


def _days_until_fiscal_year_end(fiscal_year_end: str) -> int | None:
    """Calculate days until the next occurrence of the fiscal year-end date."""
    if not fiscal_year_end:
        return None
    try:
        fye = datetime.strptime(fiscal_year_end, "%Y-%m-%d")
        today = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
        # Set FYE to current year
        fye_this_year = fye.replace(year=today.year)
        if fye_this_year < today:
            fye_this_year = fye.replace(year=today.year + 1)
        return (fye_this_year - today).days
    except Exception:
        return None


async def run_deadline_reminders() -> None:
    """Check all active corporations and send deadline alerts as needed."""
    from schema import list_active_corps, save_corp
    from documents.generator import generate_directors_resolution, generate_shareholders_resolution
    from documents.pdf import docx_to_pdf_bytes
    from email_sender import send_deadline_alert

    corps = list_active_corps()
    logger.info(f"Checking deadlines for {len(corps)} active corporations")

    for customer_id, corp in corps.items():
        days = _days_until_fiscal_year_end(corp.fiscal_year_end)
        if days is None:
            continue

        approval_link = f"https://{settings.domain}/approve/{customer_id}"

        # 60-day alert
        if 58 <= days <= 62 and not corp.deadline_alert_60_sent:
            logger.info(f"Sending 60-day alert to {corp.corp_name}")
            try:
                draft_doc = generate_directors_resolution(corp)
                pdf_bytes = docx_to_pdf_bytes(draft_doc)
                send_deadline_alert(
                    to=corp.customer_email,
                    corp_name=corp.corp_name,
                    days_remaining=days,
                    fiscal_year_end=corp.fiscal_year_end,
                    pdf_bytes=pdf_bytes,
                    approval_link=approval_link,
                )
                corp.deadline_alert_60_sent = True
                save_corp(customer_id, corp)
            except Exception as e:
                logger.error(f"60-day alert failed for {corp.corp_name}: {e}")

        # 30-day alert (if not yet approved)
        elif 28 <= days <= 32 and not corp.resolutions_approved and not corp.deadline_alert_30_sent:
            logger.info(f"Sending 30-day alert to {corp.corp_name}")
            try:
                draft_doc = generate_directors_resolution(corp)
                pdf_bytes = docx_to_pdf_bytes(draft_doc)
                send_deadline_alert(
                    to=corp.customer_email,
                    corp_name=corp.corp_name,
                    days_remaining=days,
                    fiscal_year_end=corp.fiscal_year_end,
                    pdf_bytes=pdf_bytes,
                    approval_link=approval_link,
                )
                corp.deadline_alert_30_sent = True
                save_corp(customer_id, corp)
            except Exception as e:
                logger.error(f"30-day alert failed for {corp.corp_name}: {e}")

        # 7-day final warning
        elif 5 <= days <= 8 and not corp.resolutions_approved:
            logger.info(f"Sending 7-day final warning to {corp.corp_name}")
            try:
                draft_doc = generate_directors_resolution(corp)
                pdf_bytes = docx_to_pdf_bytes(draft_doc)
                send_deadline_alert(
                    to=corp.customer_email,
                    corp_name=corp.corp_name,
                    days_remaining=days,
                    fiscal_year_end=corp.fiscal_year_end,
                    pdf_bytes=pdf_bytes,
                    approval_link=approval_link,
                )
            except Exception as e:
                logger.error(f"7-day alert failed for {corp.corp_name}: {e}")

        # Reset flags after fiscal year-end passes
        if days > 300:
            corp.deadline_alert_60_sent = False
            corp.deadline_alert_30_sent = False
            corp.resolutions_approved = False
            save_corp(customer_id, corp)
