from nutrition_mcp.nutrition import glycemic_load


def test_glycemic_load() -> None:
    assert glycemic_load(70, 30) == 21.0


def test_glycemic_load_rounds_to_one_decimal() -> None:
    # 62.45 * 23.4 / 100 = 14.6133
    assert glycemic_load(62.45, 23.4) == 14.6


def test_glycemic_load_of_no_carbohydrate_is_zero() -> None:
    # 0 g carbohydrate is a measured value, so the load is a real 0 and not missing.
    assert glycemic_load(70, 0) == 0.0


def test_glycemic_load_missing() -> None:
    assert glycemic_load(None, 30) is None
    assert glycemic_load(70, None) is None
    assert glycemic_load(None, None) is None
