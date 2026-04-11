"""Seedwake i18n — minimal translation system.

Call init() once at startup with the language code from config.
Then use t() to look up translated strings throughout the codebase.
"""

import logging

logger = logging.getLogger(__name__)

_strings: dict[str, str] = {}
_stopwords: dict[str, set[str]] = {}
_language: str = ""
_lang_mod: object = None


def init(language: str = "zh") -> None:
    """Load the language module. Call once at startup before any t() calls."""
    global _strings, _stopwords, _language, _lang_mod
    if language == "zh":
        from core.i18n import zh as lang_mod
    elif language == "en":
        from core.i18n import en as lang_mod
    else:
        raise ValueError(f"Unsupported language: {language}")
    _strings = lang_mod.STRINGS
    _stopwords = {
        "stagnation": lang_mod.STOPWORDS_STAGNATION,
        "habit": lang_mod.STOPWORDS_HABIT,
    }
    _lang_mod = lang_mod
    _language = language
    logger.info("i18n initialized: language=%s, keys=%d", language, len(_strings))


def prompt_block(name: str) -> object:
    """Get a module-level prompt block constant from the current language module."""
    return getattr(_lang_mod, name, "")


def t(key: str, **kwargs: object) -> str:
    """Look up a translated string, optionally formatting with kwargs."""
    template = _strings[key]
    if kwargs:
        return template.format(**kwargs)
    return template


def language() -> str:
    """Return the current language code."""
    return _language


def thought_types() -> tuple[str, str, str, str]:
    """Return localized (thinking, intention, reaction, reflection) labels."""
    return (
        _strings["thought_type.thinking"],
        _strings["thought_type.intention"],
        _strings["thought_type.reaction"],
        _strings["thought_type.reflection"],
    )

def localized_thought_type(canonical: str) -> str:
    """Map canonical key ('thinking') to localized label ('思考')."""
    localized_labels = {
        "thinking": _strings["thought_type.thinking"],
        "intention": _strings["thought_type.intention"],
        "reaction": _strings["thought_type.reaction"],
        "reflection": _strings["thought_type.reflection"],
    }
    return localized_labels.get(canonical, canonical)


def stopwords(name: str) -> set[str]:
    """Return a named stopword set for the current language."""
    return _stopwords.get(name, set())


def validate_against(other_language: str) -> list[str]:
    """Check that current language and another have the same key sets.

    Returns a list of error messages (empty if valid).
    """
    if other_language == _language:
        return []
    if other_language == "zh":
        from core.i18n import zh as other_mod
    elif other_language == "en":
        from core.i18n import en as other_mod
    else:
        return [f"Unknown language for validation: {other_language}"]
    current_keys = set(_strings.keys())
    other_keys = set(other_mod.STRINGS.keys())
    errors: list[str] = []
    missing_in_other = current_keys - other_keys
    missing_in_current = other_keys - current_keys
    if missing_in_other:
        errors.append(
            f"{other_language} is missing {len(missing_in_other)} keys: "
            f"{sorted(missing_in_other)[:5]}..."
        )
    if missing_in_current:
        errors.append(
            f"{_language} is missing {len(missing_in_current)} keys: "
            f"{sorted(missing_in_current)[:5]}..."
        )
    return errors
