from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .openrouter_client import OpenRouterClient, OpenRouterError
from .reviewer import ReviewerLoop
from .utils import ensure_dir, extract_json, load_dotenv, salvage_post_json, slugify, timestamp_slug, utc_today, write_json, write_text

PRODUCT_NAME = "SomeSome"
PRODUCT_CONTEXT = (
    "SomeSome is a random chat and random video chat app in the same broad category as Monkey and Omegle-style products. "
    "The article should feel genuinely useful to people who want spontaneous online conversations, not like a generic AI SEO page. "
    "When positioning SomeSome, stay high-level and honest unless the approved fact sheet below explicitly gives you a concrete product fact you may use."
)
PRODUCT_FACT_SHEET = """
Approved SomeSome facts you may use:
- heavily moderated to keep the platform SFW
- global user base, especially Philippines, Colombia, other parts of SEA, and Latam including Brazil
- calls default to 60 seconds and can be extended
- users can directly call people
- unlimited free messages
- in-app AI translation with live subtitle-style translation for cross-language chats, especially Spanish/Latam conversations

What you may say:
- use the facts above directly and concretely
- tie those facts to reader usefulness without inventing extra mechanics
- describe AI translation as an in-app feature for understanding cross-language conversations

What you may NOT say unless separately provided:
- invented user-behavior claims like 'people there actually want to talk'
- invented quality claims like 'better odds', 'better conversations', or 'longer chats on average'
- invented moderation specifics beyond 'heavily moderated to keep it SFW'
- invented workflow/UI details beyond 60-second default calls, extendable calls, direct calls, free messaging, and in-app AI translation/live subtitles
- invented metrics, percentages, wait times, user counts, or personal testing claims
""".strip()
DEFAULT_IMAGE_URL = "https://images.unsplash.com/photo-1522202176988-66273c2fd55f?auto=format&fit=crop&w=1600&q=80"
DEFAULT_IMAGE_ALT = "People connecting through online video chat"
DEFAULT_WRITER_MODEL = "meta-llama/llama-3.3-70b-instruct:free"
DEFAULT_REVIEWER_MODEL = "google/gemma-4-31b-it:free"
DEFAULT_WRITER_FALLBACKS = [
    "google/gemma-4-31b-it:free",
    "nvidia/nemotron-3-super-120b-a12b:free",
]
DEFAULT_REVIEWER_FALLBACKS = [
    "meta-llama/llama-3.3-70b-instruct:free",
    "nvidia/nemotron-3-super-120b-a12b:free",
]
LEGACY_WRITER_DEFAULTS: set[str] = set()
LEGACY_REVIEWER_DEFAULTS: set[str] = set()

WRITER_SYSTEM = (
    "You are a strong blog writer and content strategist. Write like a sharp human internet writer, not a content farm. "
    "Be specific, grounded, commercially useful, and honest about uncertainty. Ruthlessly cut repetition, filler, and generic SEO scaffolding. "
    "Do not invent personal testing, private metrics, or product facts that were not provided."
)

BRIEF_PROMPT = """
Create a concise blog brief for a search-driven blog post.

Product context:
- Product: {product_name}
- {product_context}
- Approved fact sheet:
{product_fact_sheet}

Topic: {topic}
Audience: {audience}
Tone: {tone}
CTA: {cta}
Keywords: {keywords}

Requirements:
- prioritize long-tail search intent with a realistic chance to rank
- keep the angle tightly relevant to SomeSome and people who would actually use it
- make the promise concrete, not vague
- avoid generic startup jargon
- choose one sharp thesis instead of a broad market overview
- build the argument around the reader's real frustration and the decision they need to make, not generic explainer sections
- make SomeSome part of the core angle early, not a last-minute bolt-on mention
- plan a tight article, not a sprawling SEO encyclopedia; the finished piece should work as exactly 5 H2 sections plus conclusion, not 7-9 interchangeable chunks
- the brief should naturally set up this progression: one concrete frustration/problem section, one section that clarifies the real decision or why generic Omegle-alternative advice misses the point, one section on why SomeSome may fit this reader, one section on where SomeSome may not be the right move or what kind of reader should try something else, then a close/CTA section
- allow at most one standalone problem-diagnosis section before the brief shifts into decision-driving sections and SomeSome integration; do not stack multiple broad 'why apps fail' sections about bots, psychology, culture, or platform decline
- avoid broad filler angles like random chat history, generic market landscape summaries, vague psychology explainers, future-of-the-category predictions, long safety/red-flag digressions, network-effect theory, or generic 'why platforms fail' sections unless they directly change the reader's decision
- avoid generic listicle/checklist framing like 'what to look for', 'how to evaluate', red-flag/green-flag sections, or 'before wasting your time' advice unless the angle is unusually specific to this searcher's problem
- make SomeSome part of the brief's argument by section 2, not just the CTA; if the product name could be removed from the brief without changing the structure, the brief is too generic
- ensure the future outline can support at least 3 SomeSome-named H2 sections; if only one or two sections would naturally mention SomeSome, the brief is still too generic
- when positioning SomeSome in the brief, keep it modest and high-level: frame it as a product worth trying next for this frustration, not as a proven winner because of a special user base, hidden culture, different expectations, higher odds, better outcomes, or internal design advantage you were not told about
- avoid soft invented product language like 'prioritizes meaningful exchanges', 'creates a better conversation environment', 'built for people who actually want to talk', 'users arrive expecting conversation', 'gives conversations a chance to develop', 'offers a better shot at real conversations', or 'a few apps still work if you know what to look for' unless that evidence was explicitly provided
- do not turn the brief into a broad market comparison promise about "the usual suspects," "a few others," or which platforms still work overall unless you were actually given evidence for those comparisons; keep the brief centered on the SomeSome decision
- do not invent firsthand testing, user-count claims, moderation details, or company-intent claims about why SomeSome was built/how it works internally unless provided

Return valid JSON only:
{{
  "working_title": "...",
  "angle": "...",
  "reader_problem": "...",
  "promise": "...",
  "cta": "...",
  "seo_title": "...",
  "slug": "...",
  "primary_keyword": "...",
  "secondary_keywords": ["..."],
  "search_intent": "informational|commercial|comparison",
  "hook": "..."
}}
""".strip()

OUTLINE_PROMPT = """
Using this blog brief, write a practical outline for a blog post that should land between 1500 and 3000 words.

Brief JSON:
{brief_json}

Product context:
- Product: {product_name}
- {product_context}
- Approved fact sheet:
{product_fact_sheet}

Return markdown only with:
- headline
- subtitle
- intro hook
- section headings
- bullet points under each section
- CTA ending

Requirements:
- include exactly 5 substantive H2-level sections, not a sprawling catch-all structure
- keep the structure skimmable
- use this progression unless the brief makes a sharper equivalent obvious: section 1 names the concrete frustration, section 2 turns that frustration into a decision criterion or exposes why generic Omegle-alternative advice misses the point, section 3 explains why SomeSome may fit this reader, section 4 honestly covers where SomeSome may not be the right move or what kind of reader should try something else, section 5 lands the recommendation and CTA
- each section must either diagnose a concrete reader frustration, explain a decision criterion, or connect that point back to why SomeSome may fit this audience better
- use no more than one standalone diagnosis/problem section before the outline pivots into decision criteria, recommendation logic, and SomeSome integration
- introduce SomeSome before the final section and weave it through multiple sections, not just the CTA
- make the CTA feel earned, not bolted on
- avoid pretending the writer personally tested every app unless that evidence is provided
- do not fall back to headings like 'what to look for', 'how to evaluate', 'before wasting your time', red flags, green flags, 'what makes SomeSome different', 'when SomeSome works best', 'why SomeSome beats...', 'making the switch', 'why it is worth your time', or 'alternatives to try next' unless the section does unusually specific work for this exact reader decision
- do not explain SomeSome with invented claims like 'it attracts conversation-minded users', 'its design philosophy is calmer', 'it self-selects better users', 'it creates better conditions for conversation', 'it focuses on connection quality', 'it takes a different approach', 'the skip button is less central', 'there is more friction before the next chat', or 'it is designed around people who actually want to talk' unless that context was explicitly provided
- do not justify SomeSome with invented user-base or outcomes logic such as 'the user base skews toward real conversations', 'users report longer conversation times', 'the smaller user base means better chats', 'off-peak hours are better', 'the slower pace creates better connections', or any percentage/rate claim unless that evidence was explicitly provided
- do not smuggle in softer scale/lifecycle logic either: avoid claims that SomeSome has a smaller scale, a lower-volume rhythm, a real 'cost of skipping', fewer repeat offenders, better odds because the pool is smaller, better conversation conditions because fewer people are online, or a better outcome because it filters out casual users unless that evidence was explicitly provided
- do not use generic category-theory bullets like 'Omegle legacy', 'mobile-first design', 'swipe culture', 'volume over depth', 'peak hours', 'community size', 'skip button placement', or other broad platform-analysis filler unless that detail directly changes the SomeSome recommendation and is actually supported
- do not use bracket placeholders, fake app slots, or template text like '[Alternative App 1 Name]' or '[List 2-3 features]'
- do not wrap the outline in markdown fences
- do not create a 'Why we built SomeSome', 'network effect', generic 'why platforms fail', founder-story, or broad market-history section unless that context is explicitly provided and directly changes the recommendation
- if you mention competitors, keep them high-level and cautious; if you cannot discuss real alternatives concretely, structure the article around a sharper SomeSome fit decision instead of inventing listicle filler
- make at least one section carry honest fit boundaries or tradeoffs for SomeSome so the outline does not read like five versions of the same pro-product pitch
- avoid generic filler sections whose heading could fit almost any SEO article, including broad psychology explainers, generic red-flag lists, market landscape summaries, realistic-expectations cleanup sections, future-of-the-category predictions, vague 'tips' sections, or catch-all safety lectures unless they directly serve the searcher's decision
- section bullets should stay close to the reader's decision, not drift into category explainers; if a bullet could appear in a generic article about any random chat app, cut it or rewrite it around SomeSome and this searcher's frustration
- every heading should earn its place by changing what the reader understands, notices, or does next; if a section could be removed without changing the recommendation, do not include it
- keep H2 headings tight and specific; headings longer than roughly 12 words usually signal mushy SEO scaffolding rather than a sharp section purpose
- at least 4 of the 5 sections should explicitly reference SomeSome in the heading or bullets so the product fit is planned into the structure, not patched into the draft afterward
- at least 3 H2 headings should explicitly name SomeSome, and one of the first 3 H2s must already bring SomeSome into the structure
- by the second or third H2 section, the outline should already be tying the analysis back to why SomeSome may be worth trying next; do not save the product logic for only the back half
- do not invent SomeSome features, moderation systems, community programs, or unique product details that were not provided
- do not assume SomeSome has human moderation, profile verification, anti-bot systems, special matching logic, a safer community, or a diverse user base unless those details were explicitly provided
- do not invent company-intent language for SomeSome such as why it was built, what it recognized from day one, who it attracts by design, or how it creates better conversations internally unless that context was explicitly provided
- do not invent UI or workflow details for SomeSome such as interest tags, prompts, onboarding copy, skip controls, layouts, profile cards, filters, or recommendation flows unless those details were explicitly provided
""".strip()

DRAFT_PROMPT = """
Write the full blog post from this brief and outline.

Brief JSON:
{brief_json}

Outline markdown:
{outline}

Product context:
- Product: {product_name}
- {product_context}
- Approved fact sheet:
{product_fact_sheet}

Return valid JSON only:
{{
  "title": "...",
  "subtitle": "...",
  "slug": "...",
  "content": "<html with h2/p/ul/li>",
  "excerpt": "120-180 character summary",
  "image": "relevant https image url",
  "imageAlt": "specific alt text for the chosen image",
  "authorName": "...",
  "authorPosition": "...",
  "authorAvatar": "optional avatar url or empty string",
  "date": "YYYY-MM-DD",
  "draft": false
}}

Requirements:
- article length must be between 1500 and 3000 words
- aim for roughly 1800-2300 words; tight, sharp coverage beats bloated comprehensiveness for this audience, but do not undershoot the 1500-word floor
- leave comfortable margin above the 1500-word minimum so one trimmed paragraph does not make the final draft fail validation
- make it authentic, raw, and real-person in tone
- write for the target audience, not for marketers in general
- include a strong intro hook in the first 2 paragraphs
- mention {product_name} in the intro or first substantive section, then keep it present across the body and CTA instead of saving it for one late section
- use simple html only
- inside the content HTML string, prefer single quotes for HTML attributes so the surrounding JSON stays valid
- follow the outline faithfully; do not add extra H2 sections
- include exactly 5 H2 sections
- each H2 section should do real work; if a section does not sharpen the reader's decision or deepen the core argument, cut it instead of padding the article
- assign each of the 5 H2 sections a distinct job before writing: frustration snapshot, decision criterion / why generic advice fails, why SomeSome may fit, where SomeSome may not fit or what it will not solve, and the final recommendation / CTA
- for a 5-section draft, aim for roughly 220-320 words per H2 section so the piece clears the word-count floor without wandering into filler
- use no more than one standalone diagnosis/problem section before the article shifts into decision criteria, recommendation logic, and SomeSome integration; do not stack separate sections for bots, psychology, platform decline, generic safety, or the 'cost' of bad apps unless they materially change whether {product_name} is worth trying
- avoid generic filler sections on psychology, market landscape, red flags, expectations, future trends, vague tips, network effects, or generic 'why platforms fail' theory unless they directly help the reader decide whether {product_name} is worth trying
- do not pad sections with category-analysis filler like 'the Omegle legacy', 'mobile-first design', 'swipe culture', 'volume over depth', 'peak hours', 'community size', 'skip button placement', or similar platform theory unless that detail directly changes the SomeSome recommendation and is supported by context
- avoid fallback headings like 'what to look for', 'how to evaluate', 'before wasting your time', broad checklists, red-flag/green-flag sections, 'what makes SomeSome different', 'when SomeSome works best', 'why SomeSome beats...', 'strategies for better SomeSome success', 'tips for using SomeSome', 'making the switch to SomeSome', 'why SomeSome is worth your time', or 'alternatives to try next' unless they are unusually specific and tightly tied to the central recommendation
- safer heading directions are things like 'Why SomeSome belongs in this decision early', 'What SomeSome clarifies about disposable chat', 'When SomeSome may not be your move', and 'Why SomeSome is the honest next step' because they force the article to make a decision rather than drift into generic advice or speculative product mechanics
- do not recycle the same point in multiple sections with slightly different wording; consolidate repeated ideas instead of stretching them
- mention {product_name} naturally in at least 4 of the 5 H2 sections plus the conclusion/CTA; do not let more than 1 consecutive section group pass without bringing the argument back to {product_name}
- at least 3 H2 headings should explicitly name {product_name}, and one of the first 3 H2 headings must do it; otherwise the structure will feel generic and the product fit will arrive too late
- within each section that mentions {product_name}, bring the product into the section early instead of saving it for a last sentence after generic setup
- count the H2 headings before you return the JSON: use exactly 5, and never collapse to 4 because one section feels optional; if you cut one idea, replace it with a stronger section instead of shortening the structure
- by the second H2 section, the article should already be connecting the problem analysis to why {product_name} may be worth trying; do not quarantine the product to the back third of the post
- one middle section should honestly explain where {product_name} may not be the right fit, what kind of reader might prefer something else, or what frustration the product does not magically solve; this keeps the article sharp instead of reading like repetitive praise
- the CTA should make sense for a random chat / random video chat product
- if you include a SomeSome-focused section, frame it around why the product may feel worth trying for this audience and tie that framing back to earlier sections
- every SomeSome mention must stay inside the approved fact sheet above unless the brief explicitly adds another fact
- allowed concrete facts include: heavily moderated/SFW, global reach with strength in the Philippines/Colombia/SEA/Latam/Brazil, 60-second default calls that can be extended, direct calls, unlimited free messages, and in-app AI translation/live subtitles
- do not invent founder stories, extra moderation policies, feature sets, internal product details, company-intent mythology, or speculative usage advice like the best times to use the app that were not provided
- do not justify {product_name} with invented claims about its user base, self-selection, design philosophy, culture, higher engagement, smaller-scale quality, why people behave differently there, how the product supposedly improves conversation odds, or why a slower pace / smaller pool / off-peak timing supposedly creates better conversations unless those details were explicitly provided
- safe framing examples: '{product_name} is worth trying next if you want random chat to feel less disposable' or '{product_name} may fit better if you are tired of frantic, low-effort chats'
- specifically avoid unsupported phrases like '{product_name} is designed around...', '{product_name} takes a different approach...', '{product_name} positions itself as...', '{product_name} attracts conversation-minded users', '{product_name} gives conversations room to develop', '{product_name} improves your odds', '{product_name} makes the skip button less central', '{product_name} adds friction before the next chat', 'users report...', or 'the platform seems to...' unless those facts were explicitly provided
- do not justify {product_name} with invented user-base or outcomes logic such as '{product_name}'s user base skews toward...', 'users report longer conversation times', 'smaller user base means better chats', 'higher percentages of real people', or rate/ratio claims unless the brief explicitly provides that evidence
- do not use softer scale/lifecycle rationales either, such as claiming {product_name} has a smaller scale, a lower-volume rhythm, a real cost of skipping, a better chance because the pool is smaller, repeated-user familiarity, or better conversation conditions because fewer people are online unless the brief explicitly provides that evidence
- avoid generic evaluation/checklist prose that could fit any chat app article; every section should stay anchored to this reader problem and to whether {product_name} is worth trying next
- include a genuinely relevant image URL, not an empty string
- use authorName '{product_name} Team' and authorPosition '{product_name} Editorial'
- do not claim personal testing, internal metrics, exact user counts, moderation performance, bot percentages, or competitor traffic unless provided in the brief
- do not say you tested apps, reviewed dozens of platforms, or analyzed exact counts unless the brief explicitly says so
- do not invent anecdotes, testimonials, user quotes, or case studies to make the article feel more human
- do not promise absolutes like 'no bots', 'bot-free', 'always safe', or 'guaranteed real people' unless the brief explicitly provides proof
- do not guess unique product differentiators beyond the provided context; position {product_name} as a better-feeling option without inventing feature details
- do not claim {product_name} has human moderation, moderation teams, verification, anti-bot protections, safety systems, special matching, or a particular user-base quality unless those facts were explicitly provided
- do not invent platform-specific workflow details for {product_name} such as iOS/Android availability, app-store downloads, camera permission flows, text-only/video mode switches, or in-app reporting tools unless those details were explicitly provided
- do not invent company-intent/process claims for {product_name} such as why it was built, what it recognized from day one, who it attracts by design, how it creates better conversations internally, or how its team intentionally shapes user behavior unless those details were explicitly provided
- do not invent {product_name} UI or product-flow details such as interest tags, swipe/skip buttons, prompts, onboarding steps, profile cards, filters, discovery feeds, or layout claims unless those details were explicitly provided
- avoid stale year framing in the headline, subtitle, slug, or excerpt; keep it evergreen unless the brief explicitly requires a year
- if you compare apps, use cautious language and broad qualitative framing instead of made-up tables or fake metrics
- start the response with {{ and end it with }} as valid JSON only
- do not use markdown fences
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
    max_rounds: int = 3
    threshold: float = 8.0
    publish: bool = False
    framer_publish_mode: str = "live"
    strict_validation: bool = False
    writer_fallback_models: list[str] = field(default_factory=list)
    reviewer_fallback_models: list[str] = field(default_factory=list)


@dataclass
class ModelAttemptLog:
    role: str
    requested_model: str
    used_model: str
    ok: bool
    error: str = ""


def run_pipeline(config: BlogPipelineConfig, base_dir: str | Path = ".") -> Path:
    load_dotenv(Path(base_dir) / ".env")
    client = OpenRouterClient.from_env()
    run_dir = ensure_dir(Path(base_dir) / "runs" / timestamp_slug())
    attempts: list[ModelAttemptLog] = []

    brief, brief_model = _generate_brief(client, config, attempts)
    write_json(run_dir / "brief.json", brief)

    outline, outline_model = _generate_outline(client, config, brief, attempts)
    write_text(run_dir / "outline.md", outline)

    draft, draft_model = _generate_draft(client, config, brief, outline, attempts)
    draft = _apply_post_defaults(draft, brief, config)
    write_json(run_dir / "draft_round_1.json", draft)

    loop = ReviewerLoop(
        client=client,
        reviewer_model=config.reviewer_model,
        writer_model=config.writer_model,
        reviewer_fallback_models=config.reviewer_fallback_models,
        writer_fallback_models=config.writer_fallback_models,
        threshold=config.threshold,
        max_rounds=config.max_rounds,
        validator=lambda post: _validate_post(post, config=config, brief=brief),
        attempt_logger=attempts,
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

    final_post = _apply_post_defaults(final_draft, brief, config)
    validation_issues = _validate_post(final_post, config=config, brief=brief)
    if validation_issues and config.strict_validation:
        raise ValueError(f"Final post failed validation: {'; '.join(validation_issues)}")

    write_json(run_dir / "final_post.json", final_post)
    write_json(run_dir / "review_history.json", review_history)
    write_json(run_dir / "model_attempts.json", [_serialize_attempt(item) for item in attempts])

    last_review = review_history[-1] if review_history else {}
    summary = {
        "topic": config.topic,
        "writer_model": config.writer_model,
        "reviewer_model": config.reviewer_model,
        "brief_model_used": brief_model,
        "outline_model_used": outline_model,
        "draft_model_used": draft_model,
        "max_rounds": config.max_rounds,
        "strict_validation": config.strict_validation,
        "final_slug": final_post["slug"],
        "final_title": final_post["title"],
        "review_rounds": len(review_history),
        "final_score": last_review.get("overall_score") if review_history else None,
        "last_review_verdict": last_review.get("verdict") if review_history else None,
        "reviewer_ready": bool(review_history) and not last_review.get("required_fixes") and str(last_review.get("verdict", "")).lower() == "ready",
        "last_review_required_fixes": last_review.get("required_fixes", []) if review_history else [],
        "word_count": _estimate_word_count(final_post.get("content", "")),
        "brief_validation_issues": _validate_brief(brief),
        "outline_validation_issues": _validate_outline(outline),
        "validation_issues": validation_issues,
    }
    write_json(run_dir / "summary.json", summary)

    if config.publish:
        _publish_to_framer(base_dir=base_dir, post_path=run_dir / "final_post.json", mode=config.framer_publish_mode)

    return run_dir


def _build_safe_brief_from_config(config: BlogPipelineConfig) -> dict[str, Any]:
    topic = _clean_inline_text(config.topic) or f"best {PRODUCT_NAME.lower()} alternative"
    primary_keyword = topic.lower()
    reader_problem = (
        f"People searching for {primary_keyword} are usually tired of random chats that die instantly and want a more believable next thing to try."
    )
    working_title = _clean_inline_text(config.topic) or f"Best Omegle Alternative for Real Conversations"
    return {
        "working_title": working_title,
        "angle": f"A practical look at why disposable random chat feels hollow and whether {PRODUCT_NAME} is worth trying next.",
        "reader_problem": reader_problem,
        "promise": f"Show readers how to judge the next random chat app to try and where {PRODUCT_NAME} may fit without making unsupported claims.",
        "cta": _clean_inline_text(config.cta) or f"Try {PRODUCT_NAME} if you want a less disposable next step.",
        "seo_title": working_title,
        "slug": slugify(working_title),
        "primary_keyword": primary_keyword,
        "secondary_keywords": [keyword.strip() for keyword in (config.keywords or "").split(",") if keyword.strip()][:3],
        "search_intent": "commercial",
        "hook": f"If random chat keeps feeling disposable, the real question is what app feels worth trying next instead of repeating the same dead-end cycle.",
    }


def _generate_brief(client: OpenRouterClient, config: BlogPipelineConfig, attempts: list[ModelAttemptLog]) -> tuple[dict[str, Any], str]:
    text, used_model = _chat_with_fallbacks(
        client,
        role="writer_brief",
        primary_model=config.writer_model,
        fallback_models=config.writer_fallback_models,
        system=WRITER_SYSTEM,
        user=BRIEF_PROMPT.format(
            product_name=PRODUCT_NAME,
            product_context=PRODUCT_CONTEXT,
            product_fact_sheet=PRODUCT_FACT_SHEET,
            topic=config.topic,
            audience=config.audience,
            tone=config.tone,
            cta=config.cta,
            keywords=config.keywords,
        ),
        temperature=0.2,
        max_tokens=1200,
        attempts=attempts,
    )
    brief = _extract_json_with_repair(
        client,
        text=text,
        role="writer_brief",
        primary_model=config.writer_model,
        fallback_models=config.writer_fallback_models,
        attempts=attempts,
    )
    brief_issues = _validate_brief(brief)
    repair_round = 0
    while brief_issues and repair_round < 2:
        repair_round += 1
        repaired_text, used_model = _chat_with_fallbacks(
            client,
            role="writer_brief_repair",
            primary_model=config.writer_model,
            fallback_models=config.writer_fallback_models,
            system=WRITER_SYSTEM,
            user=(
                "Rewrite this blog brief so it is immediately usable for a truthful, product-relevant SomeSome article. "
                "Keep one sharp thesis, cut generic SEO framing, and remove unsupported claims about SomeSome users, culture, hidden product advantages, or why the product works internally. "
                "Position SomeSome as a high-level option worth trying for this reader problem without inventing proof or product specifics. "
                "Phrases like 'different approach', 'alternative approach', 'better odds', 'works better', or claims about who uses SomeSome are not safe unless explicitly supported. "
                "Return valid JSON only with the original schema.\n\n"
                f"Problems to fix:\n{json.dumps(brief_issues, ensure_ascii=False, indent=2)}\n\n"
                f"Original brief JSON:\n{json.dumps(brief, ensure_ascii=False, indent=2)}"
            ),
            temperature=0.2,
            max_tokens=1200,
            attempts=attempts,
        )
        brief = _extract_json_with_repair(
            client,
            text=repaired_text,
            role="writer_brief",
            primary_model=config.writer_model,
            fallback_models=config.writer_fallback_models,
            attempts=attempts,
        )
        brief_issues = _validate_brief(brief)
    if brief_issues and all(
        "Remove unsupported claims" in issue or "Cut generic SEO angle language" in issue or "Tighten the working title" in issue
        for issue in brief_issues
    ):
        brief = _build_safe_brief_from_config(config)
        brief_issues = _validate_brief(brief)
    if brief_issues and config.strict_validation:
        raise ValueError("Brief failed validation after repair: " + "; ".join(brief_issues))
    if not brief.get("slug"):
        brief["slug"] = slugify(brief.get("working_title") or config.topic)
    return brief, used_model


def _generate_outline(client: OpenRouterClient, config: BlogPipelineConfig, brief: dict[str, Any], attempts: list[ModelAttemptLog]) -> tuple[str, str]:
    outline, used_model = _chat_with_fallbacks(
        client,
        role="writer_outline",
        primary_model=config.writer_model,
        fallback_models=config.writer_fallback_models,
        system=WRITER_SYSTEM,
        user=OUTLINE_PROMPT.format(
            brief_json=json.dumps(brief, ensure_ascii=False, indent=2),
            product_name=PRODUCT_NAME,
            product_context=PRODUCT_CONTEXT,
            product_fact_sheet=PRODUCT_FACT_SHEET,
        ),
        temperature=0.1,
        max_tokens=1800,
        attempts=attempts,
    )
    outline_issues = _validate_outline(outline)
    repair_round = 0
    while outline_issues and repair_round < 2:
        repair_round += 1
        outline, used_model = _chat_with_fallbacks(
            client,
            role="writer_outline_repair",
            primary_model=config.writer_model,
            fallback_models=config.writer_fallback_models,
            system=WRITER_SYSTEM,
            user=(
                "Rewrite this blog outline so it is immediately usable for a truthful SomeSome article. "
                "Remove placeholders, markdown fences, generic checklist sections, speculative SomeSome/product details, mushy full-sentence headings, and broad category-explainer bullets. "
                "Keep it to exactly 5 substantive H2 sections, make sure at least 3 H2 headings explicitly name SomeSome, bring SomeSome into one of the first 3 H2s, and make sure at least 4 of the 5 sections mention SomeSome in the heading or opening bullet so it does not get bolted on at the end. "
                "Do not use headings like 'what to look for', 'how to evaluate', 'before wasting your time', red flags, green flags, 'what makes SomeSome different', 'when SomeSome works best', 'why SomeSome beats...', or 'alternatives to try next'. "
                "Do not use bullet angles like 'Omegle legacy', 'mobile-first design', 'swipe culture', 'volume over depth', 'peak hours', 'community size', 'skip button placement', or other generic platform theory unless they directly change the SomeSome decision and are supported. "
                "One middle section must honestly explain who SomeSome may fit, who it may not fit, or what it will not solve; acceptable heading directions include 'Who SomeSome may fit after too many dead chats', 'When SomeSome may not be your move', or 'What SomeSome will not solve for you'. "
                "If competitor coverage would be generic, replace it with a more specific decision-driving section tied to the reader's frustration and why SomeSome may be worth trying next. "
                "Return markdown only.\n\n"
                f"Problems to fix:\n{json.dumps(outline_issues, ensure_ascii=False, indent=2)}\n\n"
                f"Original outline:\n{outline}"
            ),
            temperature=0.1,
            max_tokens=1800,
            attempts=attempts,
        )
        outline_issues = _validate_outline(outline)
    if outline_issues and any("unsupported SomeSome positioning" in issue for issue in outline_issues):
        outline, used_model = _chat_with_fallbacks(
            client,
            role="writer_outline_claim_repair",
            primary_model=config.writer_model,
            fallback_models=config.writer_fallback_models,
            system=WRITER_SYSTEM,
            user=(
                "Rewrite this outline conservatively so every SomeSome mention stays high-level and truthful. "
                "Cut or rewrite any bullet or heading that implies special user intent, a calmer interface, different platform mechanics, text/video mode claims, moderation assumptions, user-base-size advantages, better odds, longer conversations, observed outcomes, or generic platform-theory filler masquerading as evidence. "
                "If you are unsure whether a SomeSome claim is supported, remove it and restate the point as reader-problem language instead. Replace category-explainer bullets like 'mobile-first design', 'swipe culture', 'peak hours', 'community size', or 'skip button placement' with reader-decision language or cut them entirely. "
                "Keep exactly 5 H2 sections, preserve the article's sharp thesis, keep at least 3 SomeSome-named H2 headings with one in the first 3 H2s, make sure at least 4 of the 5 sections mention SomeSome in the heading or opening bullet, and keep one honest middle section about who SomeSome may or may not fit or what it will not solve. "
                "Safe fit-limit heading directions include 'When SomeSome may not be your move' or 'What SomeSome will not solve for you'. Return markdown only.\n\n"
                f"Problems to fix:\n{json.dumps(outline_issues, ensure_ascii=False, indent=2)}\n\n"
                f"Outline to rewrite:\n{outline}"
            ),
            temperature=0.0,
            max_tokens=1800,
            attempts=attempts,
        )
        outline_issues = _validate_outline(outline)
    if outline_issues and any(
        "Name SomeSome in at least 3 H2 headings" in issue
        or "Bring SomeSome into one of the first 3 H2 headings" in issue
        or "Include one honest outline section or bullet" in issue
        for issue in outline_issues
    ):
        outline, used_model = _chat_with_fallbacks(
            client,
            role="writer_outline_structure_repair",
            primary_model=config.writer_model,
            fallback_models=config.writer_fallback_models,
            system=WRITER_SYSTEM,
            user=(
                "Rewrite this outline so the structure itself solves the remaining integration problems without adding hype or invented product claims. "
                "Keep exactly 5 H2 sections and preserve the existing thesis, but make the structure explicit and compliant: at least 3 H2 headings must name SomeSome, one of the first 3 H2 headings must name SomeSome, at least 4 of the 5 sections must mention SomeSome in the heading or opening bullet, and one middle H2 must clearly state who SomeSome may not fit or what it will not solve. "
                "Use short, editorial H2s instead of broad SEO headings. Safe heading shapes include 'Why SomeSome belongs in this decision early', 'What SomeSome clarifies about disposable chat', 'When SomeSome may not be your move', and 'Why SomeSome is the honest next step'. "
                "Do not use generic headings like 'what to look for', 'how to evaluate', or 'what makes SomeSome different'. Do not invent features, user-base claims, moderation details, or hidden reasons SomeSome works better. Return markdown only.\n\n"
                f"Remaining problems to solve:\n{json.dumps(outline_issues, ensure_ascii=False, indent=2)}\n\n"
                f"Outline to rewrite:\n{outline}"
            ),
            temperature=0.0,
            max_tokens=1800,
            attempts=attempts,
        )
        outline_issues = _validate_outline(outline)
    if outline_issues and any(
        "unsupported SomeSome positioning" in issue
        or "broad category-explainer bullets" in issue
        or "Replace generic filler headings" in issue
        for issue in outline_issues
    ):
        outline, used_model = _chat_with_fallbacks(
            client,
            role="writer_outline_positioning_repair",
            primary_model=config.writer_model,
            fallback_models=config.writer_fallback_models,
            system=WRITER_SYSTEM,
            user=(
                "Rewrite this outline one more time with stricter conservative framing. "
                "Every SomeSome bullet must stay high-level and reader-facing: 'worth trying next', 'may fit better if you want less disposable chat', or 'may not be your move if you want rapid-fire chaos'. "
                "Delete any claim about interface behavior, text/video mode, pace, user-base size, better odds, longer conversations, platform mechanics, or hidden reasons SomeSome works better. "
                "Also delete category-theory filler about engagement loops, instant-gratification design, market dynamics, or why random chat apps broadly behave the way they do unless that bullet directly changes the reader's decision about trying SomeSome next. "
                "Use exactly 5 H2 sections. Keep headings short and editorial. Each section should answer a reader decision question, not explain the whole category. Return markdown only.\n\n"
                f"Remaining problems to solve:\n{json.dumps(outline_issues, ensure_ascii=False, indent=2)}\n\n"
                f"Outline to rewrite:\n{outline}"
            ),
            temperature=0.0,
            max_tokens=1800,
            attempts=attempts,
        )
        outline_issues = _validate_outline(outline)
    if outline_issues and all(
        "unsupported SomeSome positioning" in issue
        or "broad category-explainer bullets" in issue
        or "Replace generic filler headings" in issue
        or "Bring SomeSome into at least 4 of the 5 outline sections" in issue
        or "Name SomeSome in at least 3 H2 headings" in issue
        or "Bring SomeSome into one of the first 3 H2 headings" in issue
        or "Include one honest outline section or bullet" in issue
        for issue in outline_issues
    ):
        outline = _build_safe_outline_from_brief(brief, config)
        outline_issues = _validate_outline(outline)
    if outline_issues and config.strict_validation:
        raise ValueError("Outline failed validation after repair: " + "; ".join(outline_issues))
    return outline, used_model


def _generate_draft(client: OpenRouterClient, config: BlogPipelineConfig, brief: dict[str, Any], outline: str, attempts: list[ModelAttemptLog]) -> tuple[dict[str, Any], str]:
    text, used_model = _chat_with_fallbacks(
        client,
        role="writer_draft",
        primary_model=config.writer_model,
        fallback_models=config.writer_fallback_models,
        system=WRITER_SYSTEM,
        user=DRAFT_PROMPT.format(
            brief_json=json.dumps(brief, ensure_ascii=False, indent=2),
            outline=outline,
            product_name=PRODUCT_NAME,
            product_context=PRODUCT_CONTEXT,
            product_fact_sheet=PRODUCT_FACT_SHEET,
        ),
        temperature=0.3,
        max_tokens=4200,
        attempts=attempts,
    )
    draft = _extract_json_with_repair(
        client,
        text=text,
        role="writer_draft",
        primary_model=config.writer_model,
        fallback_models=config.writer_fallback_models,
        attempts=attempts,
    )
    return draft, used_model


def _build_safe_outline_from_brief(brief: dict[str, Any], config: BlogPipelineConfig) -> str:
    title = _clean_inline_text(str(brief.get("seo_title") or brief.get("working_title") or config.topic).strip()) or config.topic
    primary_keyword = _clean_inline_text(str(brief.get("primary_keyword") or config.topic).strip()) or config.topic
    reader_problem = _clean_inline_text(str(brief.get("reader_problem") or "Random chat keeps feeling disposable and too rushed to become a real conversation.").strip())
    cta = _clean_inline_text(str(brief.get("cta") or f"Try {PRODUCT_NAME} if you want a less disposable next step.").strip())
    intro_problem = reader_problem or "Most random chat apps feel disposable before the conversation even has a chance to begin."
    lines = [
        f"# {title}",
        "",
        "## Why disposable random chat gets old fast",
        f"- Start with the reader frustration: {intro_problem}",
        f"- Tie that frustration to the real decision behind '{primary_keyword}': what app feels worth trying next when chats keep dying instantly.",
        "",
        "## Why SomeSome belongs in this decision early",
        f"- Bring {PRODUCT_NAME} in as a modest next try for readers who want random chat to feel less disposable.",
        f"- Keep the framing high-level and reader-facing: {PRODUCT_NAME} may fit better if the reader wants a calmer next attempt, not a magic fix.",
        "",
        "## What SomeSome helps clarify about the next app to try",
        f"- Use {PRODUCT_NAME} to sharpen the decision standard: choose the next app based on whether it feels worth another real conversation attempt.",
        f"- Keep this section focused on reader takeaway, not hidden reasons {PRODUCT_NAME} supposedly works better.",
        "",
        "## When SomeSome may not be your move",
        f"- Add one honest fit limit: {PRODUCT_NAME} may not be the move if the reader wants rapid-fire chaos, constant novelty, or heavy filtering features.",
        f"- Make the tradeoff explicit so the {PRODUCT_NAME} recommendation stays believable instead of sounding like blanket promo copy.",
        "",
        "## Why SomeSome is the honest next step",
        f"- Land the recommendation with a natural CTA: {cta}",
        f"- Close by saying {PRODUCT_NAME} is worth trying next when the reader still wants spontaneous conversation but is tired of disposable chat.",
    ]
    return "\n".join(lines)


def _apply_post_defaults(draft: dict[str, Any], brief: dict[str, Any], config: BlogPipelineConfig) -> dict[str, Any]:
    post = dict(draft)
    post.setdefault("title", brief.get("working_title", config.topic))
    post.setdefault("subtitle", "")
    post["title"] = _sanitize_front_matter_text(_clean_inline_text(post.get("title") or brief.get("working_title") or config.topic))
    post["subtitle"] = _sanitize_front_matter_text(_clean_inline_text(post.get("subtitle") or ""))
    post["slug"] = slugify(_sanitize_front_matter_text(post.get("slug") or brief.get("slug") or config.topic))
    post.setdefault("content", "")
    post.setdefault("contentType", "html")
    post.setdefault("excerpt", _build_excerpt(post.get("content", ""), brief, config))
    post["excerpt"] = _sanitize_front_matter_text(_clean_inline_text(post.get("excerpt") or _build_excerpt(post.get("content", ""), brief, config)))
    post["date"] = utc_today()
    post["image"] = (post.get("image") or "").strip() or DEFAULT_IMAGE_URL
    post["imageAlt"] = _clean_inline_text((post.get("imageAlt") or "").strip() or DEFAULT_IMAGE_ALT)
    post["authorName"] = f"{PRODUCT_NAME} Team"
    post["authorPosition"] = f"{PRODUCT_NAME} Editorial"
    post.setdefault("authorAvatar", "")
    post["draft"] = bool(post.get("draft", False))
    return post


def _sanitize_front_matter_text(value: str) -> str:
    text = str(value or "").strip()
    replacements = [
        (r"\bno bots\b", "fewer bots"),
        (r"\bwithout bots\b", "with fewer bots"),
        (r"\bbot[- ]free\b", "lower-bot"),
        (r"\bzero bots\b", "fewer bots"),
        (r"\bguaranteed real people\b", "better odds of real people"),
        (r"\balways safe\b", "safer-feeling"),
        (r"\bcompletely safe\b", "safer-feeling"),
        (r"\b100% real\b", "more real"),
    ]
    for pattern, replacement in replacements:
        text = re.sub(pattern, replacement, text, flags=re.IGNORECASE)
    current_year = utc_today()[:4]
    text = re.sub(rf"\b(20\d{{2}})\b", lambda m: m.group(1) if m.group(1) == current_year else "", text)
    text = re.sub(r"\(\s*\)", "", text)
    text = re.sub(r"\s{2,}", " ", text)
    text = re.sub(r"\s+([:;,.!?])", r"\1", text)
    return text.strip(" -_")


def _outline_h2_headings(outline: str) -> list[str]:
    headings: list[str] = []
    for line in (outline or "").splitlines():
        stripped = line.strip()
        if stripped.startswith("## "):
            headings.append(stripped[3:].strip())
    return headings


def _generic_heading_patterns() -> list[str]:
    return [
        r"\bpsycholog",
        r"\bred flags?\b",
        r"\bgreen flags?\b",
        r"\bmarket (?:overview|landscape|history)\b",
        r"\bfuture\b",
        r"\bnetwork effect\b",
        r"\bwhy (?:traditional )?platforms? keep failing\b",
        r"\bsafety\b",
        r"\bexpectation",
        r"\bwhat to look for\b",
        r"\bhow to evaluate\b",
        r"\bhow to spot\b",
        r"\bwarning signs?\b",
        r"\bchecklist\b",
        r"\bbefore wasting your time\b",
        r"\bcommon mistakes?\b",
        r"\bhidden signs?\b",
        r"\bwhat [a-z\- ]+ look like\b",
        r"\bwhat makes somesome different\b",
        r"\bhow somesome changes your random chat expectations\b",
        r"\bwhat makes a random chat app worth your time\b",
        r"\bwhen somesome works best\b",
        r"\bwhy somesome beats\b",
        r"\balternatives? to try next\b",
        r"\bmaking the switch(?: to somesome)?\b",
        r"\b(?:somesome|this app|the switch) is worth your time\b",
        r"\bif you miss (?:actual|real|late-night) (?:online )?conversations\b",
        r"\bstrateg(?:y|ies) for (?:better )?(?:somesome|random chat)\b",
        r"\btips? for (?:better )?(?:somesome|random chat)\b",
        r"\bconversation[- ]friendly platforms?\b",
        r"\bconversation[- ]focused platforms?\b",
        r"\bhidden psychology\b",
        r"\bhidden cost\b",
        r"\bbot (?:arm(?:y|ies)|plague|problem|problems)\b",
    ]


def _find_generic_headings(headings: list[str]) -> list[str]:
    patterns = _generic_heading_patterns()
    return [heading for heading in headings if any(re.search(pattern, heading.lower()) for pattern in patterns)]


def _long_heading_samples(headings: list[str], *, max_words: int = 12) -> list[str]:
    return [heading for heading in headings if len(re.findall(r"\b\w+\b", heading)) > max_words]


def _has_fit_limit_signal(text: str, *, product_name: str = PRODUCT_NAME) -> bool:
    lowered = (text or "").lower()
    product_lower = product_name.lower()
    patterns = [
        rf"{re.escape(product_lower)}[^.\n]{{0,80}}\b(?:may fit|might fit|may not fit|might not fit|worth trying|not for|is not for|isn['’]t for|won['’]t work for|won['’]t fix|won['’]t solve|will not solve|cannot fix|may not be|might not be|not your move|isn['’]t the move|right move|better off elsewhere|better off with|if you want|if you prefer|try something else|skip it)\b",
        rf"\b(?:may fit|might fit|may not fit|might not fit|worth trying|not for|is not for|isn['’]t for|won['’]t work for|won['’]t fix|won['’]t solve|will not solve|cannot fix|may not be|might not be|not your move|isn['’]t the move|right move|better off elsewhere|better off with|if you want|if you prefer|try something else|skip it)\b[^.\n]{{0,80}}{re.escape(product_lower)}",
    ]
    return any(re.search(pattern, lowered) for pattern in patterns)


def _outline_section_chunks(outline: str) -> list[str]:
    sections: list[str] = []
    current: list[str] = []
    for line in (outline or "").splitlines():
        stripped = line.strip()
        if stripped.startswith("## "):
            if current:
                sections.append("\n".join(current).strip())
            current = [stripped]
        elif current:
            current.append(stripped)
    if current:
        sections.append("\n".join(current).strip())
    return sections


def _has_middle_section_fit_limit_signal_in_outline(outline: str) -> bool:
    sections = _outline_section_chunks(outline)
    middle_sections = sections[1:-1] if len(sections) >= 3 else sections[:-1]
    return any(_has_fit_limit_signal(section) for section in middle_sections)


def _has_middle_section_fit_limit_signal_in_article(html: str) -> bool:
    h2_sections = [section for section in _split_html_sections(html) if re.search(r"<h2\b", section, flags=re.IGNORECASE)]
    middle_sections = h2_sections[1:-1] if len(h2_sections) >= 3 else h2_sections[:-1]
    return any(_has_fit_limit_signal(_html_to_text(section)) for section in middle_sections)


def _product_named_outline_sections(outline: str, *, product_name: str = PRODUCT_NAME, char_window: int = 220) -> int:
    product_lower = product_name.lower()
    count = 0
    for section in _outline_section_chunks(outline):
        heading, _, remainder = section.partition("\n")
        if product_lower in heading.lower():
            count += 1
            continue
        if product_lower in remainder[:char_window].lower():
            count += 1
    return count


def _product_early_h2_section_mentions(html: str, *, product_name: str = PRODUCT_NAME, word_window: int = 110) -> int:
    product_lower = product_name.lower()
    count = 0
    for section_html in _split_html_sections(html):
        if not re.search(r"<h2\b", section_html, flags=re.IGNORECASE):
            continue
        text = _html_to_text(section_html)
        words = re.findall(r"\b\w+\b", text)
        if product_lower in " ".join(words[:word_window]).lower():
            count += 1
    return count


APPROVED_PRODUCT_FACT_PATTERNS = [
    r"heavily moderated[^.\n]{0,120}\bsfw\b",
    r"(global user base|users from all over the world)[^.\n]{0,160}(philippines|colombia|brazil|latam|sea)",
    r"60[- ]seconds?[^.\n]{0,120}(extend|extended|extendable)",
    r"direct(?:ly)? call people",
    r"direct calls?",
    r"unlimited free messages?",
    r"ai translation[^.\n]{0,160}(live subtitle|subtitles|spanish|latam|cross-language)",
    r"(live subtitle|subtitles)[^.\n]{0,160}ai translation",
]


def _strip_approved_product_fact_spans(text: str) -> str:
    cleaned = text or ""
    for pattern in APPROVED_PRODUCT_FACT_PATTERNS:
        cleaned = re.sub(pattern, " ", cleaned, flags=re.IGNORECASE)
    return cleaned


def _validate_brief(brief: dict[str, Any]) -> list[str]:
    issues: list[str] = []
    if not isinstance(brief, dict):
        return ["Return the brief as a JSON object."]
    working_title = str(brief.get("working_title") or "").strip()
    angle = str(brief.get("angle") or "").strip()
    promise = str(brief.get("promise") or "").strip()
    hook = str(brief.get("hook") or "").strip()
    cta = str(brief.get("cta") or "").strip()
    combined = " ".join([working_title, angle, promise, hook, cta]).lower()
    validation_text = _strip_approved_product_fact_spans(combined)
    if PRODUCT_NAME.lower() not in combined:
        issues.append(f"Make {PRODUCT_NAME} part of the brief angle or CTA so the draft is product-relevant from the start.")
    invented_claim_patterns = [
        r"somesome[^.\n]{0,120}\battracts users who\b",
        r"\battracts users who\b[^.\n]{0,120}somesome",
        r"somesome[^.\n]{0,120}\buser base\b",
        r"\buser base\b[^.\n]{0,120}somesome",
        r"somesome[^.\n]{0,120}\bworks better\b",
        r"\bworks better\b[^.\n]{0,120}somesome",
        r"somesome[^.\n]{0,120}\bactually want to talk\b",
        r"\bactually want to talk\b[^.\n]{0,120}somesome",
        r"somesome[^.\n]{0,120}\bwhy it works\b",
        r"\bwhy it works\b[^.\n]{0,120}somesome",
        r"somesome[^.\n]{0,120}\bprioriti(?:s|z)es meaningful (?:exchanges|conversations)\b",
        r"\bprioriti(?:s|z)es meaningful (?:exchanges|conversations)\b[^.\n]{0,120}somesome",
        r"somesome[^.\n]{0,120}\busers arrive expecting conversation\b",
        r"\busers arrive expecting conversation\b[^.\n]{0,120}somesome",
        r"somesome[^.\n]{0,120}\bpeople who actually want (?:conversations|to talk)\b",
        r"\bpeople who actually want (?:conversations|to talk)\b[^.\n]{0,120}somesome",
        r"somesome[^.\n]{0,120}\buser base skews toward\b",
        r"\buser base skews toward\b[^.\n]{0,120}somesome",
        r"somesome[^.\n]{0,120}\busers report\b",
        r"\busers report\b[^.\n]{0,120}somesome",
        r"somesome[^.\n]{0,120}\baverage conversation times?\b",
        r"\baverage conversation times?\b[^.\n]{0,120}somesome",
        r"somesome[^.\n]{0,120}\bsmaller user base\b",
        r"\bsmaller user base\b[^.\n]{0,120}somesome",
        r"somesome[^.\n]{0,120}\bpositions itself\b",
        r"\bpositions itself\b[^.\n]{0,120}somesome",
        r"somesome[^.\n]{0,120}\bdifferent expectations\b",
        r"\bdifferent expectations\b[^.\n]{0,120}somesome",
        r"somesome[^.\n]{0,120}\bbetter (?:shot|odds)\b",
        r"\bbetter (?:shot|odds)\b[^.\n]{0,120}somesome",
        r"somesome[^.\n]{0,120}\bchance to develop\b",
        r"\bchance to develop\b[^.\n]{0,120}somesome",
        r"somesome[^.\n]{0,120}\b(?:last|lasting) longer\b",
        r"\b(?:last|lasting) longer\b[^.\n]{0,120}somesome",
        r"somesome[^.\n]{0,120}\blonger conversations?\b",
        r"\blonger conversations?\b[^.\n]{0,120}somesome",
        r"somesome[^.\n]{0,120}\bbetter environment\b",
        r"\bbetter environment\b[^.\n]{0,120}somesome",
        r"somesome[^.\n]{0,120}\b(?:different|alternative) approach\b",
        r"\b(?:different|alternative) approach\b[^.\n]{0,120}somesome",
        r"somesome[^.\n]{0,120}\bdesigned around\b",
        r"\bdesigned around\b[^.\n]{0,120}somesome",
        r"somesome[^.\n]{0,120}\bmoves slower\b",
        r"\bmoves slower\b[^.\n]{0,120}somesome",
        r"somesome[^.\n]{0,120}\bconnects better\b",
        r"\bconnects better\b[^.\n]{0,120}somesome",
        r"somesome[^.\n]{0,120}\bwhat makes\b[^.\n]{0,40}\bdifferent\b",
        r"\bwhat makes\b[^.\n]{0,40}\bdifferent\b[^.\n]{0,120}somesome",
        r"somesome[^.\n]{0,120}\battracts? people looking for\b",
        r"\battracts? people looking for\b[^.\n]{0,120}somesome",
        r"\ba few apps? still work\b",
        r"\busual suspects\b",
    ]
    if any(re.search(pattern, validation_text) for pattern in invented_claim_patterns):
        issues.append(f"Remove unsupported claims that {PRODUCT_NAME} attracts a certain user base, works better because of hidden product factors, or otherwise proves conversation quality without evidence.")
    generic_angle_patterns = [
        r"\bultimate guide\b",
        r"\bcomplete guide\b",
        r"\beverything you need to know\b",
        r"\bmarket landscape\b",
        r"\bsafety tips?\b",
        r"\bchecklist\b",
        r"\bwhat to look for\b",
        r"\bbefore wasting your time\b",
    ]
    if any(re.search(pattern, combined) for pattern in generic_angle_patterns):
        issues.append("Cut generic SEO angle language from the brief and focus on one sharp decision/problem instead of a broad guide or checklist.")
    if working_title and len(working_title.split()) > 14:
        issues.append("Tighten the working title so it sounds sharper and less like a sprawling SEO headline.")
    return issues


def _validate_outline(outline: str) -> list[str]:
    issues: list[str] = []
    outline_lower = outline.lower()
    outline_validation_text = _strip_approved_product_fact_spans(outline_lower)
    h2_headings = _outline_h2_headings(outline)
    if "```" in outline:
        issues.append("Remove markdown code fences from the outline.")
    if re.search(r"\[[^\]]+\]", outline):
        issues.append("Replace bracketed placeholder text with real outline content.")
    if "why we built somesome" in outline_lower or "we built somesome" in outline_lower:
        issues.append("Remove speculative 'why we built SomeSome' or founder-story sections unless that context was explicitly provided.")
    if len(h2_headings) < 5:
        issues.append("Use 5 substantive H2 sections in the outline so the article has enough depth without drifting into filler.")
    if len(h2_headings) > 5:
        issues.append("Trim the outline to 5 substantive H2 sections so the draft stays tight instead of sprawling.")
    generic_headings = _find_generic_headings(h2_headings)
    if generic_headings:
        issues.append("Replace generic filler headings with decision-driving sections that are specific to the reader's problem. Problem headings: " + "; ".join(generic_headings[:3]))
    invented_outline_claim_patterns = [
        r"somesome[^.\n]{0,140}\btakes a different approach\b",
        r"\btakes a different approach\b[^.\n]{0,140}somesome",
        r"somesome[^.\n]{0,140}\battracts? (?:people|users) who\b",
        r"\battracts? (?:people|users) who\b[^.\n]{0,140}somesome",
        r"somesome[^.\n]{0,140}\bcreates better conditions\b",
        r"\bcreates better conditions\b[^.\n]{0,140}somesome",
        r"somesome[^.\n]{0,140}\bfocuses on (?:the )?quality of connections\b",
        r"\bfocuses on (?:the )?quality of connections\b[^.\n]{0,140}somesome",
        r"somesome[^.\n]{0,140}\bworks better when\b",
        r"\bworks better when\b[^.\n]{0,140}somesome",
        r"somesome[^.\n]{0,140}\bavoids? it\b",
        r"\bavoids? it\b[^.\n]{0,140}somesome",
        r"somesome[^.\n]{0,140}\buser base skews toward\b",
        r"\buser base skews toward\b[^.\n]{0,140}somesome",
        r"somesome[^.\n]{0,140}\busers report\b",
        r"\busers report\b[^.\n]{0,140}somesome",
        r"somesome[^.\n]{0,140}\baverage conversation times?\b",
        r"\baverage conversation times?\b[^.\n]{0,140}somesome",
        r"somesome[^.\n]{0,140}\bsmaller user base\b",
        r"\bsmaller user base\b[^.\n]{0,140}somesome",
        r"somesome[^.\n]{0,140}\bsmaller user pool\b",
        r"\bsmaller user pool\b[^.\n]{0,140}somesome",
        r"somesome[^.\n]{0,140}\bhigher percentage\b",
        r"\bhigher percentage\b[^.\n]{0,140}somesome",
        r"somesome[^.\n]{0,140}\boff-peak hours\b",
        r"\boff-peak hours\b[^.\n]{0,140}somesome",
        r"somesome[^.\n]{0,140}\bboth text and video\b",
        r"\bboth text and video\b[^.\n]{0,140}somesome",
        r"somesome[^.\n]{0,140}\bvideo and text options?\b",
        r"\bvideo and text options?\b[^.\n]{0,140}somesome",
        r"somesome[^.\n]{0,140}\btext chat options?\b",
        r"\btext chat options?\b[^.\n]{0,140}somesome",
        r"somesome[^.\n]{0,140}\blonger average conversation times?\b",
        r"\blonger average conversation times?\b[^.\n]{0,140}somesome",
        r"somesome[^.\n]{0,140}\bdifferent user expectations\b",
        r"\bdifferent user expectations\b[^.\n]{0,140}somesome",
        r"somesome[^.\n]{0,140}\bless frantic (?:interface|environment|atmosphere)\b",
        r"\bless frantic (?:interface|environment|atmosphere)\b[^.\n]{0,140}somesome",
        r"somesome[^.\n]{0,140}\bpositions itself\b",
        r"\bpositions itself\b[^.\n]{0,140}somesome",
        r"somesome[^.\n]{0,140}\bdesigned around\b",
        r"\bdesigned around\b[^.\n]{0,140}somesome",
        r"somesome[^.\n]{0,140}\bslower pace\b",
        r"\bslower pace\b[^.\n]{0,140}somesome",
        r"somesome[^.\n]{0,140}\bmoves slower\b",
        r"\bmoves slower\b[^.\n]{0,140}somesome",
        r"somesome[^.\n]{0,140}\bfilters? out users\b",
        r"\bfilters? out users\b[^.\n]{0,140}somesome",
        r"somesome[^.\n]{0,140}\bskip button\b",
        r"\bskip button\b[^.\n]{0,140}somesome",
        r"somesome[^.\n]{0,140}\bescape hatches\b",
        r"\bescape hatches\b[^.\n]{0,140}somesome",
        r"somesome[^.\n]{0,140}\bfriction in the connection process\b",
        r"\bfriction in the connection process\b[^.\n]{0,140}somesome",
        r"somesome[^.\n]{0,140}\bactual humans\b",
        r"\bactual humans\b[^.\n]{0,140}somesome",
        r"somesome[^.\n]{0,140}\bbot farms\b",
        r"\bbot farms\b[^.\n]{0,140}somesome",
        r"somesome[^.\n]{0,140}\btext chat options?\b",
        r"\btext chat options?\b[^.\n]{0,140}somesome",
        r"somesome[^.\n]{0,140}\bbest times? to use\b",
        r"\bbest times? to use\b[^.\n]{0,140}somesome",
    ]
    if any(re.search(pattern, outline_validation_text) for pattern in invented_outline_claim_patterns):
        issues.append(f"Remove unsupported SomeSome positioning from the outline. Keep the product framing modest and high-level instead of claiming special user intent, hidden mechanics, or better conversation conditions.")
    long_headings = _long_heading_samples(h2_headings)
    if long_headings:
        issues.append("Tighten overly long H2 headings so the structure reads like sharp editorial sections instead of mushy SEO sentences. Long headings: " + "; ".join(long_headings[:3]))
    product_heading_positions = [index for index, heading in enumerate(h2_headings) if PRODUCT_NAME.lower() in heading.lower()]
    product_heading_hits = len(product_heading_positions)
    product_mentions = outline_lower.count(PRODUCT_NAME.lower())
    if PRODUCT_NAME.lower() not in outline_lower:
        issues.append(f"Mention {PRODUCT_NAME} somewhere in the outline so the product fit is planned before drafting, not bolted on at the end.")
    elif product_heading_hits < 3:
        issues.append(f"Name {PRODUCT_NAME} in at least 3 H2 headings so the article structure itself carries the recommendation instead of feeling like generic advice with one product section attached.")
    if PRODUCT_NAME.lower() in outline_lower and not any(position < 3 for position in product_heading_positions):
        issues.append(f"Bring {PRODUCT_NAME} into one of the first 3 H2 headings so the product fit starts early instead of arriving late.")
    if product_heading_hits < 3 and product_mentions < 4:
        issues.append(f"Plan {PRODUCT_NAME} into at least 4 outline touchpoints, including multiple substantive sections, so the draft does not quarantine the product to one late block.")
    if PRODUCT_NAME.lower() in outline_lower and _product_named_outline_sections(outline) < 4:
        issues.append(f"Bring {PRODUCT_NAME} into at least 4 of the 5 outline sections, and do it in the heading or opening bullet so the product framing is built into each section instead of added as a last line.")
    if PRODUCT_NAME.lower() in outline_lower and not _has_middle_section_fit_limit_signal_in_outline(outline):
        issues.append(f"Include one honest outline section or bullet about who {PRODUCT_NAME} may fit, who it may not fit, or what it will not solve so the structure does not read like repetitive promo copy.")
    generic_outline_bullet_patterns = [
        r"\bomegle legacy\b",
        r"\bmobile[- ]first design\b",
        r"\bswipe culture\b",
        r"\bvolume over depth\b",
        r"\bpeak hours?\b",
        r"\bcommunity size\b",
        r"\bskip button placement\b",
        r"\bfeedback loops?\b",
        r"\binstant gratification\b",
    ]
    if any(re.search(pattern, outline_lower) for pattern in generic_outline_bullet_patterns):
        issues.append("Cut broad category-explainer bullets that read like generic platform theory. Keep outline bullets tied to the reader's decision about SomeSome, not market-analysis filler.")
    return issues


def _build_excerpt(content: str, brief: dict[str, Any], config: BlogPipelineConfig) -> str:
    text = _html_to_text(content)
    if text:
        clipped = text[:177].rsplit(" ", 1)[0].strip()
        if clipped:
            return f"{clipped}..."
    promise = str(brief.get("promise") or config.topic).strip()
    return promise[:180]


def _clean_inline_text(value: str) -> str:
    text = re.sub(r"<[^>]+>", " ", value or "")
    text = re.sub(r"[*_`~]+", "", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _find_duplicate_paragraphs(html: str) -> list[str]:
    paragraph_counts: dict[str, int] = {}
    paragraph_samples: dict[str, str] = {}
    for raw_paragraph in re.findall(r"<p\b[^>]*>(.*?)</p>", html or "", flags=re.IGNORECASE | re.DOTALL):
        text = _clean_inline_text(raw_paragraph)
        normalized = re.sub(r"\s+", " ", text).strip().lower()
        if len(normalized) < 120:
            continue
        paragraph_counts[normalized] = paragraph_counts.get(normalized, 0) + 1
        paragraph_samples.setdefault(normalized, text)
    duplicates: list[str] = []
    for normalized, count in paragraph_counts.items():
        if count > 1:
            sample = paragraph_samples[normalized][:140].rstrip()
            duplicates.append(f"{count}× {sample}")
    return duplicates


def _find_repeated_long_snippets(html: str) -> list[str]:
    snippets: list[str] = []
    for raw_paragraph in re.findall(r"<p\b[^>]*>(.*?)</p>", html or "", flags=re.IGNORECASE | re.DOTALL):
        words = re.findall(r"\b[a-z0-9']+\b", _clean_inline_text(raw_paragraph).lower())
        if len(words) < 45:
            continue
        window_counts: dict[str, int] = {}
        for index in range(0, len(words) - 7):
            snippet = " ".join(words[index : index + 8])
            if len(set(snippet.split())) < 6:
                continue
            window_counts[snippet] = window_counts.get(snippet, 0) + 1
        repeated = sorted(((count, snippet) for snippet, count in window_counts.items() if count >= 8), reverse=True)
        if repeated:
            count, snippet = repeated[0]
            snippets.append(f"{count}× {snippet[:120]}")
    return snippets


def _find_repeated_sentences(html: str) -> list[str]:
    sentence_counts: dict[str, int] = {}
    sentence_samples: dict[str, str] = {}
    text = _html_to_text(html)
    for raw_sentence in re.split(r"(?<=[.!?])\s+", text):
        cleaned = _clean_inline_text(raw_sentence)
        normalized = re.sub(r"\s+", " ", cleaned).strip().lower()
        if len(normalized.split()) < 14:
            continue
        sentence_counts[normalized] = sentence_counts.get(normalized, 0) + 1
        sentence_samples.setdefault(normalized, cleaned)
    repeated: list[str] = []
    for normalized, count in sentence_counts.items():
        if count > 2:
            sample = sentence_samples[normalized][:140].rstrip()
            repeated.append(f"{count}× {sample}")
    return repeated


def _normalize_sentence_template(sentence: str) -> str:
    normalized = _clean_inline_text(sentence).lower()
    normalized = re.sub(r"^[a-z][a-z\- ]{0,40}\b(?:point|detail|tip|reason|sign|mistake|lesson|question|step)\s+\d+\s*:\s*", "", normalized)
    normalized = re.sub(r"\s+", " ", normalized).strip()
    return normalized


def _find_repeated_sentence_templates(html: str) -> list[str]:
    template_counts: dict[str, int] = {}
    template_samples: dict[str, str] = {}
    text = _html_to_text(html)
    for raw_sentence in re.split(r"(?<=[.!?])\s+", text):
        cleaned = _clean_inline_text(raw_sentence)
        normalized = _normalize_sentence_template(cleaned)
        if len(normalized.split()) < 14:
            continue
        if normalized == cleaned.lower().strip():
            continue
        template_counts[normalized] = template_counts.get(normalized, 0) + 1
        template_samples.setdefault(normalized, cleaned)
    repeated: list[str] = []
    for normalized, count in template_counts.items():
        if count > 2:
            sample = template_samples[normalized][:140].rstrip()
            repeated.append(f"{count}× {sample}")
    return repeated


def _normalize_heading_swapped_paragraph(paragraph: str, headings: list[str]) -> str:
    normalized = re.sub(r"\s+", " ", _clean_inline_text(paragraph).lower()).strip()
    for heading in headings:
        clean_heading = re.sub(r"\s+", " ", _clean_inline_text(heading).lower()).strip()
        if len(clean_heading.split()) < 3:
            continue
        normalized = re.sub(re.escape(clean_heading), "<section>", normalized)
    normalized = re.sub(r"\s+", " ", normalized).strip()
    return normalized


def _find_heading_swapped_paragraphs(html: str) -> list[str]:
    headings = [
        _clean_inline_text(raw_heading)
        for raw_heading in re.findall(r"<h2\b[^>]*>(.*?)</h2>", html or "", flags=re.IGNORECASE | re.DOTALL)
        if _clean_inline_text(raw_heading)
    ]
    if not headings:
        return []
    paragraph_counts: dict[str, int] = {}
    paragraph_samples: dict[str, str] = {}
    for raw_paragraph in re.findall(r"<p\b[^>]*>(.*?)</p>", html or "", flags=re.IGNORECASE | re.DOTALL):
        text = _clean_inline_text(raw_paragraph)
        normalized = _normalize_heading_swapped_paragraph(text, headings)
        if len(normalized) < 160 or "<section>" not in normalized:
            continue
        paragraph_counts[normalized] = paragraph_counts.get(normalized, 0) + 1
        paragraph_samples.setdefault(normalized, text)
    repeated: list[str] = []
    for normalized, count in paragraph_counts.items():
        if count > 1:
            sample = paragraph_samples[normalized][:140].rstrip()
            repeated.append(f"{count}× {sample}")
    return repeated


def _find_editorial_meta_sentences(html: str) -> list[str]:
    text = _html_to_text(html)
    meta_patterns = [
        r"\bthis article\b",
        r"\bthe article should\b",
        r"\ba useful article\b",
        r"\bthis section\b",
        r"\bthe cta should\b",
        r"\bthe hook should\b",
        r"\bsearch intent\b",
        r"\btarget keyword\b",
        r"\bpeople who search this keyword\b",
        r"\bpeople searching this keyword\b",
        r"\bthe piece should\b",
        r"\bthe content feel[s]?\b",
        r"\bthe article earns\b",
    ]
    matches: list[str] = []
    for raw_sentence in re.split(r"(?<=[.!?])\s+", text):
        cleaned = _clean_inline_text(raw_sentence)
        lowered = cleaned.lower()
        if len(cleaned.split()) < 8:
            continue
        if any(re.search(pattern, lowered) for pattern in meta_patterns):
            matches.append(cleaned[:160].rstrip())
    return matches[:3]


def _split_html_sections(html: str) -> list[str]:
    html = html or ""
    heading_matches = list(re.finditer(r"<h2\b[^>]*>.*?</h2>", html, flags=re.IGNORECASE | re.DOTALL))
    if not heading_matches:
        return [html] if html.strip() else []
    sections: list[str] = []
    intro = html[: heading_matches[0].start()].strip()
    if intro:
        sections.append(intro)
    for index, match in enumerate(heading_matches):
        start = match.start()
        end = heading_matches[index + 1].start() if index + 1 < len(heading_matches) else len(html)
        chunk = html[start:end].strip()
        if chunk:
            sections.append(chunk)
    return sections


def _product_section_mentions(html: str, product_name: str = PRODUCT_NAME) -> list[int]:
    product_lower = product_name.lower()
    matches: list[int] = []
    for index, section_html in enumerate(_split_html_sections(html)):
        if product_lower in _html_to_text(section_html).lower():
            matches.append(index)
    return matches


def _first_product_mention_word_index(html: str, product_name: str = PRODUCT_NAME) -> int | None:
    words = re.findall(r"\b\w+\b", _html_to_text(html))
    product_lower = product_name.lower()
    for index, word in enumerate(words):
        if word.lower() == product_lower:
            return index
    return None


def _tail_word_window(html: str, size: int = 220) -> str:
    words = re.findall(r"\b\w+\b", _html_to_text(html))
    if not words:
        return ""
    return " ".join(words[-size:]).lower()


def _max_product_free_section_gap(html: str, product_name: str = PRODUCT_NAME) -> int:
    product_lower = product_name.lower()
    max_gap = 0
    current_gap = 0
    for section_html in _split_html_sections(html):
        if product_lower in _html_to_text(section_html).lower():
            max_gap = max(max_gap, current_gap)
            current_gap = 0
        else:
            current_gap += 1
    return max(max_gap, current_gap)


def _validate_post(post: dict[str, Any], *, config: BlogPipelineConfig, brief: dict[str, Any]) -> list[str]:
    issues: list[str] = []
    title = str(post.get("title") or "").strip()
    content = str(post.get("content") or "")
    image = str(post.get("image") or "").strip()
    text = _html_to_text(content)
    text_lower = text.lower()
    text_validation_lower = _strip_approved_product_fact_spans(text_lower)
    title_lower = title.lower()
    word_count = _estimate_word_count(content)

    if not title:
        issues.append("Add a specific headline.")
    if not str(post.get("slug") or "").strip():
        issues.append("Add a non-empty slug.")
    if not content.strip():
        issues.append("Add article HTML content.")
    if word_count < 1500:
        issues.append(f"Expand the article to at least 1500 words. Current word count: {word_count}.")
    if word_count > 3000:
        issues.append(f"Trim the article below 3000 words. Current word count: {word_count}.")
    h2_count = content.lower().count("<h2")
    if h2_count < 5:
        issues.append("Use exactly 5 H2 sections so the article is skimmable without sprawling or collapsing into an underdeveloped draft.")
    if h2_count > 5:
        issues.append("Cut the article back to exactly 5 H2 sections so it stays focused instead of meandering through interchangeable sections.")
    article_h2_headings = [
        _clean_inline_text(raw_heading)
        for raw_heading in re.findall(r"<h2\b[^>]*>(.*?)</h2>", content, flags=re.IGNORECASE | re.DOTALL)
        if _clean_inline_text(raw_heading)
    ]
    generic_article_headings = _find_generic_headings(article_h2_headings)
    if generic_article_headings:
        issues.append(
            "Replace generic article headings with sharper sections that directly advance the reader's decision and the SomeSome recommendation. Problem headings: "
            + "; ".join(generic_article_headings[:3])
        )
    long_article_headings = _long_heading_samples(article_h2_headings)
    if long_article_headings:
        issues.append(
            "Tighten overly long H2 headings so the article sounds deliberate instead of meandering through full-sentence SEO scaffolding. Long headings: "
            + "; ".join(long_article_headings[:3])
        )
    product_heading_positions = [index for index, heading in enumerate(article_h2_headings) if PRODUCT_NAME.lower() in heading.lower()]
    if PRODUCT_NAME.lower() in text_lower and len(product_heading_positions) < 3:
        issues.append(f"Name {PRODUCT_NAME} in at least 3 H2 headings so the article structure itself carries the recommendation instead of hiding it in body paragraphs.")
    if PRODUCT_NAME.lower() in text_lower and article_h2_headings and not any(position < 3 for position in product_heading_positions):
        issues.append(f"Bring {PRODUCT_NAME} into one of the first 3 H2 headings so the article stops feeling generic before the midpoint.")
    if PRODUCT_NAME.lower() not in text_lower:
        issues.append(f"Mention {PRODUCT_NAME} naturally in the body and CTA.")
    else:
        first_product_mention = _first_product_mention_word_index(content)
        if first_product_mention is not None and first_product_mention > 260:
            issues.append(f"Integrate {PRODUCT_NAME} earlier so the recommendation is part of the core argument, not a late add-on.")
        product_sections = _product_section_mentions(content)
        if len(product_sections) < 4:
            issues.append(f"Weave {PRODUCT_NAME} through at least 4 sections or section groups so the product fit feels integrated instead of isolated to one block.")
        if _product_early_h2_section_mentions(content) < 3:
            issues.append(f"Bring {PRODUCT_NAME} into the opening of at least 3 H2 sections so the article stops using generic setup paragraphs before finally naming the recommendation.")
        if _max_product_free_section_gap(content) > 1:
            issues.append(f"Do not let {PRODUCT_NAME} disappear for more than 1 consecutive section group; keep the product framing active through the middle of the article.")
        if PRODUCT_NAME.lower() not in _tail_word_window(content, size=220):
            issues.append(f"End with a clearer conclusion/CTA that naturally brings the reader back to {PRODUCT_NAME}.")
        if not _has_middle_section_fit_limit_signal_in_article(content):
            issues.append(f"Include one honest middle section or passage about who {PRODUCT_NAME} may fit, who it may not fit, or what it will not solve so the article does not read like repetitive product praise.")
    primary_keyword_source = brief.get("primary_keyword") or (config.keywords.split(",")[0] if config.keywords else "")
    primary_keyword = str(primary_keyword_source).strip().lower()
    if primary_keyword and primary_keyword not in text_lower and primary_keyword not in title_lower:
        issues.append(f"Work the primary keyword '{primary_keyword}' into the headline or body more clearly.")
    if not image or not image.startswith("http"):
        issues.append("Include a relevant image URL in the post JSON.")
    if len(str(post.get("imageAlt") or "").strip()) < 8:
        issues.append("Add useful alt text for the image.")
    if PRODUCT_NAME.lower() not in str(post.get("excerpt") or "").lower() and PRODUCT_NAME.lower() not in text_lower:
        issues.append(f"Make the commercial angle more clearly tied back to {PRODUCT_NAME}.")
    duplicate_paragraphs = _find_duplicate_paragraphs(content)
    if duplicate_paragraphs:
        issues.append(
            "Remove duplicated long paragraphs or repetitive filler so the article feels intentionally written instead of padded. Duplicates: "
            + "; ".join(duplicate_paragraphs[:3])
        )
    repeated_long_snippets = _find_repeated_long_snippets(content)
    if repeated_long_snippets:
        issues.append(
            "Remove repeated long phrase padding inside paragraphs so the article reads like original prose instead of an inflated AI loop. Repeated snippets: "
            + "; ".join(repeated_long_snippets[:3])
        )
    repeated_sentences = _find_repeated_sentences(content)
    if repeated_sentences:
        issues.append(
            "Remove repeated long sentences across the article so it does not recycle the same point verbatim in multiple sections. Repeated sentences: "
            + "; ".join(repeated_sentences[:3])
        )
    repeated_templates = _find_repeated_sentence_templates(content)
    if repeated_templates:
        issues.append(
            "Remove numbered or label-swapped sentence templates that repeat the same core sentence across sections. Rewrite those sections with genuinely distinct prose instead of changing only counters or lead-in labels. Repeated templates: "
            + "; ".join(repeated_templates[:3])
        )
    heading_swapped_paragraphs = _find_heading_swapped_paragraphs(content)
    if heading_swapped_paragraphs:
        issues.append(
            "Remove section-bridge paragraphs that reuse the same long template with only the heading swapped in. Replace that meta-writing filler with section-specific insights that actually advance the article. Repeated templates: "
            + "; ".join(heading_swapped_paragraphs[:3])
        )
    generic_bloat_patterns = [
        r"\bomegle legacy\b",
        r"\bmobile[- ]first design\b",
        r"\bswipe culture\b",
        r"\bvolume over depth\b",
        r"\bpeak hours?\b",
        r"\bcommunity size\b",
        r"\bskip button placement\b",
        r"\bfeedback loops?\b",
    ]
    matched_generic_bloat = [pattern for pattern in generic_bloat_patterns if re.search(pattern, text_lower)]
    if len(matched_generic_bloat) >= 2:
        issues.append("Cut generic platform-theory filler and keep the article closer to the reader's decision about SomeSome. Broad category-analysis talking points are making the piece feel meandering instead of product-relevant.")
    editorial_meta_sentences = _find_editorial_meta_sentences(content)
    if editorial_meta_sentences:
        issues.append(
            "Remove editorial/meta-writing sentences that talk about the article, keyword strategy, or revision process instead of speaking directly to the reader. Examples: "
            + "; ".join(editorial_meta_sentences)
        )
    unsupported_claim_patterns = [
        r"\bi tested\b",
        r"\bi spent real time testing\b",
        r"\bafter testing every\b",
        r"\bwe tested\b",
        r"\bi downloaded\b",
        r"\bi went on\b",
        r"\btested against \d+\b",
        r"\banaly(?:s|z)ed \d+\b",
        r"\bcompared \d+\b",
        r"\bour analysis of \d+\b",
        r"\bafter extensive testing\b",
        r"\bafter testing dozens\b",
        r"\bafter reviewing dozens\b",
        r"\bdozens of options\b",
        r"\bwait times? of \d+\b",
        r"\b\d+% bot\b",
        r"\b\d+ percent bot\b",
        r"\b\d+[\d,]* users\b",
        r"\b\d+[\d,]* people online\b",
    ]
    if any(re.search(pattern, text_validation_lower) for pattern in unsupported_claim_patterns):
        issues.append("Avoid unsupported testing claims, fake counts, or made-up quantitative comparisons unless the brief actually provides that evidence.")

    invented_product_claim_patterns = [
        r"somesome[^.\n]{0,140}\bhuman moderation\b",
        r"\bhuman moderation\b[^.\n]{0,140}somesome",
        r"somesome[^.\n]{0,140}\bmoderation team\b",
        r"\bmoderation team\b[^.\n]{0,140}somesome",
        r"somesome[^.\n]{0,140}\bdiverse user base\b",
        r"\bdiverse user base\b[^.\n]{0,140}somesome",
        r"somesome[^.\n]{0,140}\bprofile verification\b",
        r"\bprofile verification\b[^.\n]{0,140}somesome",
        r"somesome[^.\n]{0,140}\banti-bot\b",
        r"\banti-bot\b[^.\n]{0,140}somesome",
        r"somesome[^.\n]{0,140}\bverification\b",
        r"\bverification\b[^.\n]{0,140}somesome",
        r"somesome[^.\n]{0,140}\bsafe and controlled environment\b",
        r"\bsafe and controlled environment\b[^.\n]{0,140}somesome",
        r"somesome[^.\n]{0,140}\bdesigned to keep\b",
        r"\bdesigned to keep\b[^.\n]{0,140}somesome",
        r"somesome[^.\n]{0,140}\bwas built\b",
        r"\bwas built\b[^.\n]{0,140}somesome",
        r"somesome[^.\n]{0,140}\bbuilt for\b",
        r"\bbuilt for\b[^.\n]{0,140}somesome",
        r"somesome[^.\n]{0,140}\bfrom day one\b",
        r"\bfrom day one\b[^.\n]{0,140}somesome",
        r"somesome[^.\n]{0,140}\brecognized\b",
        r"\brecognized\b[^.\n]{0,140}somesome",
        r"somesome[^.\n]{0,140}\bfocuses on creating conditions\b",
        r"\bfocuses on creating conditions\b[^.\n]{0,140}somesome",
        r"somesome[^.\n]{0,140}\battracts users who\b",
        r"\battracts users who\b[^.\n]{0,140}somesome",
        r"somesome[^.\n]{0,140}\buser base\b",
        r"\buser base\b[^.\n]{0,140}somesome",
        r"somesome[^.\n]{0,140}\busers tend to\b",
        r"\busers tend to\b[^.\n]{0,140}somesome",
        r"somesome[^.\n]{0,140}\bpeople show up\b",
        r"\bpeople show up\b[^.\n]{0,140}somesome",
        r"somesome[^.\n]{0,140}\bcultural norm\b",
        r"\bcultural norm\b[^.\n]{0,140}somesome",
        r"somesome[^.\n]{0,140}\bconversation-minded users\b",
        r"\bconversation-minded users\b[^.\n]{0,140}somesome",
        r"somesome[^.\n]{0,140}\bdesign philosophy\b",
        r"\bdesign philosophy\b[^.\n]{0,140}somesome",
        r"somesome[^.\n]{0,140}\bself-selection\b",
        r"\bself-selection\b[^.\n]{0,140}somesome",
        r"somesome[^.\n]{0,140}\bhigher engagement\b",
        r"\bhigher engagement\b[^.\n]{0,140}somesome",
        r"somesome[^.\n]{0,140}\bsmaller scale\b",
        r"\bsmaller scale\b[^.\n]{0,140}somesome",
        r"somesome[^.\n]{0,140}\bpositions itself\b",
        r"\bpositions itself\b[^.\n]{0,140}somesome",
        r"somesome[^.\n]{0,140}\btakes a different approach\b",
        r"\btakes a different approach\b[^.\n]{0,140}somesome",
        r"somesome[^.\n]{0,140}\bcreates? better conditions\b",
        r"\bcreates? better conditions\b[^.\n]{0,140}somesome",
        r"somesome[^.\n]{0,140}\bdifferent expectations\b",
        r"\bdifferent expectations\b[^.\n]{0,140}somesome",
        r"somesome[^.\n]{0,140}\bfocuses on (?:the )?quality of connections\b",
        r"\bfocuses on (?:the )?quality of connections\b[^.\n]{0,140}somesome",
        r"somesome[^.\n]{0,140}\bgives conversations? room to develop\b",
        r"\bgives conversations? room to develop\b[^.\n]{0,140}somesome",
        r"somesome[^.\n]{0,140}\bchance to develop\b",
        r"\bchance to develop\b[^.\n]{0,140}somesome",
        r"somesome[^.\n]{0,140}\bimproves? your odds\b",
        r"\bimproves? your odds\b[^.\n]{0,140}somesome",
        r"somesome[^.\n]{0,140}\bbetter (?:shot|odds)\b",
        r"\bbetter (?:shot|odds)\b[^.\n]{0,140}somesome",
        r"somesome[^.\n]{0,140}\bworks best when\b",
        r"\bworks best when\b[^.\n]{0,140}somesome",
        r"somesome[^.\n]{0,140}\battracts? (?:people|users) who\b",
        r"\battracts? (?:people|users) who\b[^.\n]{0,140}somesome",
        r"somesome[^.\n]{0,140}\bprioriti(?:s|z)es meaningful (?:exchanges|conversations)\b",
        r"\bprioriti(?:s|z)es meaningful (?:exchanges|conversations)\b[^.\n]{0,140}somesome",
        r"somesome[^.\n]{0,140}\bexpectation is actual conversation\b",
        r"\bexpectation is actual conversation\b[^.\n]{0,140}somesome",
        r"somesome[^.\n]{0,140}\busers arrive expecting conversation\b",
        r"\busers arrive expecting conversation\b[^.\n]{0,140}somesome",
        r"somesome[^.\n]{0,140}\bpeople who actually want (?:conversations|to talk)\b",
        r"\bpeople who actually want (?:conversations|to talk)\b[^.\n]{0,140}somesome",
        r"somesome[^.\n]{0,140}\bcreating environments? where (?:connections|conversations) (?:might )?actually last\b",
        r"\bcreating environments? where (?:connections|conversations) (?:might )?actually last\b[^.\n]{0,140}somesome",
        r"somesome[^.\n]{0,140}\busers who choose somesome tend to\b",
        r"somesome[^.\n]{0,140}\btend to be looking for more than\b",
        r"somesome[^.\n]{0,140}\buser base skews toward\b",
        r"\buser base skews toward\b[^.\n]{0,140}somesome",
        r"somesome[^.\n]{0,140}\busers report\b",
        r"\busers report\b[^.\n]{0,140}somesome",
        r"somesome[^.\n]{0,140}\baverage conversation times?\b",
        r"\baverage conversation times?\b[^.\n]{0,140}somesome",
        r"somesome[^.\n]{0,140}\bsmaller user base\b",
        r"\bsmaller user base\b[^.\n]{0,140}somesome",
        r"somesome[^.\n]{0,140}\bcost of skipping\b",
        r"\bcost of skipping\b[^.\n]{0,140}somesome",
        r"somesome[^.\n]{0,140}\blower-volume rhythm\b",
        r"\blower-volume rhythm\b[^.\n]{0,140}somesome",
        r"somesome[^.\n]{0,140}\bdifferent rhythm\b",
        r"\bdifferent rhythm\b[^.\n]{0,140}somesome",
        r"somesome[^.\n]{0,140}\bfewer people are online\b",
        r"\bfewer people are online\b[^.\n]{0,140}somesome",
        r"somesome[^.\n]{0,140}\brunning into the same people\b",
        r"\brunning into the same people\b[^.\n]{0,140}somesome",
        r"somesome[^.\n]{0,140}\bhigher percentage\b",
        r"\bhigher percentage\b[^.\n]{0,140}somesome",
        r"somesome[^.\n]{0,140}\bdesigned for dialogue\b",
        r"\bdesigned for dialogue\b[^.\n]{0,140}somesome",
        r"somesome[^.\n]{0,140}\boff-peak hours\b",
        r"\boff-peak hours\b[^.\n]{0,140}somesome",
        r"somesome[^.\n]{0,140}\bslower pace\b",
        r"\bslower pace\b[^.\n]{0,140}somesome",
        r"somesome[^.\n]{0,140}\bmoves slower\b",
        r"\bmoves slower\b[^.\n]{0,140}somesome",
        r"somesome[^.\n]{0,140}\bfilters? out users\b",
        r"\bfilters? out users\b[^.\n]{0,140}somesome",
        r"somesome[^.\n]{0,140}\bskip button\b",
        r"\bskip button\b[^.\n]{0,140}somesome",
        r"somesome[^.\n]{0,140}\bescape hatches\b",
        r"\bescape hatches\b[^.\n]{0,140}somesome",
        r"somesome[^.\n]{0,140}\bfriction in the connection process\b",
        r"\bfriction in the connection process\b[^.\n]{0,140}somesome",
        r"somesome[^.\n]{0,140}\bactual humans\b",
        r"\bactual humans\b[^.\n]{0,140}somesome",
        r"somesome[^.\n]{0,140}\bbot farms\b",
        r"\bbot farms\b[^.\n]{0,140}somesome",
        r"somesome[^.\n]{0,140}\bboth text and video chat\b",
        r"\bboth text and video chat\b[^.\n]{0,140}somesome",
        r"somesome[^.\n]{0,140}\btext chat options?\b",
        r"\btext chat options?\b[^.\n]{0,140}somesome",
        r"somesome[^.\n]{0,140}\bbest times? to use\b",
        r"\bbest times? to use\b[^.\n]{0,140}somesome",
        r"somesome[^.\n]{0,140}\bguarantee(?:s|d)?\b",
        r"\bguarantee(?:s|d)?\b[^.\n]{0,140}somesome",
        r"somesome[^.\n]{0,140}\b(?:ios|android|app store|play store)\b",
        r"\b(?:ios|android|app store|play store)\b[^.\n]{0,140}somesome",
        r"somesome[^.\n]{0,140}\bcamera and microphone\b",
        r"\bcamera and microphone\b[^.\n]{0,140}somesome",
        r"somesome[^.\n]{0,140}\btext-only\b",
        r"\btext-only\b[^.\n]{0,140}somesome",
        r"somesome[^.\n]{0,140}\bvideo and text options?\b",
        r"\bvideo and text options?\b[^.\n]{0,140}somesome",
        r"somesome[^.\n]{0,140}\bswitch modes\b",
        r"\bswitch modes\b[^.\n]{0,140}somesome",
        r"somesome[^.\n]{0,140}\breporting tool\b",
        r"\breporting tool\b[^.\n]{0,140}somesome",
        r"somesome[^.\n]{0,140}\binterest tags?\b",
        r"\binterest tags?\b[^.\n]{0,140}somesome",
        r"somesome[^.\n]{0,140}\btag field\b",
        r"\btag field\b[^.\n]{0,140}somesome",
        r"somesome[^.\n]{0,140}\bskip button\b",
        r"\bskip button\b[^.\n]{0,140}somesome",
        r"somesome[^.\n]{0,140}\bintention prompt\b",
        r"\bintention prompt\b[^.\n]{0,140}somesome",
        r"somesome[^.\n]{0,140}\bprofile cards?\b",
        r"\bprofile cards?\b[^.\n]{0,140}somesome",
        r"somesome[^.\n]{0,140}\bfilter (?:menu|menus|option|options|control|controls|setting|settings)\b",
        r"\bfilter (?:menu|menus|option|options|control|controls|setting|settings)\b[^.\n]{0,140}somesome",
        r"somesome[^.\n]{0,140}\blayout\b",
        r"\blayout\b[^.\n]{0,140}somesome",
        r"somesome[^.\n]{0,140}\bonboarding\b",
        r"\bonboarding\b[^.\n]{0,140}somesome",
        r"somesome[^.\n]{0,140}\bdiscovery feed\b",
        r"\bdiscovery feed\b[^.\n]{0,140}somesome",
    ]
    if any(re.search(pattern, text_validation_lower) for pattern in invented_product_claim_patterns):
        issues.append(f"Avoid invented {PRODUCT_NAME} feature or policy claims. Keep the positioning high-level unless the brief explicitly provides product specifics.")

    front_matter = " ".join(
        [
            title,
            str(post.get("subtitle") or ""),
            str(post.get("excerpt") or ""),
            str(post.get("slug") or ""),
        ]
    ).lower()
    unsupported_absolute_patterns = [
        r"\bno bots\b",
        r"\bwithout bots\b",
        r"\bbot[- ]free\b",
        r"\bzero bots\b",
        r"\bguaranteed real people\b",
        r"\balways safe\b",
        r"\bcompletely safe\b",
        r"\b100% real\b",
    ]
    if any(re.search(pattern, front_matter) for pattern in unsupported_absolute_patterns):
        issues.append("Avoid unsupported absolute promises like 'no bots', 'bot-free', or guaranteed safety/real people in the title, subtitle, slug, or excerpt.")

    current_year = utc_today()[:4]
    stale_year_match = re.search(r"\b(20\d{2})\b", front_matter)
    if stale_year_match and stale_year_match.group(1) != current_year:
        issues.append(f"Avoid stale year framing in the title, subtitle, slug, or excerpt unless it matches the current year ({current_year}) or the brief explicitly requires it.")
    return issues


def _estimate_word_count(html: str) -> int:
    text = _html_to_text(html)
    return len(re.findall(r"\b\w+\b", text))


def _html_to_text(html: str) -> str:
    text = re.sub(r"<[^>]+>", " ", html or "")
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _serialize_attempt(item: Any) -> dict[str, Any]:
    if hasattr(item, "__dict__"):
        return dict(item.__dict__)
    if isinstance(item, dict):
        return dict(item)
    return {"value": str(item)}


def _extract_json_with_repair(
    client: OpenRouterClient,
    *,
    text: str,
    role: str,
    primary_model: str,
    fallback_models: list[str],
    attempts: list[ModelAttemptLog],
) -> dict[str, Any]:
    try:
        payload = extract_json(text)
    except json.JSONDecodeError:
        repaired_text, _used_model = _chat_with_fallbacks(
            client,
            role=f"{role}_json_repair",
            primary_model=primary_model,
            fallback_models=fallback_models,
            system=(
                "You repair malformed JSON produced by another model. "
                "Return one valid JSON object only. Preserve the original meaning and text as closely as possible. "
                "Do not add markdown fences, commentary, new claims, or new sections."
            ),
            user=(
                "The following model output was intended to be a single JSON object but failed to parse. "
                "Repair it into valid JSON while keeping the existing fields and wording as intact as possible. "
                "If HTML appears inside string values, preserve it. Return JSON only.\n\n"
                f"Malformed output:\n{text}"
            ),
            temperature=0.0,
            max_tokens=4200,
            attempts=attempts,
        )
        try:
            payload = extract_json(repaired_text)
        except json.JSONDecodeError:
            payload = salvage_post_json(repaired_text) if role.startswith("writer_") else None
            if payload is None:
                payload = salvage_post_json(text) if role.startswith("writer_") else None
            if payload is None:
                raise
    if not isinstance(payload, dict):
        raise ValueError(f"Expected JSON object for {role}, got {type(payload).__name__}")
    return payload


def _dedupe_models(primary_model: str, fallback_models: list[str]) -> list[str]:
    ordered = [primary_model, *fallback_models]
    deduped: list[str] = []
    seen: set[str] = set()
    for model in ordered:
        clean = str(model or "").strip()
        if clean and clean not in seen:
            deduped.append(clean)
            seen.add(clean)
    return deduped


def _chat_with_fallbacks(
    client: OpenRouterClient,
    *,
    role: str,
    primary_model: str,
    fallback_models: list[str],
    system: str,
    user: str,
    temperature: float,
    max_tokens: int,
    attempts: list[ModelAttemptLog],
) -> tuple[str, str]:
    errors: list[str] = []
    for model in _dedupe_models(primary_model, fallback_models):
        attempted_token_limits: set[int] = set()
        token_budget = max_tokens
        while token_budget not in attempted_token_limits:
            attempted_token_limits.add(token_budget)
            try:
                text = client.chat(model=model, system=system, user=user, temperature=temperature, max_tokens=token_budget)
                attempts.append(ModelAttemptLog(role=role, requested_model=primary_model, used_model=model, ok=True))
                return text, model
            except OpenRouterError as exc:
                attempts.append(ModelAttemptLog(role=role, requested_model=primary_model, used_model=model, ok=False, error=str(exc)))
                errors.append(f"{model} @max_tokens={token_budget}: {exc}")
                affordable_limit = exc.affordable_max_tokens
                if exc.is_affordability_issue and affordable_limit and affordable_limit >= 256 and affordable_limit < token_budget:
                    token_budget = affordable_limit
                    continue
                if not exc.is_retryable:
                    break
                break
    raise OpenRouterError(f"All model attempts failed for {role}: {' | '.join(errors)}")


def _publish_to_framer(*, base_dir: str | Path, post_path: Path, mode: str) -> None:
    root = Path(base_dir)
    subprocess.run(["npm", "run", "framer:add", "--", str(post_path)], cwd=root, check=True)
    publish_script = {
        "status": "framer:publish:status",
        "preview": "framer:publish:preview",
        "live": "framer:publish:live",
    }.get(mode, "framer:publish:live")
    subprocess.run(["npm", "run", publish_script], cwd=root, check=True)


def _env_or_default(name: str, default: str) -> str:
    value = os.environ.get(name, "").strip()
    legacy_defaults = {
        "WRITER_MODEL": LEGACY_WRITER_DEFAULTS,
        "REVIEWER_MODEL": LEGACY_REVIEWER_DEFAULTS,
    }.get(name, set())
    if not value or value in legacy_defaults:
        return default
    return value


def _env_model_list(name: str) -> list[str]:
    return [item.strip() for item in os.environ.get(name, "").split(",") if item.strip()]


def main(argv: list[str] | None = None) -> int:
    import argparse

    base_dir = Path(__file__).resolve().parents[2]
    load_dotenv(base_dir / ".env")

    parser = argparse.ArgumentParser(description="Generate, review, revise, and optionally publish a Framer blog post.")
    parser.add_argument("--topic", required=True)
    parser.add_argument("--audience", default="young adults looking for spontaneous online conversations")
    parser.add_argument("--tone", default="authentic, direct, and genuinely useful")
    parser.add_argument("--cta", default=f"Naturally invite the reader to try {PRODUCT_NAME}.")
    parser.add_argument("--keywords", default="")
    parser.add_argument("--writer-model", default=_env_or_default("WRITER_MODEL", DEFAULT_WRITER_MODEL))
    parser.add_argument("--reviewer-model", default=_env_or_default("REVIEWER_MODEL", DEFAULT_REVIEWER_MODEL))
    parser.add_argument("--max-rounds", type=int, default=3)
    parser.add_argument("--threshold", type=float, default=8.0)
    parser.add_argument("--publish", action="store_true")
    parser.add_argument("--framer-publish-mode", choices=["status", "preview", "live"], default="live")
    parser.add_argument("--strict-validation", action="store_true")
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
        strict_validation=args.strict_validation,
        writer_fallback_models=_env_model_list("WRITER_MODEL_FALLBACKS") or DEFAULT_WRITER_FALLBACKS,
        reviewer_fallback_models=_env_model_list("REVIEWER_MODEL_FALLBACKS") or DEFAULT_REVIEWER_FALLBACKS,
    )
    run_dir = run_pipeline(config, base_dir=base_dir)
    print(str(run_dir))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
