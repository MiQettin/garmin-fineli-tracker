"""Food name search for the diary, recipe and favorites pickers.

Matching: every word of the query must appear somewhere in a name (any
order, any of the searched fields), case-insensitive also for ä/ö/å. A word
also matches its synonyms ("kana" finds "BROILERI, ...").

Ranking: Fineli names are comma-separated, most general part first
("MARJAHILLO, KESKIARVO", "JAUHO, VEHNÄJAUHO, PUOLIKARKEA"). In a Finnish
compound the head is the *last* part - marjahillo is jam, hillopiirakka is a
pie - so a name whose first part ends with the word ranks above one that
only starts with it. See _match_rank.

Typos: when a word matches nothing, it is replaced with the closest word in
the Fineli names ("jugurtti" -> "jogurtti") and the search is run again.
"""
import difflib
import re
from dataclasses import dataclass, field

from django.db.models import Count, Max, Q

from .food_language import name_field
from .models import FineliFood

FINELI_FIELDS = ("name_fi", "name_sv", "name_en")

# Words that mean the same food. Each group is searched as one word: any of
# them matches, the typed one ranks a little higher. Lower case.
SYNONYM_GROUPS = [
    {"kana", "broileri"},
    {"hillo", "marmeladi"},
    {"chicken", "broiler"},
    {"jam", "marmalade"},
]
_SYNONYMS = {word: group for group in SYNONYM_GROUPS for word in group}

# Compound-head and typo matching need a few letters to mean anything.
MIN_HEAD_LENGTH = 3
MIN_TYPO_LENGTH = 4
TYPO_CUTOFF = 0.8


def search_words(query):
    """The words of a search query ("maito, lak" -> ["maito", "lak"])."""
    return query.replace(",", " ").split()


def word_alternatives(word):
    """The word itself first, then its synonyms."""
    lowered = word.casefold()
    return [word] + sorted(_SYNONYMS.get(lowered, set()) - {lowered})


def name_search(fields, query):
    """Q for names that contain every word of `query` (or a synonym).

    SQLite's LIKE folds case for ASCII letters only, so "vehnäjauho" never
    matched Fineli's upper-case "VEHNÄJAUHO". Matching each word as typed,
    upper-, lower- and capitalised covers Fineli's all-caps names and normal
    mixed-case custom food names."""
    condition = Q()
    for word in search_words(query):
        word_matches = Q()
        for alternative in word_alternatives(word):
            variants = {alternative, alternative.upper(), alternative.lower(), alternative.capitalize()}
            for name_field_ in fields:
                for variant in variants:
                    word_matches |= Q(**{f"{name_field_}__icontains": variant})
        condition &= word_matches
    return condition


def _is_head(part, word):
    """`part` is `word`, or its last word is a compound ending in `word`
    ("marjahillo" for "hillo", "kaura-ruispuuro" for "puuro")."""
    if part == word:
        return True
    if len(word) < MIN_HEAD_LENGTH:
        return False
    last = re.split(r"[\s\-]+", part)[-1]
    return last.endswith(word)


def _match_rank(name, word):
    """Relevance of a name for one query word, lower is better:
    0 the first part is the word,
    1 the first part is a compound ending in it (MARJAHILLO, KAURAPUURO),
    2 the second part is or ends in it (JAUHO, VEHNÄJAUHO),
    3 a later part is or ends in it,
    4 the first part starts with it (HILLOPIIRAKKA),
    5 any word starts with it,
    6 anywhere else (MAUSTETTU HILLOLLA)."""
    word = word.casefold()
    parts = [part.strip() for part in (name or "").casefold().split(",")]
    if parts[0] == word:
        return 0
    if _is_head(parts[0], word):
        return 1
    if len(parts) > 1 and _is_head(parts[1], word):
        return 2
    if any(_is_head(part, word) for part in parts[2:]):
        return 3
    if parts[0].startswith(word):
        return 4
    if any(token.startswith(word) for part in parts for token in re.split(r"[\s\-]+", part)):
        return 5
    return 6


def _word_rank(names, word):
    """Best rank of `word` (or a synonym, one step lower) over the names.
    Doubled so a synonym's match sits just behind the same match of the
    typed word."""
    ranks = []
    for index, alternative in enumerate(word_alternatives(word)):
        penalty = 0 if index == 0 else 1
        ranks.extend(_match_rank(name, alternative) * 2 + penalty for name in names if name)
    return min(ranks, default=12)


@dataclass
class FineliSearchResult:
    foods: list = field(default_factory=list)
    total: int = 0
    # The query actually searched when typos were corrected, else None.
    corrected_query: str = None


def _ranked_rows(query, fields, by_usage):
    rank_field = name_field()
    columns = list(dict.fromkeys((rank_field,) + tuple(fields)))
    rows = FineliFood.objects.filter(name_search(fields, query)).values_list(
        "fineli_id", "usage_count", *columns,
    )
    words = search_words(query)
    # The first word decides most (it's usually the food itself: "maito"
    # in "maito lak"), the rest break ties; then shorter = more generic.
    return sorted(rows, key=lambda r: (
        -(r[1] or 0) if by_usage else 0,
        _word_rank(r[2:], words[0]),
        sum(_word_rank(r[2:], word) for word in words[1:]),
        len(r[2] or ""),
        r[2] or "",
    ))


_vocabulary_cache = {}


def _vocabulary(fields):
    """Words of every Fineli name (lower case) and their prefixes, for typo
    correction. Cached per process; rebuilt when the foods change (a Fineli
    import adds rows or touches last_fetched)."""
    stamp = FineliFood.objects.aggregate(n=Count("id"), last=Max("last_fetched"), top=Max("id"))
    key = (tuple(fields), stamp["n"], stamp["last"], stamp["top"])
    if key not in _vocabulary_cache:
        words = set()
        for names in FineliFood.objects.values_list(*fields):
            for name in names:
                words.update(re.findall(r"[^\W\d_]{%d,}" % MIN_TYPO_LENGTH, (name or "").casefold()))
        _vocabulary_cache.clear()
        _vocabulary_cache[key] = sorted(words)
    return _vocabulary_cache[key]


def _closest_word(word, fields):
    """The Fineli word closest to a mistyped `word`, or None. Compares whole
    words first, then word beginnings of the same length, so a half-typed
    word with a typo ("jugurt") still finds "jogurtti"."""
    word = word.casefold()
    if len(word) < MIN_TYPO_LENGTH:
        return None
    vocabulary = _vocabulary(fields)
    match = difflib.get_close_matches(word, vocabulary, n=1, cutoff=TYPO_CUTOFF)
    if match:
        return match[0]
    prefixes = sorted({w[:len(word)] for w in vocabulary if len(w) > len(word)})
    match = difflib.get_close_matches(word, prefixes, n=1, cutoff=TYPO_CUTOFF)
    return match[0] if match else None


def corrected_query(query, fields=FINELI_FIELDS):
    """`query` with each word that matches no food replaced by the closest
    Fineli word, or None when nothing could be corrected."""
    words = search_words(query)
    corrected = []
    changed = False
    for word in words:
        if FineliFood.objects.filter(name_search(fields, word)).exists():
            corrected.append(word)
            continue
        closest = _closest_word(word, fields)
        if not closest:
            return None
        corrected.append(closest)
        changed = True
    return " ".join(corrected) if changed else None


def fineli_search(query, fields=FINELI_FIELDS, limit=20, by_usage=False):
    """FineliFood matches for `query`, best first, optionally most-used
    first. Ranks on the lightweight values, then loads only the top `limit`
    rows with their units. With no matches, retries with typos corrected."""
    rows = _ranked_rows(query, fields, by_usage)
    corrected = None
    if not rows:
        corrected = corrected_query(query, fields)
        if corrected:
            rows = _ranked_rows(corrected, fields, by_usage)
    top_ids = [r[0] for r in rows[:limit]]
    foods = FineliFood.objects.filter(fineli_id__in=top_ids).prefetch_related("units").in_bulk(field_name="fineli_id")
    return FineliSearchResult(
        foods=[foods[i] for i in top_ids],
        total=len(rows),
        corrected_query=corrected if rows else None,
    )


def ranked_fineli_search(query, fields=FINELI_FIELDS, limit=20, by_usage=False):
    """Just the foods of fineli_search()."""
    return fineli_search(query, fields=fields, limit=limit, by_usage=by_usage).foods
