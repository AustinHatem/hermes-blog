# hermes-blog

A lightweight ARIS-style blog pipeline:
- one model writes
- a different model reviews
- the writer revises from the review
- final post can be pushed to Framer and published live

This project is inspired by the workflow shape of Auto-claude-code-research-in-sleep, but adapted for blog writing instead of ML research.

## What it does

The pipeline imitates the useful ARIS pattern:
1. writer model creates a brief
2. writer model creates an outline
3. writer model writes a draft
4. reviewer model scores the draft and lists minimum fixes
5. writer model revises
6. repeat for a few rounds
7. save all artifacts in `runs/`
8. optionally publish to Framer

## Key idea

This repo keeps the same core architecture that makes ARIS useful:
- executor model and reviewer model are different
- reviewer has a narrow, consistent contract
- every run saves state and artifacts
- publishing is a final step, not mixed into drafting

## Files

- `blog_pipeline.py` — CLI entrypoint
- `src/hermes_blog/openrouter_client.py` — OpenRouter calls
- `src/hermes_blog/reviewer.py` — review + revise loop
- `src/hermes_blog/pipeline.py` — end-to-end orchestration
- `framer/*.mjs` — Framer CMS add/publish helpers

## Setup

1. Clone the repo
2. Create `.env` from the example:

```bash
cp .env.example .env
```

3. Fill in:
- `OPENROUTER_API_KEY`
- `FRAMER_API_KEY`
- `FRAMER_PROJECT_URL`
- `FRAMER_COLLECTION_NAME`

4. Install dependencies:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
npm install
```

## Generate a blog post

```bash
python3 blog_pipeline.py \
  --topic "Why random video chat apps are growing again" \
  --audience "creators and startup founders" \
  --tone "clear, persuasive, startup-savvy" \
  --cta "Point readers toward SomeSome" \
  --keywords "random video chat, Omegle alternatives, creator growth"
```

This creates a new run folder under `runs/` with:
- `brief.json`
- `outline.md`
- `draft_round_1.json`
- `review_round_1.json`
- further revision files if needed
- `final_post.json`
- `summary.json`

## Generate and publish to Framer

```bash
python3 blog_pipeline.py \
  --topic "Why random video chat apps are growing again" \
  --audience "creators and startup founders" \
  --tone "clear, persuasive, startup-savvy" \
  --cta "Point readers toward SomeSome" \
  --keywords "random video chat, Omegle alternatives, creator growth" \
  --publish
```

## Framer-only helpers

List collections:

```bash
npm run framer:list
```

Add a prepared post JSON:

```bash
npm run framer:add -- ./runs/<run>/final_post.json
```

Add and publish live immediately:

```bash
npm run framer:add:publish -- ./runs/<run>/final_post.json
```

Publish existing staged changes:

```bash
npm run framer:publish:live
```

## Default model split

Defaults are intentionally cross-model:
- writer: `meta-llama/llama-3.3-70b-instruct:free`
- reviewer: `google/gemma-4-31b-it:free`

You can override them:

```bash
python3 blog_pipeline.py \
  --topic "..." \
  --writer-model "meta-llama/llama-3.3-70b-instruct:free" \
  --reviewer-model "google/gemma-4-31b-it:free"
```

## Notes

- Secrets are read from `.env` and `.env` is gitignored.
- `runs/` is gitignored so generated drafts and publish artifacts are not committed.
- Framer scripts use `node@22` through `npx` because `framer-api` expects Node 22+.

## Typical workflow

1. Run `blog_pipeline.py`
2. Inspect the generated `runs/.../final_post.json`
3. Publish to Framer with `--publish` or `npm run framer:add:publish`

## Future improvements

- add reusable prompt templates under `prompts/`
- add internal linking and SEO meta generation
- add topic backlog / idea memory
- add a longer async reviewer bridge if you want a closer MCP-style ARIS setup
