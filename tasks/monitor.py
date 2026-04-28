"""
Legal monitoring task — runs every 48 hours at 2:00 AM.
Scrapes federal and provincial corporate law pages for changes.
Flags affected corporations and queues document regeneration.
"""
import hashlib
import json
import logging
from datetime import datetime
from pathlib import Path

from config import settings

logger = logging.getLogger("corpminute.monitor")

# Snapshot file to detect changes between runs
SNAPSHOT_FILE = settings.data_dir / "_law_snapshots.json"

LAW_SOURCES = {
    "federal": {
        "url": "https://laws-lois.justice.gc.ca/eng/acts/C-44/",
        "name": "Canada Business Corporations Act (CBCA)",
        "sections": ["Part IV — Shareholders", "Part XIII — Records"],
    },
    "ontario": {
        "url": "https://www.ontario.ca/laws/statute/90b16",
        "name": "Business Corporations Act (Ontario)",
        "sections": ["Section 140", "Section 141"],
    },
    "bc": {
        "url": "https://www.bclaws.gov.bc.ca/civix/document/id/complete/statreg/02057_00",
        "name": "Business Corporations Act (BC)",
        "sections": ["Division 8"],
    },
    "alberta": {
        "url": "https://kings-printer.alberta.ca/documents/Acts/B09.pdf",
        "name": "Business Corporations Act (Alberta)",
        "sections": ["Part 7"],
    },
    "quebec": {
        "url": "https://www.legisquebec.gouv.qc.ca/en/document/cs/S-31.1",
        "name": "Loi sur les sociétés par actions (Quebec)",
        "sections": ["Art. 105", "Art. 106"],
    },
}


def _load_snapshots() -> dict:
    if SNAPSHOT_FILE.exists():
        try:
            return json.loads(SNAPSHOT_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def _save_snapshots(snapshots: dict) -> None:
    SNAPSHOT_FILE.write_text(json.dumps(snapshots, indent=2), encoding="utf-8")


def _hash_content(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:16]


async def _fetch_page(url: str) -> str:
    """Fetch a web page with Playwright (headless)."""
    try:
        from playwright.async_api import async_playwright
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page()
            await page.goto(url, timeout=30000, wait_until="domcontentloaded")
            content = await page.content()
            await browser.close()
            return content
    except Exception as e:
        logger.error(f"Failed to fetch {url}: {e}")
        return ""


def _extract_relevant_text(html: str, sections: list[str]) -> str:
    """Extract text around relevant section markers."""
    try:
        from html.parser import HTMLParser

        class TextExtractor(HTMLParser):
            def __init__(self):
                super().__init__()
                self.text_parts = []
                self._in_body = True

            def handle_data(self, data):
                if self._in_body and data.strip():
                    self.text_parts.append(data.strip())

        parser = TextExtractor()
        parser.feed(html)
        full_text = " ".join(parser.text_parts)

        # Extract context around each section reference
        relevant = []
        for section in sections:
            idx = full_text.find(section)
            if idx != -1:
                start = max(0, idx - 200)
                end = min(len(full_text), idx + 500)
                relevant.append(full_text[start:end])

        return " | ".join(relevant) if relevant else full_text[:2000]
    except Exception:
        return html[:2000]


async def run_legal_monitoring() -> dict[str, str]:
    """
    Main monitoring task.
    Returns dict of province -> change_description for any detected changes.
    """
    logger.info("Starting legal monitoring scan")
    snapshots = _load_snapshots()
    changes = {}

    for province, source in LAW_SOURCES.items():
        try:
            html = await _fetch_page(source["url"])
            if not html:
                continue

            relevant = _extract_relevant_text(html, source["sections"])
            current_hash = _hash_content(relevant)
            previous_hash = snapshots.get(province, {}).get("hash", "")

            if current_hash != previous_hash:
                change_summary = (
                    f"A potential change was detected in {source['name']}. "
                    f"Please review the legislation at {source['url']} for updates "
                    f"affecting: {', '.join(source['sections'])}."
                )
                changes[province] = change_summary
                logger.info(f"Change detected in {province}: {source['name']}")

                snapshots[province] = {
                    "hash": current_hash,
                    "last_checked": datetime.utcnow().isoformat(),
                    "url": source["url"],
                }
            else:
                if province not in snapshots:
                    snapshots[province] = {}
                snapshots[province]["last_checked"] = datetime.utcnow().isoformat()
                logger.info(f"No change in {province}")

        except Exception as e:
            logger.error(f"Error monitoring {province}: {e}")

    _save_snapshots(snapshots)
    return changes


async def flag_affected_corps_and_regenerate(changes: dict[str, str]) -> None:
    """
    For provinces with detected law changes:
    1. Flag all active corporations in those provinces
    2. Regenerate documents
    3. Email customers
    """
    if not changes:
        return

    from schema import list_active_corps, save_corp
    from documents.generator import generate_full_minute_book, docs_to_zip
    from documents.pdf import generate_minute_book_pdf
    from email_sender import send_law_change_alert

    corps = list_active_corps()

    for customer_id, corp in corps.items():
        if corp.province not in changes:
            continue

        change_summary = changes[corp.province]
        corp.law_changes_pending.append(
            f"{datetime.utcnow().isoformat()}: {change_summary}"
        )
        save_corp(customer_id, corp)

        logger.info(f"Regenerating docs for {corp.corp_name} due to {corp.province} law change")

        try:
            docs = generate_full_minute_book(corp)
            zip_bytes = docs_to_zip(docs)
            pdf_bytes = generate_minute_book_pdf(docs)

            send_law_change_alert(
                to=corp.customer_email,
                corp_name=corp.corp_name,
                province=corp.province,
                change_summary=change_summary,
                pdf_bytes=pdf_bytes,
                zip_bytes=zip_bytes,
            )

            corp.last_generated = datetime.utcnow().isoformat()
            corp.law_changes_pending = []
            save_corp(customer_id, corp)

        except Exception as e:
            logger.error(f"Failed to regenerate for {corp.corp_name}: {e}")


async def run_full_monitoring_cycle() -> None:
    """Complete 48-hour monitoring cycle: detect + regenerate."""
    changes = await run_legal_monitoring()
    await flag_affected_corps_and_regenerate(changes)
    logger.info(f"Monitoring cycle complete. Changes detected: {list(changes.keys())}")
