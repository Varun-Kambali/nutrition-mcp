# 0001. Write our own recipes instead of copying a dish table
Date: 2026-10-19 · Status: accepted

## Context
The standard source for Indian foods is ICMR-NIN's Indian Food Composition Tables 2017 (IFCT). It is a copyrighted book, so an open tool cannot ship its values. The 2024 Indian Nutrient Databank is built on those tables and its repository carries no open license, so it is out for the same reason.

The open sources have ingredients but few Indian dishes. USDA FoodData Central is public domain, but a search of its Foundation, SR Legacy and survey data on 2026-10-19 found one dal, one idli and two chapati entries, and nothing for rajma, chole or ragi. CoFID 2021 is released under the Open Government Licence v3.0 and covers some of the gaps, such as paneer, methi leaves, curry leaves and split dals.

Every number the server returns has to trace to a source row with a license that allows redistribution. The requirements treat two things as failures: a dish value typed in by hand, and one copied from a source we cannot redistribute.

## Decision
Take nutrient values only from openly licensed sources (USDA FoodData Central and CoFID), and ship each ingredient row with its source, record ID and license. Glycemic index values come from the compendium of GI values for non-Western foods (Henry et al., 2021, CC BY 4.0). Write the dish recipes ourselves as standard home recipes: raw grams per ingredient, cooked weight, servings, household measures, and an assumptions note that states the fat used. Compute each dish's per-100 g nutrition when the dataset loads, from its ingredient rows. The recipes are our own work and are released under CC BY 4.0.

## Alternatives considered
- **Ship IFCT dish values.** Not allowed, because the book is copyrighted.
- **Use the Indian Nutrient Databank.** It derives from IFCT and has no open license.
- **Type in dish values from recipe blogs or apps.** Those numbers have no traceable source and nothing to check them against, which is the problem this project is meant to fix.

## Consequences
Every dish number can be traced to ingredient rows and a recipe that anyone can read and argue with. A team with a licensed IFCT copy or lab-tested recipes can load them in the same file format, and the data-accuracy evaluation then shows how much better data helps.

Dish accuracy now depends on our recipes. Home cooking varies, and oil varies most, so each recipe states its fat assumption and needs a hand review. An ingredient missing from both USDA and CoFID (jaggery, ragi and poha at kickoff) cannot be used until an open source turns up, so dishes that need it are dropped or replaced. Cooking losses are not applied in v1, which makes dish values for heat-sensitive vitamins raw-ingredient totals.

Watch the data-accuracy evaluation, which compares computed dishes with independent references. When one dish is far off, check its recipe first.
