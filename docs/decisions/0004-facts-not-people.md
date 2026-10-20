# 0004. The server holds food facts, the planner holds the person
Date: 2026-10-20 · Status: accepted

## Context
The people who most need this data are managing diabetes or a diagnosed deficiency. One option is to make the server aware of them: accept a health profile, flag a meal as unsuitable, suggest more iron. A food database that decides what is right for a particular person is making a medical judgment, though, and the requirements count that as a failure of v1: the server must never infer a condition or deficiency, or suggest a medicine or a dose.

The next project, the meal-planner agent, is where the person lives. It will know the user's profile and goals, and it needs the server for every food number it uses.

## Decision
The server answers questions about food. Its tools take food IDs, amounts and units, and return nutrients, glycemic load, rankings and the assumptions behind them. They have no notion of who is asking. `find_foods` ranks by iron because the caller asked for iron, not because anyone has a deficiency.

The one tool that takes details of a person is `compute_targets`. It is a stateless function: sex, age, height, weight, activity and goal go in, calorie and protein targets come out, and the inputs live only for the length of the call. Nothing is stored, and the logs record the tool name, latency, outcome and result count, never the inputs. It takes no conditions, deficiencies, medicines or doses, and the output says it is an estimate and not medical advice. [ADR 0005](0005-targets-formula.md) has the formula.

The planner holds the person: the profile, the conditions, which nutrients matter, the reference intakes to compare against, and the judgment about what to eat. It calls the server for food facts and does that reasoning itself.

## Alternatives considered
- **Accept a health profile in each call and tailor the answer.** The server would rank by glycemic load for someone with diabetes without being asked. That puts medical rules in a data server, where they are hard to test against anything and easy to get wrong, and it is the failure the requirements rule out.
- **Remember the user between calls.** It would save the client repeating itself, but it makes the server a store of personal health data, which breaks the "nothing about the user is stored" rule and the public, no-personal-data constraint.
- **Leave `compute_targets` out and let the planner calculate targets.** Every client would then write its own version of the formula, and a model doing arithmetic in its head is the problem this project exists to fix. A pure function that keeps no state is the smaller risk.

## Consequences
The server stays testable: every tool is a function of food data and its arguments, and the fixtures in the tests are enough to check it. The planner has more to build, because condition-aware logic is its job, and it will need reference intakes from somewhere else.

`compute_targets` inputs are still personal, and they pass through whatever model the client uses before they reach the server. The server cannot control that, and the README says so in its "Use it" section.

If a later feature seems to need the server to know the person, such as flagging a food as unsafe for someone, it belongs in the planner, or this decision has to be reopened first.
