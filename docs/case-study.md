# Case study: nutrition-mcp

Draft written at kickoff on 2026-10-19, before any code. The results section fills in as the evaluations run.

## Why: the problem

When I ask an AI assistant to help with meal prep, the answers are written for someone in the US or Europe. It suggests ingredients that are expensive or hard to find here because they are popular online. It knows little about the dals, millets and sabzis people in India cook every day. And when I ask "how much protein is in 2 rotis and a katori of rajma?", it gives a confident number with no source and no stated portion.

The stakes are higher than calorie counting. Diabetes and deficiencies such as iron, vitamin B12 and vitamin D are common health problems in India, and what people eat is part of managing all of them. Someone watching their blood sugar needs the carbohydrate and glycemic load of the meal on their plate, not of a Western equivalent. Someone told by their doctor that they are low in iron needs to know which foods they can actually buy and cook are good sources.

Three things go wrong at once:

1. **The data isn't there.** Models have seen far more about Western food than Indian food, and the open databases reflect the same gap. In USDA FoodData Central, the largest public food database, a search of its Foundation, SR Legacy and survey data found one "Dal", one "Idli" and two chapati entries, and nothing for rajma, chole or ragi. Paneer, jaggery and poha were missing as ingredients (searched 2026-10-19).
2. **Portions are vague.** A katori, a roti or "a bowl" can mean very different weights, and the model rarely says which one it assumed.
3. **The arithmetic slips.** Even when a model knows per-100 g values, adding up a four-item meal with unit conversions is where errors creep in.

The authoritative Indian source, ICMR-NIN's [Indian Food Composition Tables 2017](https://nin.res.in/ebooks/IFCT2017.pdf), is a copyrighted book, so an open tool can't ship its values. The 2024 [Indian Nutrient Databank](https://pmc.ncbi.nlm.nih.gov/articles/PMC11277795/) is built on those tables, and its [repository](https://github.com/lindsayjaacks/Indian-Nutrient-Databank-INDB-) carries no open license.

## Why I built it

The moment above is mine. I wanted help planning meals around ingredients I can buy in my city, sized to my own goal, and got generic advice instead. I want to propose a change in how Indians eat their meals: it needs to be with intention. That starts with knowing what is actually on the plate.

## What: the solution

nutrition-mcp is an open-source [Model Context Protocol](https://modelcontextprotocol.io) server. Any MCP client (Claude Desktop, Claude Code, Cursor, or an agent built with LangGraph) can call it to get the nutrition of Indian dishes and ingredients for any amount, in grams or household measures. Every answer states its source and the portion it assumed.

In scope for v1:

- **Ingredients** from USDA FoodData Central (public domain) and the UK's CoFID 2021 (Open Government Licence), which fills Indian gaps such as paneer, methi leaves, curry leaves and split dals. Each has its Indian and English names.
- **About 40 dishes,** 10 each from North, South, East and West India, written as standard recipes. Their nutrition is calculated from the ingredient rows, never typed in.
- **Nutrients that matter for Indian health problems,** not just calories: protein, fat, saturated fat, available carbohydrate, sugars and fibre; sodium; iron, calcium and zinc; folate and vitamins A, B12, C and D.
- **Glycemic index and glycemic load** for dishes that have a published value in an openly licensed compendium of GI values for non-Western foods, which covers idli, dosa, upma, roti, ragi, basmati rice and khichdi.
- **Five tools:** search, nutrition for an amount, meal totals, ranking foods by any nutrient (for example iron per 100 kcal, or the lowest glycemic load), and daily calorie and protein targets.
- **Two evaluations:** data accuracy against independent references, and the change in a model's accuracy when it uses the tools.

Out of scope for v1, and why:

- **The person.** Health conditions, diagnosed deficiencies, goals and preferences, and meal plans that target them, belong to an agent that calls this server. A planner agent built on top of it is my next project.
- **Packaged foods and live regional prices.** Open Food Facts and the government's Agmarknet mandi prices both have usable licenses, so they are planned extensions rather than v1 work.
- **A hosted public endpoint.** v1 runs on the user's machine over stdio.
- **Medical advice.** The server never infers a condition or a deficiency, never suggests medicines or insulin doses, and labels its targets as estimates from a named formula.

## Who and where: users and fit

- **People who use an assistant** to log meals or plan food get numbers with a source and a portion instead of a guess.
- **People managing diabetes or a diagnosed deficiency,** through an assistant or a planner, get carbohydrate, glycemic load and micronutrient numbers for the food they actually eat, with the uncertainty stated.
- **Developers of assistants and agents** get nutrition data as a typed tool over MCP, without writing their own lookup and unit-conversion code.
- **Companies in this space** (fitness and diet apps, diabetes care programmes, dietitian platforms, food delivery menus) are the bigger fit. The bundled data is a small open reference set. A company can load its own data through the same documented file format: a licensed copy of IFCT, lab-tested recipes, or its restaurant partners' menus. The tools don't change. The evaluation runs on their data too, so they can measure what the layer does for them before adopting it.

## How: design and trade-offs

The server is a thin MCP layer over a small in-memory data layer. At startup it loads dataset folders (ingredients as CSV, dishes as YAML), validates every row, and computes each dish's nutrition per 100 g from its ingredients and cooked weight. The tools are plain Python functions over that data, exposed with typed inputs and structured outputs. The diagram and data flow are in [architecture.md](architecture.md).

The data foundation is a table of 66 ingredients. Each one comes from a single USDA FoodData Central or CoFID 2021 record, checked by hand against the source's own description: raw, the part that is eaten, and split dal or whole seed as the recipe uses it. USDA comes first because its records carry the full nutrient set. CoFID fills what USDA lacks or has only in the wrong form, such as paneer, methi leaves, curry leaves, rohu and four of the split dals. The rules for choosing a record and mapping each source's nutrients are in [data.md](data.md) and [ADR 0002](decisions/0002-data-sources.md).

The decisions that mattered:

1. **Write our own recipes instead of copying a dish table.** The Indian tables can't be redistributed. Standard recipes computed from openly licensed ingredients mean every number traces back to a source row and a stated assumption. The alternatives were shipping IFCT values (not allowed) or typing in dish values from blogs (no traceable source).
2. **Arithmetic in code, never in the model.** Unit conversion, meal totals and glycemic load are deterministic functions. The model decides what to ask for, and the server does the maths.
3. **Facts here, the person elsewhere.** The server knows foods, not people. Keeping health profiles out of it keeps it simple to trust and safe to reuse, and gives the planner agent a clean boundary.
4. **Unknown is not zero.** When a source doesn't report a nutrient, the value is missing, and any dish that depends on it says which ingredient is missing instead of quietly under-counting.
5. **Two evaluations, not one.** Grading the model against my own data would only show that it used the tool. A separate check compares my computed dish values with independently measured ones, such as CoFID's homemade sambar, saag and dals.
6. **Keep each source's values as published.** USDA and CoFID define some nutrients differently. The mapping converts only what has a clear rule (USDA carbohydrate by difference minus fibre gives available carbohydrate) and does not recompute energy. data.md records each difference so a reader can judge a number.

Each decision gets an ADR in [decisions/](decisions/) as it is built.

## Results

Not measured yet. These rows come from `evals/results/summary.md` once the evaluations have run.

| Metric | Without tools | With tools | How measured |
|---|---|---|---|
| Mean absolute error on calories, [N] questions | [X]% | [Y]% | grounding eval |
| Mean absolute error on carbohydrate, [N] questions | [ ] | [ ] | grounding eval |
| Answers within 10% on calories | [ ] | [ ] | grounding eval |
| Mean absolute error against independent references, [N] dishes | n/a | [ ] | data-accuracy eval |
| p95 tool latency | n/a | [ ] ms | grounding eval |

## Limits, and what better inputs would change

- **Dish values depend on my recipes.** Home cooking varies, and oil varies most of all: one extra spoon of ghee changes a meal more than most errors in the composition data. Every answer states its oil and portion assumptions so the user can adjust.
- **Energy is on two bases.** USDA counts fibre as carbohydrate when it computes kcal, and CoFID gives fibre no energy. For the USDA pulses that fibre is 11% to 25% of their kcal, so a dish built from USDA pulses or atta reads high next to a CoFID reference dish even when the recipe is right. The data-accuracy evaluation has to show the energy basis of each side of a comparison.
- **Split dals have no fibre value.** Toor, moong, urad and chana dal come from CoFID records that report no fibre, so every dish made with them reports fibre as incomplete until a source with fibre for split dals is found.
- **Some ingredients have no open source.** Jaggery, ragi and poha have no record in USDA or CoFID. A dish that needs one of them as a core ingredient is dropped or replaced with another dish. A dish that uses only a small, optional amount, such as the jaggery in bisi bele bath, leaves it out and says so in its assumptions.
- **Micronutrients are rougher than macronutrients.** They vary with variety, soil and storage, and heat destroys much of the folate, vitamin C and B vitamins. In v1, dish micronutrients are totals of the raw ingredients, and they are labelled that way.
- **What the body absorbs is not what is on paper.** Iron from plants is absorbed far less than iron from meat, and tea with a meal reduces it further. The server can say which foods are good sources; it can't measure absorption.
- **Glycemic index values are averages from small studies,** and they shift with how a food is cooked and what it is eaten with.
- **A deficiency is diagnosed with a blood test, not a food log.** The server and the planner take deficiencies as facts from the user's doctor.
- **41 dishes is a small set.** When a dish is missing, the server says so and returns the closest ingredients, so the assistant can build the dish from its parts instead of guessing.
- **Better data improves accuracy without code changes.** With a licensed IFCT copy or lab-tested recipes loaded, the data-accuracy evaluation would show how much.

## What I learned

Written as the build goes.
