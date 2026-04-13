"""Telegram delivery projection and smart chunking from canonical HTML."""

from __future__ import annotations

from dataclasses import dataclass
from html import unescape
from html.parser import HTMLParser
import re
from typing import Callable


@dataclass(slots=True, frozen=True)
class TelegramDeliveryProjection:
    final_title_text: str
    article_lead_text: str
    cover_caption_text: str | None
    telegram_article_body_text: str
    article_chunks: tuple[str, ...]
    image_url: str | None


@dataclass(slots=True, frozen=True)
class _ArticleBlock:
    kind: str
    text: str


class TelegramDeliveryProjector:
    """Builds a Telegram-ready projection from canonical HTML artifacts."""

    _SERVICE_PREFIXES = (
        "изображение:",
        "image:",
        "image placeholder",
        "[image placeholder",
        "technical metadata",
        "render timestamp",
        "timestamp:",
        "generated at:",
        "сгенерировано:",
        "метаданные:",
        "for more insights",
        "visit the full report",
        "these trends highlight the growing impact of artificial intelligence",
    )

    def __init__(self, *, text_limit: int = 4000, caption_safe_limit: int = 900) -> None:
        self._text_limit = max(64, text_limit)
        self._caption_safe_limit = max(128, min(caption_safe_limit, 1024))

    def project(self, *, html: str) -> TelegramDeliveryProjection:
        parser = _ArticleHtmlParser()
        parser.feed(html)
        parser.close()

        blocks = parser.blocks
        if not blocks:
            blocks = self._fallback_blocks(html)

        title, body_blocks = self._extract_title(blocks)
        body_blocks = self._drop_service_blocks(body_blocks)
        lead = self._extract_lead(body_blocks, fallback_title=title)

        # Keep full article body for Telegram message delivery.
        # Image caption is an additional cover block and must not remove paragraphs from article chunks.
        article_blocks = [_ArticleBlock(kind="h1", text=title), *body_blocks]
        article_blocks = self._drop_service_blocks(article_blocks)

        body_text = self._render_blocks(article_blocks)
        chunks = tuple(self._chunk_by_h2(article_blocks)) if article_blocks else tuple()

        caption = None
        if parser.image_url is not None:
            caption = self._build_cover_caption(title=title, lead=lead)

        return TelegramDeliveryProjection(
            final_title_text=title,
            article_lead_text=lead,
            cover_caption_text=caption,
            telegram_article_body_text=body_text,
            article_chunks=chunks,
            image_url=parser.image_url,
        )

    @staticmethod
    def _extract_title(blocks: list[_ArticleBlock]) -> tuple[str, list[_ArticleBlock]]:
        for index, block in enumerate(blocks):
            if block.kind == "h1" and block.text:
                remaining = blocks[:index] + blocks[index + 1 :]
                return block.text, remaining

        for index, block in enumerate(blocks):
            if block.text:
                remaining = blocks[:index] + blocks[index + 1 :]
                return block.text, remaining

        return "Article", []

    @staticmethod
    def _extract_lead(blocks: list[_ArticleBlock], *, fallback_title: str) -> str:
        for block in blocks:
            if block.kind == "p" and block.text:
                return block.text
        for block in blocks:
            if block.kind == "li" and block.text:
                return block.text
        return fallback_title

    def _build_cover_caption(self, *, title: str, lead: str) -> str:
        clean_title = self._clean_text(title)
        clean_lead = self._clean_text(lead)
        if not clean_title:
            clean_title = "Article"

        if not clean_lead or clean_lead.casefold() == clean_title.casefold():
            return self._truncate_with_ellipsis(clean_title, self._caption_safe_limit)

        base = f"{clean_title}\n\n{clean_lead}"
        if len(base) <= self._caption_safe_limit:
            return base

        title_part = self._truncate_with_ellipsis(clean_title, self._caption_safe_limit)
        if len(title_part) >= self._caption_safe_limit - 20:
            return title_part

        remaining = self._caption_safe_limit - len(title_part) - 2
        if remaining <= 0:
            return title_part

        lead_part = self._truncate_with_ellipsis(clean_lead, remaining)
        if not lead_part:
            return title_part
        return f"{title_part}\n\n{lead_part}"

    def _drop_service_blocks(self, blocks: list[_ArticleBlock]) -> list[_ArticleBlock]:
        filtered = [block for block in blocks if not self._is_service_block(block)]

        # Defensive trim for trailing timestamps/metadata that slipped in as plain text.
        while filtered and self._is_service_block(filtered[-1]):
            filtered.pop()

        return filtered

    def _is_service_block(self, block: _ArticleBlock) -> bool:
        text = self._clean_text(block.text)
        if not text:
            return True

        lowered = text.casefold()
        if any(lowered.startswith(prefix) for prefix in self._SERVICE_PREFIXES):
            return True

        if len(text) <= 32 and re.fullmatch(r"\d{4}-\d{2}-\d{2}(?:[ T]\d{2}:\d{2}(?::\d{2})?)?", text):
            return True

        return False

    def _chunk_by_h2(self, blocks: list[_ArticleBlock]) -> list[str]:
        units = self._split_by_heading(blocks, heading_kind="h2")
        return self._chunk_units(units, self._chunk_by_h3)

    def _chunk_by_h3(self, blocks: list[_ArticleBlock]) -> list[str]:
        units = self._split_by_heading(blocks, heading_kind="h3")
        return self._chunk_units(units, self._chunk_by_blocks)

    def _chunk_by_blocks(self, blocks: list[_ArticleBlock]) -> list[str]:
        units = [[block] for block in blocks]
        return self._chunk_units(units, self._chunk_single_block)

    def _chunk_single_block(self, blocks: list[_ArticleBlock]) -> list[str]:
        text = self._render_blocks(blocks)
        if len(text) <= self._text_limit:
            return [text]
        return self._split_text_by_sentences(text, self._text_limit)

    def _chunk_units(
        self,
        units: list[list[_ArticleBlock]],
        split_large_unit: Callable[[list[_ArticleBlock]], list[str]],
    ) -> list[str]:
        chunks: list[str] = []
        current = ""

        for unit in units:
            unit_text = self._render_blocks(unit)
            if not unit_text:
                continue

            if len(unit_text) > self._text_limit:
                if current:
                    chunks.append(current)
                    current = ""
                chunks.extend(split_large_unit(unit))
                continue

            candidate = unit_text if not current else f"{current}\n\n{unit_text}"
            if len(candidate) <= self._text_limit:
                current = candidate
            else:
                if current:
                    chunks.append(current)
                current = unit_text

        if current:
            chunks.append(current)

        return [chunk for chunk in chunks if chunk.strip()]

    @staticmethod
    def _split_by_heading(blocks: list[_ArticleBlock], *, heading_kind: str) -> list[list[_ArticleBlock]]:
        if not blocks:
            return []

        units: list[list[_ArticleBlock]] = []
        current: list[_ArticleBlock] = []

        for block in blocks:
            if block.kind == heading_kind and current:
                units.append(current)
                current = [block]
            else:
                current.append(block)

        if current:
            units.append(current)
        return units

    @staticmethod
    def _render_blocks(blocks: list[_ArticleBlock]) -> str:
        lines: list[str] = []
        previous_kind: str | None = None

        for block in blocks:
            text = TelegramDeliveryProjector._clean_text(block.text)
            if not text:
                continue

            if block.kind in {"h1", "h2", "h3"}:
                if lines and lines[-1] != "":
                    lines.append("")
                lines.append(text)
                lines.append("")
                previous_kind = block.kind
                continue

            if block.kind == "li":
                if previous_kind not in {"li"} and lines and lines[-1] != "":
                    lines.append("")
                lines.append(f"- {text}")
                previous_kind = block.kind
                continue

            if previous_kind == "li" and lines and lines[-1] != "":
                lines.append("")
            elif lines and lines[-1] != "":
                lines.append("")
            lines.append(text)
            previous_kind = block.kind

        normalized: list[str] = []
        previous_empty = True
        for line in lines:
            if not line:
                if not previous_empty:
                    normalized.append("")
                previous_empty = True
                continue
            normalized.append(line)
            previous_empty = False

        while normalized and normalized[0] == "":
            normalized.pop(0)
        while normalized and normalized[-1] == "":
            normalized.pop()

        return "\n".join(normalized).strip()

    @staticmethod
    def _split_text_by_sentences(text: str, limit: int) -> list[str]:
        normalized = TelegramDeliveryProjector._clean_text(text)
        if len(normalized) <= limit:
            return [normalized]

        sentence_parts = [part.strip() for part in re.split(r"(?<=[.!?])\s+", normalized) if part.strip()]
        if not sentence_parts:
            sentence_parts = [normalized]

        chunks: list[str] = []
        current = ""
        for part in sentence_parts:
            candidate = part if not current else f"{current} {part}"
            if len(candidate) <= limit:
                current = candidate
                continue

            if current:
                chunks.append(current)
                current = ""

            if len(part) <= limit:
                current = part
                continue

            word_chunks = TelegramDeliveryProjector._split_text_by_words(part, limit)
            if not word_chunks:
                continue
            chunks.extend(word_chunks[:-1])
            current = word_chunks[-1]

        if current:
            chunks.append(current)

        return [chunk for chunk in chunks if chunk.strip()]

    @staticmethod
    def _split_text_by_words(text: str, limit: int) -> list[str]:
        words = [word for word in text.split() if word]
        if not words:
            return []

        chunks: list[str] = []
        current = ""

        for word in words:
            candidate = word if not current else f"{current} {word}"
            if len(candidate) <= limit:
                current = candidate
                continue

            if current:
                chunks.append(current)
                current = ""

            if len(word) <= limit:
                current = word
                continue

            token = word
            while len(token) > limit:
                chunks.append(token[:limit])
                token = token[limit:]
            current = token

        if current:
            chunks.append(current)

        return [chunk for chunk in chunks if chunk.strip()]

    @staticmethod
    def _truncate_with_ellipsis(text: str, limit: int) -> str:
        normalized = TelegramDeliveryProjector._clean_text(text)
        if len(normalized) <= limit:
            return normalized
        if limit <= 1:
            return normalized[:limit]

        cut = normalized[: limit - 1]
        split_index = cut.rfind(" ")
        if split_index >= int((limit - 1) * 0.6):
            cut = cut[:split_index]
        cut = cut.rstrip(" ,.;:-")
        if not cut:
            cut = normalized[: limit - 1]
        return f"{cut}…"

    @staticmethod
    def _clean_text(value: str) -> str:
        text = unescape(value or "")
        text = re.sub(r"\s+", " ", text)
        return text.strip()

    @staticmethod
    def _fallback_blocks(html: str) -> list[_ArticleBlock]:
        normalized = unescape(html)
        normalized = re.sub(r"(?is)<br\s*/?>", "\n", normalized)
        normalized = re.sub(r"(?is)</(p|h1|h2|h3|li|ul|ol|article|footer|figure|time)>", "\n", normalized)
        normalized = re.sub(r"(?is)<[^>]+>", "", normalized)
        lines = [line.strip() for line in normalized.splitlines() if line.strip()]
        if not lines:
            return []

        blocks: list[_ArticleBlock] = []
        for index, line in enumerate(lines):
            kind = "h1" if index == 0 else "p"
            blocks.append(_ArticleBlock(kind=kind, text=line))
        return blocks


class _ArticleHtmlParser(HTMLParser):
    _TEXT_TAGS = {"h1", "h2", "h3", "p", "li"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.blocks: list[_ArticleBlock] = []
        self.image_url: str | None = None
        self._active_tag: str | None = None
        self._buffer: list[str] = []
        self._skip_stack: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag_lower = tag.lower()

        if self._should_skip_tag(tag_lower, attrs):
            self._skip_stack.append(tag_lower)
            return

        if self._skip_stack:
            return

        if tag_lower == "img" and self.image_url is None:
            attributes = {key.lower(): value for key, value in attrs}
            src = (attributes.get("src") or "").strip()
            if src and src != "{{image_url}}":
                self.image_url = src

        if tag_lower in self._TEXT_TAGS and self._active_tag is None:
            self._active_tag = tag_lower
            self._buffer = []
            return

        if tag_lower == "br" and self._active_tag is not None:
            self._buffer.append("\n")

    def handle_endtag(self, tag: str) -> None:
        tag_lower = tag.lower()

        if self._skip_stack:
            if tag_lower == self._skip_stack[-1]:
                self._skip_stack.pop()
            return

        if self._active_tag != tag_lower:
            return

        text = self._normalize_inline("".join(self._buffer))
        if text:
            self.blocks.append(_ArticleBlock(kind=self._active_tag, text=text))
        self._active_tag = None
        self._buffer = []

    def handle_data(self, data: str) -> None:
        if self._skip_stack:
            return
        if self._active_tag is None:
            return
        self._buffer.append(data)

    @staticmethod
    def _should_skip_tag(tag: str, attrs: list[tuple[str, str | None]]) -> bool:
        if tag == "time":
            return True

        attributes = {key.lower(): (value or "") for key, value in attrs}
        class_value = attributes.get("class", "").lower()
        if any(marker in class_value for marker in ("schedule-at", "technical-meta", "render-meta", "telegram-meta")):
            return True

        return False

    @staticmethod
    def _normalize_inline(value: str) -> str:
        text = unescape(value)
        text = re.sub(r"\s+", " ", text)
        return text.strip()

