"""
Email delivery via Resend API.
All outbound customer emails go through here.
"""
import logging
from typing import Optional

import resend

from config import settings

logger = logging.getLogger("corpminute.email")


def _init_resend():
    resend.api_key = settings.resend_api_key


# ─────────────────────────────────────────────
# CORE SEND
# ─────────────────────────────────────────────
def send_email(
    to: str,
    subject: str,
    html_body: str,
    attachments: Optional[list[dict]] = None,
) -> bool:
    """
    Send an email via Resend.
    attachments: list of {"filename": "...", "content": <bytes>}
    Returns True on success.
    """
    _init_resend()
    try:
        params = {
            "from": settings.from_email,
            "to": [to],
            "subject": subject,
            "html": html_body,
        }
        if attachments:
            params["attachments"] = [
                {
                    "filename": a["filename"],
                    "content": list(a["content"]),  # Resend requires list[int]
                }
                for a in attachments
            ]
        resend.Emails.send(params)
        logger.info(f"Email sent to {to}: {subject}")
        return True
    except Exception as e:
        logger.error(f"Email failed to {to}: {e}")
        return False


# ─────────────────────────────────────────────
# TEMPLATES
# ─────────────────────────────────────────────
def send_welcome_minute_book(
    to: str,
    corp_name: str,
    pdf_bytes: bytes,
    zip_bytes: bytes,
) -> bool:
    subject = f"Your CorpMinute minute book is ready — {corp_name}"
    html = f"""
<div style="font-family:Calibri,Arial,sans-serif;max-width:600px;margin:0 auto">
  <div style="background:#1A3A6B;padding:24px;text-align:center">
    <h1 style="color:#fff;margin:0;font-size:22px">CorpMinute.ca</h1>
    <p style="color:#B8D0F0;margin:8px 0 0">Corporate Minute Book Automation</p>
  </div>
  <div style="padding:32px;background:#fff">
    <h2 style="color:#1A3A6B">Your minute book is ready</h2>
    <p>Hi,</p>
    <p>Your complete corporate minute book for <strong>{corp_name}</strong> has been generated and is attached to this email.</p>
    <p>You'll find two attachments:</p>
    <ul>
      <li><strong>minute_book_{corp_name.replace(' ','_')}.pdf</strong> — print-ready PDF of all documents</li>
      <li><strong>minute_book_{corp_name.replace(' ','_')}.zip</strong> — editable DOCX files for each document</li>
    </ul>
    <p>Your minute book includes:</p>
    <ul>
      <li>Annual Directors' Resolution</li>
      <li>Annual Shareholders' Resolution</li>
      <li>Register of Directors, Officers &amp; Shareholders</li>
      <li>Share Ledger &amp; Consent to Act as Director</li>
      <li>Organizational Resolution &amp; Banking Resolution</li>
    </ul>
    <p>We'll automatically monitor federal and provincial corporate law and alert you 60 days before your fiscal year-end when new resolutions are due.</p>
    <hr style="border:none;border-top:1px solid #eee;margin:24px 0">
    <p style="font-size:12px;color:#888">{settings.from_email} &nbsp;|&nbsp; corpminute.ca</p>
  </div>
</div>
"""
    safe_name = corp_name.replace(" ", "_")
    return send_email(
        to=to,
        subject=subject,
        html_body=html,
        attachments=[
            {"filename": f"minute_book_{safe_name}.pdf", "content": pdf_bytes},
            {"filename": f"minute_book_{safe_name}.zip", "content": zip_bytes},
        ],
    )


def send_law_change_alert(
    to: str,
    corp_name: str,
    province: str,
    change_summary: str,
    pdf_bytes: bytes,
    zip_bytes: bytes,
) -> bool:
    subject = f"Your CorpMinute documents have been updated — {corp_name}"
    html = f"""
<div style="font-family:Calibri,Arial,sans-serif;max-width:600px;margin:0 auto">
  <div style="background:#1A3A6B;padding:24px;text-align:center">
    <h1 style="color:#fff;margin:0;font-size:22px">CorpMinute.ca</h1>
  </div>
  <div style="padding:32px;background:#fff">
    <h2 style="color:#1A3A6B">Documents updated due to law change</h2>
    <p>Hi,</p>
    <p>We detected a change in <strong>{province}</strong> corporate law that affects your minute book for <strong>{corp_name}</strong>.</p>
    <div style="background:#FFF8E1;border-left:4px solid #F59E0B;padding:16px;margin:16px 0">
      <strong>What changed:</strong><br>{change_summary}
    </div>
    <p>We've automatically regenerated your affected documents. Updated copies are attached.</p>
    <hr style="border:none;border-top:1px solid #eee;margin:24px 0">
    <p style="font-size:12px;color:#888">{settings.from_email}</p>
  </div>
</div>
"""
    safe_name = corp_name.replace(" ", "_")
    return send_email(
        to=to,
        subject=subject,
        html_body=html,
        attachments=[
            {"filename": f"minute_book_{safe_name}_updated.pdf", "content": pdf_bytes},
            {"filename": f"minute_book_{safe_name}_updated.zip", "content": zip_bytes},
        ],
    )


def send_deadline_alert(
    to: str,
    corp_name: str,
    days_remaining: int,
    fiscal_year_end: str,
    pdf_bytes: bytes,
    approval_link: str,
) -> bool:
    urgency = "Final Warning" if days_remaining <= 7 else ("Second Reminder" if days_remaining <= 30 else "Heads Up")
    subject = f"{urgency}: {corp_name} Annual Resolutions Due in {days_remaining} Days"
    html = f"""
<div style="font-family:Calibri,Arial,sans-serif;max-width:600px;margin:0 auto">
  <div style="background:#{'DC2626' if days_remaining<=7 else ('F59E0B' if days_remaining<=30 else '1A3A6B')};padding:24px;text-align:center">
    <h1 style="color:#fff;margin:0;font-size:22px">CorpMinute.ca</h1>
    <p style="color:rgba(255,255,255,0.8);margin:8px 0 0">{days_remaining} days remaining</p>
  </div>
  <div style="padding:32px;background:#fff">
    <h2 style="color:#1A3A6B">Annual resolutions due in {days_remaining} days</h2>
    <p>Hi,</p>
    <p>The annual resolutions for <strong>{corp_name}</strong> are due by <strong>{fiscal_year_end}</strong>. That's {days_remaining} days from today.</p>
    <p>We've pre-drafted your resolutions. Click below to review and approve:</p>
    <div style="text-align:center;margin:24px 0">
      <a href="{approval_link}" style="background:#1A3A6B;color:#fff;padding:14px 28px;text-decoration:none;border-radius:6px;font-weight:bold">Review &amp; Approve Resolutions</a>
    </div>
    <p>Your draft is also attached as a PDF for review.</p>
    {'<div style="background:#FEE2E2;border-left:4px solid #DC2626;padding:16px;margin:16px 0"><strong>Warning:</strong> Failure to maintain annual resolutions can result in penalties and may jeopardize the corporate shield that protects your personal assets.</div>' if days_remaining <= 30 else ''}
    <hr style="border:none;border-top:1px solid #eee;margin:24px 0">
    <p style="font-size:12px;color:#888">{settings.from_email}</p>
  </div>
</div>
"""
    safe_name = corp_name.replace(" ", "_")
    return send_email(
        to=to,
        subject=subject,
        html_body=html,
        attachments=[{"filename": f"draft_resolutions_{safe_name}.pdf", "content": pdf_bytes}],
    )


def send_special_resolution(
    to: str,
    corp_name: str,
    resolution_type: str,
    pdf_bytes: bytes,
    docx_bytes: bytes,
) -> bool:
    subject = f"Special Resolution Ready — {corp_name}"
    html = f"""
<div style="font-family:Calibri,Arial,sans-serif;max-width:600px;margin:0 auto">
  <div style="background:#1A3A6B;padding:24px;text-align:center">
    <h1 style="color:#fff;margin:0;font-size:22px">CorpMinute.ca</h1>
  </div>
  <div style="padding:32px;background:#fff">
    <h2 style="color:#1A3A6B">Your special resolution is ready</h2>
    <p>Your <strong>{resolution_type.replace('_',' ').title()}</strong> resolution for <strong>{corp_name}</strong> has been generated and is attached.</p>
    <p>Both PDF and editable DOCX formats are included.</p>
    <hr style="border:none;border-top:1px solid #eee;margin:24px 0">
    <p style="font-size:12px;color:#888">{settings.from_email}</p>
  </div>
</div>
"""
    safe_name = corp_name.replace(" ", "_")
    return send_email(
        to=to,
        subject=subject,
        html_body=html,
        attachments=[
            {"filename": f"special_resolution_{safe_name}.pdf", "content": pdf_bytes},
            {"filename": f"special_resolution_{safe_name}.docx", "content": docx_bytes},
        ],
    )


def send_monthly_report(
    to: str,
    mrr: float,
    subscriber_count: int,
    wallet_balance: float,
) -> bool:
    status = "healthy" if mrr > 200 else "needs attention"
    subject = f"CorpMinute Monthly Report — ${mrr:.0f} MRR"
    html = f"""
<div style="font-family:Calibri,Arial,sans-serif;max-width:600px;margin:0 auto">
  <div style="background:#1A3A6B;padding:24px;text-align:center">
    <h1 style="color:#fff;margin:0;font-size:22px">CorpMinute.ca</h1>
    <p style="color:#B8D0F0;margin:8px 0 0">Monthly Status Report</p>
  </div>
  <div style="padding:32px;background:#fff">
    <h2 style="color:#1A3A6B">System status: {status.title()}</h2>
    <table style="width:100%;border-collapse:collapse">
      <tr><td style="padding:8px;border-bottom:1px solid #eee"><strong>Monthly Recurring Revenue</strong></td><td style="padding:8px;border-bottom:1px solid #eee">${mrr:.2f}</td></tr>
      <tr><td style="padding:8px;border-bottom:1px solid #eee"><strong>Active Subscribers</strong></td><td style="padding:8px;border-bottom:1px solid #eee">{subscriber_count}</td></tr>
      <tr><td style="padding:8px"><strong>Compute Wallet Balance</strong></td><td style="padding:8px">${wallet_balance:.2f} USDC</td></tr>
    </table>
    {'<div style="background:#FEE2E2;border-left:4px solid #DC2626;padding:16px;margin:16px 0"><strong>Low Wallet Alert:</strong> Please top up your Conway Cloud wallet to maintain service.</div>' if wallet_balance < 10 else ''}
  </div>
</div>
"""
    return send_email(to=to, subject=subject, html_body=html)


def send_creator_alert(to: str, subject: str, message: str) -> bool:
    html = f"""
<div style="font-family:Calibri,Arial,sans-serif;max-width:600px;margin:0 auto;padding:24px">
  <h2 style="color:#DC2626">CorpMinute Alert</h2>
  <p>{message}</p>
</div>
"""
    return send_email(to=to, subject=f"[CorpMinute Alert] {subject}", html_body=html)
