"""Stable prompt builder for editorial Telegram cover images."""

from __future__ import annotations


def build_editorial_image_prompt(
        *,
        article_title: str,
        article_topic: str,
        article_lead: str | None,
) -> str:
    title = (article_title or "").strip()
    topic = (article_topic or "").strip()
    lead = (article_lead or "").strip()

    if not title:
        title = "Untitled article"

    lines: list[str] = [
        "Create a realistic high-quality photo-style image for a Telegram article. The image must contain no text or characters.",
        f'Article title: "{title}"',
    ]
    if topic:
        lines.append(f'Article topic: "{topic}"')
    if lead:
        lines.append(f'Article lead: "{lead}"')

    lines.extend(
        [
            "Requirements:",
            "- visually relevant to the article",
            "- realistic and context-aware scene selection",
            "- pick concrete visual subjects from title, topic, and lead",
            "- if geography, species, culture, or industry is explicit, reflect it accurately",
            "- if animals are mentioned, show the correct natural habitat and behavior",
            "- prefer believable real-world settings over abstract generic backgrounds",
            "- vary composition, perspective, lighting, and color mood across different tasks",
            "- keep editorial publication quality",
            "- absolutely no text, no words, no letters, no typography",
            "- no captions, no titles, no headlines, no symbols, no numbers",
            "- no logos, no branding, no watermarks, no signs, no labels",
            "- no UI elements, no overlays, no interface, no screenshot fragments",
            "- avoid unnecessary close-up human faces",
            "- avoid multiple people",
            "- avoid complex anatomy",
            "- avoid repetitive template-like visuals",
            "- high visual clarity and balanced composition",
            "Strict rule: the image must not contain any readable text or characters of any kind.",
        ]
    )

    return "\n".join(lines)
