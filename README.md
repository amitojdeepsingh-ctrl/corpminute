# CorpMinute.ca — Setup Guide

## Your 0.01% — One Evening

Everything is built. You just need to connect the services.

---

## Step 1 — Install dependencies (5 min)

```bash
cd corpminute
pip install -r requirements.txt
playwright install chromium
```

---

## Step 2 — Register domain (30 min)

1. Go to [Namecheap.com](https://namecheap.com)
2. Search: `corpminute.ca` — if taken, try `minutebook.ca`
3. Register for 1 year (~$15 CAD)
4. Enable WhoisGuard (free)

---

## Step 3 — Set up Stripe (20 min)

1. Go to [stripe.com](https://stripe.com) → Create account
2. Complete identity verification
3. Create 4 Products:
   - **Solo Corp** → Recurring → $25/month
   - **Active Business** → Recurring → $49/month
   - **Catch-Up Package** → One-time → $149
   - **Special Resolution** → One-time → $9
4. Copy each **Price ID** (starts with `price_`)
5. Go to **Developers → Webhooks → Add endpoint**
   - URL: `https://corpminute.ca/webhook`
   - Events: `checkout.session.completed`, `invoice.payment_succeeded`, `customer.subscription.deleted`
6. Copy the **Webhook Signing Secret** (starts with `whsec_`)

---

## Step 4 — Set up Resend (10 min)

1. Go to [resend.com](https://resend.com) → Sign up free
2. Add domain: `corpminute.ca`
3. Add the DNS records Resend provides to Namecheap
4. Create API key → copy it

---

## Step 5 — Configure environment

```bash
cp .env.example .env
```

Edit `.env` and fill in:
```
STRIPE_SECRET_KEY=sk_live_...
STRIPE_WEBHOOK_SECRET=whsec_...
STRIPE_PRICE_SOLO_MONTHLY=price_...
STRIPE_PRICE_ACTIVE_MONTHLY=price_...
STRIPE_PRICE_CATCHUP=price_...
STRIPE_PRICE_SPECIAL_RESOLUTION=price_...
RESEND_API_KEY=re_...
CREATOR_EMAIL=amitoj.deep.singh@gmail.com
DOMAIN=corpminute.ca
```

---

## Step 6 — Run

```bash
python main.py
```

On first boot:
- Generates a test minute book for "CorpMinute Test Inc."
- Emails it to your `CREATOR_EMAIL` for quality review
- Starts the CPA dashboard on port 8080
- Registers all 4 scheduled tasks

---

## Step 7 — Deploy to server

For production, run behind a reverse proxy (nginx + SSL):

```bash
# Install nginx, certbot
# Configure nginx to proxy port 8080 → corpminute.ca
# Get SSL cert: certbot --nginx -d corpminute.ca
# Run with systemd or screen:
screen -S corpminute
python main.py
```

Or deploy to a VPS (DigitalOcean, Hetzner) for ~$6/month.

---

## Step 8 — Update landing page Stripe links

In `static/index.html`, replace these placeholders with your Stripe Buy Button links:
- `https://buy.stripe.com/SOLO_LINK`
- `https://buy.stripe.com/ACTIVE_LINK`
- `https://buy.stripe.com/CATCHUP_LINK`

---

## Step 9 — Launch

Email 5 incorporated business owners you know.  
Post in r/canadasmallbusiness.  
DM 3 accountants.

Month 1 target: 8 subscribers → $200 MRR.

---

## Architecture

```
main.py               FastAPI app + APScheduler
config.py             Settings from .env
schema.py             Corporation data model (JSON files in data/)
documents/
  generator.py        python-docx — 10 minute book documents
  pdf.py              PDF generation (LibreOffice or WeasyPrint)
tasks/
  monitor.py          Legal scraping (Playwright) — every 48h
  reminders.py        Fiscal year-end alerts — daily
  survival.py         MRR check + LinkedIn marketing — monthly
stripe_handler.py     Stripe webhook processing
email_sender.py       Resend API — all outbound email
static/
  index.html          Landing page
  dashboard.html      CPA dashboard
data/                 One JSON file per corporation
logs/                 Application logs
```

## Scheduled Tasks

| Task | Schedule | What it does |
|------|----------|-------------|
| Legal monitoring | Every 48h at 2:00 AM | Scrapes 5 provincial law sites, regenerates on change |
| Deadline reminders | Daily at 8:00 AM | 60d / 30d / 7d alerts with draft PDFs |
| Survival check | 1st of month | Counts MRR, checks wallet, emails you |
| Growth marketing | 2nd of month | Posts LinkedIn article if MRR < $5,000 |

## Revenue Targets

| Month | MRR | Action if below |
|-------|-----|----------------|
| 1 | $200 | Email 5 contacts, post Reddit |
| 2 | $500 | First CPA partner outreach |
| 3 | $1,500 | 2 CPA partners |
| 6 | $8,000 | White-label inquiries |
| 12 | $35,000 | Full system |
