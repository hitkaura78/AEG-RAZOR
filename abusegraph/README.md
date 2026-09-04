# AEG-RAZOR (AbuseGraph)

AEG-RAZOR is an end-to-end e-commerce refund risk management and fraud ring investigation platform powered by machine learning, graph network analysis, and rule policy engines.

## System Architecture & User Surfaces

The application serves three distinct user surfaces over role-scoped REST APIs:

1. **Customer Web App (`/customer.html`)**:
   - Realistic shopping and refund submission interface (`require_role("customer")`).
   - Plain customer-friendly status language ("Approved", "Under Review", "Restricted"). Strict omission of technical risk internals or ML scores.

2. **Merchant Review Interface (`/merchant.html`)**:
   - Human-in-the-loop review queue for pending refund cases (`require_role("merchant")`).
   - Displays merchant-appropriate evidence (risk bands, human-readable reason codes, written investigation agent narratives) and action buttons (`accept`/`reject`). Omits raw ML/graph score vectors and topology graphs.

3. **Admin Technical Investigation Dashboard (`/admin.html`)**:
   - Unrestricted technical investigation surface (`require_role("admin")`).
   - Overview metrics, full unfiltered risk case list, interactive `vis-network` relationship graph topology, XGBoost feature importances & PR curve metrics, live event simulator, and complete audit trail logs.

---

## Setup & Running the Application

### 1. Environment Setup

```powershell
python -m venv venv
.\venv\Scripts\Activate.ps1
pip install -r backend\requirements.txt
```

### 2. Launching the FastAPI Server

```powershell
$env:PYTHONPATH='.'
.\venv\Scripts\uvicorn backend.app.main:app --reload --port 8000
```

Access the frontend surfaces in your browser:
- Customer UI: `http://localhost:8000/customer.html`
- Merchant UI: `http://localhost:8000/merchant.html`
- Admin UI: `http://localhost:8000/admin.html`

---

## Seeded Demo Accounts

On initial startup, the database automatically seeds demo accounts if they do not exist:

- **Merchant Account**: `merchant@demo.abusegraph` / `DemoMerchant123!`
- **Admin Account**: `admin@demo.abusegraph` / `DemoAdmin123!`
- **Customer Accounts**: Registered dynamically via `POST /api/auth/register` or the Customer UI login/register form.

---

## Database Persistence & Intentional Reseeding

### State Preservation Across Restarts
The backend uses SQLite (`abusegraph.db`). **All database state (users, orders, refunds, risk cases, audit logs) is preserved across application server restarts.** 

During server startup (`lifespan` context in `backend/app/main.py`), table schemas and default seed accounts/products are checked idempotently. Existing records are never wiped or reset automatically on restart.

### Intentional Dataset Reseeding
To intentionally retrain the ML pipeline and reset the synthetic demonstration dataset:
- **Via Admin UI**: Navigate to `http://localhost:8000/admin.html`, sign in as admin, and click **Retrain & Reseed Data**.
- **Via API**: Issue a POST request to `/api/admin/reseed` with an Admin Bearer token.

---

## Running the Automated Test Suite

To run the complete unit, integration, and end-to-end test suite:

```powershell
$env:PYTHONPATH='.'
.\venv\Scripts\pytest backend\tests\ -v
```

To run the specific end-to-end integration journey test:

```powershell
$env:PYTHONPATH='.'
.\venv\Scripts\pytest backend\tests\test_e2e.py -v
```