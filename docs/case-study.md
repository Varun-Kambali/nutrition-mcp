# Case study: nutrition-mcp

Draft written at kickoff on 2026-10-19, before any code, and updated as each milestone lands. The results section holds the first run of each evaluation, recorded on 2026-10-20 and 2026-10-21.

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
- **Glycemic index and glycemic load** for the dishes that an openly licensed compendium of GI values for non-Western foods (Henry et al., 2021, CC BY 4.0) lists as the same food with a similar preparation. 7 of the 41 dishes have one: idli, plain dosa, rava upma, chapati, methi thepla, steamed rice and Bengali khichuri. The other 34, including every dal and curry, have no GI, and their glycemic load is missing rather than estimated. The matching rule and every match are in [data.md](data.md#glycemic-index).
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

1. **Write the project's recipes instead of copying a dish table.** The Indian tables can't be redistributed. Standard recipes computed from openly licensed ingredients mean every number traces back to a source row and a stated assumption. The alternatives were shipping IFCT values (not allowed) or typing in dish values from blogs (no traceable source).
2. **Arithmetic in code, never in the model.** Unit conversion, meal totals and glycemic load are deterministic functions. The model decides what to ask for, and the server does the maths.
3. **Facts here, the person elsewhere.** The server knows foods, not people. Keeping health profiles out of it keeps it simple to trust and safe to reuse, and gives the planner agent a clean boundary.
4. **Unknown is not zero.** When a source doesn't report a nutrient, the value is missing, and any dish that depends on it says which ingredient is missing instead of quietly under-counting.
5. **Two evaluations, not one.** Grading the model against my own data would only show that it used the tool. A separate check compares my computed dish values with independently measured ones, such as CoFID's homemade sambar, saag and dals.
6. **Keep each source's values as published.** USDA and CoFID define some nutrients differently. The mapping converts only what has a clear rule (USDA carbohydrate by difference minus fibre gives available carbohydrate) and does not recompute energy. data.md records each difference so a reader can judge a number.
7. **Search never swaps one food for another.** Search folds common spelling variants (sambhar, chapathi, daal) and then matches whole words, so a dish that isn't in the data gets no result and a few close names. An early version matched "chicken curry" to chana masala through its alias "chickpea curry", which is exactly the confident wrong answer this project exists to prevent. The tool description tells the model to report a missing dish as missing.

Each decision gets an ADR in [decisions/](decisions/) as it is built.

The tools sit on top of that data. Every result carries the grams it used and how it got them ("1 katori = 150 g (default katori)"), the sources behind the numbers, and which nutrients are incomplete and why. Search results list every unit a food accepts with its grams, so the model never converts a glass of milk itself. Errors are written for the model to act on: an unknown unit lists the valid ones, an unknown food gets close matches, and a bad meal item is named by its position. The server logs one line per call with the tool, time taken and outcome, and never the inputs, so nothing a person asks is kept. Calorie and protein targets use the Mifflin-St Jeor equation with cited constants, and [ADR 0005](decisions/0005-targets-formula.md) says which ones are conventions with no primary source.

The two evaluations check two different claims ([ADR 0006](decisions/0006-two-evaluations.md)). Data accuracy needs no model. For each of the 18 dishes that have an independent record (a CoFID 2021 homemade dish or a USDA survey food), it compares the values the server computes per 100 g with that record and reports the mean absolute percentage error (MAPE). kcal, protein and available carbohydrate are gated in CI; iron and calcium are only reported. Grounding asks a Gemini model 30 questions twice with the same prompt, once from memory and once with the server's tools over stdio. Each question's golden answer is the server's own total for the foods it names, and 4 of the questions name a dish that is not in the data, where the right answer is that the dish is missing. The model ends with a JSON answer, so scoring needs no judge model, and the error is the MAPE over the 26 questions that both runs answered with numbers. Grounding calls a model, so it runs on demand; data accuracy runs on every pull request.

## Results

The first run of each evaluation, copied from `evals/results/summary.md`. The grounding run is gemini-3.1-flash-lite on the free tier at the provider's default temperature, recorded on 2026-10-21 at commit 8262476 ([results file](../evals/results/2026-10-21_gemini-3.1-flash-lite_8262476.json)). The data-accuracy run was recorded on 2026-10-20 at commit 32aa03d ([results file](../evals/results/2026-10-20_data-accuracy_32aa03d.json)). Errors are the mean absolute percentage error (MAPE).

| Metric | Without tools | With tools | How measured |
|---|---|---|---|
| Calorie error against the dataset totals, MAPE (%), 26 questions | 16.0 | 0.0 | grounding eval |
| Protein error against the dataset totals, MAPE (%), 26 questions | 13.2 | 0.0 | grounding eval |
| Carbohydrate error against the dataset totals, MAPE (%), 26 questions | 20.2 | 0.0 | grounding eval |
| Answers within 10% on calories (share), 26 questions | 0.38 | 1.0 | grounding eval |
| Missing dishes reported (share), 4 questions | n/a | 0.5 | grounding eval |
| Tool errors (count), 89 tool calls | n/a | 0 | grounding eval |
| p95 tool latency (ms), 89 tool calls | n/a | 10.4 | grounding eval |
| Energy (kcal) MAPE against independent references (%), 18 dishes | n/a | 18.7 | data-accuracy eval |
| Protein (g) MAPE against independent references (%), 18 dishes | n/a | 24.8 | data-accuracy eval |
| Available carbohydrate (g) MAPE against independent references (%), 18 dishes | n/a | 16.7 | data-accuracy eval |
| Iron (mg) MAPE against independent references (%), 18 dishes | n/a | 44.2 | data-accuracy eval |
| Calcium (mg) MAPE against independent references (%), 18 dishes | n/a | 46.1 | data-accuracy eval |

The 0.0 is by construction. The golden answers are the server's own totals, so it shows that with the tools the model picked the right food and unit and used the computed total on every one of the 26 questions, while the same model from memory was off by 16.0% on calories and had 0.38 of its calorie answers within 10%. It does not show that the totals are right: the data-accuracy rows measure that, against independent records, and the limits below say more.

## Limits, and what better inputs would change

- **Dish values depend on my recipes.** Home cooking varies, and oil varies most of all: one extra spoon of ghee changes a meal more than most errors in the composition data. Every answer states its oil and portion assumptions so the user can adjust.
- **Energy is on two bases.** USDA counts fibre as carbohydrate when it computes kcal, and CoFID gives fibre no energy. For the USDA pulses that fibre is 11% to 25% of their kcal, so a dish built from USDA pulses or atta reads high next to a CoFID reference dish even when the recipe is right. The data-accuracy results list the sources behind each side of a comparison, so the energy basis of each can be read off.
- **Split dals have no fibre value.** Toor, moong, urad and chana dal come from CoFID records that report no fibre, so every dish made with them reports fibre as incomplete until a source with fibre for split dals is found.
- **Some ingredients have no open source.** Jaggery, ragi and poha have no record in USDA or CoFID. A dish that needs one of them as a core ingredient is dropped or replaced with another dish. A dish that uses only a small, optional amount, such as the jaggery in bisi bele bath, leaves it out and says so in its assumptions.
- **Micronutrients are rougher than macronutrients.** They vary with variety, soil and storage, and heat destroys much of the folate, vitamin C and B vitamins. In v1, dish micronutrients are totals of the raw ingredients, and they are labelled that way.
- **What the body absorbs is not what is on paper.** Iron from plants is absorbed far less than iron from meat, and tea with a meal reduces it further. The server can say which foods are good sources; it can't measure absorption.
- **Glycemic index values are averages from small studies,** and they shift with how a food is cooked and what it is eaten with.
- **Most dishes have no glycemic index.** The compendium has no row for any dal, curry or sabzi eaten on its own (they appear only inside mixed meals, such as flatbread with dhal curry), so only 7 of the 41 dishes carry a GI, and a meal that includes any other dish can only have a partial glycemic load. Three of the seven values come from one Kerala study. A dish the compendium doesn't name never borrows a similar dish's value, so ven pongal stays without khichdi's GI. Rows measured against white bread instead of glucose are left out, because the paper gives no factor to convert them; one of them is a rice dosa tested in India.
- **A deficiency is diagnosed with a blood test, not a food log.** The server and the planner take deficiencies as facts from the user's doctor.
- **41 dishes is a small set.** When a dish is missing, search returns no result and a few close names, and the assistant is told to say the dish is missing instead of guessing.
- **Search is literal about words.** A prefix doesn't match ("pan" doesn't find paneer), queries in Devanagari or other scripts aren't transliterated, and names from different languages can fold to similar spellings and collide. Every result shows its kind and regions, and the model is told to check the name.
- **Rankings per 100 g favour seasonings.** Ranked per 100 g or per 100 kcal, spices, fats and salt can top a list, such as cumin for iron or ghee for low sugar. Ranking dishes, or ranking per serving, gives answers closer to what people eat.
- **Targets are estimates for a population.** The Mifflin-St Jeor equation comes out below ICMR-NIN's reference energy figures for Indian adults, and protein per kg of actual body weight overstates needs at a high body weight. [ADR 0005](decisions/0005-targets-formula.md) gives the comparison.
- **The grounding 0.0 shows tool use, not correct data.** Golden answers are the server's own totals, so 0.0 means the model picked the right food and unit and used the computed total on every one of the 26 questions, and says nothing about whether the totals match what the dishes contain. The from-memory errors (16.0, 13.2 and 20.2) are also distance from those totals, not from the dishes.
- **Computed dish values differ from independent records.** Against the 18 dishes that have one, the data-accuracy run puts the mean error at 18.7% for kcal, 24.8% for protein and 16.7% for available carbohydrate, and at 44.2% for iron and 46.1% for calcium. Part of each gap is a difference between a UK or US record and the bundled recipe (water, fat, an enriched grain) or in energy basis rather than wrong data, and with 18 dishes one dish moves an average. [ADR 0006](decisions/0006-two-evaluations.md) names the largest cases.
- **A missing dish is not always reported as missing.** Of the 4 grounding questions that name a dish that is not in the data, the model with tools reported chicken curry and kheer as missing, for a share of 0.5. For pulao it made 5 searches without finding a pulao, then answered with the numbers for jeera rice, the quiet substitution this project exists to prevent. For masala dosa it made 7 searches and reached the 8-turn cap without an answer, which is the one failed answer in the run.
- **One run, one model, no server instructions.** The grounding run is a single pass of gemini-3.1-flash-lite on the free tier at the provider's default temperature, so answers vary from run to run. It passed the model the tools but not the instructions the server sends when a client connects, so for clients that pass them on, the measured gain is a lower bound, and the missing-dish behaviours above may differ.
- **Better data improves accuracy without code changes.** With a licensed IFCT copy or lab-tested recipes loaded, the data-accuracy evaluation would show how much.

## What I learned

Written as the build goes.
