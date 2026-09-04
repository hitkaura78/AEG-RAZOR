# AbuseGraph Architecture

## 1. Purpose and Boundaries

AbuseGraph evaluates order and refund risk while preserving clear boundaries
between customer-facing, merchant-facing, and internal investigation data.
The initial implementation uses SQLite as the only database. The architecture
does not depend on hardcoded local URLs, a local-only process, or a particular
deployment hostname. Application configuration supplies service and database
locations at runtime.

This is a single application architecture with clearly separated domain
modules for authentication, orders, refunds, risk evaluation, policy,
investigations, notifications, and audit logging. It does not introduce
Neo4j, Kafka, Kubernetes, or microservices. Relationship queries are modeled
with relational tables and indexes, including the device, IP address, and
address signals.

For checkout-time identity capture, each `Order` stores direct foreign keys to
one `Device` and one `IPAddress` row. The device fingerprint and IP submitted
by the demo frontend are simulated, stable per-browser-session values; they
are not real browser fingerprinting or a claim of verified network identity.
The shared resource rows let the lightweight velocity query count matching
orders across customer accounts.

Refunds use the stable contract `POST /api/refunds` with `order_id` and
`reason`, returning the refund status and customer-safe details. The endpoint
passes the order's captured device and IP context to
`evaluate_refund_risk(customer_id, order_id, device_id, ip_address)`. That
function is a `PENDING_REVIEW` placeholder until Phases 6-11 replace only its
internals with the full AbuseGraph pipeline; the request and response contract
must remain unchanged.

The order-time lightweight threshold is 5 matching orders in a rolling
2-hour window for the same simulated device or IP. Orders 1-4 remain
`APPROVED`; order 5 and later become `PENDING_REVIEW`. This deliberately
conservative demo threshold makes rapid multi-account activity visible while
allowing a normal isolated checkout through. It is not the full refund-abuse
pipeline and has no ML model, graph investigation, or agent.

The combined risk engine uses `ML_WEIGHT=0.6` and `GRAPH_WEIGHT=0.4`. Graph
scores from the relationship engine are normalized by their maximum explainable
edge weight of 6 before combination. `CASE_THRESHOLD=0.55` is a configurable
demo reference tuned below a typical production threshold because the
synthetic dataset deliberately has denser suspicious behavior. The risk
engine only computes the normalized score and reason codes; Phase 10 policy
logic owns all Allow/Review/Restrict decisions.

The investigation agent is informational only. It receives the score,
relationship, timing, and customer-history evidence and returns an explanation
plus a recommendation for an authorized reviewer. It never sets an order or
case status; only `policy.decide()` may do that. Without an Anthropic API key,
the deterministic fallback uses the same evidence and shared-IP/address
caveats with no external configuration.
## 2. User Experiences and Data Visibility

### Customer

Customers can:

- Sign in and view their own account and profile data.
- View their own products or orders that the product rules permit them to
  access.
- Create orders and submit refund requests for their own eligible orders.
- View the status of their own orders and refund cases using the public status
  values `APPROVED`, `PENDING_REVIEW`, and `RESTRICTED`.
- Receive customer-safe explanations such as a request being under review or
  restricted by policy.

Customers cannot see:

- Risk scores, confidence values, model outputs, or reason codes.
- Device, IP address, address-linkage, or graph relationships.
- Investigation notes, agent output, policy internals, or audit logs.
- Merchant decisions or internal administrative actions.
- Any other customer's users, orders, refunds, identifiers, or case data.

Authorization must scope every customer read and mutation to the authenticated
customer. A customer must never see risk scores, reason codes, graph
relationships, other customers' data, or other internal risk evidence.

### Merchant

Merchants can:

- View their own catalog, orders, and refund requests relevant to their
  merchant account.
- See the operational status of an order or refund case.
- For a case requiring a merchant action, see a bounded evidence summary,
  including the decision recommendation, high-level policy-safe reasons,
  relevant transaction context, and an explanation generated for merchant
  review.
- Accept or reject a pending case through the merchant decision workflow.
- See the outcome and timestamp of their own decisions.

Merchants cannot see:

- Raw individual ML features, model internals, weights, embeddings, or
  unredacted risk calculations.
- The full investigation graph or unrestricted links to other customers.
- Internal-only identity resolution data, complete device/IP/address history,
  investigator notes, secrets, or audit logs outside their authorization.
- Data belonging to another merchant or unrelated customer.

The evidence summary is deliberately sufficient to make an accept/reject call
without exposing raw ML internals or the full investigation graph.

### Internal Admin

Admins can, subject to authentication, authorization, and audit logging:

- View all customers, merchants, users, products, orders, refunds, and cases.
- View complete risk scores, model outputs, reason codes, features permitted by
  governance, policy evaluations, agent explanations, and investigation notes.
- Explore the complete relationship graph across customers, events, devices,
  IP addresses, and addresses.
- Review and override operational decisions according to policy.
- View audit logs and the history of notifications and merchant decisions.

Admin access is the only experience that exposes the complete risk and
investigation picture. Administrative access itself is logged in `AuditLogs`.

## 3. Risk-Evaluation Paths

### 3.1 Order Placement: Lightweight Risk Check

Order placement must remain fast and use a lightweight check:

1. The customer creates an order.
2. The order service records the order and its event context, including the
   authenticated customer, device when available, IP address, and timestamp.
3. The order risk evaluator checks device velocity and IP velocity over the
   configured recent windows, along with the order context needed for the
   lightweight decision. It does not invoke the full refund investigation
   pipeline.
4. The evaluator writes an `order_risk_evaluated` audit event and assigns the
   order one of these statuses:
   `APPROVED`, `PENDING_REVIEW`, or `RESTRICTED`.
5. A pending or restricted result can create or link a generalized
   `RiskCase`, depending on policy configuration, and the customer sees only
   the resulting customer-safe order status.

The lightweight check is an operational velocity control, not a conclusion
that the customer is fraudulent.

### 3.2 Refund Request: Full AbuseGraph Pipeline

Refund requests use the full pipeline designed for the original AbuseGraph
build. IP is explicitly added as a third relationship signal alongside device
and address:

1. The customer requests a refund for an eligible order.
2. The refund service records the request and its event context.
3. The individual ML model evaluates the refund/customer/order evidence.
4. The relationship layer finds relevant links between customers and events
   through shared devices, shared IP addresses, and shared addresses.
5. The risk engine combines the individual model result with relationship
   evidence and other configured risk factors.
6. The policy engine applies business rules, thresholds, eligibility, and
   required review behavior to the risk-engine result.
7. The investigation agent produces an explanation and supporting case
   narrative for authorized reviewers. Agent output is evidence and context,
   not an authority to bypass policy or access controls.
8. The system creates or updates a generalized `RiskCase`, records the
   evaluation and explanation audit events, and assigns the case one of:
   `APPROVED`, `PENDING_REVIEW`, or `RESTRICTED`.
9. If merchant action is required, the merchant receives a bounded evidence
   summary and can accept or reject the pending case. The decision and
   notification are audited.
10. The customer receives only the public case status and a customer-safe
    message.

The refund path must preserve the distinction between a relationship signal,
an evaluated risk, and a final policy decision. A shared relationship alone
does not determine the case status.

## 4. Relationship Signals and Fairness Safeguards

Shared IP, shared device, and shared address are relationship **signals**.
They are never automatic proof of fraud and must not independently trigger an
automatic fraud flag. The system must combine signals with event context,
time, velocity, transaction behavior, model output, and policy before deciding
whether review is needed.

Legitimate explanations for a shared relationship include:

- Family members sharing a device or address.
- Residents of a hostel sharing an IP address or network.
- Employees sharing an office network or address.
- Students sharing campus Wi-Fi.
- Mobile carrier NAT causing many customers to share a public IP address.

These situations must **not** be auto-flagged solely because the relationship
exists. Relationship evidence shown to admins or summarized for merchants
must distinguish an observed connection from an allegation. The system should
retain the signal's source and timestamp so reviewers can assess whether it
is current, reliable, and relevant.

## 5. Data Model

All entities have stable identifiers, creation and update timestamps where
applicable, and authorization ownership fields. Sensitive fields are stored
and returned according to the visibility rules above.

### Users

Authentication identities and authorization principals.

- `id`
- login identifier and credential or external-auth reference
- `role`: `customer`, `merchant`, or `admin`
- active/disabled state
- authentication timestamps and standard created/updated timestamps

### Customers

Customer business profiles linked to a `User`. A customer owns their own
orders, refunds, devices, addresses, and customer-scoped events.

- `id`, `user_id`
- profile and contact fields
- account state
- created/updated timestamps

### Products

Merchant-owned items that can appear in orders.

- `id`, `merchant_user_id` or merchant account reference
- product name, description, price, currency, and availability
- created/updated timestamps

### Orders

Purchase records and the event context used by the lightweight order risk
check.

- `id`, `customer_id`, merchant reference
- line-item/product references, amount, currency
- order status: `APPROVED`, `PENDING_REVIEW`, or `RESTRICTED`
- placement timestamp and fulfillment/payment context
- originating `device_id` and `ip_address_id` when available
- linked `risk_case_id` when an order creates a case

### Refunds

Refund requests attached to an order and evaluated by the full pipeline.

- `id`, `order_id`, `customer_id`
- requested amount, reason, and request status
- requested/resolved timestamps
- originating `device_id` and `ip_address_id` when available
- linked `risk_case_id`

### Devices

Known device identities or privacy-preserving device fingerprints. A device
can be associated with multiple customers and events over time.

- `id`, stable fingerprint/reference, metadata permitted by policy
- first-seen and last-seen timestamps
- status and provenance

### IPAddresses

Observed IP address records, preferably normalized and protected according to
privacy requirements. An IP address has a many-to-many-style association to
customers and events through explicit association records rather than a
single customer foreign key.

- `id`, normalized address or protected representation
- network metadata and first/last-seen timestamps where permitted
- `CustomerIPAddress` association: `customer_id`, `ip_address_id`, first/last
  seen, source, and event reference
- `EventIPAddress` association: event type/id, `ip_address_id`, timestamp,
  and source

The equivalent event associations may be represented by a general event
context table, but both customer-to-IP and event-to-IP history must be
queryable.

### Addresses

Customer-provided shipping, billing, or other authorized addresses. Addresses
may be normalized into a privacy-conscious matching representation.

- `id`, `customer_id`, address fields or protected normalized representation
- address type and validity state
- created/updated timestamps
- association history to relevant orders, refunds, or other events

### RiskCases

Generalized cases covering both order risk events and refund risk events.

- `id`
- `case_type`: `ORDER` or `REFUND`
- target reference to the order or refund event
- `customer_id` and merchant reference
- status: `APPROVED`, `PENDING_REVIEW`, or `RESTRICTED`
- risk score and model outputs, protected from customers and bounded for
  merchants
- reason codes, relationship findings, policy result, and agent explanation
- merchant decision and decision timestamp when applicable
- created/updated/resolved timestamps

Relationship findings should preserve the signal type (`device`, `ip`, or
`address`), the related entity/event, source, timestamp, and review context.

### AuditLogs

Immutable or append-only records of security, risk, policy, investigation,
notification, and decision activity.

- `id`, event name, actor/user reference when applicable
- subject type/id and related order, refund, or case references
- timestamp, outcome, correlation/request identifier
- structured metadata appropriate to the actor's authorization

Audit records must not leak secrets. Admin access to sensitive records and
merchant/customer-visible status changes are themselves auditable.

## 6. Audit Event Vocabulary

The following event names are required throughout the workflows:

| Event | When it is logged |
| --- | --- |
| `customer_logged_in` | A customer successfully authenticates. |
| `order_created` | An order is created. |
| `order_risk_evaluated` | The lightweight order risk check completes. |
| `refund_requested` | A customer submits a refund request. |
| `risk_evaluated` | A risk evaluation completes, including the full refund pipeline. |
| `case_created` | A `RiskCase` is created. |
| `agent_explanation_generated` | The investigation agent generates an explanation. |
| `policy_evaluated` | The policy engine evaluates the risk result. |
| `merchant_notified` | A merchant receives a case notification. |
| `merchant_decision` | A merchant accepts or rejects a case. |

Events include the relevant subject and correlation identifiers so an admin
can reconstruct an order or refund decision without exposing those records to
customers or unauthorized merchants.

## 7. Verification Gate for Phase 0

This document is the deliverable for the planning phase. No application code,
database schema, API endpoint, model implementation, or agent integration
should be written until a human reviewer confirms that this architecture
matches the requested behavior, visibility boundaries, signal safeguards,
data model, and audit vocabulary.