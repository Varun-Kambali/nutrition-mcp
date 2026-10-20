from typing import Any

import pytest

from nutrition_mcp.schemas import TargetsIn
from nutrition_mcp.targets import compute_targets

DISCLAIMER = "Estimate from the Mifflin-St Jeor equation, not medical advice."


def person(**overrides: Any) -> TargetsIn:
    data: dict[str, Any] = {
        "sex": "male",
        "age": 30,
        "height_cm": 175,
        "weight_kg": 70,
        "activity": "moderate",
        "goal": "maintain",
    }
    data.update(overrides)
    return TargetsIn.model_validate(data)


def test_targets_male_example() -> None:
    result = compute_targets(person())
    # 10 x 70 + 6.25 x 175 - 5 x 30 + 5 = 1648.75; x 1.55 = 2555.56.
    assert result.bmr_kcal == 1649
    assert result.kcal == 2556
    assert result.protein_g == 84  # 1.2 g/kg
    assert result.notes == [DISCLAIMER]


def test_targets_echo_the_inputs_and_name_the_formula() -> None:
    inputs = person()
    result = compute_targets(inputs)
    assert result.inputs == inputs
    assert "Mifflin-St Jeor" in result.formula
    assert "1.55" in result.formula


def test_targets_female_loses_with_a_smaller_budget() -> None:
    result = compute_targets(
        person(sex="female", age=28, height_cm=160, weight_kg=55, activity="light", goal="lose")
    )
    # 550 + 1000 - 140 - 161 = 1249; x 1.375 x 0.80 = 1373.9.
    assert result.bmr_kcal == 1249
    assert result.kcal == 1374
    assert result.protein_g == 88  # 1.6 g/kg
    assert result.notes == [DISCLAIMER]


def test_targets_gain_adds_ten_percent() -> None:
    result = compute_targets(
        person(age=25, height_cm=180, weight_kg=75, activity="active", goal="gain")
    )
    # 750 + 1125 - 125 + 5 = 1755; x 1.725 x 1.10 = 3330.1.
    assert result.bmr_kcal == 1755
    assert result.kcal == 3330
    assert result.protein_g == 120  # 1.6 g/kg


def test_targets_very_active_factor() -> None:
    assert compute_targets(person(activity="very_active")).kcal == 3133  # 1648.75 x 1.9


def test_targets_protein_for_a_sedentary_adult_who_maintains_is_the_rda() -> None:
    assert compute_targets(person(activity="sedentary")).protein_g == 58  # 0.83 g/kg x 70


def test_targets_protein_for_other_maintainers() -> None:
    assert compute_targets(person(activity="light")).protein_g == 84  # 1.2 g/kg x 70


def test_targets_protein_for_a_sedentary_adult_who_loses_is_the_higher_figure() -> None:
    result = compute_targets(person(activity="sedentary", goal="lose"))
    assert result.protein_g == 112  # 1.6 g/kg x 70


def test_targets_round_half_up() -> None:
    # 600 + 1002.5 - 155 - 161 = 1286.5, which round() would send to the even 1286.
    result = compute_targets(person(sex="female", age=31, height_cm=160.4, weight_kg=60))
    assert result.bmr_kcal == 1287


def test_targets_floor_applied() -> None:
    result = compute_targets(
        person(sex="female", age=18, height_cm=150, weight_kg=40, activity="sedentary", goal="lose")
    )
    # 400 + 937.5 - 90 - 161 = 1086.5; x 1.2 x 0.80 = 1043, below the 1200 kcal floor for women.
    assert result.bmr_kcal == 1087
    assert result.kcal == 1200
    assert result.notes[0] == DISCLAIMER
    assert len(result.notes) == 2
    assert "1043" in result.notes[1]
    assert "1200" in result.notes[1]


def test_targets_floor_for_men_is_higher() -> None:
    result = compute_targets(
        person(age=100, height_cm=120, weight_kg=30, activity="sedentary", goal="lose")
    )
    assert result.kcal == 1500
    assert "1500" in result.notes[1]


def test_targets_no_floor_note_when_the_floor_is_not_needed() -> None:
    assert compute_targets(person()).notes == [DISCLAIMER]


def test_targets_reject_minor() -> None:
    with pytest.raises(ValueError, match="age"):
        person(age=17)


@pytest.mark.parametrize(
    "overrides",
    [
        {"age": 101},
        {"height_cm": 119},
        {"height_cm": 231},
        {"weight_kg": 29},
        {"weight_kg": 251},
        {"sex": "other"},
        {"activity": "athlete"},
        {"goal": "bulk"},
    ],
)
def test_targets_reject_out_of_range_inputs(overrides: dict[str, Any]) -> None:
    with pytest.raises(ValueError, match=next(iter(overrides))):
        person(**overrides)


def test_targets_accept_the_limits() -> None:
    assert compute_targets(person(age=18, height_cm=120, weight_kg=30)).kcal > 0
    assert compute_targets(person(age=100, height_cm=230, weight_kg=250)).kcal > 0
