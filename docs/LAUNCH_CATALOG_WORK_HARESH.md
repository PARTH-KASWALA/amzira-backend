# Work + Haresh Butta Launch Catalog

This is AMZIRA's first controlled catalog batch. The supplied inventory contains 21 listable Work products and six Haresh Butta products: 27 products, eight sizes per product, 216 exact SKUs, and 500 opening units.

## Inventory Allocation

Each product receives a provisional 18-unit allocation across the supplied size bands. The first 14 products receive one additional unit in size 33 so the catalog total remains exactly 500.

| Size code | Age band | Base units per product |
|---|---:|---:|
| 18 | 1-2Y | 1 |
| 20 | 2-3Y | 1 |
| 21 | 3-4Y | 1 |
| 22 | 4-5Y | 3 |
| 24 | 5-6Y | 3 |
| 26 | 6-7Y | 3 |
| 28 | 7-8Y | 3 |
| 33 | 9-10Y | 3 (4 for the first 14 products) |

The total is `(27 x 18) + 14 = 500`. This is the owner-directed opening total and must still be reconciled against the physical per-size count before production checkout is enabled.

## Pricing

- Work collection: MRP INR 1,999-2,199; launch price INR 1,399-1,499.
- Haresh Butta collection: MRP INR 1,899; launch price INR 1,299.

This positions AMZIRA above its marketplace brands while remaining below established premium kidswear labels. Product labels and invoices must use the same approved MRP.

## Build And Validate

```bash
python scripts/build_launch_catalog.py
python scripts/prepare_and_upload_catalog_media.py
```

The first command recreates the CSV, JSON, and media manifest. The second prepares optimized WebP files without uploading them.

## R2 Upload

Set `R2_ACCOUNT_ID`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`, and `R2_BUCKET_NAME`, then run:

```bash
python scripts/prepare_and_upload_catalog_media.py --upload
```

Do not run the real catalog import until every CDN URL returns an image and the dry run has zero rejected records.
