from __future__ import annotations

import httpx
import pytest

from scripts import reconcile_catalog


@pytest.mark.parametrize(
    "url",
    (
        "file:///etc/passwd",
        "data:text/plain,unsafe",
        "https://user:password@cdn.amzira.com/image.webp",
        "//cdn.amzira.com/image.webp",
    ),
)
def test_validated_http_url_rejects_unsafe_targets(url):
    with pytest.raises(ValueError):
        reconcile_catalog._validated_http_url(url)


def test_media_head_rejects_an_unapproved_host_without_network(monkeypatch):
    def unexpected_head(*args, **kwargs):
        raise AssertionError("network request should not be attempted")

    monkeypatch.setattr(httpx, "head", unexpected_head)

    ok, reason = reconcile_catalog._head(
        "https://untrusted.example/image.webp", allowed_host="cdn.amzira.com"
    )

    assert ok is False
    assert reason == "ValueError"


def test_sitemap_parser_rejects_external_entities(monkeypatch):
    malicious_xml = b"""<?xml version='1.0'?>
    <!DOCTYPE sitemap [<!ENTITY xxe SYSTEM 'file:///etc/passwd'>]>
    <urlset><url><loc>&xxe;</loc></url></urlset>
    """

    def response(*args, **kwargs):
        return httpx.Response(
            200,
            content=malicious_xml,
            request=httpx.Request("GET", "https://www.amzira.com/sitemap.xml"),
        )

    monkeypatch.setattr(httpx, "get", response)

    slugs, error = reconcile_catalog._sitemap_slugs(
        "https://www.amzira.com/sitemap.xml"
    )

    assert slugs == set()
    assert error == "Sitemap check failed: EntitiesForbidden"


def test_sitemap_parser_returns_product_slugs(monkeypatch):
    sitemap = b"""<?xml version='1.0'?>
    <urlset xmlns='http://www.sitemaps.org/schemas/sitemap/0.9'>
      <url><loc>https://www.amzira.com/product/red-lehenga</loc></url>
    </urlset>
    """

    def response(*args, **kwargs):
        return httpx.Response(
            200,
            content=sitemap,
            request=httpx.Request("GET", "https://www.amzira.com/sitemap.xml"),
        )

    monkeypatch.setattr(httpx, "get", response)

    slugs, error = reconcile_catalog._sitemap_slugs(
        "https://www.amzira.com/sitemap.xml"
    )

    assert slugs == {"red-lehenga"}
    assert error is None
