# 0003. Missing is not zero
Date: 2026-10-19 · Status: accepted

## Context
Sources do not report every nutrient for every food. A blank means the source did not report the nutrient, not that the food has none of it. If a blank becomes 0 when a dish is added up, the total comes out low and nothing says so.

The people most likely to look up iron, vitamin B12 or vitamin D are the ones managing a deficiency. An under-count misleads exactly the users who act on the number. The requirements list a missing value reported as zero as a failure of v1.

## Decision
Missing stays `None` from the CSV to the tool output. An empty cell loads as `None`, never 0, and every field on `Nutrients` is optional.

For a dish, each nutrient is summed over the ingredients that report it and divided by the cooked weight. If any ingredient lacks the nutrient, the dish gets an `incomplete` entry for it that names those ingredients. If no ingredient reports it, the dish value is `None`. A dish value with an `incomplete` entry is therefore a floor: it counts what is known and says what is not. Everything downstream (scaling to an amount, meal totals, rankings) carries the `incomplete` list forward.

## Alternatives considered
- **Treat a blank as 0.** The simplest option, and it under-counts without a warning. It breaks the rule above.
- **Set the dish value to `None` whenever any ingredient lacks the nutrient.** It is never wrong, but one spice with a blank vitamin would wipe out a dish's iron figure, and the known part is useful.
- **Fill gaps with an estimate from a similar food.** It invents a number with no source, which the project does not allow.

## Consequences
Users and agents can see which numbers are partial and why, and the fix for a gap is better source data, not code. The `incomplete` mapping is plain data, so tests can assert it.

Every consumer now has to handle `None` and `incomplete`. Meal totals track it, rankings leave out foods that lack the ranked value and count them, and glycemic load is flagged when a GI is missing. Tool output has more fields, and clients should show the flag next to the number it qualifies.

Watch how often the nutrients people care about come back incomplete in the bundled data. If one common ingredient keeps causing it, look for another source for that ingredient.
