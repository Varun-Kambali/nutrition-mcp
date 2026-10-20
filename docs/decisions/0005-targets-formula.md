# 0005. Calorie and protein targets: formula and constants
Date: 2026-10-20 · Status: accepted

## Context
`compute_targets` gives an adult a daily calorie and protein target. The requirements ask for the Mifflin-St Jeor equation for resting energy, an activity multiplier, a goal adjustment and a calorie floor, with every constant cited in the code and here. Two of the constants have no primary source that could be located, and the output has to say so instead of presenting a convention as a finding.

## Decision
```
resting = 10 x weight_kg + 6.25 x height_cm - 5 x age + (5 male | -161 female)
kcal    = resting x activity factor x goal factor, raised to the floor if it falls below it
protein = grams per kg x weight_kg
```
Calories and protein are worked out from the unrounded resting energy and rounded once at the end, half up. Python's `round()` sends an exact .5 to the nearest even number, which would give 1286 for 1286.5. Worked example, a man of 30 who is 175 cm and 70 kg, moderately active, maintaining: resting energy is 1648.75, shown as 1649; times 1.55 is 2555.56, shown as 2556 kcal; protein is 1.2 x 70 = 84 g.

| Constant | Value | Source and standing |
|---|---|---|
| Resting energy | the equation above | Mifflin MD, St Jeor ST, et al. A new predictive equation for resting energy expenditure in healthy individuals. Am J Clin Nutr 1990;51(2):241-247, doi:10.1093/ajcn/51.2.241. The abstract gives the full regression (9.99 x kg + 6.25 x cm - 4.92 x age, a sex term, -161) and this simplified sex-specific form, and says the simplification did not affect predictive value. |
| Activity factors | 1.2, 1.375, 1.55, 1.725, 1.9 (sedentary to very active) | A widely used convention. No primary source was located, and the secondary pages that attribute the set disagree about who introduced it, so none is cited. See the comparison below. |
| Goal factors | lose 0.80, maintain 1.00, gain 1.10 | A convention, checked against Jensen MD et al., 2013 AHA/ACC/TOS guideline for the management of overweight and obesity in adults, Circulation 2014;129(suppl 2):S102-S138, doi:10.1161/01.cir.0000437739.71477.ee. Recommendation 3a (Table 4, p. S109) lists an energy deficit of 500 or 750 kcal/day, and the evidence statement in section 3.3.1 (p. S120) adds a 30% deficit. A 20% cut is 500 kcal at a maintenance of 2500 kcal, less below that and more above it. No source was located for the 1.10 surplus. |
| Calorie floor | 1200 kcal (women), 1500 kcal (men) | The lower end of the diets in recommendation 3a of the Jensen guideline (Table 4, p. S109): 1200-1500 kcal/day for women and 1500-1800 kcal/day for men. That recommendation is about weight-loss diets for people with overweight or obesity. Applying the floor to every goal, including maintain and gain, is this project's extension. |
| Protein, sedentary and maintaining | 0.83 g/kg/day | ICMR-NIN 2020, the adult protein RDA (EAR 0.66 g/kg/day), in "A Brief Note on Nutrient Requirements for Indians", Table 2a and the text on protein. |
| Protein, other maintainers | 1.2 g/kg/day | Thomas DT, Erdman KA, Burke LM. Nutrition and Athletic Performance, joint position statement. Med Sci Sports Exerc 2016;48(3):543-568, doi:10.1249/MSS.0000000000000852. It puts the need for active people at 1.2 to 2.0 g/kg/day; this is the lower end. |
| Protein, losing or gaining | 1.6 g/kg/day | Morton RW et al. Br J Sports Med 2018;52(6):376-384, doi:10.1136/bjsports-2017-097608: protein above about 1.6 g/kg/day added no further resistance-training gain in fat-free mass. The Thomas statement adds that higher intakes may suit periods of reduced energy intake. |

Inputs are bounded to adults (age 18 to 100, height 120 to 230 cm, weight 30 to 250 kg). The output echoes them, names the formula with the factors used, and always carries the note "Estimate from the Mifflin-St Jeor equation, not medical advice." When the floor lifts the figure, a second note gives the original estimate and the floor.

### How the activity factors compare with FAO/WHO/UNU
The FAO/WHO/UNU report *Human energy requirements* (FAO Food and Nutrition Technical Report Series 1, 2004, Table 5.3) gives physical activity level, which is total energy divided by basal metabolic rate, in three bands: sedentary or light 1.40-1.69, active or moderately active 1.70-1.99, and vigorous 2.00-2.40 (it adds that values above 2.40 are hard to keep up). The factors 1.2 and 1.375 sit below the first band, 1.55 is inside it, 1.725 and 1.9 are inside the second, and nothing reaches the third. The scales are close but not identical, because Mifflin-St Jeor predicts resting energy expenditure, while PAL is defined against basal metabolic rate (the ICMR-NIN note writes it as TEE = BMR x PAL).

## Alternatives considered
- **Harris-Benedict (1919).** Older, and its accuracy was not compared with Mifflin-St Jeor here. Mifflin-St Jeor is the equation the requirements name.
- **Katch-McArdle.** It needs lean body mass, which a caller rarely has, so the tool would have to ask for something most people cannot supply.
- **The FAO/WHO/UNU equations with its PAL bands.** Closer to a published standard for activity. The ICMR-NIN 2020 note reports that these equations can overestimate basal metabolic rate in Indians by 10-12%, because the data they came from included young and muscular subjects. It adds that the earlier ICMR-NIN committee reduced the calculated BMR by 5% and the 2020 committee by another 5% for adults. Switching would mean building age bands and a correction that could not be validated here.
- **ICMR-NIN's energy tables.** Tables 1a and 1b of the note give energy by work category for a 65 kg reference man and a 55 kg reference woman (the energy figures are in Table 1a, the reference weights in Table 1b), not as a function of a person's height and weight, so they cannot answer for an individual.

## Consequences
The targets are estimates built partly on conventions, and the formula says which.

Known limits:

- **They may run low for Indian adults.** As a rough check, a 30-year-old man of 65 kg and 170 cm comes out at 1881 kcal sedentary and 2430 kcal moderate, against 2110 and 2710 in ICMR-NIN 2020 Tables 1a and 1b for a 65 kg reference man. A woman of 55 kg and 160 cm comes out at 1487 and 1920, against 1660 and 2130. That is about 10% below in each case. The note does not give the reference height or age, so the example inputs are age 30 and heights of 170 cm (man) and 160 cm (woman), and the work categories do not line up exactly with this tool's activity levels. The check shows the direction, not the size. This ADR's sources include no validation of Mifflin-St Jeor on Indian adults.
- **0.83 g/kg assumes good protein quality.** The ICMR-NIN table adds that people eating a cereal-based diet with low-quality protein need about 1 g/kg/day. The tool does not ask about diet, so a sedentary adult on such a diet is given a figure below that.
- **Protein per kg of actual body weight overstates needs at high body weight.** 1.6 g/kg at 150 kg is 240 g/day. Whether to use an adjusted or reference weight instead is an open question for the planner.
- **The 1.2 and 1.6 g/kg values come from studies of athletes and resistance-trained adults.** Applying them to a lightly active person or to someone gaining or losing without training is this project's extension, not what those papers tested.

The floor can also exceed what a very small adult needs to maintain weight. It is a safety bound on the number the tool prints, and the note says when it applied.

Pregnancy, breastfeeding, children and medical conditions are out of scope, as [ADR 0004](0004-facts-not-people.md) explains. Revisit the activity and goal factors if a better-sourced set turns up, and the Indian-adult gap if the planner's users report targets that feel low.
