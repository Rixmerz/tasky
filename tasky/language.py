"""Which language a text is written in, told apart by its function words, with no model.

Models asked to "write in the language the developer writes in" drift to English when the
material around the developer's words (replies, code, paths, git log) is English. Naming the
language outright, and checking what comes back against it, does not depend on the model
noticing. Function words are what separates these languages: code, paths and product names are
shared by all of them and count for none. Words two languages share ("de", "que", "a") are left
out of both lists, so each hit is evidence for one language only.
"""

from __future__ import annotations

import re
import string
import unicodedata

NAMES = {
    "es": "Spanish", "en": "English", "pt": "Portuguese", "fr": "French", "de": "German",
    "it": "Italian",
}

_LISTS: dict[str, str] = {
    "es": (
        "el la los las del al y es son está están pero porque cuando donde también muy sin "
        "sobre hasta desde hay qué cómo cuál este esta estos estas eso esto ese esa ya "
        "más mejor hacer hace puede tiene tienen ahora aquí todo todos nada algo otro otra "
        "como para por una unos unas lo le les nos yo usted ellos sí se su sus mi tu hoy "
        "entonces después antes luego bien cada ni ahí podemos vamos quiero"
    ),
    "en": (
        "the and is are was were be been being this that these those with from have has "
        "had not but what which when where who how why will would should could can does "
        "did done it its they them their there here into only also than then just about "
        "all any some other more most very of to in on for by an or if so we you my our"
    ),
    "pt": (
        "não uma um com os dos das ao aos pelo pela você isso isto mas muito então ainda "
        "também já só ele ela eles são está estão fazer faz pode tem aqui agora tudo "
        "nada outro outra mais melhor onde quando porque"
    ),
    "fr": (
        "le les des du au aux et est sont mais pour avec pas une un ce cette ces qui "
        "dans sur ou où aussi très sans nous vous ils elle faire fait peut il je tout"
    ),
    "de": (
        "der die das den dem des und ist sind nicht ein eine einen mit für auf auch "
        "aber wenn wie was wir ich sie es noch nur schon kann zu von im bei oder"
    ),
    "it": (
        "il lo gli della delle dei degli e è sono non una uno con per ma anche molto "
        "questo questa quello cosa come dove quando perché più già fare può ci"
    ),
}
_WORDS = {code: frozenset(text.split()) for code, text in _LISTS.items()}
# A word two lists share says nothing about which one it is.
_SHARED = {w for a in _WORDS for b in _WORDS if a < b for w in _WORDS[a] & _WORDS[b]}
_WORDS = {code: words - _SHARED for code, words in _WORDS.items()}

_TOKEN = re.compile(r"[^\W\d_]+")
_EDGES = string.punctuation + "¿¡«»“”‘’…"
# What a developer pastes (logs, specs, stack traces) and fenced code are someone else's words,
# usually English; only what is left around them says which language the developer writes in.
_NOT_THEIRS = re.compile(
    r"<pasted_content\b[^>]*>.*?(?:</pasted_content\b[^>]*>|\Z)|```.*?(?:```|\Z)", re.DOTALL
)
MIN_HITS = 4
LEAD = 1.5


def _tokens(text: str) -> list[str]:
    """The plain words: identifiers like APP_HAS_CONFIG, paths and file names are not prose."""
    text = unicodedata.normalize("NFC", text or "").casefold()
    words = (chunk.strip(_EDGES) for chunk in text.split())
    return [w for w in words if _TOKEN.fullmatch(w)]


def scores(text: str) -> dict[str, int]:
    words = _tokens(text)
    return {code: sum(1 for w in words if w in vocab) for code, vocab in _WORDS.items()}


def detect(text: str) -> str | None:
    """The language code a text is in, or None when it has too few function words to tell."""
    ranked = sorted(scores(text).items(), key=lambda kv: kv[1], reverse=True)
    (best, top), (_, second) = ranked[0], ranked[1]
    if top < MIN_HITS or top < second * LEAD:
        return None
    return best


def own_words(text: str) -> str:
    """The text without what was pasted into it or fenced as code."""
    return _NOT_THEIRS.sub(" ", text or "")


def name(code: str | None) -> str | None:
    return NAMES.get(code) if code else None


def code_of(value: str | None) -> str | None:
    """A language given as a code ("es") or an English name ("Spanish"); None when unknown."""
    value = (value or "").strip().casefold()
    if value in NAMES:
        return value
    return next((code for code, full in NAMES.items() if full.casefold() == value), None)
