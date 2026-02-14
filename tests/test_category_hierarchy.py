from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models.category import Category


def test_create_subcategory_with_parent(db_session: Session):
    parent = Category(name="Women", slug="women", is_active=True)
    db_session.add(parent)
    db_session.commit()
    db_session.refresh(parent)

    child = Category(
        name="Kurti",
        slug="kurti",
        description="Women kurti collection",
        is_active=True,
        display_order=1,
        parent_id=parent.id,
    )
    db_session.add(child)
    db_session.commit()
    db_session.refresh(child)

    assert child.parent_id == parent.id
    assert child.parent.id == parent.id
    assert any(item.id == child.id for item in parent.children)


def test_list_categories_nested_subcategories(client: TestClient, db_session: Session):
    parent = Category(name="Women", slug="women", is_active=True, display_order=1)
    db_session.add(parent)
    db_session.commit()
    db_session.refresh(parent)

    child = Category(
        name="Kurti",
        slug="kurti",
        description="Women kurti collection",
        is_active=True,
        display_order=1,
        parent_id=parent.id,
    )
    db_session.add(child)
    db_session.commit()

    response = client.get("/api/v1/categories?include_children=true")
    assert response.status_code == 200
    payload = response.json()
    assert payload["success"] is True

    women_entry = next((item for item in payload["data"] if item.get("slug") == "women"), None)
    assert women_entry is not None
    subcategories = women_entry.get("subcategories", [])
    assert any(subcat.get("slug") == "kurti" for subcat in subcategories)
