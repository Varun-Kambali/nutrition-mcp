"""Fuzzy food search over names, aliases and IDs, tolerant of Indian-language spellings.

Indian food names have no single Roman spelling ("sambar", "sambhar", "sambaar"), so the
query and every candidate are normalised the same way, then scored with rapidfuzz. A candidate
scores the larger of `fuzz.ratio` (whole-string similarity, so a one-letter slip still counts)
and `fuzz.token_set_ratio` (shared whole words, so "dal" is a full match for "dal tadka").
Each food keeps its best score over its name, its aliases and its ID read as words.

WRatio was the first scorer and was dropped: its partial match scores a query that sits
inside a longer word at 90, so "tea" matched "steamed", "chana" matched the alias "anna" and
"dal" matched the Tamil "kadalai". Both scorers used now compare whole words or whole strings.
A query that is only the start of a longer word scores low ("dal" against "dalma" is 75, a
suggestion and not a result); "dalma" itself is a full match.

Filters (region, diet, kind) apply before scoring, so results and suggestions both respect
them. Thresholds, on the 0 to 100 scale:
- RESULT_MIN_SCORE 80: a food at or above it is a result. A one-letter slip in a six-letter
  name scores 83 ("samber" for "sambar"), and one letter missing scores 91, so 80 keeps
  those. 85, the first cutoff, would have dropped "samber".
- SUGGESTION_MIN_SCORE 60: when no food reaches 80, up to MAX_SUGGESTIONS names at or above
  it come back as "did you mean" candidates, best first.
- MAX_QUERY_LENGTH 100 characters, and a limit of 1 to MAX_SEARCH_LIMIT results.
"""

import re
import unicodedata

from rapidfuzz import fuzz

from nutrition_mcp.models import Food
from nutrition_mcp.queries import filter_foods
from nutrition_mcp.registry import Registry
from nutrition_mcp.schemas import FoodSummary, SearchResult

RESULT_MIN_SCORE = 80.0
SUGGESTION_MIN_SCORE = 60.0
MAX_SUGGESTIONS = 5
MAX_SEARCH_LIMIT = 50
MAX_QUERY_LENGTH = 100

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


def _score(query: str, food: Food) -> float:
    names = {normalise(food.name), normalise(food.id), *(normalise(a) for a in food.aliases)}
    return max(
        (max(fuzz.ratio(query, name), fuzz.token_set_ratio(query, name)) for name in names if name),
        default=0.0,
    )


def search_foods(
    reg: Registry,
    query: str,
    region: str | None,
    diet: str | None,
    kind: str | None,
    limit: int = 10,
) -> SearchResult:
    """Find foods by name, best match first, with kind and regions on every result.

    A name that fits several foods returns all of them, so the caller can choose. When
    nothing scores high enough there are no results and up to 5 close names as suggestions.
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
    scored = [(_score(wanted, food), food) for food in candidates]
    scored.sort(key=lambda pair: (-pair[0], pair[1].name, pair[1].id))

    matches = [food for score, food in scored if score >= RESULT_MIN_SCORE]
    if matches:
        return SearchResult(
            query=query,
            results=[FoodSummary.from_food(food) for food in matches[:limit]],
            suggestions=[],
        )
    close = dict.fromkeys(food.name for score, food in scored if score >= SUGGESTION_MIN_SCORE)
    return SearchResult(query=query, results=[], suggestions=list(close)[:MAX_SUGGESTIONS])
