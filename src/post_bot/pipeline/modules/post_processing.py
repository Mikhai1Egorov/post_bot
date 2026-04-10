"""Post-processing module: raw text -> normalized HTML + preview."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from html import escape, unescape
import re

from post_bot.domain.models import Task
from post_bot.shared.errors import ValidationError


@dataclass(slots=True, frozen=True)
class RenderedContent:
    final_title_text: str
    article_lead_text: str
    body_html: str
    preview_text: str
    slug_value: str


class PostProcessingModule:
    """Converts generated raw text into canonical HTML artifact."""

    def render(self, *, task: Task, raw_output_text: str, image_url: str | None = None) -> RenderedContent:
        normalized_output = self._normalize_raw_output(raw_output_text)
        lines = [line.strip() for line in normalized_output.splitlines() if line.strip()]
        if not lines:
            raise ValidationError(
                code="RAW_OUTPUT_EMPTY",
                message="Generated output is empty.",
                details={"task_id": task.id},
            )

        title, body_lines = self._extract_title(lines)
        lead = self._extract_lead(body_lines)
        html_body = self._render_body_lines(body_lines)
        html_body = self._inject_optional_blocks(task=task, html_body=html_body, image_url=image_url)

        document = "\n".join(["<article>", f"  <h1>{escape(title)}</h1>", html_body, "</article>"])

        preview = self._build_preview(normalized_output)
        slug = self._slugify(title)

        return RenderedContent(
            final_title_text=title,
            article_lead_text=lead,
            body_html=document,
            preview_text=preview,
            slug_value=slug,
        )

    @staticmethod
    def _extract_title(lines: list[str]) -> tuple[str, list[str]]:
        first = lines[0]
        if first.startswith("# "):
            title = first[2:].strip()
            body_lines = lines[1:]
        else:
            title = first
            body_lines = lines[1:]

        if not title:
            raise ValidationError(code="TITLE_MISSING", message="Unable to determine article title.")

        return title, body_lines

    @staticmethod
    def _extract_lead(lines: list[str]) -> str:
        for line in lines:
            if line.startswith("## ") or line.startswith("### "):
                continue
            if line.startswith("- "):
                return line[2:].strip()
            if line:
                return line
        return ""

    def _render_body_lines(self, lines: list[str]) -> str:
        html_lines: list[str] = []
        in_list = False

        def close_list() -> None:
            nonlocal in_list
            if in_list:
                html_lines.append("  </ul>")
                in_list = False

        for line in lines:
            if line.startswith("### "):
                close_list()
                html_lines.append(f"  <h3>{escape(line[4:].strip())}</h3>")
                continue
            if line.startswith("## "):
                close_list()
                html_lines.append(f"  <h2>{escape(line[3:].strip())}</h2>")
                continue
            if line.startswith("- "):
                if not in_list:
                    html_lines.append("  <ul>")
                    in_list = True
                html_lines.append(f"    <li>{escape(line[2:].strip())}</li>")
                continue

            close_list()
            html_lines.append(f"  <p>{escape(line)}</p>")

        close_list()

        if not html_lines:
            raise ValidationError(code="BODY_EMPTY", message="No renderable body content after title extraction.")

        return "\n".join(html_lines)

    def _inject_optional_blocks(self, *, task: Task, html_body: str, image_url: str | None) -> str:
        blocks: list[str] = [html_body]

        if task.include_image_flag and image_url:
            image_alt = task.topic_text.strip() or "Article image"
            blocks.append(
                "\n".join(
                    [
                        "  <figure class=\"image-block\">",
                        f"    <img src=\"{escape(image_url, quote=True)}\" alt=\"{escape(image_alt)}\" />",
                        "  </figure>",
                    ]
                )
            )

        if task.footer_text:
            if task.footer_link_url:
                footer = (
                    f"  <footer class=\"user-footer\"><p>{escape(task.footer_text)} "
                    f"<a href=\"{escape(task.footer_link_url)}\">{escape(task.footer_link_url)}</a></p></footer>"
                )
            else:
                footer = f"  <footer class=\"user-footer\"><p>{escape(task.footer_text)}</p></footer>"
            blocks.append(footer)

        if task.scheduled_publish_at is not None:
            blocks.append(self._render_schedule(task.scheduled_publish_at))

        return "\n".join(blocks)

    @staticmethod
    def _render_schedule(value: datetime) -> str:
        iso = value.replace(microsecond=0).isoformat()
        human = value.strftime("%Y-%m-%d %H:%M")
        return (
            "  <p class=\"schedule-at technical-meta\">"
            f"<time datetime=\"{iso}\">{human}</time></p>"
        )

    def _normalize_raw_output(self, raw_output_text: str) -> str:
        text = unescape(raw_output_text)
        text = self._repair_mojibake(text)
        if self._looks_like_html(text):
            return self._html_like_to_lines(text)
        return text

    @staticmethod
    def _looks_like_html(text: str) -> bool:
        return bool(
            re.search(
                r"(?is)<\s*/?\s*(h1|h2|h3|p|ul|ol|li|article|footer|figure|time|em|strong|a)\b",
                text,
            )
        )

    @staticmethod
    def _html_like_to_lines(text: str) -> str:
        normalized = text
        normalized = re.sub(r"(?is)<\s*h1[^>]*>", "\n# ", normalized)
        normalized = re.sub(r"(?is)<\s*h2[^>]*>", "\n## ", normalized)
        normalized = re.sub(r"(?is)<\s*h3[^>]*>", "\n### ", normalized)
        normalized = re.sub(r"(?is)<\s*li[^>]*>", "\n- ", normalized)
        normalized = re.sub(r"(?is)</(h1|h2|h3|p|ul|ol|li|article|footer|figure|time|em|strong|a)>", "\n", normalized)
        normalized = re.sub(r"(?is)<br\s*/?>", "\n", normalized)
        normalized = re.sub(r"(?is)<[^>]+>", "", normalized)
        normalized = unescape(normalized)

        lines = [line.strip() for line in normalized.splitlines() if line.strip()]
        return "\n".join(lines)

    @staticmethod
    def _repair_mojibake(text: str) -> str:
        suspect_score = text.count("Ã") + text.count("Ã‘")
        if suspect_score < 2:
            return text

        try:
            repaired = text.encode("latin1").decode("utf-8")
        except UnicodeError:
            return text

        if repaired and re.search(r"[Ð-Ð¯Ð°-ÑÐÑ‘]", repaired):
            return repaired
        return text

    @staticmethod
    def _build_preview(raw_output_text: str, limit: int = 240) -> str:
        normalized = " ".join(raw_output_text.split())
        return normalized[:limit]

    @staticmethod
    def _slugify(title: str) -> str:
        lowered = title.strip().lower()
        slug = re.sub(r"[^a-z0-9]+", "-", lowered)
        slug = re.sub(r"-+", "-", slug).strip("-")
        return slug or "article"
