# 0007. A missing food: a note in the empty search result, and server instructions in the grounding run
Date: 2026-10-21 · Status: accepted

## Context
The first grounding run (`evals/results/2026-10-21_gemini-3.1-flash-lite_8262476.json`) asked about 4 dishes that are not in the data, and the model with tools reported 2 of them as missing. For pulao, 4 searches returned no results; a fifth, for "rice", returned 5 rice dishes, and the model answered with the numbers for jeera rice. For masala dosa it searched 7 times for the dosa and a potato filling and reached the 8-turn cap. The requirements list "A missing food or unit is quietly treated as something else" as an anti-goal.

In that run an empty `search_food` result held no results, a total of 0 and up to 5 close names as `suggestions`. The tool description already said to report a dish as missing instead of using a suggestion or another result. The run passed the model the tool descriptions but not the instructions the server sends when a client connects, and those instructions said nothing about missing foods.

## Decision
**An empty search result says what it means.** It carries a `note`: no food in the data has a name that matches every word of the query, a respelled query seldom finds more and a broader one finds other foods, and when the query is the food's name as asked, the food is to be reported as missing, not answered with a suggestion, a broader match or other foods put together. A filter can remove a food that is in the data, so an empty result from a search with a region, diet or kind says only that nothing was found under those filters, and asks for a search without them before the food is reported as missing. A result with matches has `note: null`. Tests pin both texts, because a change to either changes what the grounding evaluation measures. The server instructions gain one sentence that says the same, and the `search_food` description names the field.

The note goes in the result, not only in the description, because the model reads the result at the point where it decides what to do next, while the description is read once, among five tools. It asks for a missing report only when the query is the food's name as asked, so a query with an extra word ("homemade dal tadka") can still be shortened and run again.

**The grounding run passes the server's instructions.** The with-tools arm's system prompt is the task prompt, then the line "Instructions from the nutrition-mcp server:", then the instructions the server sent at connection. The MCP specification describes instructions as a hint for the model that a client may add to its system prompt, so the run now measures the server as such a client uses it. The without-tools arm has no server and keeps the task prompt alone. Each results file records `server_instructions`, true when they were passed. The baseline file predates the field and did not pass them. `questions_version` still covers the questions and the task prompt only; the instructions come from the server at run time, and the commit pins them, as it pins the tool descriptions.

## Alternatives considered
- **Description text only.** It was already there in the first run and did not stop the substitution.
- **A flag such as `found: false`.** An empty `results` list already says that. What the model lacked was what to do next.
- **No suggestions in an empty result.** Close names help a person who misspelled a name beyond what the typo rules catch. The note says they are other foods.
- **Keep the run without instructions and add them as a third arm.** A third arm adds half again as many requests on a rate-limited free tier, and the question the evaluation answers is what adding this server to a client changes.
- **Refuse further searches after an empty result.** The server cannot tell a missing dish from a query with an extra word, so the choice stays with the model.

## Consequences
The next grounding run differs from the baseline in three ways at once: the note, the instructions sentence, and the passing of the instructions. Its missing-dish share and failed answers cannot be credited to any one of them, and with 4 missing-dish questions one run does not say how often either behaviour happens. In the baseline run every search made for a question with items found a result, so the note did not appear in those questions.

A client that does not pass the instructions still gets the note, which arrives with the result in both the structured and the text content.

A model may now report a food as missing after a query with an extra word, where it used to search again. The condition in the note is aimed at that case. In a run it would show as a missing list or a failed answer on a question with items.
