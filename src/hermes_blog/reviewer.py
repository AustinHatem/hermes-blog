from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .openrouter_client import OpenRouterClient
from .utils import extract_json, write_json

REVIEW_SYSTEM = "You are a brutally honest senior content editor. You are not the writer. You score blog drafts, identify weaknesses, and require minimum fixes before publication."

REVIEW_PROMPT = """
Review this blog draft with a strict editorial rubric.

Context:
- Topic: {topic}
- Audience: {audience}
- Tone: {tone}
- CTA: {cta}
- Keywords: {keywords}

Current draft JSON:
{draft_json}

Respond with valid JSON only using this schema:
{{
  "overall_score": 0-10,
  "passes": true_or_false,
  "strengths": ["..."],
  "weaknesses": ["..."],
  "required_fixes": ["..."],
  "seo_notes": ["..."],
  "title_feedback": "...",
  "hook_feedback": "...",
  "cta_feedback": "...",
  "verdict": "ready|almost|not_ready"
}}

Scoring criteria:
- headline quality
- hook strength
- clarity and structure
- usefulness
- audience fit
- natural persuasive tone
- SEO usefulness without spam
- CTA quality

Pass only if the post is genuinely ready to publish.
""".strip()

REVISE_SYSTEM = "You are an expert blog writer revising work after an external editor review. Improve the draft while preserving truthfulness and readability."

REVISE_PROMPT = """
Revise this blog post based on the external editor review.

Original draft JSON:
{draft_json}

External review JSON:
{review_json}

Return valid JSON only with this schema:
{{
  "title": "...",
  "subtitle": "...",
  "slug": "...",
  "content": "<html paragraphs>",
  "authorName": "...",
  "authorPosition": "...",
  "image": "optional image url or empty string",
  "authorAvatar": "optional avatar url or empty string",
  "date": "YYYY-MM-DD",
  "draft": false
}}

Requirements:
- actually fix the required issues
- make the opening hook stronger
- keep the article skimmable with headings and short paragraphs
- keep HTML simple and Framer-friendly
- do not wrap the answer in markdown fences
""".strip()


@dataclass
class ReviewerLoop:
    client: OpenRouterClient
    reviewer_model: str
    writer_model: str
    threshold: float = 8.0
    max_rounds: int = 2

    def review(self, *, topic: str, audience: str, tone: str, cta: str, keywords: str, draft: dict[str, Any]) -> dict[str, Any]:
        text = self.client.chat(
            model=self.reviewer_model,
            system=REVIEW_SYSTEM,
            user=REVIEW_PROMPT.format(topic=topic, audience=audience, tone=tone, cta=cta, keywords=keywords, draft_json=draft),
            temperature=0.2,
            max_tokens=1800,
        )
        return extract_json(text)

    def revise(self, *, draft: dict[str, Any], review: dict[str, Any]) -> dict[str, Any]:
        text = self.client.chat(
            model=self.writer_model,
            system=REVISE_SYSTEM,
            user=REVISE_PROMPT.format(draft_json=draft, review_json=review),
            temperature=0.5,
            max_tokens=2600,
        )
        return extract_json(text)

    def run(self, *, run_dir: Path, topic: str, audience: str, tone: str, cta: str, keywords: str, initial_draft: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        draft = initial_draft
        history: list[dict[str, Any]] = []
        for round_idx in range(1, self.max_rounds + 1):
            review = self.review(topic=topic, audience=audience, tone=tone, cta=cta, keywords=keywords, draft=draft)
            write_json(run_dir / f"review_round_{round_idx}.json", review)
            history.append(review)
            score = float(review.get("overall_score", 0))
            verdict = str(review.get("verdict", "")).lower()
            if score >= self.threshold or review.get("passes") or verdict in {"ready", "almost"}:
                return draft, history
            draft = self.revise(draft=draft, review=review)
            write_json(run_dir / f"draft_round_{round_idx + 1}.json", draft)
        return draft, history
