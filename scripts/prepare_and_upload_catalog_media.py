"""Optimize launch catalog images and optionally upload them to Cloudflare R2."""

from __future__ import annotations

import argparse
import csv
import os
from pathlib import Path

import boto3
from botocore.config import Config
from PIL import Image, ImageOps


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST = ROOT / "docs/catalog-media-manifest.csv"
DEFAULT_OUTPUT = ROOT / "build/catalog-media"


def require_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"{name} is required for --upload")
    return value


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--upload", action="store_true")
    args = parser.parse_args()

    with args.manifest.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    if not rows:
        raise RuntimeError("Media manifest is empty")

    client = None
    bucket = None
    if args.upload:
        account_id = require_env("R2_ACCOUNT_ID")
        bucket = require_env("R2_BUCKET_NAME")
        client = boto3.client(
            "s3",
            endpoint_url=f"https://{account_id}.r2.cloudflarestorage.com",
            aws_access_key_id=require_env("R2_ACCESS_KEY_ID"),
            aws_secret_access_key=require_env("R2_SECRET_ACCESS_KEY"),
            region_name="auto",
            config=Config(signature_version="s3v4", retries={"max_attempts": 4, "mode": "standard"}),
        )

    args.output_dir.mkdir(parents=True, exist_ok=True)
    uploaded = 0
    for row in rows:
        source = Path(row["source_path"])
        destination = args.output_dir / row["r2_key"]
        destination.parent.mkdir(parents=True, exist_ok=True)
        with Image.open(source) as image:
            image = ImageOps.exif_transpose(image).convert("RGB")
            image.thumbnail((1800, 2400), Image.Resampling.LANCZOS)
            image.save(destination, "WEBP", quality=88, method=6)
        if client and bucket:
            client.upload_file(
                str(destination),
                bucket,
                row["r2_key"],
                ExtraArgs={"ContentType": "image/webp", "CacheControl": "public, max-age=31536000, immutable"},
            )
            client.head_object(Bucket=bucket, Key=row["r2_key"])
            uploaded += 1
    print({"prepared": len(rows), "uploaded": uploaded, "output_dir": str(args.output_dir)})


if __name__ == "__main__":
    main()
