# The reference ERP

A small purchase-to-pay system for a made-up buying company, "Meridian Works". The demo, the tests and the benchmarks
all run on it. Every company, person and number in it is made up by `seed.py`.

Flow: **Purchase order → Goods receipt → Supplier invoice → Payment**

## Tables

| Table | One row is |
|---|---|
| `departments` | A department that orders things |
| `products` | Something that can be ordered |
| `suppliers` | A company that sells to Meridian Works |
| `users` | A person who can log in |
| `purchase_orders`, `purchase_order_lines` | An order to one supplier, and what is on it |
| `goods_receipts`, `goods_receipt_lines` | A delivery against an order, and what arrived |
| `supplier_invoices`, `supplier_invoice_lines` | A supplier's bill, and what is on it |
| `payments`, `payment_allocations` | Money sent to a supplier, and which invoices it pays |
| `audit_log` | Internal log of approvals and reviews |

## Roles

| Role | Who | What they should be able to read |
|---|---|---|
| `admin` | Meridian Works staff | Everything |
| `buyer` | Meridian Works staff | Only the orders they own, and the records linked to them. No payments. |
| `supplier_user` | Works for one supplier | Only their own supplier's orders (once sent), invoices and payments |

The data guard enforces these rules. They are written as a policy in `policy.toml`. Install it with
`python -m agent_workcell.guard apply` (see the main README).

Every seeded user has the password `workcell-demo`.

## Traps

The schema is awkward on purpose. Each trap reflects a common way an AI assistant gives a wrong answer on a real
system.

| Trap | Where |
|---|---|
| The number people use is not the id | `po_number`, `supplier_number`, `document_number`, `payment_number` |
| Two numbers for one thing | An invoice has our `document_number` and the supplier's `invoice_number`. Two suppliers can use the same `invoice_number`. |
| Stored words differ from screen words | Status columns: `sent` is shown as "Sent to Supplier", `under_review` as "In Review" (the screen words are in `knowledge/`) |
| No stored totals | An order line's value is `quantity * unit_price * (1 - COALESCE(discount_pct, 0) / 100)`. An order's value is the sum of its lines. |
| Empty means zero | `discount_pct` and `tax_rate` can be empty |
| Links with no foreign key, and names that hide the target | `approved_by`, `received_by`, `submitted_by`, `reviewed_by` are user ids. `supplier_invoices.purchase_order_id` has no foreign key. |
| A link that can be empty | Invoices for freight or service have no purchase order |
| No direct path | A payment reaches an order only through `payment_allocations` and `supplier_invoices` |
| Joins that multiply rows | Joining an invoice to its lines and to its payment allocations at once counts money twice |
| Rows that must not count | Reversed goods receipts, failed payments, soft-deleted rows (`deleted_at`) |
| Not every table has `deleted_at` | Invoices and payments are never soft-deleted |
| Received is not accepted | Accepted quantity is `quantity_received - quantity_rejected` |
| A link whose target changes | `audit_log.entity_id` points to a different table depending on `entity_type` |
| Sensitive columns | `users.password_hash`, `suppliers.tax_id`, `suppliers.bank_account` |
| Hostile text in records | A few `purchase_orders.notes` hold prompt-injection text, a spreadsheet formula, an image tag and a link (`HOSTILE_NOTES` in `seed.py`) |

## Loading the data

```bash
python -m reference_erp.seed                 # 3,000 purchase orders
python -m reference_erp.seed --scale 300     # about 900,000, for benchmarks
python -m reference_erp.seed --reset         # empty the reference ERP tables first
```

The same `--seed` and `--scale` always give the same rows. Nothing reads the clock: "today" in the data is
`ANCHOR_DATE` (2026-06-30), and every date is counted back from it. Tests and benchmarks must use that date as today.

`seed.py` makes the tables when they are missing, and refuses to add rows to tables that already have some.

## The chat page

`host_app/` is a small host application for the reference ERP. It does what a real ERP does in front of Agent
Workcell: it signs people in, keeps their principal in a signed session, and calls the answer server's API with the
host key. The browser sends only the question.

```bash
python -m agent_workcell.server        # the answer server, on 127.0.0.1:8080
python -m reference_erp.host_app       # sign-in and the chat page, on 127.0.0.1:8081
```

It reads `ERP_DATABASE_URL`, `WORKCELL_HOST_KEY`, `HOST_APP_SESSION_SECRET` and, optionally, `WORKCELL_ANSWER_URL`.
Sign in with any seeded user and the password `workcell-demo`. The sign-in page lists one account for each role;
choosing one fills the form. A buyer sees their own orders, and a supplier user sees their own supplier's. The chat
page shows, above the conversation, what the signed-in person may see, and each answer can show the queries behind
it.

It is a reference for the integration, not a login system to deploy: it has no limit on sign-in attempts and no
password change.
