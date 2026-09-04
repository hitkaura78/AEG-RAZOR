# abusegraph

Python project skeleton for Windows/PowerShell development.

The project currently uses SQLite only. Application code will live under
`backend/app` and tests under `backend/tests`.

## Setup

```powershell
python -m venv venv
.\venv\Scripts\Activate.ps1
pip install -r backend\requirements.txt
```

## Demo Accounts

The first server startup creates these accounts if they do not already exist:

- Merchant: `merchant@demo.abusegraph` / `DemoMerchant123!`
- Admin: `admin@demo.abusegraph` / `DemoAdmin123!`

Customers register through `POST /api/auth/register`.