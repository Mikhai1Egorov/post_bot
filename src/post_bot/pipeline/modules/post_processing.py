"""Post-processing module: raw text -> normalized HTML + preview."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from html import escape
import re

from post_bot.domain.models import Task
from post_bot.shared.errors import ValidationError


@dataclass(slots=True, frozen=True)
class RenderedContent:
    final_title_text: str
    body_html: str
    preview_text: str
    slug_value: str


class PostProcessingModule:
    """Converts generated raw text into canonical HTML artifact."""

    def render(self, *, task: Task, raw_output_text: str) -> RenderedContent:
        lines = [line.strip() for line in raw_output_text.splitlines() if line.strip()]
        if not lines:
            raise ValidationError(
                code="RAW_OUTPUT_EMPTY",
                message="Generated output is empty.",
                details={"task_id": task.id},
            )

        title, body_lines = self._extract_title(lines)
        html_body = self._render_body_lines(body_lines)
        html_body = self._inject_optional_blocks(task=task, html_body=html_body)

        document = "\n".join(["<article>", f"  <h1>{escape(title)}</h1>", html_body, "</article>"])

        preview = self._build_preview(raw_output_text)
        slug = self._slugify(title)

        return RenderedContent(
            final_title_text=title,
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

    def _inject_optional_blocks(self, *, task: Task, html_body: str) -> str:
        blocks: list[str] = [html_body]

        if task.include_image_flag:
            blocks.append(
                "\n".join(
                    [
                        "  <figure class=\"image-block\">",
                        "    <img src=\"{{image_url}}\" alt=\"Article image\" />",
                        "  </figure>",
                    ]
                )
            )

        if task.footer_text:
            if task.footer_link_url:
                footer = f"  <footer><p>{escape(task.footer_text)} <a href=\"{escape(task.footer_link_url)}\">{escape(task.footer_link_url)}</a></p></footer>"
            else:
                footer = f"  <footer><p>{escape(task.footer_text)}</p></footer>"
            blocks.append(footer)

        if task.scheduled_publish_at is not None:
            blocks.append(self._render_schedule(task.scheduled_publish_at))

        return "\n".join(blocks)

    @staticmethod
    def _render_schedule(value: datetime) -> str:
        iso = value.replace(microsecond=0).isoformat()
        human = value.strftime("%Y-%m-%d %H:%M")
        return f"  <p class=\"schedule-at\"><time datetime=\"{iso}\">{human}</time></p>"

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

