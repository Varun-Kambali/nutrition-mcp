"""Fuzzy food search over names, aliases and IDs, tolerant of Indian-language spellings.

Indian food names have no single Roman spelling ("sambar", "sambhar", "sambaar"), so the
query and every candidate are normalised the same way, then scored with rapidfuzz. A food's
candidates are its name, its aliases and its ID read as words. A candidate scores
`fuzz.token_set_ratio` when it holds every word of the query ("dal" is a full match for
"dal tadka") and `fuzz.ratio`, the similarity of the whole strings, otherwise (so a
one-letter slip still counts). Each food keeps its best score.

A food is a result only when one candidate passes two checks:
- Every query word matches a word in that candidate. Two words match when they are equal,
  or when both have 5 or more letters, start with the same letter and are one edit apart
  (one letter inserted, deleted or changed): "masla" matches "masala", "samber" matches
  "sambar".
- That candidate scores at least RESULT_MIN_SCORE.

The word check is what keeps a dish that is not in the data from returning a different one.
On whole-string score alone, "chicken curry" matched chana masala through its alias
"chickpea curry" (88), "pulao" matched chapati through "phulka" (80) and "vada" matched
ginger through "ada" (86). Short words must be equal because one letter changes a short
word into another food ("sag" and "sago"), and the first letter must agree because "chole"
and "whole" are one letter apart. The score check stays because a one-edit word inside a
longer name is not enough on its own: "butter" folds to "buter", one edit from "biter" in
"bitter gourd", which scores 50.

Results are ordered by score, then how close the best name is as a whole string (plain
`fuzz.ratio`), then name, then ID. The second key matters because a food whose words contain
every query word scores 100 like the exact name: "paneer" ties Palak paneer, "chapati" ties
atta (alias "chapati flour"), and the exact name has to come first.

Two earlier scorers were dropped. WRatio's partial match scores a query that sits inside a
longer word at 90, so "tea" matched "steamed", "chana" matched the alias "anna" and "dal"
matched the Tamil "kadalai". An unconditional token_set_ratio scores 100 when a short alias
sits inside a longer query, so "aloo paratha" matched potato through "aloo" and "pao bhaji"
matched pav; now the token set counts only when the candidate has all the query's words.
A query that is only the start of a longer word is left out of the results ("dal" against
"dalma" scores 75); "dalma" itself is a full match.

Filters (region, diet, kind) apply before scoring, so results and suggestions both respect
them. Thresholds, on the 0 to 100 scale:
- RESULT_MIN_SCORE 80: the score a candidate that passes the word check needs for its food
  to be a result. A one-letter slip in a six-letter name scores 83 ("samber" for "sambar"),
  and one letter missing scores 91, so 80 keeps those. 85, the first cutoff, would have
  dropped "samber".
- SUGGESTION_MIN_SCORE 60: when no food is a result, up to MAX_SUGGESTIONS names at or above
  it come back as "did you mean" candidates, best first. A food that fails the word check
  can still be a suggestion, so "chicken curry" suggests Chana masala without returning it.
- MAX_QUERY_LENGTH 100 characters, and a limit of 1 to MAX_SEARCH_LIMIT results.
"""

import re
import unicodedata

from rapidfuzz import fuzz
from rapidfuzz.distance import Levenshtein

from nutrition_mcp.models import Food
from nutrition_mcp.queries import filter_foods
from nutrition_mcp.registry import Registry
from nutrition_mcp.schemas import FoodSummary, SearchResult

RESULT_MIN_SCORE = 80.0
SUGGESTION_MIN_SCORE = 60.0
MAX_SUGGESTIONS = 5
MAX_SEARCH_LIMIT = 50
MAX_QUERY_LENGTH = 100
# Words this long or longer may be one edit apart and still match.
TYPO_MIN_LETTERS = 5

# Common transliteration variants, applied in this order. The long vowels fold before the
# doubled letters collapse: collapsing first would turn "paneer" into "paner", while
# folding first gives "panir", which is how the same word is also spelled.
_FOLDS: tuple[tuple[str, str], ...] = (
    ("aa", "a"),
    ("ee", "i"),
    ("oo", "u"),
    ("bh", "b"),
    ("dh", "d"),
    ("kh", "k"),
    ("gh", "g"),
    ("th", "t"),
    ("ph", "p"),
    ("sh", "s"),
    ("w", "v"),
)
_DOUBLED_LETTER = re.compile(r"([^\W\d_])\1+")


def normalise(text: str) -> str:
    """Reduce a name to a form that its common spellings share.

    In order: lowercase, split accents off with NFKD and drop them, replace anything that
    is not a letter or digit with a space (so "whole-wheat" and "dal_tadka" read as words),
    collapse spaces, apply the folds in `_FOLDS` order, then collapse doubled letters.
    """
    decomposed = unicodedata.normalize("NFKD", text.lower())
    unmarked = (char for char in decomposed if not unicodedata.combining(char))
    words = "".join(char if char.isalnum() else " " for char in unmarked).split()
    folded = " ".join(words)
    for variant, plain in _FOLDS:
        folded = folded.replace(variant, plain)
    return _DOUBLED_LETTER.sub(r"\1", folded)


def _same_word(query_word: str, name_word: str) -> bool:
    """Equal, or one edit apart when both have TYPO_MIN_LETTERS or more and start alike."""
    if query_word == name_word:
        return True
    return (
        len(query_word) >= TYPO_MIN_LETTERS
        and len(name_word) >= TYPO_MIN_LETTERS
        and query_word[0] == name_word[0]
        and Levenshtein.distance(query_word, name_word, score_cutoff=1) <= 1
    )


def _has_every_word(query_words: list[str], name_words: list[str]) -> bool:
    return all(any(_same_word(word, other) for other in name_words) for word in query_words)


def _score(query: str, food: Food) -> tuple[float, float, bool]:
    """The food's score, how close its best name is as a whole, and whether it is a result.

    It is a result when one name holds every query word and scores RESULT_MIN_SCORE or more.
    """
    names = {normalise(food.name), normalise(food.id), *(normalise(a) for a in food.aliases)}
    query_words = query.split()
    words = set(query_words)
    score = 0.0
    closest = 0.0
    is_result = False
    for name in names:
        name_words = name.split()
        whole = fuzz.ratio(query, name)
        closest = max(closest, whole)
        name_score = fuzz.token_set_ratio(query, name) if words <= set(name_words) else whole
        score = max(score, name_score)
        if name_score >= RESULT_MIN_SCORE and _has_every_word(query_words, name_words):
            is_result = True
    return score, closest, is_result


def search_foods(
    reg: Registry,
    query: str,
    region: str | None,
    diet: str | None,
    kind: str | None,
    limit: int = 10,
) -> SearchResult:
    """Find foods by name, best match first, with kind and regions on every result.

    A name that fits several foods returns all of them, so the caller can choose. A food is a
    result only when one of its names holds every query word (see the module docstring), so
    a dish that is not in the data returns no results and up to 5 close names as suggestions.
    Raises ValueError for a limit outside 1 to 50, a query over 100 characters or with no
    letters or digits, or a region, diet or kind that is not valid.
    """
    if not 1 <= limit <= MAX_SEARCH_LIMIT:
        raise ValueError(f"limit must be 1 to {MAX_SEARCH_LIMIT}, got {limit}")
    if len(query) > MAX_QUERY_LENGTH:
        raise ValueError(f"query must be at most {MAX_QUERY_LENGTH} characters, got {len(query)}")
    wanted = normalise(query)
    if not wanted:
        raise ValueError("query must contain at least one letter or digit")

    candidates = filter_foods(reg.foods(), region, diet, kind)
    scored = [(*_score(wanted, food), food) for food in candidates]
    scored.sort(key=lambda row: (-row[0], -row[1], row[3].name, row[3].id))

    matches = [food for _, _, is_result, food in scored if is_result]
    if matches:
        return SearchResult(
            query=query,
            results=[FoodSummary.from_food(food) for food in matches[:limit]],
            total=len(matches),
            suggestions=[],
        )
    close = dict.fromkeys(
        food.name for score, _, _, food in scored if score >= SUGGESTION_MIN_SCORE
    )
    return SearchResult(query=query, results=[], total=0, suggestions=list(close)[:MAX_SUGGESTIONS])
