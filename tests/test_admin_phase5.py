from io import BytesIO
import zipfile

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core.security import hash_password
from app.models.category import Category
from app.models.product import Occasion, Product, ProductVariant
from app.models.user import User, UserRole


def _create_admin(db: Session, email: str = "phase5-admin@example.com") -> User:
    admin = User(
        email=email,
        full_name="Phase 5 Admin",
        phone="9876543200",
        password_hash=hash_password("StrongPass1"),
        role=UserRole.ADMIN,
        is_active=True,
    )
    db.add(admin)
    db.commit()
    db.refresh(admin)
    return admin


def _login(client: TestClient, email: str, password: str = "StrongPass1") -> None:
    response = client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert response.status_code == 200


def _csrf_headers(client: TestClient) -> dict[str, str]:
    token_response = client.get("/api/v1/auth/csrf-token")
    assert token_response.status_code == 200
    token = token_response.cookies.get("csrf_token")
    assert token is not None
    return {"X-CSRF-Token": token}


def _build_minimal_xlsx(rows: list[list[str]]) -> bytes:
    shared_strings: list[str] = []
    string_index: dict[str, int] = {}

    def get_shared_index(value: str) -> int:
        if value not in string_index:
            string_index[value] = len(shared_strings)
            shared_strings.append(value)
        return string_index[value]

    sheet_rows: list[str] = []
    for row_number, row in enumerate(rows, start=1):
        cells: list[str] = []
        for column_index, value in enumerate(row):
            column_letter = chr(ord("A") + column_index)
            if value.replace(".", "", 1).isdigit() and value.count(".") <= 1:
                cells.append(f'<c r="{column_letter}{row_number}"><v>{value}</v></c>')
            else:
                shared_index = get_shared_index(value)
                cells.append(f'<c r="{column_letter}{row_number}" t="s"><v>{shared_index}</v></c>')
        sheet_rows.append(f'<row r="{row_number}">{"".join(cells)}</row>')

    shared_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        f'count="{len(shared_strings)}" uniqueCount="{len(shared_strings)}">'
        + "".join(f"<si><t>{value}</t></si>" for value in shared_strings)
        + "</sst>"
    )
    sheet_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        f"<sheetData>{''.join(sheet_rows)}</sheetData>"
        "</worksheet>"
    )
    workbook_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        '<sheets><sheet name="Sheet1" sheetId="1" r:id="rId1"/></sheets></workbook>'
    )
    rels_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" '
        'Target="worksheets/sheet1.xml"/>'
        '<Relationship Id="rId2" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/sharedStrings" '
        'Target="sharedStrings.xml"/>'
        '</Relationships>'
    )
    root_rels_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
        'Target="xl/workbook.xml"/>'
        '</Relationships>'
    )
    content_types_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/xl/workbook.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
        '<Override PartName="/xl/worksheets/sheet1.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
        '<Override PartName="/xl/sharedStrings.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sharedStrings+xml"/>'
        '</Types>'
    )

    output = BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as workbook:
        workbook.writestr("[Content_Types].xml", content_types_xml)
        workbook.writestr("_rels/.rels", root_rels_xml)
        workbook.writestr("xl/workbook.xml", workbook_xml)
        workbook.writestr("xl/_rels/workbook.xml.rels", rels_xml)
        workbook.writestr("xl/sharedStrings.xml", shared_xml)
        workbook.writestr("xl/worksheets/sheet1.xml", sheet_xml)
    return output.getvalue()


def test_admin_bulk_upload_template_returns_headers(client: TestClient, db_session: Session):
    admin = _create_admin(db_session)
    _login(client, admin.email)

    response = client.get("/api/v1/admin/products/bulk-upload/template")

    assert response.status_code == 200
    data = response.json()["data"]
    assert "headers" in data
    assert "name" in data["headers"]
    assert "occasion_slugs" in data["headers"]


def test_admin_inventory_overview_returns_low_stock_summary(client: TestClient, db_session: Session):
    admin = _create_admin(db_session)
    _login(client, admin.email)

    category = Category(name="Men", slug="men", is_active=True)
    db_session.add(category)
    db_session.flush()

    low_stock_product = Product(
        category_id=category.id,
        name="Low Stock Kurta",
        slug="low-stock-kurta",
        base_price=2500.0,
        is_active=True,
    )
    db_session.add(low_stock_product)
    db_session.flush()
    db_session.add(ProductVariant(product_id=low_stock_product.id, size="M", sku="LOW-STOCK-M", stock_quantity=2))

    sold_out_product = Product(
        category_id=category.id,
        name="Sold Out Sherwani",
        slug="sold-out-sherwani",
        base_price=5500.0,
        is_active=True,
    )
    db_session.add(sold_out_product)
    db_session.flush()
    db_session.add(ProductVariant(product_id=sold_out_product.id, size="L", sku="SOLD-OUT-L", stock_quantity=0))
    db_session.commit()

    response = client.get("/api/v1/admin/inventory/overview?low_stock_threshold=3")

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["total_products"] == 2
    assert data["out_of_stock_variants"] == 1
    assert data["low_stock_variants"] == 1
    assert data["low_stock_products"][0]["slug"] == "low-stock-kurta"


def test_admin_bulk_upload_supports_csv_and_creates_variants(client: TestClient, db_session: Session):
    admin = _create_admin(db_session)
    _login(client, admin.email)
    headers = _csrf_headers(client)

    category = Category(name="Men", slug="men", is_active=True)
    occasion = Occasion(name="Wedding", slug="wedding")
    db_session.add_all([category, occasion])
    db_session.commit()

    csv_content = (
        "name,category_id,base_price,sale_price,sizes,colors,stock,image_urls,occasion_slugs\n"
        f"Ivory Sherwani,{category.id},6999,6499,\"M,L\",\"Ivory,Gold\",4,"
        "\"https://cdn.amzira.test/ivory-front.jpg,https://cdn.amzira.test/ivory-back.jpg\",wedding\n"
    )

    response = client.post(
        "/api/v1/admin/products/bulk-upload",
        headers=headers,
        files={"file": ("products.csv", csv_content, "text/csv")},
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["created_count"] == 1
    assert data["created_variant_count"] == 4

    product = db_session.query(Product).filter(Product.slug == "ivory-sherwani").first()
    assert product is not None
    assert len(product.variants) == 4
    assert product.occasions[0].slug == "wedding"


def test_admin_bulk_upload_supports_xlsx(client: TestClient, db_session: Session):
    admin = _create_admin(db_session, email="phase5-admin-xlsx@example.com")
    _login(client, admin.email)
    headers = _csrf_headers(client)

    category = Category(name="Women", slug="women", is_active=True)
    db_session.add(category)
    db_session.commit()

    xlsx_bytes = _build_minimal_xlsx(
        [
            ["name", "category_id", "base_price", "sizes", "stock"],
            ["Rose Lehenga", str(category.id), "7999", "S,M", "3"],
        ]
    )

    response = client.post(
        "/api/v1/admin/products/bulk-upload",
        headers=headers,
        files={"file": ("products.xlsx", xlsx_bytes, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["created_count"] == 1

    product = db_session.query(Product).filter(Product.slug == "rose-lehenga").first()
    assert product is not None
    assert len(product.variants) == 2


def _catalog_product_payload(slug: str = "ruby-pattu-pavadai") -> dict:
    return {
        "name": "Ruby Pattu Pavadai",
        "slug": slug,
        "category_slug": "pattu-pavadai",
        "base_price": "3499.00",
        "sale_price": "3199.00",
        "audience": "kids_girls",
        "occasion_slugs": ["festive"],
        "images": [
            {
                "image_url": "https://cdn.amzira.test/ruby-front.jpg",
                "is_primary": True,
            }
        ],
        "external_source": "myntra",
        "external_id": "STYLE-001",
        "style_code": "ETHZY-001",
        "variants": [
            {"sku": "AMZ-RUBY-24", "size": "24", "color": "Ruby", "stock_quantity": 4},
            {"sku": "AMZ-RUBY-26", "size": "26", "color": "Ruby", "stock_quantity": 3},
        ],
    }


def _create_catalog_dependencies(db: Session) -> None:
    kids = Category(name="Kids", slug="kids", is_active=True)
    db.add(kids)
    db.flush()
    db.add_all(
        [
            Category(
                name="Pattu Pavadai",
                slug="pattu-pavadai",
                parent_id=kids.id,
                is_active=True,
            ),
            Occasion(name="Festive", slug="festive"),
        ]
    )
    db.commit()


def test_catalog_import_dry_run_validates_without_writes(client: TestClient, db_session: Session):
    admin = _create_admin(db_session, email="catalog-dry-run@example.com")
    _create_catalog_dependencies(db_session)
    _login(client, admin.email)

    response = client.post(
        "/api/v1/admin/products/catalog-import/json",
        headers=_csrf_headers(client),
        json={"products": [_catalog_product_payload()], "dry_run": True},
    )

    assert response.status_code == 200
    assert response.json()["data"]["dry_run"] is True
    assert db_session.query(Product).filter(Product.slug == "ruby-pattu-pavadai").first() is None


def test_catalog_import_creates_exact_variants_atomically(client: TestClient, db_session: Session):
    admin = _create_admin(db_session, email="catalog-create@example.com")
    _create_catalog_dependencies(db_session)
    _login(client, admin.email)

    response = client.post(
        "/api/v1/admin/products/catalog-import/json",
        headers=_csrf_headers(client),
        json={"products": [_catalog_product_payload()]},
    )

    assert response.status_code == 200
    report = response.json()["data"]
    assert report["products_created"] == 1
    assert report["variants_created"] == 2
    product = db_session.query(Product).filter(Product.slug == "ruby-pattu-pavadai").one()
    assert {variant.sku for variant in product.variants} == {"AMZ-RUBY-24", "AMZ-RUBY-26"}
    assert product.external_id == "STYLE-001"


def test_catalog_import_rejects_whole_batch_for_duplicate_sku(client: TestClient, db_session: Session):
    admin = _create_admin(db_session, email="catalog-duplicate@example.com")
    _create_catalog_dependencies(db_session)
    _login(client, admin.email)
    first = _catalog_product_payload()
    second = _catalog_product_payload("emerald-pattu-pavadai")
    second["external_id"] = "STYLE-002"
    second["variants"] = [{"sku": "AMZ-RUBY-24", "size": "28", "stock_quantity": 2}]

    response = client.post(
        "/api/v1/admin/products/catalog-import/json",
        headers=_csrf_headers(client),
        json={"products": [first, second]},
    )

    assert response.status_code == 422
    assert db_session.query(Product).count() == 0


def test_catalog_upsert_updates_stock_and_deactivates_omitted_skus(client: TestClient, db_session: Session):
    admin = _create_admin(db_session, email="catalog-upsert@example.com")
    _create_catalog_dependencies(db_session)
    _login(client, admin.email)
    headers = _csrf_headers(client)
    payload = _catalog_product_payload()
    create_response = client.post(
        "/api/v1/admin/products/catalog-import/json",
        headers=headers,
        json={"products": [payload]},
    )
    assert create_response.status_code == 200

    payload["base_price"] = "3599.00"
    payload["variants"] = [{"sku": "AMZ-RUBY-24", "size": "24", "color": "Ruby", "stock_quantity": 11}]
    update_response = client.post(
        "/api/v1/admin/products/catalog-import/json",
        headers=headers,
        json={"products": [payload], "mode": "upsert"},
    )

    assert update_response.status_code == 200
    product = db_session.query(Product).filter(Product.slug == "ruby-pattu-pavadai").one()
    variants = {variant.sku: variant for variant in product.variants}
    assert float(product.base_price) == 3599.0
    assert variants["AMZ-RUBY-24"].stock_quantity == 11
    assert variants["AMZ-RUBY-26"].stock_quantity == 0
    assert variants["AMZ-RUBY-26"].is_active is False
