# AMZIRA Production Launch Runbook

This runbook covers the external acceptance work that must be completed after the repository-controlled implementation. Do not enable live checkout until every stop condition is cleared.

## Selected Production Platform

- Frontend target: Vercel Pro, connected to the `amzira-frontend` repository.
  The account must report Pro or Enterprise before commercial launch; Vercel
  [restricts Hobby to personal, non-commercial use](https://vercel.com/docs/plans/hobby).
- Backend stack: Render Blueprint from `render.yaml` in Singapore (`amzira-api`, `amzira-worker`, `amzira-beat`, PostgreSQL, and persistent Key Value).
- Product media: Cloudflare R2 bucket `amzira-products` on `https://cdn.amzira.com`.
- Transactional email: Resend SMTP after SPF, DKIM, and DMARC verification.
- Error monitoring: separate Sentry projects for frontend and backend.

The Blueprint provisions compute and datastores, but vendor keys marked `sync: false` must be supplied in Render during initial creation. Never commit them.

## 1. Provision Production Services

1. Create a managed PostgreSQL database with automated daily backups and point-in-time recovery.
2. Create Redis for Celery and reservation expiry processing.
3. Create the R2 bucket and public CDN hostname for product media.
4. Create SMTP credentials, a Sentry project, and the Shiprocket integration.
5. Generate independent random values for `SECRET_KEY`, `HEALTHCHECK_TOKEN`, database password, and webhook secrets.
6. Populate every variable in `.env.example`; keep `ENVIRONMENT=production`, `DEBUG=False`, and `ALLOW_STARTUP_SCHEMA_PATCHING=False`.

## 2. Configure Domains And Vendors

1. Point `amzira.com`, `www.amzira.com`, the API host, and CDN host to production and confirm valid TLS certificates.
2. Set `FRONTEND_URL` and `BACKEND_CORS_ORIGINS` to HTTPS AMZIRA origins only.
3. Add Razorpay live keys and a distinct webhook secret. Register the backend webhook URL for payment and refund events.
4. Add Shiprocket credentials, pickup details, and webhook secret. Register its HTTPS webhook URL.
5. Verify sender identities for transactional email and configure SPF, DKIM, and DMARC.

## 3. Deploy In Order

Preferred managed deployment:

1. Push a reviewed commit to GitHub and create the Render Blueprint from `render.yaml`.
2. Supply every prompted secret. The API pre-deploy command runs Alembic and creates the first admin; worker and Beat wait for migration head.
3. Remove `DEFAULT_ADMIN_PASSWORD` from Render after first login and rotate that admin password.
4. Attach `api.amzira.com`, verify `/health`, and keep checkout closed.
5. Import the frontend repository into Vercel Pro with the variables below and attach `amzira.com` plus `www.amzira.com`.

```dotenv
NEXT_PUBLIC_API_BASE_URL=https://api.amzira.com/api/v1
NEXT_PUBLIC_SITE_URL=https://amzira.com
NEXT_PUBLIC_LIVE_CATEGORY_API_SLUG=kids
NEXT_PUBLIC_ENABLE_CATALOG_FALLBACK=false
NEXT_PUBLIC_RAZORPAY_KEY_ID=rzp_live_replace_me
NEXT_PUBLIC_SENTRY_DSN=replace_me
```

Docker Compose remains the self-hosted fallback:

```bash
docker compose config -q
docker compose build --pull
docker compose up -d db redis
docker compose run --rm migrate
docker compose up -d backend worker beat
docker compose ps
```

Deploy the frontend with `NEXT_PUBLIC_ENABLE_CATALOG_FALLBACK=false`, the production API URL, live Razorpay key ID, and canonical site URL.

## 4. Import Catalog Safely

1. Upload product images to the AMZIRA-owned R2/CDN first.
2. Fill `docs/catalog-import-template.csv` with one row per exact SKU/size/color variant.
3. Keep every product in `audience=kids_girls` and use the seeded kids-girls category slugs.
4. Submit the complete file to `POST /api/v1/admin/products/catalog-import?dry_run=true&mode=upsert` as an administrator.
5. Correct every rejected row. Do not continue until the dry run reports zero rejected products.
6. Back up PostgreSQL, then repeat with `dry_run=false`.
7. Verify product count, unique SKU count, image count, stock totals, prices, and `/health/catalog-launch`.

For the first Work + Haresh Butta batch:

```bash
python scripts/build_launch_catalog.py
python scripts/prepare_and_upload_catalog_media.py --upload
python scripts/validate_launch_catalog.py --database-url "$DATABASE_URL"
```

The expected dry-run result is 20 accepted products, zero rejected products, 140 exact SKUs, 120 media objects, and 500 units. Only after the production backup succeeds should the same command be rerun with `--apply`.

For the corrected full photo and stock catalog, use a new CDN prefix so Cloudflare's immutable cache cannot continue serving the old back-view objects:

```bash
python scripts/build_updated_inventory_photo_catalog.py \
  --media-base-url https://cdn.amzira.com/catalog-v2 \
  --media-prefix catalog-v2 \
  --output-json build/catalog-updated-inventory-photos-public.json \
  --output-manifest build/catalog-updated-inventory-photos-public-media.csv
python scripts/prepare_and_upload_catalog_media.py \
  --manifest build/catalog-updated-inventory-photos-public-media.csv \
  --output-dir build/catalog-updated-inventory-photos-media \
  --upload
python scripts/import_full_inventory_catalog.py \
  --catalog build/catalog-updated-inventory-photos-public.json \
  --expected-products 110 \
  --apply
```

Run the upload before the database import, and run both against the production environment only after taking a database backup. The generated catalog marks the front view as primary and assigns each active size a deterministic stock value between 21 and 50 (inclusive).

## 5. Acceptance Before Live Payments

Run the public production verifier first. It fails unless checkout and COD remain
disabled, and it prints no product names, customer data, order numbers, tokens, or
credentials:

```bash
.venv/bin/python scripts/verify_production_launch.py
```

For the operator-only pass, provide `AMZIRA_HEALTHCHECK_TOKEN`,
`AMZIRA_SELLER_EMAIL`, and `AMZIRA_SELLER_PASSWORD` through the approved secret
manager, run from an address in `ADMIN_ALLOWED_IPS`, and require both protected
groups:

```bash
.venv/bin/python scripts/verify_production_launch.py \
  --require-protected-health \
  --require-seller
```

Seller acceptance establishes and closes an admin session; it does not mutate an
order. Save the PII-free summary in the launch ticket. This verifier does not
replace the transaction, refund, delivery, monitoring, or restore drills below.

1. Confirm `/health`, `/health/database`, `/health/email`, and `/health/catalog-launch` are healthy using the health token.
2. Run registration, login, refresh, logout, password reset, and a second-customer authorization test.
3. In Razorpay test mode, purchase one low-stock SKU; verify one order, one payment, correct stock, email, and My Orders visibility.
4. Replay the payment callback and webhook; confirm no duplicate order or stock deduction.
5. Send invalid signatures; confirm rejection and no state changes.
6. Allow one checkout reservation to expire; confirm stock and coupon capacity are restored by Celery Beat.
7. Complete Shiprocket shipment creation and tracking webhook acceptance.
8. Issue a partial and full test refund and confirm Razorpay webhook reconciliation and one-time restocking.
9. Run a backup, restore it into a separate empty database, migrate to head, and compare row counts.
10. Check browser console errors, mobile checkout, accessibility, Sentry delivery, logs, and alert routing.

## 6. Go/No-Go Gate

Commercial launch and live payments are **NO-GO** while the Vercel team remains
on Hobby. They are also **NO-GO** if any health check fails, credentials are
still test-mode, a dry-run catalog import has rejections, webhook signatures are
not accepted, backup restore is unproven, checkout creates duplicate orders,
totals differ from Razorpay, or stock becomes negative.

After all checks pass, switch to the live Razorpay key pair, perform one controlled real transaction and refund, verify settlement/refund in the Razorpay dashboard, and then open checkout publicly.

## Rollback

1. Disable public checkout in Vercel before changing production data.
2. Roll the API, worker, and Beat back to the last successful Render deploy.
3. For catalog-only failure, restore the pre-import PostgreSQL dump into a separate database, verify it, and switch `DATABASE_URL` only after confirming row counts and migration head.
4. Verify `/health`, `/health/database`, and storefront read paths before reopening traffic.

## Backup And Restore

```bash
DATABASE_URL='postgresql://...' BACKUP_DIR=/secure/backups ./scripts/backup_postgres.sh
RESTORE_DATABASE_URL='postgresql://.../empty_restore_db' ./scripts/restore_postgres.sh /secure/backups/amzira-TIMESTAMP.dump
```

Store encrypted backups outside the application host and schedule the backup script daily. Record a successful restore drill before launch and at least monthly afterward.
