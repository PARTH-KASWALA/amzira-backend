from scripts.build_updated_inventory_photo_catalog import stock_left


def test_updated_catalog_stock_range_is_deterministic_and_between_21_and_50():
    sizes = ["1-2Y", "3-4Y", "9-10Y"]
    values = {stock_left("ishani-red-black-checked-butta-pattu-pavadai", size) for size in sizes}

    assert values == {stock_left("ishani-red-black-checked-butta-pattu-pavadai", size) for size in sizes}
    assert all(21 <= value <= 50 for value in values)
