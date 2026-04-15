"""Simple prompt builder for Stability image generation."""

from __future__ import annotations

import re

_ASCII_TOKEN_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9+_./#&%-]*")
_EN_STOPWORDS = {
    "the",
    "a",
    "an",
    "and",
    "or",
    "for",
    "with",
    "without",
    "about",
    "from",
    "into",
    "how",
    "what",
    "why",
    "to",
    "of",
    "in",
    "on",
    "at",
    "is",
    "are",
}
_THEME_FALLBACK = "general editorial concept"


def _clean(value: str | None) -> str:
    return " ".join((value or "").strip().split())


def _truncate(value: str, *, max_chars: int) -> str:
    if len(value) <= max_chars:
        return value
    return value[:max_chars].rstrip(" .,;:") + "..."


def _english_only_hint(value: str | None, *, max_chars: int) -> str:
    normalized = _clean(value)
    if not normalized:
        return ""
    tokens = _ASCII_TOKEN_RE.findall(normalized)
    if not tokens:
        return ""
    return _truncate(" ".join(tokens), max_chars=max_chars)


def _infer_theme_from_multilingual_text(*, title: str | None, keywords: str | None) -> str:
    haystack = " ".join([_clean(title), _clean(keywords)]).casefold()
    if not haystack:
        return _THEME_FALLBACK

    mappings: tuple[tuple[tuple[str, ...], str], ...] = (
        (
            ("crypto", "cryptocurrency", "bitcoin", "blockchain", "крипто", "криптовалют", "блокчейн", "биткоин"),
            "cryptocurrency and blockchain technology",
        ),
        (
            ("ai", "artificial", "machine learning", "ии", "искусствен", "нейросет"),
            "artificial intelligence technology",
        ),
        (
            ("health", "healthy", "diet", "nutrition", "food", "питани", "здоров", "рацион"),
            "healthy nutrition and wellness",
        ),
        (
            ("travel", "tourism", "trip", "europe", "city", "путеше", "туризм", "европ", "город"),
            "travel destinations and city landmarks",
        ),
        (
            ("sport", "fitness", "gym", "workout", "training", "спорт", "фитнес", "трениров"),
            "sports and fitness equipment",
        ),
        (
            ("finance", "investment", "banking", "economy", "финанс", "инвест", "эконом"),
            "finance and economic analysis",
        ),
    )
    for needles, theme in mappings:
        if any(needle in haystack for needle in needles):
            return theme

    title_tokens = _ASCII_TOKEN_RE.findall(_clean(title))
    keyword_tokens = _ASCII_TOKEN_RE.findall(_clean(keywords))
    combined_tokens = [
        token.lower()
        for token in [*title_tokens, *keyword_tokens]
        if token and token.lower() not in _EN_STOPWORDS and len(token) > 1
    ]
    if not combined_tokens:
        return _THEME_FALLBACK
    return _truncate(" ".join(combined_tokens[:8]), max_chars=120)


def build_editorial_image_prompt(
    *,
    article_title: str,
    article_topic: str,
    article_lead: str | None,
    article_keywords: str | None = None,
    response_language: str = "en",
) -> str:
    # Keep parameters for compatibility with existing call sites.
    _ = article_topic
    _ = article_lead
    _ = response_language

    title_hint = _english_only_hint(article_title, max_chars=160)
    keywords_hint = _english_only_hint(article_keywords, max_chars=160)
    theme_line = _infer_theme_from_multilingual_text(title=article_title, keywords=article_keywords)
    keywords_line = keywords_hint or "general context"

    # Structured template per Stability guidance: subject + environment + style + strict exclusions.
    return (
        "prompt: Create one original editorial photograph. "
        f"Main subject: {theme_line}. "
        "Scene: realistic environment with objects, architecture, symbols, or landscapes relevant to the subject. "
        "Style: clean photographic realism, natural lighting, balanced composition, high detail. "
        "Strict exclusions: no people, no human faces, no body parts, no humanoids, no masks, no mannequins. "
        "Strict exclusions: no text, letters, numbers, logos, trademarks, or watermarks. "
        "Originality rule: do not imitate existing photos, stock portraits, or recognizable characters. "
        f"Theme: {theme_line}. "
        f"Title hint: {title_hint or 'none'}. "
        f"Keywords: {keywords_line}."
    )


def build_editorial_image_negative_prompt(
    *,
    article_title: str,
    article_topic: str,
    article_keywords: str | None = None,
    article_lead: str | None = None,
) -> str:
    _ = article_title
    _ = article_topic
    _ = article_keywords
    _ = article_lead
    return (
        "person, people, human, man, woman, boy, girl, child, face, portrait, headshot, selfie, couple, crowd, "
        "body, hand, hands, fingers, skin, humanoid, robot face, mannequin, mask, doll, puppet, statue, "
        "text, letters, words, numbers, typography, captions, subtitles, ui, interface, "
        "logo, watermark, trademark, branded product, celebrity, recognizable character, stock photo clone"
    )
