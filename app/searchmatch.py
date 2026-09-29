"""Forgiving fixture search: one matcher for every source.

The Add dialog searches three places (installed fixtures, the bundled
libraries, GDTF Share) and each used to have its own rule - all of them
strict enough that a correct-looking query found nothing: "ADJ" never
matched "American DJ", one extra word ("LED", "moving head") or one typo
emptied the list, and GDTF Share compared the whole query against the
brand OR the model, so typing both ("BeamZ Cobra 120") could never match.

`score()` returns None for no match, else a sort key where lower is
better: every word found exactly beats a typo, which beats a match with
one word missing.  Words are compared after lower-casing and dropping
punctuation, with and without spaces ("beam z" == "beamz"), and brands
have their common short forms.
"""
from __future__ import annotations

import re

# brand spellings people type -> the words the libraries use
ALIASES = {
    "adj": ("american dj", "americandj"),
    "americandj": ("adj",),
    "cp": ("clay paky", "claypaky"),
    "claypaky": ("clay paky",),
    "chauvetdj": ("chauvet",),
    "gloriousled": ("glorious",),
    "mh": ("moving head",),
    "movinghead": ("moving head", "mover"),
    "par": ("parcan", "par can"),
}

# words that describe rather than name a light: a query may carry them
# when the library's name does not
FILLER = {"led", "leds", "moving", "head", "heads", "mover", "light", "lights", "fixture",
          "dmx", "the", "a", "and", "with", "pro", "series", "mk", "mkii", "mk2", "w"}


def norm(text) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(text or "").lower()).strip()


def _typo_ok(word: str, token: str) -> bool:
    """One edit apart (two for long words) - a slip, not a different model.
    Numbers must match exactly: a 100 is not a 120."""
    if word.isdigit() or token.isdigit() or any(c.isdigit() for c in word):
        return False
    limit = 2 if len(word) >= 8 else 1
    if len(word) < 4 or abs(len(word) - len(token)) > limit:
        return False
    prev = list(range(len(token) + 1))
    for i, a in enumerate(word, 1):
        cur = [i] + [0] * len(token)
        best = cur[0]
        for j, b in enumerate(token, 1):
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (a != b))
            best = min(best, cur[j])
        if best > limit:
            return False
        prev = cur
    return prev[-1] <= limit


def _word_hit(word: str, hay: str, squashed: str, tokens: list[str]) -> int | None:
    """0 = found, 1 = found as a typo, None = missing."""
    if word in hay or word in squashed:
        return 0
    for alt in ALIASES.get(word, ()):
        if alt in hay or alt.replace(" ", "") in squashed:
            return 0
    if any(_typo_ok(word, t) for t in tokens):
        return 1
    return None


def score(query: str, *fields) -> tuple | None:
    """Sort key for how well `query` names a fixture described by `fields`
    (maker, model, type...), or None if it does not."""
    q = norm(query)
    words = q.split()
    if not words:
        return (0, 0, 0)
    hay = norm(" ".join(str(f or "") for f in fields))
    squashed = hay.replace(" ", "")
    qs = q.replace(" ", "")
    if len(qs) >= 3 and qs in squashed:            # typed as one run: "beamzcobra120"
        return (0, 0, 0)
    tokens = hay.split()
    typos, missing, missing_real = 0, 0, 0
    for w in words:
        hit = _word_hit(w, hay, squashed, tokens)
        if hit is None:
            missing += 1
            if w not in FILLER:
                missing_real += 1
        else:
            typos += hit
    if missing == 0:
        return (0, typos, 0)
    found = len(words) - missing
    # describing words the name lacks ("LED", "moving head") never cost a match
    if missing_real == 0 and found:
        return (0, typos, missing)
    # one naming word missing: still offered, below every full match, and
    # only when what DID match is specific (a number or a long word)
    if missing_real == 1 and len(words) >= 3 and any(
            (w.isdigit() or len(w) >= 5) and _word_hit(w, hay, squashed, tokens) is not None
            for w in words if w not in FILLER):
        return (1, typos, missing)
    return None
