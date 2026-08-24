# AMZIRA Launch Execution Status - 2026-08-12

## Verdict

Repository and catalog preparation are ready for production deployment. Public checkout remains **NO-GO** until the external services and live acceptance gates below are completed with real credentials.

## Completed Today

- Adopted eight launch sizes: 1-2Y, 2-3Y, 3-4Y, 4-5Y, 5-6Y, 6-7Y, 7-8Y, and 9-10Y. The former 2-4Y stock is split across 2-3Y and 3-4Y.
- Selected every listable supplied product: 21 Work products and six Haresh Butta products. The source inventory contains 27 products, not 28.
- Created product names, descriptions, metadata, collection tags, color labels, style codes, and 216 exact SKUs.
- Allocated the owner-confirmed 500 opening units across the 27 products and eight sizes.
- Created the production CSV/JSON catalog and a 164-image R2 manifest.
- Prepared and verified all 164 WebP images under the backend's local static-media path.
- Migrated a clean PostgreSQL database to Alembic head `d6e7f8a9b0c1`.
- Ran the production CSV parser and importer with `dry_run=true`: 27 accepted, 0 rejected.
- Took and checksum-verified a pre-import PostgreSQL backup.
- Performed a backed-up atomic import into the local storefront database and updated it to 27 products, 216 variants, 500 units.
- Verified `/health/catalog-launch`: healthy, all three launch category requirements ready.
- Restored the pre-import backup into a separate database: checksum valid and 22 tables restored.
- Backend: 73 tests passed, 1 optional PostgreSQL concurrency test skipped in the normal suite.
- Acceptance subset: 40 payment, replay, expiry, shipment, return, refund, and launch tests passed.
- Backend dependency audit: no known vulnerabilities.
- Frontend: typecheck, lint, optimized 32-route build, and production dependency audit passed.
- Added and schema-validated `render.yaml` for API, PostgreSQL, persistent Key Value, Celery worker, and Beat in Singapore.
- Added `cdn.amzira.com` to the frontend image allowlist.
- Updated parent-category filtering so the public Kids collection includes all three launch child categories.
- Browser-verified 27 product cards on desktop and a seven-size product detail page on mobile with no broken images, error overlay, or horizontal overflow.

## External Gates Still Required

1. Replace the current Shopify DNS cutover. On 2026-08-12, `amzira.com` resolved to `23.227.38.65`, `www` pointed to `shops.myshopify.com`, and the served `*.myshopify.com` certificate had expired on 2025-11-15.
2. Create or authenticate Render, Vercel Pro, Cloudflare, Resend, Sentry, Razorpay Live, and Shiprocket accounts.
3. Supply every `sync: false` secret requested by the Render Blueprint.
4. Connect Cloudflare R2 to `cdn.amzira.com`, upload the prepared media, and verify all 164 URLs.
5. Verify Resend sender DNS records (SPF and DKIM) and publish a DMARC policy.
6. Point `api.amzira.com` to Render and `amzira.com` / `www.amzira.com` to Vercel; verify TLS.
7. Deploy the backend and frontend from reviewed Git commits with catalog fallback disabled.
8. Back up production, repeat the zero-rejection dry run, apply the real catalog import, and verify counts.
9. Register Razorpay and Shiprocket production webhooks and verify signatures and replay behavior.
10. Run test-mode checkout, reservation expiry, shipment, return, partial refund, and full refund acceptance against deployed services.
11. Perform one controlled live Razorpay transaction and refund, then reconcile both dashboards.
12. Open public checkout only after all health checks, backup restore, worker, email, payment, and shipment gates pass.

## Deployment Target And Cost

- Vercel Pro frontend: USD 20/month plus usage.
- Render: three Starter compute services, Basic PostgreSQL with 15 GB, and persistent Key Value. Budget approximately USD 40-55/month before growth.
- Cloudflare R2: this 36 MB launch batch is inside the current free storage/operation allowance.
- Resend and Sentry can begin on free tiers; budget upgrades when transaction volume requires them.
- Expected starting total: approximately USD 60-90/month, roughly INR 5,000-8,000/month before payment gateway fees and significant traffic growth.

Pricing references:

- https://vercel.com/pricing
- https://render.com/pricing
- https://developers.cloudflare.com/r2/pricing/
- https://resend.com/docs/knowledge-base/what-is-resend-pricing

## Rollback

Keep checkout disabled during deployment. Roll Render services back to the last successful deploy for application regressions. For catalog failure, restore the checksum-verified pre-import database dump into a separate database, verify migration head and row counts, then switch the production connection only after validation.

## Time After Access Is Available

Allow 3-5 focused engineering hours for provider setup, deployment, media upload, production import, and test-mode acceptance. DNS propagation, sender-domain verification, Shiprocket review, and Razorpay live activation can extend elapsed launch time beyond that window.
