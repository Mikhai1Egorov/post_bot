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
        "Create a high-quality editorial cover image for a Telegram article post.",
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
            "- pick concrete visual subjects from title/topic/lead (not generic placeholders)",
            "- if geography, species, culture, or industry is explicit, reflect it accurately",
            "- if animals are mentioned, show the correct natural habitat and behavior",
            "- prefer believable real-world settings over abstract generic backgrounds",
            "- keep composition clean and readable, but allow richer scene detail",
            "- vary composition, perspective, lighting, and color mood across different tasks",
            "- keep editorial quality suitable for publication",
            "- no text on image",
            "- no letters",
            "- no logos",
            "- no watermarks",
            "- no UI or screenshot elements",
            "- avoid unnecessary close-up human faces",
            "- avoid multiple people",
            "- avoid complex anatomy",
            "- avoid repetitive template-like visuals",
            "- high visual clarity and balanced composition",
        ]
    )

    return "\n".join(lines)
