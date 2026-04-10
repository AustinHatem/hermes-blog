from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .openrouter_client import OpenRouterClient
from .reviewer import ReviewerLoop
from .utils import ensure_dir, extract_json, load_dotenv, slugify, timestamp_slug, utc_today, write_json, write_text

WRITER_SYSTEM = "You are a strong blog writer and content strategist. Produce practical, internet-ready blog content."

BRIEF_PROMPT = """
Create a concise blog brief for this topic.

Topic: {topic}
Audience: {audience}
Tone: {tone}
CTA: {cta}
Keywords: {keywords}

Return valid JSON only:
{{
  "working_title": "...",
  "angle": "...",
  "reader_problem": "...",
  "promise": "...",
  "cta": "...",
  "seo_title": "...",
  "slug": "..."
}}
""".strip()

OUTLINE_PROMPT = """
Using this blog brief, write a practical outline.

Brief JSON:
{brief_json}

Return markdown only with:
- headline
- subtitle
- intro hook
- section headings
- bullet points under each section
- CTA ending
""".strip()

DRAFT_PROMPT = """
Write the full blog post from this brief and outline.

Brief JSON:
{brief_json}

Outline markdown:
{outline}

Return valid JSON only:
{{
  "title": "...",
  "subtitle": "...",
  "slug": "...",
  "content": "<html with h2/p/ul/li>",
  "authorName": "...",
  "authorPosition": "...",
  "image": "optional image url or empty string",
  "authorAvatar": "optional avatar url or empty string",
  "date": "YYYY-MM-DD",
  "draft": false
}}

Requirements:
- make it readable and useful
- keep the writing concise and clear
- include a strong intro hook
- use simple html only
- write for the target audience
- include a clean CTA at the end
""".strip()


@dataclass
class BlogPipelineConfig:
    topic: str
    audience: str
    tone: str
    cta: str
    keywords: str
    writer_model: str
    reviewer_model: str
    max_rounds: int = 2
    threshold: float = 8.0
    publish: bool = False
    framer_publish_mode: str = "live"


def run_pipeline(config: BlogPipelineConfig, base_dir: str | Path = ".") -> Path:
    load_dotenv(Path(base_dir) / ".env")
    client = OpenRouterClient.from_env()
    run_dir = ensure_dir(Path(base_dir) / "runs" / timestamp_slug())

    brief = _generate_brief(client, config)
    write_json(run_dir / "brief.json", brief)

    outline = _generate_outline(client, config, brief)
    write_text(run_dir / "outline.md", outline)

    draft = _generate_draft(client, config, brief, outline)
    draft.setdefault("slug", brief.get("slug") or slugify(draft.get("title", config.topic)))
    draft.setdefault("date", utc_today())
    draft.setdefault("draft", False)
    write_json(run_dir / "draft_round_1.json", draft)

    loop = ReviewerLoop(
        client=client,
        reviewer_model=config.reviewer_model,
        writer_model=config.writer_model,
        threshold=config.threshold,
        max_rounds=config.max_rounds,
    )
    final_draft, review_history = loop.run(
        run_dir=run_dir,
        topic=config.topic,
        audience=config.audience,
        tone=config.tone,
        cta=config.cta,
        keywords=config.keywords,
        initial_draft=draft,
    )

    final_post = {
        "title": final_draft.get("title", brief.get("working_title", config.topic)),
        "subtitle": final_draft.get("subtitle", ""),
        "slug": final_draft.get("slug") or brief.get("slug") or slugify(config.topic),
        "content": final_draft.get("content", ""),
        "contentType": "html",
        "date": final_draft.get("date", utc_today()),
        "image": final_draft.get("image", ""),
        "authorName": final_draft.get("authorName", "Hermes"),
        "authorPosition": final_draft.get("authorPosition", "Content Team"),
        "authorAvatar": final_draft.get("authorAvatar", ""),
        "draft": bool(final_draft.get("draft", False)),
    }
    write_json(run_dir / "final_post.json", final_post)
    write_json(run_dir / "review_history.json", review_history)

    summary = {
        "topic": config.topic,
        "writer_model": config.writer_model,
        "reviewer_model": config.reviewer_model,
        "max_rounds": config.max_rounds,
        "final_slug": final_post["slug"],
        "final_title": final_post["title"],
        "review_rounds": len(review_history),
        "final_score": review_history[-1].get("overall_score") if review_history else None,
    }
    write_json(run_dir / "summary.json", summary)

    if config.publish:
        _publish_to_framer(base_dir=base_dir, post_path=run_dir / "final_post.json", mode=config.framer_publish_mode)

    return run_dir


def _generate_brief(client: OpenRouterClient, config: BlogPipelineConfig) -> dict[str, Any]:
    text = client.chat(
        model=config.writer_model,
        system=WRITER_SYSTEM,
        user=BRIEF_PROMPT.format(topic=config.topic, audience=config.audience, tone=config.tone, cta=config.cta, keywords=config.keywords),
        temperature=0.4,
        max_tokens=1200,
    )
    brief = extract_json(text)
    if not brief.get("slug"):
        brief["slug"] = slugify(brief.get("working_title") or config.topic)
    return brief


def _generate_outline(client: OpenRouterClient, config: BlogPipelineConfig, brief: dict[str, Any]) -> str:
    return client.chat(
        model=config.writer_model,
        system=WRITER_SYSTEM,
        user=OUTLINE_PROMPT.format(brief_json=json.dumps(brief, ensure_ascii=False, indent=2)),
        temperature=0.5,
        max_tokens=1800,
    )


def _generate_draft(client: OpenRouterClient, config: BlogPipelineConfig, brief: dict[str, Any], outline: str) -> dict[str, Any]:
    text = client.chat(
        model=config.writer_model,
        system=WRITER_SYSTEM,
        user=DRAFT_PROMPT.format(brief_json=json.dumps(brief, ensure_ascii=False, indent=2), outline=outline),
        temperature=0.6,
        max_tokens=3200,
    )
    draft = extract_json(text)
    draft.setdefault("authorName", "Hermes")
    draft.setdefault("authorPosition", "Content Team")
    return draft


def _publish_to_framer(*, base_dir: str | Path, post_path: Path, mode: str) -> None:
    root = Path(base_dir)
    subprocess.run(["npm", "run", "framer:add", "--", str(post_path)], cwd=root, check=True)
    publish_script = {
        "status": "framer:publish:status",
        "preview": "framer:publish:preview",
        "live": "framer:publish:live",
    }.get(mode, "framer:publish:live")
    subprocess.run(["npm", "run", publish_script], cwd=root, check=True)


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Generate, review, revise, and optionally publish a Framer blog post.")
    parser.add_argument("--topic", required=True)
    parser.add_argument("--audience", default="general internet readers")
    parser.add_argument("--tone", default="clear, direct, and useful")
    parser.add_argument("--cta", default="Encourage the reader to learn more or try the product.")
    parser.add_argument("--keywords", default="")
    parser.add_argument("--writer-model", default="meta-llama/llama-3.3-70b-instruct:free")
    parser.add_argument("--reviewer-model", default="google/gemma-4-31b-it:free")
    parser.add_argument("--max-rounds", type=int, default=2)
    parser.add_argument("--threshold", type=float, default=8.0)
    parser.add_argument("--publish", action="store_true")
    parser.add_argument("--framer-publish-mode", choices=["status", "preview", "live"], default="live")
    args = parser.parse_args(argv)

    config = BlogPipelineConfig(
        topic=args.topic,
        audience=args.audience,
        tone=args.tone,
        cta=args.cta,
        keywords=args.keywords,
        writer_model=args.writer_model,
        reviewer_model=args.reviewer_model,
        max_rounds=args.max_rounds,
        threshold=args.threshold,
        publish=args.publish,
        framer_publish_mode=args.framer_publish_mode,
    )
    run_dir = run_pipeline(config, base_dir=Path(__file__).resolve().parents[2])
    print(str(run_dir))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
