"""Daily calorie and protein targets for an adult.

Every constant below says where it comes from. docs/decisions/0005-targets-formula.md has the
sources in full and the limits of each. The output is an estimate, not advice for a person.
"""

import math

from nutrition_mcp.schemas import TargetsIn, TargetsResult

DISCLAIMER = "Estimate from the Mifflin-St Jeor equation, not medical advice."

# Mifflin MD, St Jeor ST, et al. A new predictive equation for resting energy expenditure in
# healthy individuals. Am J Clin Nutr 1990;51(2):241-247, doi:10.1093/ajcn/51.2.241.
# The abstract gives the full regression (9.99 x kg + 6.25 x cm - 4.92 x age, a sex term, -161)
# and the simplified sex-specific form used here: 10 x kg + 6.25 x cm - 5 x age, plus 5 for men
# and minus 161 for women. It says the simplification did not affect predictive value.
_SEX_OFFSET = {"male": 5, "female": -161}

# Multipliers on resting energy for a week's activity. No primary source was located; this set
# is a widely used convention. Compared with the FAO/WHO/UNU 2004 bands in the ADR.
ACTIVITY_FACTORS = {
    "sedentary": 1.2,
    "light": 1.375,
    "moderate": 1.55,
    "active": 1.725,
    "very_active": 1.9,
}

# Share of maintenance energy to eat. A convention too, checked against the 500 to 750 kcal/day
# deficit in Jensen MD et al., 2013 AHA/ACC/TOS obesity guideline, Circulation 2014;
# 129(suppl 2):S102-S138, doi:10.1161/01.cir.0000437739.71477.ee.
GOAL_FACTORS = {"lose": 0.80, "maintain": 1.00, "gain": 1.10}

# Lowest calorie target given. The lower end of the 1200-1500 kcal/day (women) and
# 1500-1800 kcal/day (men) diets in the same Jensen guideline, recommendation 3a, which covers
# weight-loss diets. Applying it to every goal is this project's extension.
CALORIE_FLOORS = {"female": 1200, "male": 1500}

# ICMR-NIN, Nutrient Requirements for Indians (2020): protein RDA for adults, 0.83 g/kg/day.
PROTEIN_RDA_G_PER_KG = 0.83
# Thomas DT, Erdman KA, Burke LM. Nutrition and Athletic Performance, joint position statement.
# Med Sci Sports Exerc 2016;48(3):543-568, doi:10.1249/MSS.0000000000000852: 1.2 to 2.0 g/kg/day
# for active people. The lower end is used for anyone who is not sedentary.
PROTEIN_ACTIVE_G_PER_KG = 1.2
# Morton RW et al. Br J Sports Med 2018;52(6):376-384, doi:10.1136/bjsports-2017-097608: extra
# protein added no further resistance-training gain in fat-free mass above about 1.6 g/kg/day.
# Used when losing or gaining; the Thomas statement also says higher intakes may suit periods
# of reduced energy intake.
PROTEIN_CHANGE_G_PER_KG = 1.6


def _round_half_up(value: float) -> int:
    # round() sends .5 to the nearest even number, so 1286.5 would come out lower than 1287.
    return math.floor(value + 0.5)


def _protein_g_per_kg(activity: str, goal: str) -> float:
    if goal != "maintain":
        return PROTEIN_CHANGE_G_PER_KG
    return PROTEIN_RDA_G_PER_KG if activity == "sedentary" else PROTEIN_ACTIVE_G_PER_KG


def compute_targets(t: TargetsIn) -> TargetsResult:
    """Daily calorie and protein targets from sex, age, height, weight, activity and goal.

    Everything is worked out from the unrounded resting energy and rounded once at the end.
    Nothing is stored.
    """
    bmr = 10 * t.weight_kg + 6.25 * t.height_cm - 5 * t.age + _SEX_OFFSET[t.sex]
    activity_factor = ACTIVITY_FACTORS[t.activity]
    goal_factor = GOAL_FACTORS[t.goal]
    protein_per_kg = _protein_g_per_kg(t.activity, t.goal)

    kcal = _round_half_up(bmr * activity_factor * goal_factor)
    notes = [DISCLAIMER]
    floor = CALORIE_FLOORS[t.sex]
    if kcal < floor:
        notes.append(
            f"Calorie floor applied: the estimate was {kcal} kcal, below the {floor} kcal "
            f"minimum used for {'women' if t.sex == 'female' else 'men'}, so {floor} kcal is shown."
        )
        kcal = floor

    return TargetsResult(
        kcal=kcal,
        protein_g=_round_half_up(protein_per_kg * t.weight_kg),
        bmr_kcal=_round_half_up(bmr),
        formula=(
            f"Mifflin-St Jeor resting energy x {activity_factor} ({t.activity} activity) "
            f"x {goal_factor:.2f} ({t.goal}); protein {protein_per_kg} g/kg body weight"
        ),
        inputs=t,
        notes=notes,
    )
