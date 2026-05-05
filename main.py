"""
CorpMinute.ca — Main application entry point.
FastAPI server + APScheduler for all autonomous tasks.
"""
import asyncio
import io
import json
import logging
import secrets
import time
import uuid
from datetime import datetime, timedelta
from pathlib import Path

import stripe
from fastapi import FastAPI, Request, HTTPException, Header, Depends
from fastapi.responses import HTMLResponse, JSONResponse, Response, FileResponse
from fastapi.staticfiles import StaticFiles
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from config import settings
from schema import Corporation, Director, Officer, Shareholder, save_corp, load_corp, list_active_corps, list_all_corps
from documents.generator import generate_full_minute_book, docs_to_zip, generate_special_resolution
from documents.pdf import generate_minute_book_pdf, docx_to_pdf_bytes
from email_sender import send_welcome_minute_book, send_special_resolution, send_creator_alert, send_catchup_offer
from stripe_handler import verify_webhook, handle_payment_succeeded, handle_subscription_deleted, get_mrr

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s — %(message)s",
    handlers=[
        logging.FileHandler(settings.log_dir / "corpminute.log"),
        logging.StreamHandler(),
    ]
)
logger = logging.getLogger("corpminute")

app = FastAPI(title="CorpMinute.ca", docs_url=None, redoc_url=None)

# Serve static files
if Path("static").exists():
    app.mount("/static", StaticFiles(directory="static"), name="static")

scheduler = AsyncIOScheduler()

# Magic-link token store (in-memory, keyed by token → {email, expires})
_magic_tokens: dict[str, dict] = {}


# ─────────────────────────────────────────────
# STATIC PAGES
# ─────────────────────────────────────────────
@app.get("/", response_class=HTMLResponse)
async def landing():
    return Path("static/index.html").read_text(encoding="utf-8")


@app.get("/blog", response_class=HTMLResponse)
async def blog():
    return Path("static/blog.html").read_text(encoding="utf-8")


@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard():
    return Path("static/dashboard.html").read_text(encoding="utf-8")


@app.get("/login", response_class=HTMLResponse)
async def login_page():
    return """<!DOCTYPE html>
<html><head><title>CorpMinute — CPA Login</title>
<style>body{font-family:Calibri,Arial,sans-serif;display:flex;align-items:center;justify-content:center;min-height:100vh;background:#F3F6FA}
.box{background:#fff;padding:40px;border-radius:12px;box-shadow:0 2px 12px rgba(0,0,0,.1);width:360px;text-align:center}
h1{color:#1A3A6B;font-size:22px;margin-bottom:8px}p{color:#777;font-size:13px;margin-bottom:24px}
input{width:100%;padding:12px;border:1px solid #ddd;border-radius:6px;font-size:14px;margin-bottom:12px}
button{width:100%;background:#1A3A6B;color:#fff;padding:13px;border:none;border-radius:6px;font-size:15px;font-weight:700;cursor:pointer}
.msg{font-size:13px;margin-top:12px;color:#10B981;display:none}</style></head>
<body><div class="box">
<h1>CorpMinute CPA Portal</h1>
<p>Enter your registered email to receive a login link.</p>
<input type="email" id="email" placeholder="cpa@example.com">
<button onclick="requestLink()">Send Login Link</button>
<p class="msg" id="msg">Check your email for the login link.</p>
</div>
<script>
async function requestLink(){
  const email=document.getElementById('email').value;
  if(!email)return;
  await fetch('/api/auth/magic-link',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({email})});
  document.getElementById('msg').style.display='block';
}
</script></body></html>"""


# ─────────────────────────────────────────────
# AUTH — Magic Link
# ─────────────────────────────────────────────
@app.post("/api/auth/magic-link")
async def send_magic_link(request: Request):
    body = await request.json()
    email = body.get("email", "").strip().lower()
    if not email:
        raise HTTPException(400, "Email required")

    # Verify this email has at least one CPA-plan corporation
    corps = list_all_corps()
    is_cpa = any(c.cpa_email.lower() == email or c.customer_email.lower() == email for c in corps.values())
    if not is_cpa and email != settings.creator_email.lower():
        # Still return 200 — don't reveal whether email is registered
        return {"ok": True}

    token = secrets.token_urlsafe(32)
    _magic_tokens[token] = {"email": email, "expires": time.time() + 3600}

    link = f"https://{settings.domain}/dashboard?token={token}"

    from email_sender import send_email
    send_email(
        to=email,
        subject="Your CorpMinute login link",
        html_body=f"""<div style="font-family:Calibri,Arial,sans-serif;padding:32px">
<h2 style="color:#1A3A6B">Your login link</h2>
<p>Click below to access your CPA dashboard. Link expires in 1 hour.</p>
<a href="{link}" style="display:inline-block;background:#1A3A6B;color:#fff;padding:14px 28px;border-radius:6px;font-weight:bold;margin:16px 0">Access Dashboard</a>
<p style="font-size:12px;color:#888">If you didn't request this, ignore this email.</p></div>"""
    )
    return {"ok": True}


def _verify_cpa_token(authorization: str = Header(default="")) -> str:
    token = authorization.replace("Bearer ", "").strip()
    entry = _magic_tokens.get(token)
    if not entry or entry["expires"] < time.time():
        raise HTTPException(401, "Invalid or expired token")
    return entry["email"]


# ─────────────────────────────────────────────
# CPA API
# ─────────────────────────────────────────────
@app.get("/api/cpa/corps")
async def get_cpa_corps(cpa_email: str = Depends(_verify_cpa_token)):
    all_corps = list_all_corps()
    # Filter to corps this CPA manages (or creator sees all)
    if cpa_email == settings.creator_email.lower():
        visible = all_corps
    else:
        visible = {
            cid: c for cid, c in all_corps.items()
            if c.cpa_email.lower() == cpa_email or c.customer_email.lower() == cpa_email
        }
    return {"cpa_email": cpa_email, "corps": {k: v.model_dump() for k, v in visible.items()}}


@app.post("/api/cpa/add-corp")
async def cpa_add_corp(request: Request, cpa_email: str = Depends(_verify_cpa_token)):
    body = await request.json()
    customer_id = str(uuid.uuid4())

    corp = Corporation(
        corp_name=body.get("corp_name", ""),
        corp_number=body.get("corp_number", ""),
        province=body.get("province", "ontario"),
        fiscal_year_end=body.get("fiscal_year_end", ""),
        incorporation_date=body.get("incorporation_date", ""),
        customer_email=body.get("customer_email", ""),
        cpa_email=cpa_email,
        plan=body.get("plan", "cpa"),
        status="active",
        directors=[Director(**d) for d in body.get("directors", [])],
        officers=[Officer(**o) for o in body.get("officers", [])],
        shareholders=[Shareholder(**s) for s in body.get("shareholders", [])],
    )
    save_corp(customer_id, corp)

    # Generate and deliver asynchronously
    asyncio.create_task(_generate_and_deliver_task(customer_id, corp))
    return {"ok": True, "customer_id": customer_id}


@app.post("/api/cpa/regenerate/{customer_id}")
async def cpa_regenerate(customer_id: str, cpa_email: str = Depends(_verify_cpa_token)):
    corp = load_corp(customer_id)
    if not corp:
        raise HTTPException(404, "Corporation not found")
    asyncio.create_task(_generate_and_deliver_task(customer_id, corp))
    return {"ok": True}


@app.post("/api/cpa/bulk-generate")
async def cpa_bulk_generate(cpa_email: str = Depends(_verify_cpa_token)):
    all_corps = list_all_corps()
    if cpa_email == settings.creator_email.lower():
        targets = all_corps
    else:
        targets = {cid: c for cid, c in all_corps.items() if c.cpa_email.lower() == cpa_email}

    for cid, corp in targets.items():
        if corp.status == "active":
            asyncio.create_task(_generate_and_deliver_task(cid, corp))

    return {"ok": True, "count": len(targets)}


@app.get("/api/cpa/download/{customer_id}")
async def cpa_download(customer_id: str, token: str):
    entry = _magic_tokens.get(token)
    if not entry or entry["expires"] < time.time():
        raise HTTPException(401, "Invalid token")
    corp = load_corp(customer_id)
    if not corp:
        raise HTTPException(404)
    docs = generate_full_minute_book(corp)
    zip_bytes = docs_to_zip(docs)
    safe_name = corp.corp_name.replace(" ", "_")
    return Response(
        content=zip_bytes,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="minute_book_{safe_name}.zip"'}
    )


# ─────────────────────────────────────────────
# STRIPE WEBHOOK
# ─────────────────────────────────────────────
@app.post("/webhook")
async def stripe_webhook(request: Request):
    payload = await request.body()
    sig = request.headers.get("stripe-signature", "")
    try:
        event = verify_webhook(payload, sig)
    except stripe.SignatureVerificationError:
        raise HTTPException(400, "Invalid signature")

    event_type = event["type"]
    logger.info(f"Stripe webhook: {event_type}")

    if event_type in ("checkout.session.completed", "invoice.payment_succeeded"):
        asyncio.create_task(asyncio.to_thread(handle_payment_succeeded, event))
    elif event_type == "customer.subscription.deleted":
        asyncio.create_task(asyncio.to_thread(handle_subscription_deleted, event))

    return {"received": True}


# ─────────────────────────────────────────────
# SPECIAL RESOLUTION FORM
# ─────────────────────────────────────────────
@app.post("/api/special-resolution")
async def special_resolution_request(request: Request):
    """Public endpoint for special resolution requests (linked from customer emails)."""
    body = await request.json()
    customer_id = body.get("customer_id", "")
    resolution_type = body.get("resolution_type", "")
    details = body.get("details", "")
    resolution_date = body.get("date", "")

    corp = load_corp(customer_id)
    if not corp or corp.status != "active":
        raise HTTPException(404, "Corporation not found or inactive")

    doc = generate_special_resolution(corp, resolution_type, details, resolution_date)

    doc_buf = io.BytesIO()
    doc.save(doc_buf)
    docx_bytes = doc_buf.getvalue()
    pdf_bytes = docx_to_pdf_bytes(doc)

    send_special_resolution(
        to=corp.customer_email,
        corp_name=corp.corp_name,
        resolution_type=resolution_type,
        pdf_bytes=pdf_bytes,
        docx_bytes=docx_bytes,
    )

    logger.info(f"Special resolution generated: {resolution_type} for {corp.corp_name}")
    return {"ok": True, "message": "Resolution generated and emailed within 30 seconds."}


# ─────────────────────────────────────────────
# RESOLUTION APPROVAL (from deadline alert email)
# ─────────────────────────────────────────────
@app.get("/approve/{customer_id}", response_class=HTMLResponse)
async def approve_resolutions(customer_id: str):
    corp = load_corp(customer_id)
    if not corp:
        raise HTTPException(404)
    corp.resolutions_approved = True
    save_corp(customer_id, corp)
    return f"""<!DOCTYPE html><html><head><title>Resolutions Approved</title></head>
<body style="font-family:Calibri,Arial,sans-serif;display:flex;align-items:center;justify-content:center;min-height:100vh;background:#F3F6FA">
<div style="background:#fff;padding:40px;border-radius:12px;text-align:center;max-width:400px">
<div style="font-size:48px;margin-bottom:16px">✅</div>
<h2 style="color:#1A3A6B">Resolutions Approved</h2>
<p style="color:#555;margin-top:8px">Annual resolutions for <strong>{corp.corp_name}</strong> have been marked as approved. We'll generate and deliver your final documents shortly.</p>
</div></body></html>"""


# ─────────────────────────────────────────────
# CLIENT READ-ONLY SHARE LINK
# ─────────────────────────────────────────────
@app.get("/client/{customer_id}", response_class=HTMLResponse)
async def client_view(customer_id: str):
    corp = load_corp(customer_id)
    if not corp:
        raise HTTPException(404)
    return f"""<!DOCTYPE html><html><head><title>{corp.corp_name} — CorpMinute</title>
<style>body{{font-family:Calibri,Arial,sans-serif;background:#F3F6FA;padding:40px 5%}}
h1{{color:#1A3A6B}}table{{border-collapse:collapse;width:100%;margin-top:16px}}
td,th{{padding:10px 14px;border:1px solid #ddd;font-size:13px}}</style></head>
<body>
<h1>{corp.corp_name}</h1>
<p style="color:#777;margin-top:6px">Corporate records — read-only view provided by CorpMinute.ca</p>
<table>
<tr><th>Field</th><th>Value</th></tr>
<tr><td>Province</td><td>{corp.province}</td></tr>
<tr><td>Incorporation Date</td><td>{corp.incorporation_date or '—'}</td></tr>
<tr><td>Fiscal Year-End</td><td>{corp.fiscal_year_end or '—'}</td></tr>
<tr><td>Plan</td><td>{corp.plan}</td></tr>
<tr><td>Last Documents Generated</td><td>{corp.last_generated[:10] if corp.last_generated else '—'}</td></tr>
<tr><td>Resolutions Approved</td><td>{'Yes' if corp.resolutions_approved else 'Pending'}</td></tr>
</table>
</body></html>"""


# ─────────────────────────────────────────────
# INTERNAL API — Sovereign Auditor agent only
# ─────────────────────────────────────────────
def _check_internal_key(x_api_key: str = Header(default="")) -> None:
    """Dependency that enforces the internal API key."""
    expected = settings.internal_api_key
    if not expected or x_api_key != expected:
        raise HTTPException(status_code=401, detail="Invalid internal API key")


@app.post("/api/internal/seed-test-corp")
async def seed_test_corp(
    request: Request,
    _: None = Depends(_check_internal_key),
) -> JSONResponse:
    """Creates a test corporation for Auditor agent testing. Idempotent."""
    body = await request.json()
    customer_email = body.get("email", "")
    if not customer_email:
        raise HTTPException(status_code=400, detail="email required")

    # Check if test corp already exists
    corps = list_all_corps()
    for cid, corp in corps.items():
        if corp.corp_name == "Amitoj Test Holdings Inc.":
            return JSONResponse({"created": False, "customer_id": cid, "message": "Already exists"})

    customer_id = str(uuid.uuid4())
    corp = Corporation(
        corp_name="Amitoj Test Holdings Inc.",
        corp_number="ON-1234567",
        province="ontario",
        incorporation_date="2022-01-15",
        fiscal_year_end="2024-12-31",
        customer_email=customer_email,
        plan="solo",
        status="pending",
        created_at="2026-04-20T10:00:00",
        directors=[Director(name="Amitoj Singh", address="123 Test St, Toronto ON", appointed="2022-01-15")],
        officers=[Officer(name="Amitoj Singh", role="President", appointed="2022-01-15")],
        shareholders=[Shareholder(name="Amitoj Singh", share_class="Common", quantity=100)],
    )
    save_corp(customer_id, corp)
    return JSONResponse({"created": True, "customer_id": customer_id})


@app.get("/api/internal/audit-candidates")
async def audit_candidates(
    _: None = Depends(_check_internal_key),
) -> JSONResponse:
    """
    Returns corporations that may need a compliance follow-up:
    - Have a customer email
    - Are on the free 'solo' plan or have never generated documents
    - Were created more than 3 days ago
    """
    from datetime import timezone
    corps = list_all_corps()
    now = datetime.now(timezone.utc)
    candidates = []

    for cid, corp in corps.items():
        if not corp.customer_email:
            continue
        if corp.plan not in ("solo", "pending") and corp.last_generated:
            continue
        try:
            created = datetime.fromisoformat(corp.created_at.replace("Z", "+00:00"))
            if created.tzinfo is None:
                created = created.replace(tzinfo=timezone.utc)
            days_old = (now - created).days
        except Exception:
            days_old = 0

        if days_old < 3:
            continue

        candidates.append({
            "customer_id": cid,
            "corp_name": corp.corp_name,
            "email": corp.customer_email,
            "plan": corp.plan,
            "days_since_signup": days_old,
            "last_generated": corp.last_generated or None,
            "province": corp.province,
        })

    return JSONResponse({"candidates": candidates, "total": len(candidates)})


@app.post("/api/internal/send-followup")
async def send_followup(
    request: Request,
    _: None = Depends(_check_internal_key),
) -> JSONResponse:
    """
    Sends a Catch-Up Package offer email to a specific corporation.
    Body: { "customer_id": "...", "years_missing": 2 }
    """
    body = await request.json()
    customer_id = body.get("customer_id", "")
    years_missing = int(body.get("years_missing", 1))

    corps = list_all_corps()
    corp = corps.get(customer_id)
    if not corp:
        raise HTTPException(status_code=404, detail="Corporation not found")
    if not corp.customer_email:
        raise HTTPException(status_code=400, detail="No customer email on file")

    sent = send_catchup_offer(
        to=corp.customer_email,
        corp_name=corp.corp_name,
        years_missing=years_missing,
    )

    logger.info(f"Auditor follow-up {'sent' if sent else 'FAILED'} to {corp.customer_email} for {corp.corp_name}")
    return JSONResponse({"sent": sent, "to": corp.customer_email, "corp": corp.corp_name})


# ─────────────────────────────────────────────
# HEALTH CHECK
# ─────────────────────────────────────────────
@app.get("/health")
async def health():
    corps = list_all_corps()
    active = sum(1 for c in corps.values() if c.status == "active")
    return {"status": "operational", "active_corps": active, "timestamp": datetime.utcnow().isoformat()}


# ─────────────────────────────────────────────
# BACKGROUND TASK HELPER
# ─────────────────────────────────────────────
async def _generate_and_deliver_task(customer_id: str, corp: Corporation) -> None:
    try:
        docs = generate_full_minute_book(corp)
        zip_bytes = docs_to_zip(docs)
        pdf_bytes = generate_minute_book_pdf(docs, corp=corp)
        corp.last_generated = datetime.utcnow().isoformat()
        save_corp(customer_id, corp)
        send_welcome_minute_book(
            to=corp.customer_email,
            corp_name=corp.corp_name,
            pdf_bytes=pdf_bytes,
            zip_bytes=zip_bytes,
        )
        logger.info(f"Documents delivered for {corp.corp_name}")
    except Exception as e:
        logger.error(f"Generation failed for {customer_id}: {e}")
        send_creator_alert(settings.creator_email, "Generation failure", f"{corp.corp_name}: {e}")


# ─────────────────────────────────────────────
# SCHEDULED TASKS
# ─────────────────────────────────────────────
async def task_legal_monitoring():
    logger.info("TASK: Legal monitoring started")
    from tasks.monitor import run_full_monitoring_cycle
    await run_full_monitoring_cycle()


async def task_deadline_reminders():
    logger.info("TASK: Deadline reminders started")
    from tasks.reminders import run_deadline_reminders
    await run_deadline_reminders()


async def task_survival_check():
    logger.info("TASK: Monthly survival check")
    from tasks.survival import run_survival_check
    report = await run_survival_check()
    return report


async def task_growth_marketing():
    logger.info("TASK: Growth marketing")
    from tasks.survival import run_growth_marketing
    mrr, subs = get_mrr()
    await run_growth_marketing(mrr, subs)


def setup_scheduler():
    # Legal monitoring — every 48h at 2:00 AM
    scheduler.add_job(task_legal_monitoring, CronTrigger(hour=2, minute=0, day="*/2"), id="legal_monitor", replace_existing=True)

    # Deadline reminders — daily at 8:00 AM
    scheduler.add_job(task_deadline_reminders, CronTrigger(hour=8, minute=0), id="deadline_reminders", replace_existing=True)

    # Survival check — 1st of month at 6:00 AM
    scheduler.add_job(task_survival_check, CronTrigger(day=1, hour=6, minute=0), id="survival_check", replace_existing=True)

    # Growth marketing — 2nd of month at 10:00 AM
    scheduler.add_job(task_growth_marketing, CronTrigger(day=2, hour=10, minute=0), id="growth_marketing", replace_existing=True)

    scheduler.start()
    logger.info("Scheduler started — 4 recurring tasks registered")


# ─────────────────────────────────────────────
# STARTUP
# ─────────────────────────────────────────────
@app.on_event("startup")
async def startup():
    logger.info("=" * 60)
    logger.info("CorpMinute.ca starting up")
    logger.info(f"Domain: {settings.domain}")
    logger.info(f"Data dir: {settings.data_dir}")
    logger.info("=" * 60)
    setup_scheduler()

    # Run boot sequence
    asyncio.create_task(boot_sequence())


async def boot_sequence():
    await asyncio.sleep(3)  # Wait for server to be ready

    logger.info("Running boot sequence...")

    # Verify API keys
    issues = []
    if not settings.stripe_secret_key:
        issues.append("STRIPE_SECRET_KEY not set")
    if not settings.resend_api_key:
        issues.append("RESEND_API_KEY not set")

    if issues:
        logger.warning(f"Boot issues: {', '.join(issues)}")
        if settings.creator_email:
            send_creator_alert(settings.creator_email, "Configuration incomplete", "\n".join(issues))
    else:
        logger.info("All API keys present")

    # Generate test document on first boot
    boot_flag = settings.data_dir / "_booted.flag"
    if not boot_flag.exists():
        logger.info("First boot — generating test minute book")
        await _run_first_boot_test()
        boot_flag.write_text(datetime.utcnow().isoformat())

    logger.info("CorpMinute is operational. Ready for first customer.")


async def _run_first_boot_test():
    """Generate a test minute book and email it to creator for quality review."""
    from schema import Director, Officer, Shareholder

    test_corp = Corporation(
        corp_name="CorpMinute Test Inc.",
        corp_number="BC1234567",
        province="bc",
        incorporation_date="2024-01-15",
        fiscal_year_end="2024-12-31",
        directors=[Director(name="Jane Test", address="123 Test St, Vancouver BC V6B 1A1", appointed="2024-01-15")],
        officers=[Officer(name="Jane Test", role="President and Secretary", appointed="2024-01-15")],
        shareholders=[Shareholder(name="Jane Test", share_class="Common", quantity=100)],
        customer_email=settings.creator_email,
        status="active",
    )

    try:
        docs = generate_full_minute_book(test_corp)
        zip_bytes = docs_to_zip(docs)
        pdf_bytes = generate_minute_book_pdf(docs, corp=test_corp)

        if settings.creator_email and settings.resend_api_key:
            send_welcome_minute_book(
                to=settings.creator_email,
                corp_name="CorpMinute Test Inc.",
                pdf_bytes=pdf_bytes,
                zip_bytes=zip_bytes,
            )
            logger.info(f"Test minute book emailed to {settings.creator_email}")
        else:
            # Save locally
            out_dir = Path("output/test_minute_book")
            from documents.generator import save_docs_to_dir
            save_docs_to_dir(docs, out_dir)
            logger.info(f"Test minute book saved to {out_dir}")
    except Exception as e:
        logger.error(f"Boot test generation failed: {e}")


@app.on_event("shutdown")
async def shutdown():
    scheduler.shutdown()
    logger.info("CorpMinute shutting down")


# ─────────────────────────────────────────────
# ENTRY POINT
# ─────────────────────────────────────────────
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=settings.port, reload=False)
