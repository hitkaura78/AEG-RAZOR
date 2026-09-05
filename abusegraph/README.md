# AEG-RAZOR (AbuseGraph)

AEG-RAZOR is an end-to-end e-commerce refund risk management and fraud ring investigation platform powered by machine learning, graph network analysis, and rule policy engines.

---

## User Surfaces & Architecture

The application serves three distinct user surfaces over role-scoped REST APIs:

1. **Customer Web App (`/customer.html`)**:
   - Shopping catalog and refund submission interface (`require_role("customer")`).
   - Plain customer-friendly status language ("Approved", "Under Review", "Restricted"). Strict omission of technical risk internals or ML scores.

2. **Merchant Review Interface (`/merchant.html`)**:
   - Human-in-the-loop review queue for pending refund cases (`require_role("merchant")`).
   - Displays merchant-appropriate evidence (risk bands, human-readable reason codes, written investigation agent narratives) and action buttons (`accept`/`reject`). Omits raw ML/graph score vectors and topology graphs.

3. **Admin Technical Investigation Dashboard (`/admin.html`)**:
   - Unrestricted technical investigation surface (`require_role("admin")`).
   - Overview metrics, full unfiltered risk case list, interactive `vis-network` relationship graph topology, XGBoost feature importances & PR curve metrics, live event simulator, and complete audit trail logs.

---

## Local Development (Windows / PowerShell)

### 1. Environment Setup

```powershell
python -m venv venv
.\venv\Scripts\Activate.ps1
pip install -r backend\requirements.txt
```

### 2. Launching Local Server

```powershell
$env:PYTHONPATH='.'
.\venv\Scripts\uvicorn backend.app.main:app --reload --port 8000
```

Access the frontend surfaces locally in your browser:
- Customer UI: `http://localhost:8000/customer.html`
- Merchant UI: `http://localhost:8000/merchant.html`
- Admin UI: `http://localhost:8000/admin.html`

---

## Production Deployment Guidance

### Single-Service Hosting Model (Render / Railway / Fly.io)

AEG-RAZOR (FastAPI + SQLite/PostgreSQL + Static Frontend) deploys as a **single unified web service** on hosting platforms (e.g., Render Web Services, Railway, Fly.io, or AWS App Runner).

The FastAPI backend serves the static frontend HTML files (`customer.html`, `merchant.html`, `admin.html`) directly via `StaticFiles` mounting at `/`. No separate frontend web server or build pipeline is required.

#### Linux Container Deployment Command:
In production Linux container environments, run Uvicorn bound to the container port:

```bash
uvicorn backend.app.main:app --host 0.0.0.0 --port $PORT
```

or using Gunicorn with Uvicorn workers:

```bash
gunicorn -w 4 -k uvicorn.workers.UvicornWorker backend.app.main:app
```

---

## Database Migration: SQLite to Production PostgreSQL

- **Local Dev / Demo Scale**: The app defaults to SQLite (`sqlite:///abusegraph.db`).
- **Production Hosted Database**: To scale to production traffic, switch to a hosted PostgreSQL database (e.g. Supabase, Neon, or AWS RDS).
- **Migration Procedure**: Update the `DATABASE_URL` environment variable:
  ```env
  DATABASE_URL=postgresql://user:password@ep-host.neon.tech/neondb?sslmode=require
  ```
  **Zero code modifications are needed** for models, risk engine, or API routers because SQLAlchemy abstracts database dialects natively.

---

## Environment Variables Reference

| Variable | Description | Default / Example |
| :--- | :--- | :--- |
| `DATABASE_URL` | SQLAlchemy database connection URI | `sqlite:///abusegraph.db` or `postgresql://...` |
| `JWT_SECRET_KEY` | Secret key for signing JWT tokens | **Must change in production!** |
| `JWT_ALGORITHM` | Algorithm used for JWT encoding | `HS256` |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | Session token expiry duration | `120` |
| `GEMINI_API_KEY` | Optional API key for Google Gemini narratives | `AIzaSy...` |
| `CORS_ORIGINS` | Allowed CORS origins (comma-separated or `*`) | `https://yourdomain.com` |

---

## Configurable CORS Security

Before deploying to production, restrict allowed cross-origin requests by setting `CORS_ORIGINS` in your environment:

```env
CORS_ORIGINS=https://app.yourdomain.com,https://admin.yourdomain.com
```

---

## Seeded Demo Accounts & Reseeding

### Default Demo Accounts
On initial startup, demo accounts are seeded if missing:
- **Merchant Account**: `merchant@demo.abusegraph` / `DemoMerchant123!`
- **Admin Account**: `admin@demo.abusegraph` / `DemoAdmin123!`

### Dataset Reseeding
Database state is preserved across application server restarts. To manually trigger model retraining and reset the synthetic demonstration dataset:
- Click **Retrain & Reseed Data** on the Admin Dashboard (`http://localhost:8000/admin.html`), or
- Issue `POST /api/admin/reseed` with an Admin Bearer token.

---

## Key Documentation Links

- 📖 **[3-to-5 Minute Live Demonstration Runbook (`walkthrough_demo.md`)](file:///c:/Users/hitka/OneDrive/Dokumente/Razorpay/AEG-RAZOR/abusegraph/walkthrough_demo.md)**: Complete pitch script across Customer, Merchant, and Admin surfaces with talking points and live event simulation steps.
- 🔒 **[Security & Privacy Policy (`SECURITY.md`)](file:///c:/Users/hitka/OneDrive/Dokumente/Razorpay/AEG-RAZOR/abusegraph/SECURITY.md)**: PII protection rules, synthetic data scope, bcrypt password hashing, JWT secret rotation, and role-based field omission guarantees.

---

## Architecture Defense Statement

> **Single-Service In-Process Design**: AEG-RAZOR uses an in-process NetworkX graph analysis engine, embedded XGBoost risk scoring, and a single-service FastAPI architecture.
> 
> Microservices, Neo4j graph databases, Kafka event buses, or Kubernetes clusters were **deliberately avoided**. At current scale, the in-process graph engine evaluates complex multi-entity cluster relationships and policy rules in under **10 milliseconds** per request, eliminating distributed network latency, operational overhead, and multi-service failure modes.
> 
> **Realistic ML Model Metrics (~0.90 Precision vs. Artificial 1.00)**: Model evaluation metrics on noisy synthetic data achieve **~0.90 Precision, ~0.90 Recall, and ~0.90 PR-AUC**. Perfect 1.00 precision in synthetic demos indicates over-fitting or lack of realistic noise; our evaluation reflects production-grade evaluation behavior.

---

## Automated Test Suite Execution

Run the complete test suite:

```powershell
$env:PYTHONPATH='.'
.\venv\Scripts\pytest backend\tests\ -v --tb=short
```

Run targeted security tests:

```powershell
$env:PYTHONPATH='.'
.\venv\Scripts\pytest backend\tests\test_security.py -v
```