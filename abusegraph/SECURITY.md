# Security & Privacy Policy

## Overview & Demo Scope

AEG-RAZOR (AbuseGraph) is a proof-of-concept application built for hackathon demonstration. It processes transaction signals, device metadata, and network graph topology to identify coordinated refund fraud rings.

---

## Data Privacy & Synthetic Datasets

1. **Synthetic Data Only**:
   - The application relies on synthetically generated customer, order, device, and network data for model training and demonstration.
   - **Do NOT enter real Personally Identifiable Information (PII)** or real payment card details into the system.

2. **Simulated Device & IP Metadata**:
   - Device fingerprints and IP addresses accepted by the customer API (`/api/orders`) are simulated test strings. The application does not deploy real browser tracking, invasive device fingerprinting scripts, or third-party geo-location trackers.

---

## Authentication & Credential Protection

1. **Bcrypt Password Hashing**:
   - Passwords are strictly hashed using `bcrypt` (`passlib.hash.bcrypt`) before being stored in `users.password_hash`.
   - Plaintext passwords are **never stored, logged, or transmitted** in audit logs or database tables.

2. **JWT Secret Management**:
   - Authentication tokens are signed using `HS256`.
   - The repository includes a default `SECRET_KEY` in `.env` for local testing. **This secret MUST be rotated and set via environment variables before any production or public staging deployment.**

3. **Role-Based Access Control (RBAC)**:
   - Endpoints are strictly scoped to role requirements (`require_role("customer")`, `require_role("merchant")`, `require_role("admin")`).
   - Cross-customer data boundaries are enforced at the database query level (`customer_id == current_user.customer_id`).

4. **Internal Field Omission**:
   - Customer responses strictly omit internal risk scores, model weights, and reason codes.
   - Merchant responses omit raw machine learning vectors (`ml_score`, `graph_score`, `final_score`) and full network topology graphs, presenting only human-understandable evidence summaries.

---

## Production Deployment Roadmap (Deferred Features)

A production deployment of AEG-RAZOR would require the following controls beyond the hackathon scope:

- **Explicit User Consent & Privacy Policy**: Consent banners and privacy disclosures regarding device and IP telemetry collection.
- **Automated Data Retention & Erasure**: Data retention policies and automated purging of historical logs in compliance with GDPR/CCPA.
- **Rate Limiting**: IP and user-level rate limiting on authentication endpoints (`/api/auth/login`, `/api/auth/register`) to prevent brute-force attacks.
- **Immutable Audit Access Logging**: Secure access logging for admin investigation views and merchant decision actions.
- **Secret Rotation**: KMS/Secrets Manager integration for database credentials and JWT signing keys.
