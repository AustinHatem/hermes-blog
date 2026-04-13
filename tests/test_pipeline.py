import json
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from hermes_blog.openrouter_client import BedrockClient, OpenRouterClient, OpenRouterError
from hermes_blog.pipeline import (
    BlogPipelineConfig,
    _apply_post_defaults,
    _chat_with_fallbacks,
    _env_or_default,
    _estimate_word_count,
    _extract_json_with_repair,
    _find_duplicate_paragraphs,
    _find_repeated_long_snippets,
    _find_heading_swapped_paragraphs,
    _find_editorial_meta_sentences,
    _find_repeated_sentence_templates,
    _find_repeated_sentences,
    _generate_brief,
    _generate_outline,
    _has_fit_limit_signal,
    _product_early_h2_section_mentions,
    _sanitize_front_matter_text,
    _validate_brief,
    _validate_outline,
    _validate_post,
    run_pipeline,
)
from hermes_blog.utils import extract_json, salvage_post_json


class FakeClient:
    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    def chat(self, **kwargs):
        model = kwargs["model"]
        self.calls.append(model)
        response = self.responses[model]
        if isinstance(response, Exception):
            raise response
        return response


class BudgetAwareClient:
    def __init__(self, affordable_limit, success_text='{"ok": true}'):
        self.affordable_limit = affordable_limit
        self.success_text = success_text
        self.calls = []

    def chat(self, **kwargs):
        self.calls.append((kwargs["model"], kwargs["max_tokens"]))
        if kwargs["max_tokens"] > self.affordable_limit:
            raise OpenRouterError(
                f"This request requires more credits, or fewer max_tokens. You requested up to {kwargs['max_tokens']} tokens, but can only afford {self.affordable_limit}.",
                status_code=402,
                payload={"error": {"code": 402}},
            )
        return self.success_text


class PromptSequencedClient:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def chat(self, **kwargs):
        self.calls.append(kwargs["user"][:80])
        if not self.responses:
            raise AssertionError("No more queued responses for PromptSequencedClient")
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


class EndToEndFakeClient:
    def __init__(self):
        self.calls = []
        sections = [
            (
                "Why late-night random chat still matters",
                [
                    "Late-night random chat still matters because the mood is different after midnight. People are less performative, less rushed, and a little more willing to let a strange conversation breathe instead of treating every stranger like disposable content.",
                    "That matters for readers hunting for the best omegle alternative for late-night conversations. They usually are not chasing novelty for its own sake. They want the small chance of stumbling into a conversation that feels funny, calm, confessional, or unexpectedly real.",
                    "That emotional reality matters as much as the practical comparison. Most readers are trying to recover a specific feeling, not just compare feature lists.",
                    "Honesty about disappointment matters because most people searching this topic have already bounced through enough random video chat apps to know when a recommendation is bluffing.",
                    "Ground the advice in lived internet behavior: instant skips, chaotic attention, fake authority, and the feeling that most platforms are optimized for motion instead of connection.",
                    "Late-night readers also need permission to admit what they are really chasing. Usually it is not a feature list or a cleaner interface. It is the slim chance that one stranger will stay long enough for the conversation to become funny, personal, and weirdly memorable instead of collapsing into another instant skip.",
                ],
            ),
            (
                "Why SomeSome belongs in this conversation early",
                [
                    "SomeSome belongs in the article early because the reader is not looking for a giant category tour. They are looking for an honest next thing to try when other random chat apps keep feeling frantic, empty, or embarrassing.",
                    "That does not require inflated promises. SomeSome can be framed modestly as a product worth trying when the reader wants random chat to feel less disposable and less like a speed-run through strangers.",
                    "The useful move here is to connect the product to the frustration quickly. If every recommendation waits until the final paragraph to mention SomeSome, the article reads like generic filler with a brand name pasted on top.",
                    "Bringing SomeSome into the argument early also keeps the article commercially useful without sounding salesy. The recommendation starts to feel earned because it grows out of the actual problem instead of interrupting it.",
                    "That is especially important for skeptical readers. They have seen too many pages that pretend to review the whole market just to smuggle in a pitch at the end.",
                    "A sharper article says the quiet part out loud: if your real frustration is disposable chat, SomeSome is worth trying earlier in the search journey than another generic listicle full of vague platform talk.",
                ],
            ),
            (
                "What SomeSome helps clarify about disposable chats",
                [
                    "SomeSome helps clarify the real decision the reader is making. The question is not which app looks most impressive on paper. The question is which product feels worth opening when you are tired of chats that die before they become anything.",
                    "That shift matters because it turns a sloppy comparison into a cleaner standard. You stop asking for a perfect app and start asking whether the experience feels patient enough to let one good conversation happen.",
                    "Once that standard is clear, a lot of generic article habits start looking useless. Fake rankings, made-up scores, and broad safety lectures usually tell the reader less than one honest paragraph about why chats keep feeling hollow.",
                    "SomeSome fits naturally inside that standard because the article can describe it as an honest next try for people who miss spontaneous conversations and want a less disposable random-chat mood.",
                    "That framing stays truthful because it does not pretend to know hidden product mechanics. It just ties the recommendation back to the reader's actual frustration.",
                    "Keep returning to that standard instead of wandering into broad market talk. The moment that standard gets buried, SomeSome disappears from the argument and the recommendation starts feeling generic again.",
                ],
            ),
            (
                "When SomeSome may not be your move",
                [
                    "SomeSome may not be your move if what you really want is maximum volume, endless novelty, or a rapid-fire browse experience where you bounce through strangers in seconds. The honest recommendation gets sharper the moment the article says that out loud.",
                    "That matters because not every disappointed random chat reader wants the same thing next. Some people still prefer a chaotic, high-throughput app because they want quick entertainment more than a conversation that settles in.",
                    "So the useful framing is not that SomeSome magically fixes the category. It is that SomeSome makes more sense when you are tired of disposable chats and want a more grounded next try, while other readers may still prefer something faster and less patient.",
                    "That kind of fit limit keeps the article believable. It sounds like a real recommendation instead of a generic pitch trying to force every frustrated reader into the same answer.",
                    "It also helps the middle of the article stay tight. Instead of spinning off into broad theory, the piece can keep asking one grounded question: who does this make SomeSome more worth trying for, and who will still want something else?",
                    "If the answer is neither, the section probably does not belong. That keeps the product integration active and the prose sharper.",
                ],
            ),
            (
                "Why SomeSome is the honest next step tonight",
                [
                    "SomeSome becomes the honest next step when the article ends the same way it started: with the reader's real frustration, not with a fake grand finale. They are not looking for internet destiny. They are looking for one better shot at a real exchange.",
                    "That is why the best CTA stays quiet and direct. Try SomeSome the next time you want random video chat to feel less empty and more like an actual conversation.",
                    "The recommendation lands because it is proportionate. It does not promise perfection, claim special insider knowledge, or pretend every chat will suddenly become memorable.",
                    "It simply gives the reader a next move that fits the problem the article spent its time naming clearly.",
                    "That kind of close also helps the whole piece feel more human. Readers in this category usually trust recognition more than hype.",
                    "So the ending should leave them with a believable mood, not a slogan: if you still want spontaneous conversations online, SomeSome is worth trying before you give up on the category completely.",
                ],
            ),
        ]
        content_parts = [
            "<p>Late-night random chat can still feel weirdly special when the conversation lasts long enough for two strangers to relax and stop performing.</p>",
            "<p>If you are searching for the best omegle alternative because every other app feels dead, frantic, or robotic, you are probably not asking for more features. You are asking for a less disposable place to keep trying for a real conversation, which is exactly why SomeSome belongs in the conversation early.</p>",
            "<p>That frustration is specific. It is the feeling of opening another app with a little hope, getting three instant skips in a row, and realizing the whole experience is starting to feel emotionally flat before it even gets interesting.</p>",
            "<p>So the focus stays on conversation quality instead of fake certainty. Most readers have already tried enough random video chat apps to know when the recommendation feels copied, overbuilt, or weirdly detached from how these products actually feel in real life, and SomeSome only works when it stays tied to that frustration.</p>",
            "<p>That skepticism matters. A believable recommendation has to sound grounded in the actual mood of random chat instead of pretending there is a perfect answer hiding behind fake certainty or inflated feature talk. Readers can feel the difference almost immediately.</p>",
            "<p>That is also why tighter structure matters here. Keep circling back to the same real question: what do you try next when random chat still sounds fun in theory but keeps feeling hollow in practice?</p>",
        ]
        for heading, paragraphs in sections:
            content_parts.append(f"<h2>{heading}</h2>")
            content_parts.extend(f"<p>{paragraph}</p>" for paragraph in paragraphs)
        content_parts.append("<p>The big takeaway is simple: people searching for the best omegle alternative are usually not asking for an app that looks more impressive on paper. They are asking for a better chance at a conversation that survives the first awkward seconds, settles into a real rhythm, and leaves them feeling like they met a person instead of another piece of internet noise. That is the emotional frame that makes SomeSome relevant here.</p>")
        content_parts.append("<p>That frame matters because it keeps the recommendation honest. The strongest reason to try SomeSome is not that somebody promised impossible certainty. It is that the product still makes sense when you are tired of disposable chat, tired of fake authority, and still open to the possibility that one decent stranger conversation can change the mood of your night.</p>")
        content_parts.append("<p>That is enough for this kind of keyword today. Readers do not need another fake expert voice pretending to know everything. They need a grounded next move that sounds like it was written by someone who understands why random chat still matters and why disappointment has made them cautious.</p>")
        content_parts.append("<p>If you want a best omegle alternative that feels less disposable and more human, try SomeSome the next time you are in the mood for a real conversation. The point is not perfection. The point is giving spontaneity one more honest shot when the rest of the internet feels flat, rushed, and forgettable.</p>")
        self.draft = {
            "title": "Best Omegle Alternative for People Who Actually Want to Talk at Night",
            "subtitle": "A more honest guide to random video chat when you want real conversation instead of noise.",
            "slug": "best-omegle-alternative-for-people-who-actually-want-to-talk-at-night",
            "content": "".join(content_parts),
            "excerpt": "Looking for the best omegle alternative for real late-night conversations? Here is how to choose one and why SomeSome is worth a try.",
            "image": "https://images.unsplash.com/photo-1522202176988-66273c2fd55f?auto=format&fit=crop&w=1600&q=80",
            "imageAlt": "Young adults talking over a late-night video chat session",
            "authorName": "SomeSome Team",
            "authorPosition": "SomeSome Editorial",
            "authorAvatar": "",
            "date": "2026-04-11",
            "draft": False,
        }

    def chat(self, **kwargs):
        user = kwargs["user"]
        self.calls.append((kwargs["model"], kwargs["max_tokens"], user[:80]))
        if "Create a concise blog brief" in user:
            return '{"working_title":"Best Omegle Alternative for People Who Actually Want to Talk at Night","angle":"A practical guide for people who miss genuine late-night random chat","reader_problem":"Most random chat apps feel empty, fake, or too fast to create a real conversation","promise":"Show readers how to judge random chat apps for conversation quality and position SomeSome as a better-feeling option","cta":"Invite readers to try SomeSome when they want random chat that feels more human","seo_title":"Best Omegle Alternative for People Who Actually Want to Talk at Night","slug":"best-omegle-alternative-for-people-who-actually-want-to-talk-at-night","primary_keyword":"best omegle alternative","secondary_keywords":["random video chat for real conversations","late night random chat app"],"search_intent":"comparison","hook":"You are not crazy if random chat feels worse now than it used to."}'
        if "Using this blog brief, write a practical outline" in user:
            return "# Best Omegle Alternative for People Who Actually Want to Talk at Night\n\n## Why late-night random chat still matters\n- Open with the weird late-night feeling of hoping for a real conversation and getting instant skips instead\n- name the real frustration without drifting into generic market history\n\n## Why SomeSome belongs in this conversation early\n- frame SomeSome as an honest next thing to try when other apps feel disposable\n- keep claims modest and high-level\n\n## What SomeSome helps clarify about disposable chats\n- turn the frustration into a cleaner decision standard\n- show why fake rankings and generic advice are less useful than honest positioning\n\n## When SomeSome may not be your move\n- name the readers who still want a faster, more disposable app experience\n- keep the fit limits honest so the recommendation stays believable\n\n## Why SomeSome is the honest next step tonight\n- invite the reader to try SomeSome\n- keep the CTA natural and honest"
        if "Rewrite this blog outline so it is immediately usable for a truthful SomeSome article." in user:
            return "# Best Omegle Alternative for People Who Actually Want to Talk at Night\n\n## Why late-night random chat still matters\n- Open with the weird late-night feeling of hoping for a real conversation and getting instant skips instead\n- name the real frustration without drifting into generic market history\n\n## Why SomeSome belongs in this conversation early\n- frame SomeSome as an honest next thing to try when other apps feel disposable\n- keep claims modest and high-level\n\n## What SomeSome helps clarify about disposable chats\n- turn the frustration into a cleaner decision standard\n- show why fake rankings and generic advice are less useful than honest positioning\n\n## When SomeSome may not be your move\n- name the readers who still want a faster, more disposable app experience\n- keep the fit limits honest so the recommendation stays believable\n\n## Why SomeSome is the honest next step tonight\n- invite the reader to try SomeSome\n- keep the CTA natural and honest"
        if "Write the full blog post from this brief and outline." in user:
            return json.dumps(self.draft)
        if "Revise this blog post based on the external editor review." in user:
            return json.dumps(self.draft)
        if "Review this blog draft with a strict editorial rubric." in user:
            return '{"overall_score":9.2,"passes":true,"strengths":["strong hook","clear audience fit","good SomeSome relevance"],"weaknesses":[],"required_fixes":[],"seo_notes":["primary keyword appears naturally"],"title_feedback":"strong","hook_feedback":"strong","cta_feedback":"natural","image_feedback":"relevant","product_fit_feedback":"good","verdict":"ready"}'
        raise AssertionError(f"Unexpected prompt: {user[:120]}")


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.config = BlogPipelineConfig(
            topic="best omegle alternative for real conversations",
            audience="young adults who miss the fun of random chat but want less chaos",
            tone="raw, honest, useful",
            cta="Invite readers to try SomeSome",
            keywords="best omegle alternative, random video chat for real conversations",
            writer_model="writer-primary",
            reviewer_model="reviewer-primary",
        )
        self.brief = {
            "working_title": "Best Omegle Alternative for Real Conversations",
            "slug": "best-omegle-alternative-real-conversations",
            "primary_keyword": "best omegle alternative",
            "promise": "A practical look at where random chat still feels real.",
        }

    def test_chat_with_fallbacks_uses_second_model_after_rate_limit(self):
        client = FakeClient(
            {
                "writer-primary": OpenRouterError("rate limited", status_code=429),
                "writer-fallback": "{\"ok\": true}",
            }
        )
        attempts = []
        text, used_model = _chat_with_fallbacks(
            client,
            role="writer_draft",
            primary_model="writer-primary",
            fallback_models=["writer-fallback"],
            system="system",
            user="user",
            temperature=0.1,
            max_tokens=10,
            attempts=attempts,
        )
        self.assertEqual(text, '{"ok": true}')
        self.assertEqual(used_model, "writer-fallback")
        self.assertEqual(client.calls, ["writer-primary", "writer-fallback"])
        self.assertEqual(len(attempts), 2)
        self.assertFalse(attempts[0].ok)
        self.assertTrue(attempts[1].ok)

    def test_chat_with_fallbacks_uses_second_model_after_affordability_error(self):
        client = FakeClient(
            {
                "reviewer-primary": OpenRouterError(
                    "This request requires more credits, or fewer max_tokens.",
                    status_code=402,
                    payload={"error": {"code": 402}},
                ),
                "reviewer-fallback": "{\"ok\": true}",
            }
        )
        attempts = []
        text, used_model = _chat_with_fallbacks(
            client,
            role="reviewer",
            primary_model="reviewer-primary",
            fallback_models=["reviewer-fallback"],
            system="system",
            user="user",
            temperature=0.1,
            max_tokens=10,
            attempts=attempts,
        )
        self.assertEqual(text, '{"ok": true}')
        self.assertEqual(used_model, "reviewer-fallback")
        self.assertEqual(client.calls, ["reviewer-primary", "reviewer-fallback"])
        self.assertEqual(len(attempts), 2)
        self.assertFalse(attempts[0].ok)
        self.assertTrue(attempts[1].ok)

    def test_chat_with_fallbacks_retries_same_model_with_lower_affordable_token_budget(self):
        client = BudgetAwareClient(affordable_limit=600)
        attempts = []
        text, used_model = _chat_with_fallbacks(
            client,
            role="writer_draft",
            primary_model="writer-primary",
            fallback_models=["writer-fallback"],
            system="system",
            user="user",
            temperature=0.1,
            max_tokens=1200,
            attempts=attempts,
        )
        self.assertEqual(text, '{"ok": true}')
        self.assertEqual(used_model, "writer-primary")
        self.assertEqual(client.calls, [("writer-primary", 1200), ("writer-primary", 600)])
        self.assertEqual(len(attempts), 2)
        self.assertFalse(attempts[0].ok)
        self.assertTrue(attempts[1].ok)

    def test_openrouter_error_distinguishes_insufficient_credits_from_token_budget_affordability(self):
        budget_error = OpenRouterError(
            "OpenRouter error 402: This request requires more credits, or fewer max_tokens. You requested up to 4200 tokens, but can only afford 545.",
            status_code=402,
            payload={"error": {"code": 402, "message": "This request requires more credits, or fewer max_tokens. You requested up to 4200 tokens, but can only afford 545."}},
        )
        credit_error = OpenRouterError(
            "OpenRouter error 402: Insufficient credits. This account never purchased credits.",
            status_code=402,
            payload={"error": {"code": 402, "message": "Insufficient credits. This account never purchased credits. Purchase more to continue."}},
        )
        self.assertTrue(budget_error.is_affordability_issue)
        self.assertFalse(budget_error.is_insufficient_credits)
        self.assertEqual(budget_error.affordable_max_tokens, 545)
        self.assertFalse(credit_error.is_affordability_issue)
        self.assertTrue(credit_error.is_insufficient_credits)
        self.assertIsNone(credit_error.affordable_max_tokens)
        self.assertFalse(credit_error.is_retryable)

    def test_openrouter_error_uses_embedded_error_code_for_retryability(self):
        embedded = OpenRouterError(
            "OpenRouter embedded error 524: Provider timeout",
            payload={"error": {"code": 524, "message": "Provider timeout"}},
        )
        self.assertEqual(embedded.effective_status_code, 524)
        self.assertTrue(embedded.is_retryable)

    def test_openrouter_client_raises_embedded_error_payload_even_on_http_200(self):
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"error": {"code": 524, "message": "Provider returned error"}}
        with patch("hermes_blog.openrouter_client.requests.post", return_value=mock_response):
            client = OpenRouterClient(api_key="test-key")
            with self.assertRaises(OpenRouterError) as ctx:
                client.chat(model="nvidia/nemotron-3-super-120b-a12b:free", system="system", user="user", max_tokens=42)
        self.assertEqual(ctx.exception.effective_status_code, 524)
        self.assertTrue(ctx.exception.is_retryable)
        self.assertIn("embedded error 524", str(ctx.exception).lower())

    def test_openrouter_client_from_env_prefers_bedrock_when_requested(self):
        previous = {key: os.environ.get(key) for key in ["LLM_PROVIDER", "AWS_REGION", "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "BEDROCK_MODEL_ID"]}
        try:
            os.environ["LLM_PROVIDER"] = "bedrock"
            os.environ["AWS_REGION"] = "us-east-2"
            os.environ["AWS_ACCESS_KEY_ID"] = "test-access"
            os.environ["AWS_SECRET_ACCESS_KEY"] = "test-secret"
            os.environ["BEDROCK_MODEL_ID"] = "anthropic.claude-opus-4-6-v1"
            client = OpenRouterClient.from_env()
            self.assertIsInstance(client, BedrockClient)
            self.assertEqual(client.model_id, "anthropic.claude-opus-4-6-v1")
        finally:
            for key, value in previous.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value

    def test_bedrock_client_chat_parses_anthropic_response(self):
        fake_body = Mock()
        fake_body.read.return_value = json.dumps({"content": [{"type": "text", "text": "hello from bedrock"}]}).encode("utf-8")
        fake_runtime = Mock()
        fake_runtime.invoke_model.return_value = {"body": fake_body}
        fake_boto3 = types.SimpleNamespace(client=Mock(return_value=fake_runtime))
        fake_config_class = Mock(return_value=object())
        fake_botocore_config = types.SimpleNamespace(Config=fake_config_class)
        fake_botocore_exceptions = types.SimpleNamespace(BotoCoreError=Exception, ClientError=Exception)
        with patch.dict(
            sys.modules,
            {
                "boto3": fake_boto3,
                "botocore.config": fake_botocore_config,
                "botocore.exceptions": fake_botocore_exceptions,
            },
        ):
            client = BedrockClient(
                region="us-east-2",
                access_key_id="test-access",
                secret_access_key="test-secret",
                model_id="anthropic.claude-opus-4-6-v1",
            )
            text = client.chat(model="anthropic.claude-opus-4-6-v1", system="system", user="user", max_tokens=42)
        self.assertEqual(text, "hello from bedrock")
        fake_boto3.client.assert_called_once()
        fake_runtime.invoke_model.assert_called_once()

    def test_generate_brief_revalidates_after_repair_until_safe(self):
        invalid_brief = json.dumps(
            {
                "working_title": "Best Omegle Alternative for Real Conversations",
                "angle": "SomeSome might be worth trying next.",
                "reader_problem": "Readers are tired of disposable chats.",
                "promise": "A realistic look at what SomeSome offers as an alternative approach.",
                "cta": "Try SomeSome next.",
                "seo_title": "Best Omegle Alternative for Real Conversations",
                "slug": "best-omegle-alternative-real-conversations",
                "primary_keyword": "best omegle alternative",
                "secondary_keywords": ["random video chat for real conversations"],
                "search_intent": "commercial",
                "hook": "Why SomeSome works better when you want real conversation.",
            }
        )
        repaired_brief = json.dumps(
            {
                "working_title": "Best Omegle Alternative for Real Conversations",
                "angle": "SomeSome might be worth trying next.",
                "reader_problem": "Readers are tired of disposable chats.",
                "promise": "A realistic look at why disposable chats feel hollow and whether SomeSome is worth trying next.",
                "cta": "Try SomeSome next.",
                "seo_title": "Best Omegle Alternative for Real Conversations",
                "slug": "best-omegle-alternative-real-conversations",
                "primary_keyword": "best omegle alternative",
                "secondary_keywords": ["random video chat for real conversations"],
                "search_intent": "commercial",
                "hook": "Why random chat keeps feeling hollow and what to try next.",
            }
        )
        client = PromptSequencedClient([invalid_brief, invalid_brief, repaired_brief])
        attempts = []
        brief, used_model = _generate_brief(client, self.config, attempts)
        self.assertEqual(used_model, "writer-primary")
        self.assertEqual(_validate_brief(brief), [])
        self.assertEqual(len(attempts), 3)
        self.assertEqual(brief["promise"], "A realistic look at why disposable chats feel hollow and whether SomeSome is worth trying next.")

    def test_generate_brief_falls_back_to_safe_template_when_model_keeps_drifting(self):
        invalid_brief = json.dumps(
            {
                "working_title": "Best Omegle Alternative for Real Conversations",
                "angle": "SomeSome works better for people who actually want to talk.",
                "reader_problem": "Readers are tired of disposable chats.",
                "promise": "A realistic look at why SomeSome offers a different approach with better odds.",
                "cta": "Try SomeSome next.",
                "seo_title": "Best Omegle Alternative for Real Conversations",
                "slug": "best-omegle-alternative-real-conversations",
                "primary_keyword": "best omegle alternative",
                "secondary_keywords": ["random video chat for real conversations"],
                "search_intent": "commercial",
                "hook": "Why SomeSome works better when you want real conversation.",
            }
        )
        client = PromptSequencedClient([invalid_brief, invalid_brief, invalid_brief])
        attempts = []
        brief, used_model = _generate_brief(client, self.config, attempts)
        self.assertEqual(used_model, "writer-primary")
        self.assertEqual(_validate_brief(brief), [])
        self.assertIn("without making unsupported claims", brief["promise"])
        self.assertEqual(len(attempts), 3)

    def test_generate_outline_uses_targeted_positioning_repair_for_stubborn_outline_drift(self):
        brief = {
            "working_title": "Best Omegle Alternative for Real Conversations",
            "angle": "A practical guide for people tired of disposable chats who may want to try SomeSome next.",
            "reader_problem": "Readers are tired of random chats that die instantly.",
            "promise": "Show readers how to judge the next app to try and where SomeSome may fit.",
            "cta": "Try SomeSome if you want a less disposable next step.",
            "seo_title": "Best Omegle Alternative for Real Conversations",
            "slug": "best-omegle-alternative-real-conversations",
            "primary_keyword": "best omegle alternative",
        }
        initial_outline = """# Best Omegle Alternative for Real Conversations

## Why Random Chat Apps Turn Into Skip Festivals
- Most platforms reward quick connections over meaningful ones
- Apps designed around instant gratification attract people looking for instant gratification

## What SomeSome Gets Right About Random Conversations
- SomeSome positions itself as a random chat app that doesn't optimize purely for volume
- The interface doesn't push you toward the next chat the moment there's a pause in conversation

## When SomeSome Might Not Be Your Best Move
- The user base is likely smaller than major alternatives
- The app is still random matching, so you're not guaranteed better conversations, just better conditions for them

## Why SomeSome Beats the 30-Second Conversation Cycle
- SomeSome offers a different pace without losing spontaneity
- The app gives you a realistic shot at conversations that develop past the initial hello

## Trying SomeSome vs. Staying Frustrated With Current Apps
- Try SomeSome if you want a better shot at actual conversations
- It's still random chat, but designed around the idea that some conversations are worth having longer
"""
        still_bad_outline = """# Best Omegle Alternative for Real Conversations

## Why Random Chat Apps Turn Into Skip Festivals
- Most platforms reward quick connections over meaningful ones
- Apps designed around instant gratification attract people looking for instant gratification

## What SomeSome Gets Right About Random Conversations
- SomeSome positions itself as a random chat app that doesn't optimize purely for volume
- The interface doesn't push you toward the next chat the moment there's a pause in conversation

## When SomeSome Might Not Be Your Best Move
- The user base is likely smaller than major alternatives
- The app is still random matching, so you're not guaranteed better conversations, just better conditions for them

## SomeSome After Too Many Dead Chats Elsewhere
- SomeSome offers a different pace without losing spontaneity
- The app gives you a realistic shot at conversations that develop past the initial hello

## Trying SomeSome vs. Staying Frustrated With Current Apps
- Try SomeSome if you want a better shot at actual conversations
- SomeSome works for spontaneous conversations without making every interaction feel disposable
"""
        repaired_outline = """# Best Omegle Alternative for Real Conversations

## Why disposable chat gets old fast
- The real problem is not novelty. It is how quickly most chats die before anything human can happen.
- That frustration matters because it changes what kind of app is worth trying next.

## Why SomeSome belongs in this decision early
- SomeSome is worth considering early if you want random chat to feel less disposable.
- The useful question is whether the next app feels worth opening when you still want a real conversation.

## What SomeSome helps clarify about the next app to try
- SomeSome fits best as a modest next try, not as a magic fix.
- The recommendation stays honest when it stays tied to the reader's frustration instead of platform theory.

## When SomeSome may not be your move
- SomeSome may not be your move if you want rapid-fire chaos and endless novelty.
- That fit limit makes the recommendation more believable and more useful.

## Why SomeSome is the honest next step
- Try SomeSome next if you want a less disposable random chat option.
- The takeaway is simple: choose the next app based on whether it feels worth another real attempt.
"""
        client = PromptSequencedClient([initial_outline, still_bad_outline, still_bad_outline, repaired_outline])
        attempts = []
        outline, used_model = _generate_outline(client, self.config, brief, attempts)
        self.assertEqual(used_model, "writer-primary")
        self.assertEqual(_validate_outline(outline), [])
        self.assertIn("Why SomeSome belongs in this decision early", outline)
        self.assertEqual(len(attempts), 4)

    def test_generate_outline_falls_back_to_safe_template_when_model_keeps_drifting(self):
        brief = {
            "working_title": "Best Omegle Alternative for Real Conversations",
            "angle": "A practical guide for people tired of disposable chats who may want to try SomeSome next.",
            "reader_problem": "Readers are tired of random chats that die instantly.",
            "promise": "Show readers how to judge the next app to try and where SomeSome may fit.",
            "cta": "Try SomeSome if you want a less disposable next step.",
            "seo_title": "Best Omegle Alternative for Real Conversations",
            "slug": "best-omegle-alternative-real-conversations",
            "primary_keyword": "best omegle alternative",
        }
        stubborn_outline = """# Best Omegle Alternative for Real Conversations

## What SomeSome Gets Right About Random Conversations
- SomeSome positions itself as a random chat app that doesn't optimize purely for volume
- The interface doesn't push you toward the next chat the moment there's a pause in conversation

## Why Random Chat Apps Turn Into Skip Festivals
- Most platforms reward quick connections over meaningful ones
- Apps designed around instant gratification attract people looking for instant gratification

## SomeSome After Too Many Dead Chats Elsewhere
- SomeSome offers a different pace without losing spontaneity
- The app gives you a realistic shot at conversations that develop past the initial hello

## Trying SomeSome vs. Staying Frustrated With Current Apps
- Try SomeSome if you want a better shot at actual conversations
- SomeSome works for spontaneous conversations without making every interaction feel disposable

## When SomeSome Might Not Be Your Best Move
- The user base is likely smaller than major alternatives
- The app is still random matching, so you're not guaranteed better conversations, just better conditions for them
"""
        client = PromptSequencedClient([stubborn_outline, stubborn_outline, stubborn_outline, stubborn_outline, stubborn_outline])
        attempts = []
        outline, used_model = _generate_outline(client, self.config, brief, attempts)
        self.assertEqual(used_model, "writer-primary")
        self.assertEqual(_validate_outline(outline), [])
        self.assertIn("Why SomeSome belongs in this decision early", outline)
        self.assertIn("When SomeSome may not be your move", outline)
        self.assertEqual(len(attempts), 5)

    def test_apply_defaults_and_validate_accepts_publishable_post(self):
        content = (
            "<p>SomeSome gives people a better way to start random video chat without the usual weird dead-end feeling.</p>"
            + "".join(
                f"<h2>{['Why SomeSome belongs in the conversation early','How better pacing changes everything','Why SomeSome feels worth trying when chats get disposable','What a stronger random chat recommendation should sound like','Why SomeSome earns the final recommendation'][idx-1]}</h2>"
                f"<p>Section {idx} opens by explaining why better pacing changes the entire mood of random video chat. Readers looking for a best omegle alternative usually do not want novelty by itself in section {idx}; they want enough calm, curiosity, and momentum for a stranger to feel like a person instead of another disposable swipe.</p>"
                f"<p>Section {idx} then digs into the practical side of conversation quality. A stronger app does not need fake authority or perfect promises in section {idx}; it needs to make room for a chat to settle, let two people trade some honesty, and give the interaction a chance to become funny, awkward, or surprisingly memorable in a way readers can actually recognize.</p>"
                f"<p>Section {idx} closes by tying that feeling back to SomeSome. The point in section {idx} is not that SomeSome promises magic; it is that SomeSome is worth trying when you want random video chat that feels less hollow, less frantic, and more likely to turn into a real conversation you do not instantly abandon.</p>"
                f"<p>Section {idx} also explains why readers respond to grounded language. They do not need inflated claims about perfect safety or total bot removal in section {idx}; they need a believable reason to think a better random chat experience is still possible if the product respects pacing, attention, and the basic human desire to keep talking when a conversation starts to click.</p>"
                f"<p>Section {idx} ends with a practical takeaway for readers who want random video chat for real conversations. In section {idx}, the difference between dead motion and real momentum becomes easier to spot, which gives SomeSome a credible place in the decision without pretending the platform has hidden features or policies.</p>"
                f"<p>Section {idx} adds one last layer of practical guidance by reminding readers that this choice is emotional as much as technical. In section {idx}, the focus stays on wanting the next conversation to feel less empty, which is why a grounded recommendation for SomeSome works better than a generic listicle stuffed with fake rankings, recycled talking points, and bloated filler.</p>"
                for idx in range(1, 6)
            )
            + "<p>If you want a best omegle alternative that still feels human, try SomeSome.</p>"
        )
        post = _apply_post_defaults(
            {
                "title": "Best *Omegle* Alternative for Real Conversations",
                "subtitle": "Raw _subtitle_",
                "content": content,
                "image": "https://example.com/chat.jpg",
                "imageAlt": "Friends meeting over random video chat",
            },
            self.brief,
            self.config,
        )
        issues = _validate_post(post, config=self.config, brief=self.brief)
        self.assertEqual(issues, [])
        self.assertGreaterEqual(_estimate_word_count(post["content"]), 1500)
        self.assertEqual(post["contentType"], "html")
        self.assertTrue(post["excerpt"])
        self.assertEqual(post["title"], "Best Omegle Alternative for Real Conversations")
        self.assertEqual(post["subtitle"], "Raw subtitle")
        self.assertEqual(post["authorName"], "SomeSome Team")
        self.assertEqual(post["authorPosition"], "SomeSome Editorial")

    def test_sanitize_front_matter_text_softens_absolute_claims_and_stale_years(self):
        self.assertEqual(
            _sanitize_front_matter_text("Random Video Chat WITHOUT Bots (2024)"),
            "Random Video Chat with fewer bots",
        )
        self.assertEqual(
            _sanitize_front_matter_text("Guaranteed real people in 2026"),
            "better odds of real people in 2026",
        )

    def test_validate_rejects_short_post_without_product_mention_or_image(self):
        post = _apply_post_defaults(
            {
                "title": "Short post",
                "content": "<h2>One</h2><p>Too short.</p>",
                "image": "",
                "imageAlt": "",
            },
            self.brief,
            self.config,
        )
        issues = _validate_post(post, config=self.config, brief=self.brief)
        joined = " ".join(issues)
        self.assertIn("1500 words", joined)
        self.assertIn("SomeSome", joined)
        self.assertIn("5 H2 sections", joined)

    def test_validate_rejects_late_and_isolated_product_positioning(self):
        content = "".join(
            [
                *(
                    f"<h2>Section {idx}</h2>"
                    f"<p>Section {idx} explains in specific terms why late-night random video chat falls apart when everyone expects instant entertainment instead of an actual exchange. Readers looking for the best omegle alternative usually notice that section {idx} has its own version of the same problem: too much speed, too little patience, and too many dead-end chats before anything personal can develop. {'unique%d ' % idx * 85}</p>"
                    for idx in range(1, 6)
                ),
                "<p>SomeSome might be worth trying at the very end if you still want a better conversation.</p>",
            ]
        )
        post = _apply_post_defaults(
            {
                "title": "Best Omegle Alternative for Real Conversations",
                "content": content,
                "image": "https://example.com/chat.jpg",
                "imageAlt": "Friends meeting over random video chat",
            },
            self.brief,
            self.config,
        )
        issues = _validate_post(post, config=self.config, brief=self.brief)
        joined = " ".join(issues)
        self.assertIn("Integrate SomeSome earlier", joined)
        self.assertIn("at least 4 sections", joined)
        self.assertIn("more than 1 consecutive section group", joined)

    def test_product_early_h2_section_mentions_and_validate_rejects_last_line_product_name(self):
        content = "".join(
            [
                "<p>SomeSome enters the intro early so the piece technically mentions the product before the sections begin. intro intro intro intro intro intro intro intro intro intro intro intro intro intro intro intro intro intro intro intro intro intro intro intro intro intro intro intro intro intro intro intro intro intro intro intro intro intro intro intro</p>",
                *(
                    f"<h2>Section {idx}</h2>"
                    f"<p>Section {idx} spends most of its time talking in generic terms about disposable random chat, instant skips, and the search for a best omegle alternative that feels more human without naming the actual recommendation until the closing sentence. {'word ' * 85}</p>"
                    f"<p>The final line of section {idx} finally says SomeSome may be worth trying.</p>"
                    for idx in range(1, 6)
                ),
                "<p>If you want the best omegle alternative for real conversations, try SomeSome.</p>",
            ]
        )
        self.assertLess(_product_early_h2_section_mentions(content), 4)
        post = _apply_post_defaults(
            {
                "title": "Best Omegle Alternative for Real Conversations",
                "content": content,
                "image": "https://example.com/chat.jpg",
                "imageAlt": "Friends meeting over random video chat",
            },
            self.brief,
            self.config,
        )
        issues = _validate_post(post, config=self.config, brief=self.brief)
        self.assertIn(
            "Bring SomeSome into the opening of at least 3 H2 sections so the article stops using generic setup paragraphs before finally naming the recommendation.",
            issues,
        )

    def test_validate_rejects_when_h2_structure_keeps_somesome_generic(self):
        content = "".join(
            [
                "<p>SomeSome appears in the intro so the article technically mentions the product early, but the structure still stays generic for too long while the middle keeps circling the same broad frustration instead of building a product-shaped argument. opening opening opening opening opening opening opening opening opening opening opening opening opening opening opening opening opening opening opening opening opening opening opening opening opening opening opening opening opening opening opening opening opening opening opening opening opening opening opening opening</p>",
                "<h2>Why random chat feels empty now</h2><p>This section explains the frustration behind the best omegle alternative search and keeps talking about why fast, disposable chats feel dead on arrival. It stays broad on purpose so the validator can see a structurally generic section rather than a clean recommendation. word word word word word word word word word word word word word word word word word word word word word word word word word word word word word word word word word word word word word word word word word word word word word word word word word word word word word word word word word word word word word word word word word word word word</p>",
                "<h2>Why instant skips ruin the whole mood</h2><p>This section stays generic too, even though the body can briefly mention SomeSome once in passing without giving the structure a product-shaped spine. SomeSome gets one sentence, but the heading and main purpose still read like they could belong to any random chat article. word word word word word word word word word word word word word word word word word word word word word word word word word word word word word word word word word word word word word word word word word word word word word word word word word word word word word word word word word word word word word word word word word word word word</p>",
                "<h2>What better conversations actually need</h2><p>By this point the article is still mostly generic advice with only light body references to SomeSome, so the recommendation feels delayed even when the product name appears in passing. SomeSome is mentioned again here, but the section shape still feels interchangeable. word word word word word word word word word word word word word word word word word word word word word word word word word word word word word word word word word word word word word word word word word word word word word word word word word word word word word word word word word word word word word word word word word word word word</p>",
                "<h2>Why SomeSome might be worth trying</h2><p>This is where the article finally gives SomeSome a direct heading. Until now, though, the structure could mostly have worked with any placeholder app name. word word word word word word word word word word word word word word word word word word word word word word word word word word word word word word word word word word word word word word word word word word word word word word word word word word word word word word word word word word word word word word word word word word word word</p>",
                "<h2>Try a calmer random chat app if you still want real conversations</h2><p>The ending brings the recommendation home, but it has to do too much because the product logic was under-structured earlier. word word word word word word word word word word word word word word word word word word word word word word word word word word word word word word word word word word word word word word word word word word word word word word word word word word word word word word word word word word word word word word word word word word word word</p>",
                "<p>If you want the best omegle alternative for real conversations, try SomeSome.</p>",
            ]
        )
        post = _apply_post_defaults(
            {
                "title": "Best Omegle Alternative for Real Conversations",
                "content": content,
                "image": "https://example.com/chat.jpg",
                "imageAlt": "Friends meeting over random video chat",
            },
            self.brief,
            self.config,
        )
        issues = _validate_post(post, config=self.config, brief=self.brief)
        joined = " ".join(issues)
        self.assertIn("at least 3 H2 headings", joined)
        self.assertIn("one of the first 3 H2 headings", joined)

    def test_validate_rejects_too_many_h2_sections(self):
        content = "".join(
            [
                "<p>SomeSome can be a useful option when random video chat feels too disposable from the start.</p>",
                *(
                    f"<h2>Section {idx}</h2><p>Section {idx} adds a distinct point about why readers keep looking for the best omegle alternative when they want more human conversations online, and SomeSome stays relevant as a grounded recommendation throughout the piece. {'word ' * 65}</p>"
                    for idx in range(1, 8)
                ),
                "<p>If you want the best omegle alternative for real conversations, try SomeSome.</p>",
            ]
        )
        post = _apply_post_defaults(
            {
                "title": "Best Omegle Alternative for Real Conversations",
                "content": content,
                "image": "https://example.com/chat.jpg",
                "imageAlt": "Friends meeting over random video chat",
            },
            self.brief,
            self.config,
        )
        issues = _validate_post(post, config=self.config, brief=self.brief)
        self.assertIn(
            "Cut the article back to exactly 5 H2 sections so it stays focused instead of meandering through interchangeable sections.",
            issues,
        )

    def test_validate_rejects_missing_product_in_conclusion(self):
        content = "".join(
            [
                "<p>SomeSome can be a useful option when you want random video chat to feel less hollow from the start. The opening makes the product relevant early without turning the piece into a hard sell. intro intro intro intro intro intro intro intro intro intro intro intro intro intro intro intro intro intro intro intro intro intro intro intro intro intro intro intro intro intro intro intro intro intro intro intro intro intro intro intro</p>",
                *(
                    f"<h2>Section {idx}</h2>"
                    f"<p>Section {idx} explains a different reason pacing, curiosity, and honest expectations matter when readers look for a best omegle alternative that feels more human. For angle {idx}, SomeSome stays relevant because the recommendation is grounded in conversation quality, skepticism about hype, and the idea that people want more than another disposable scroll through strangers. The section leans on a distinct example about {['awkward openings','late-night honesty','skip-happy behavior','performative chaos','why calmer pacing matters'][idx-1]} so the prose is not just repeating itself. {'section%d ' % idx * 130}</p>"
                    for idx in range(1, 6)
                ),
                "<p>The ending shifts into generic encouragement about staying open-minded, keeping expectations realistic, and trying a few apps until something clicks again for you online tonight. It talks about mood, patience, timing, and luck, but never turns that closing energy back into a direct recommendation or next step. closing closing closing closing closing closing closing closing closing closing closing closing closing closing closing closing closing closing closing closing closing closing closing closing closing closing closing closing closing closing closing closing closing closing closing closing closing closing closing closing</p>",
            ]
        )
        post = _apply_post_defaults(
            {
                "title": "Best Omegle Alternative for Real Conversations",
                "content": content,
                "image": "https://example.com/chat.jpg",
                "imageAlt": "Friends meeting over random video chat",
            },
            self.brief,
            self.config,
        )
        issues = _validate_post(post, config=self.config, brief=self.brief)
        self.assertIn(
            "End with a clearer conclusion/CTA that naturally brings the reader back to SomeSome.",
            issues,
        )

    def test_validate_rejects_generic_article_evaluation_heading(self):
        content = "".join(
            [
                "<p>SomeSome can matter early in the piece when readers want a more human kind of random video chat. intro intro intro intro intro intro intro intro intro intro intro intro intro intro intro intro intro intro intro intro intro intro intro intro intro intro intro intro intro intro intro intro intro intro intro intro intro intro intro intro</p>",
                "<h2>Why random chat feels empty now</h2><p>Readers looking for the best omegle alternative usually notice how disposable every conversation feels when nobody stays long enough to get interesting. SomeSome stays relevant here because the article keeps tying the problem back to wanting better odds of a real exchange instead of more noise. word word word word word word word word word word word word word word word word word word word word word word word word word word word word word word word word word word word word word word word word word word word word word word word word word word word word word word word word word word word word</p>",
                "<h2>How to evaluate random chat apps before wasting your time</h2><p>This section drifts into generic checklist advice about warning signs, green flags, and platform evaluation instead of sharpening the recommendation. SomeSome gets mentioned, but only lightly, so the section still reads like filler. word word word word word word word word word word word word word word word word word word word word word word word word word word word word word word word word word word word word word word word word word word word word word word word word word word word word word word word word word word word word</p>",
                *(
                    f"<h2>Section {idx}</h2><p>Section {idx} explains a distinct reason pacing, curiosity, and honest expectations matter when readers look for a best omegle alternative that feels more human. SomeSome stays relevant in section {idx} because the recommendation remains grounded in conversation quality instead of inflated claims. {'section%d ' % idx * 120}</p>"
                    for idx in range(3, 6)
                ),
                "<p>If you want the best omegle alternative for real conversations, try SomeSome.</p>",
            ]
        )
        post = _apply_post_defaults(
            {
                "title": "Best Omegle Alternative for Real Conversations",
                "content": content,
                "image": "https://example.com/chat.jpg",
                "imageAlt": "Friends meeting over random video chat",
            },
            self.brief,
            self.config,
        )
        issues = _validate_post(post, config=self.config, brief=self.brief)
        self.assertTrue(any("generic article headings" in issue.lower() for issue in issues))

    def test_validate_rejects_generic_somesome_promo_sections_without_fit_limits(self):
        content = "".join(
            [
                "<p>Readers want a best omegle alternative that feels less disposable, and SomeSome enters the article early as a possible next move.</p>",
                "<h2>Why random chat became a disconnect factory</h2><p>Section 1 names the frustration and keeps it grounded in instant skips, shallow attention, and the feeling that random video chat stopped feeling human. " + ("word " * 75) + "</p>",
                "<h2>What Makes SomeSome Different from High-Volume Chat Apps</h2><p>This section praises SomeSome in broad terms without naming any honest fit limits, tradeoffs, or reader-specific constraints. It keeps saying the platform feels less disposable and more conversation-friendly. " + ("word " * 75) + "</p>",
                "<h2>When SomeSome Works Best</h2><p>This section keeps repeating the same praise instead of clarifying who should skip the recommendation or what frustration it will not solve. SomeSome stays framed as broadly better for almost everyone. " + ("word " * 75) + "</p>",
                "<h2>Why SomeSome Beats Generic Omegle Clones</h2><p>This section keeps making the same generic SomeSome-positive claim again, with no sharper decision logic and no honest boundary around the recommendation. " + ("word " * 75) + "</p>",
                "<h2>Why SomeSome is the honest next step tonight</h2><p>If you still want random video chat to feel more human, try SomeSome tonight. " + ("word " * 75) + "</p>",
                "<p>If you want the best omegle alternative, SomeSome is worth trying.</p>",
            ]
        )
        post = _apply_post_defaults(
            {
                "title": "Best Omegle Alternative for Real Conversations",
                "content": content,
                "image": "https://example.com/chat.jpg",
                "imageAlt": "Friends meeting over random video chat",
            },
            self.brief,
            self.config,
        )
        issues = _validate_post(post, config=self.config, brief=self.brief)
        joined = "\n".join(issues)
        self.assertIn("What Makes SomeSome Different from High-Volume Chat Apps", joined)
        self.assertIn("does not read like repetitive product praise", joined)

    def test_validate_rejects_unsupported_testing_and_fake_quant_claims(self):
        content = "".join(
            [
                "<p>I tested every random video chat app myself so you do not have to.</p>",
                "<p>Our analysis of 27 apps found 42% bot activity and 12 second wait times.</p>",
                *(f"<h2>Section {idx}</h2><p>SomeSome is useful for real conversations and best omegle alternative users. {'word ' * 55}</p>" for idx in range(1, 6)),
                "<p>SomeSome is worth trying.</p>",
            ]
        )
        post = _apply_post_defaults(
            {
                "title": "Best Omegle Alternative for Real Conversations",
                "content": content,
                "image": "https://example.com/chat.jpg",
                "imageAlt": "Friends meeting over random video chat",
            },
            self.brief,
            self.config,
        )
        issues = _validate_post(post, config=self.config, brief=self.brief)
        self.assertIn("Avoid unsupported testing claims, fake counts, or made-up quantitative comparisons unless the brief actually provides that evidence.", issues)

    def test_find_duplicate_paragraphs_and_validate_rejects_padded_repetition(self):
        repeated = "This is the same long paragraph about how random video chat should feel more human, less disposable, and more worth sticking with when the conversation actually starts to click for both people involved online tonight."
        content = "".join(
            [
                "<p>SomeSome can be a useful option for people who want better random video chat conversations.</p>",
                *(f"<h2>Section {idx}</h2><p>{repeated}</p><p>{repeated}</p><p>{'word ' * 70}</p>" for idx in range(1, 6)),
                "<p>If you want the best omegle alternative, SomeSome is worth trying.</p>",
            ]
        )
        duplicates = _find_duplicate_paragraphs(content)
        self.assertTrue(duplicates)
        post = _apply_post_defaults(
            {
                "title": "Best Omegle Alternative for Real Conversations",
                "content": content,
                "image": "https://example.com/chat.jpg",
                "imageAlt": "Friends meeting over random video chat",
            },
            self.brief,
            self.config,
        )
        issues = _validate_post(post, config=self.config, brief=self.brief)
        self.assertTrue(any("duplicated long paragraphs" in issue.lower() for issue in issues))

    def test_find_repeated_long_snippets_and_validate_rejects_inflated_phrase_loops(self):
        repeated_snippet = "real conversations on random video chat feel better when you stop chasing novelty and start screening for intent"
        content = "".join(
            [
                "<p>SomeSome can be a useful option for people who want better random video chat conversations.</p>",
                *(f"<h2>Section {idx}</h2><p>{' '.join([repeated_snippet for _ in range(8)])}</p><p>{'word ' * 70}</p>" for idx in range(1, 6)),
                "<p>If you want the best omegle alternative, SomeSome is worth trying.</p>",
            ]
        )
        repeated = _find_repeated_long_snippets(content)
        self.assertTrue(repeated)
        post = _apply_post_defaults(
            {
                "title": "Best Omegle Alternative for Real Conversations",
                "content": content,
                "image": "https://example.com/chat.jpg",
                "imageAlt": "Friends meeting over random video chat",
            },
            self.brief,
            self.config,
        )
        issues = _validate_post(post, config=self.config, brief=self.brief)
        self.assertTrue(any("repeated long phrase padding" in issue.lower() for issue in issues))

    def test_find_repeated_sentences_and_validate_rejects_cross_section_recycling(self):
        repeated_sentence = (
            "The useful test is whether the conversation feels like two people meeting in the moment rather than two accounts colliding for a second and disappearing."
        )
        content = "".join(
            [
                "<p>SomeSome can be a useful option for people who want better random video chat conversations.</p>",
                *(
                    f"<h2>Section {idx}</h2>"
                    f"<p>Section {idx} opens with a different point about pacing, attention, and why readers still search for the best omegle alternative when they want a more human conversation online.</p>"
                    f"<p>{repeated_sentence}</p>"
                    f"<p>Section {idx} closes with another unique point about why SomeSome can be worth trying when random chat feels too disposable and too rushed to become memorable.</p>"
                    for idx in range(1, 6)
                ),
                "<p>If you want the best omegle alternative, SomeSome is worth trying.</p>",
            ]
        )
        repeated = _find_repeated_sentences(content)
        self.assertTrue(repeated)
        post = _apply_post_defaults(
            {
                "title": "Best Omegle Alternative for Real Conversations",
                "content": content,
                "image": "https://example.com/chat.jpg",
                "imageAlt": "Friends meeting over random video chat",
            },
            self.brief,
            self.config,
        )
        issues = _validate_post(post, config=self.config, brief=self.brief)
        self.assertTrue(any("repeated long sentences across the article" in issue.lower() for issue in issues))

    def test_find_repeated_sentence_templates_and_validate_rejects_numbered_rewrites(self):
        templated_sentence = "the appeal is not randomness by itself but the feeling that a stranger might actually stay long enough to become interesting, funny, calming, or unexpectedly honest when the rest of the internet feels loud and empty."
        content = "".join(
            [
                "<p>SomeSome can be a useful option for people who want better random video chat conversations.</p>",
                *(
                    f"<h2>Section {idx}</h2>"
                    f"<p>Late-night point {idx}: {templated_sentence.capitalize()}</p>"
                    f"<p>Night detail {idx}: People who search for random video chat for real conversations usually want pacing, curiosity, and enough room for a conversation to breathe instead of getting buried under instant-skip reflexes and chaotic attention spans.</p>"
                    f"<p>Section {idx} closes with a unique point about why SomeSome can be worth trying when random chat feels too disposable and too rushed to become memorable.</p>"
                    for idx in range(1, 6)
                ),
                "<p>If you want the best omegle alternative, SomeSome is worth trying.</p>",
            ]
        )
        repeated = _find_repeated_sentence_templates(content)
        self.assertTrue(repeated)
        post = _apply_post_defaults(
            {
                "title": "Best Omegle Alternative for Real Conversations",
                "content": content,
                "image": "https://example.com/chat.jpg",
                "imageAlt": "Friends meeting over random video chat",
            },
            self.brief,
            self.config,
        )
        issues = _validate_post(post, config=self.config, brief=self.brief)
        self.assertTrue(any("label-swapped sentence templates" in issue.lower() for issue in issues))

    def test_find_heading_swapped_paragraphs_and_validate_rejects_section_bridge_templates(self):
        content = "".join(
            [
                "<p>SomeSome can be a useful option for people who want better random video chat conversations.</p>",
                *(
                    f"<h2>Section {idx} for late-night chats</h2>"
                    f"<p>Section {idx} opens with a unique point about why people keep chasing a better random chat app even after they get tired of instant skips and dead-end conversations. {'word ' * 45}</p>"
                    f"<p>Section {idx} for late-night chats matters in this search because readers are trying to protect a rare kind of online interaction: the moment a random chat stops feeling like content and starts feeling like two people honestly talking. In the section {idx} for late-night chats section, that reminder keeps the article useful, grounded, and commercially relevant to SomeSome without drifting into made-up claims. It also gives the piece a more believable rhythm for section {idx} for late-night chats, because the advice stays tied to the reader's frustration instead of wandering into generic SEO filler.</p>"
                    for idx in range(1, 6)
                ),
                "<p>If you want the best omegle alternative, SomeSome is worth trying.</p>",
            ]
        )
        repeated = _find_heading_swapped_paragraphs(content)
        self.assertTrue(repeated)
        post = _apply_post_defaults(
            {
                "title": "Best Omegle Alternative for Real Conversations",
                "content": content,
                "image": "https://example.com/chat.jpg",
                "imageAlt": "Friends meeting over random video chat",
            },
            self.brief,
            self.config,
        )
        issues = _validate_post(post, config=self.config, brief=self.brief)
        self.assertTrue(any("section-bridge paragraphs" in issue.lower() for issue in issues))

    def test_validate_rejects_generic_platform_theory_bloat_in_article_body(self):
        content = "".join(
            [
                "<p>SomeSome belongs early in the recommendation when readers want a less disposable random chat app.</p>",
                "<h2>Why SomeSome belongs in this decision early</h2><p>SomeSome can be a grounded next try when you are tired of disposable chats. " + ("word " * 65) + "</p>",
                "<h2>What SomeSome clarifies about disposable chat</h2><p>The Omegle legacy still shapes expectations, mobile-first design pulled swipe culture into random chat, and volume over depth became the category norm. Those platform-theory points start taking over the section instead of moving the reader toward a sharper SomeSome decision. " + ("word " * 65) + "</p>",
                "<h2>When SomeSome may not be your move</h2><p>SomeSome may not fit if you want a faster, broader, more novelty-driven app. " + ("word " * 65) + "</p>",
                "<h2>Why SomeSome still belongs in the shortlist</h2><p>This section drifts into peak hours, community size, and skip button placement instead of staying with the reader's decision. " + ("word " * 65) + "</p>",
                "<h2>Why SomeSome is the honest next step</h2><p>If you still want a less disposable random chat experience, SomeSome is worth trying. " + ("word " * 65) + "</p>",
                "<p>If you want the best omegle alternative for real conversations, try SomeSome.</p>",
            ]
        )
        post = _apply_post_defaults(
            {
                "title": "Best Omegle Alternative for Real Conversations",
                "content": content,
                "image": "https://example.com/chat.jpg",
                "imageAlt": "Friends meeting over random video chat",
            },
            self.brief,
            self.config,
        )
        issues = _validate_post(post, config=self.config, brief=self.brief)
        self.assertTrue(any("platform-theory filler" in issue.lower() for issue in issues))

    def test_validate_rejects_invented_somesome_feature_claims(self):
        content = "".join(
            [
                "<p>SomeSome uses human moderation and profile verification to keep every chat safe and controlled.</p>",
                *(f"<h2>Section {idx}</h2><p>SomeSome helps people find real conversations online. {'word ' * 70}</p>" for idx in range(1, 6)),
                "<p>If you want the best omegle alternative, SomeSome is worth trying.</p>",
            ]
        )
        post = _apply_post_defaults(
            {
                "title": "Best Omegle Alternative for Real Conversations",
                "content": content,
                "image": "https://example.com/chat.jpg",
                "imageAlt": "Friends meeting over random video chat",
            },
            self.brief,
            self.config,
        )
        issues = _validate_post(post, config=self.config, brief=self.brief)
        self.assertIn(
            "Avoid invented SomeSome feature or policy claims. Keep the positioning high-level unless the brief explicitly provides product specifics.",
            issues,
        )

    def test_validate_allows_approved_somesome_fact_sheet_claims(self):
        content = "".join(
            [
                "<p>SomeSome is heavily moderated to keep the platform SFW, which matters if random chat has started feeling too chaotic to bother opening at night.</p>",
                "<h2>Why SomeSome belongs in the conversation early</h2><p>SomeSome has a global user base, especially in the Philippines, Colombia, other parts of SEA, and Latam including Brazil. " + ("word " * 70) + "</p>",
                "<h2>How SomeSome handles the first minute</h2><p>Calls default to 60 seconds and can be extended, which gives strangers a low-pressure starting point without pretending every chat will be amazing. " + ("word " * 70) + "</p>",
                "<h2>What SomeSome gives you beyond random matching</h2><p>You can directly call people, send unlimited free messages, and use in-app AI translation with live subtitles for cross-language chats, including Spanish conversations. " + ("word " * 70) + "</p>",
                "<h2>When SomeSome may not be your move</h2><p>SomeSome may not be your move if you want heavy filtering instead of a simpler way to keep trying for real conversation. " + ("word " * 70) + "</p>",
                "<h2>Why SomeSome is still worth trying next</h2><p>If you want a best omegle alternative that feels more usable for global conversation, SomeSome is worth trying next without pretending every chat will go well. " + ("word " * 70) + "</p>",
            ]
        )
        post = _apply_post_defaults(
            {
                "title": "Best Omegle Alternative for Real Conversations",
                "content": content,
                "image": "https://example.com/chat.jpg",
                "imageAlt": "Friends meeting over random video chat",
            },
            self.brief,
            self.config,
        )
        issues = _validate_post(post, config=self.config, brief=self.brief)
        self.assertFalse(any("invented somesome feature or policy claims" in issue.lower() for issue in issues))

    def test_validate_rejects_invented_somesome_ui_and_workflow_claims(self):
        content = "".join(
            [
                "<p>SomeSome's layout puts interest tags next to a skip button so you can tune every chat faster.</p>",
                *(f"<h2>Section {idx}</h2><p>SomeSome helps people find better random video chat conversations. {'word ' * 70}</p>" for idx in range(1, 6)),
                "<p>If you want the best omegle alternative, SomeSome is worth trying.</p>",
            ]
        )
        post = _apply_post_defaults(
            {
                "title": "Best Omegle Alternative for Real Conversations",
                "content": content,
                "image": "https://example.com/chat.jpg",
                "imageAlt": "Friends meeting over random video chat",
            },
            self.brief,
            self.config,
        )
        issues = _validate_post(post, config=self.config, brief=self.brief)
        self.assertIn(
            "Avoid invented SomeSome feature or policy claims. Keep the positioning high-level unless the brief explicitly provides product specifics.",
            issues,
        )

    def test_validate_rejects_invented_somesome_company_intent_claims(self):
        content = "".join(
            [
                "<p>SomeSome was built from day one for people who actually want to talk, and it attracts users who are screened for better conversations.</p>",
                "<p>SomeSome has a user base that shows up with the intention to talk, and that cultural norm makes the platform feel more human.</p>",
                *(f"<h2>Section {idx}</h2><p>SomeSome helps people find better random video chat conversations. {'word ' * 70}</p>" for idx in range(1, 6)),
                "<p>If you want the best omegle alternative, SomeSome is worth trying.</p>",
            ]
        )
        post = _apply_post_defaults(
            {
                "title": "Best Omegle Alternative for Real Conversations",
                "content": content,
                "image": "https://example.com/chat.jpg",
                "imageAlt": "Friends meeting over random video chat",
            },
            self.brief,
            self.config,
        )
        issues = _validate_post(post, config=self.config, brief=self.brief)
        self.assertIn(
            "Avoid invented SomeSome feature or policy claims. Keep the positioning high-level unless the brief explicitly provides product specifics.",
            issues,
        )

    def test_validate_rejects_invented_somesome_design_philosophy_claims(self):
        content = "".join(
            [
                "<p>SomeSome takes a different approach because its design philosophy prioritizes conversation quality over volume and self-selection creates a more conversation-minded user base.</p>",
                *(f"<h2>Section {idx}</h2><p>SomeSome helps people find better random video chat conversations. {'word ' * 70}</p>" for idx in range(1, 6)),
                "<p>If you want the best omegle alternative, SomeSome is worth trying.</p>",
            ]
        )
        post = _apply_post_defaults(
            {
                "title": "Best Omegle Alternative for Real Conversations",
                "content": content,
                "image": "https://example.com/chat.jpg",
                "imageAlt": "Friends meeting over random video chat",
            },
            self.brief,
            self.config,
        )
        issues = _validate_post(post, config=self.config, brief=self.brief)
        self.assertIn(
            "Avoid invented SomeSome feature or policy claims. Keep the positioning high-level unless the brief explicitly provides product specifics.",
            issues,
        )

    def test_validate_does_not_treat_generic_filtering_language_as_invented_ui_claim(self):
        content = "".join(
            [
                "<p>SomeSome can still fit this article when the recommendation naturally filters toward people who want a slower, more human conversation instead of instant chaos.</p>",
                *(
                    f"<h2>Section {idx}</h2><p>Section {idx} explains why readers want better random video chat conversations and why grounded SomeSome positioning works without inventing product specifics. {'word ' * 85}</p>"
                    for idx in range(1, 6)
                ),
                "<p>If you want the best omegle alternative for real conversations, try SomeSome.</p>",
            ]
        )
        post = _apply_post_defaults(
            {
                "title": "Best Omegle Alternative for Real Conversations",
                "content": content,
                "image": "https://example.com/chat.jpg",
                "imageAlt": "Friends meeting over random video chat",
            },
            self.brief,
            self.config,
        )
        issues = _validate_post(post, config=self.config, brief=self.brief)
        self.assertNotIn(
            "Avoid invented SomeSome feature or policy claims. Keep the positioning high-level unless the brief explicitly provides product specifics.",
            issues,
        )

    def test_validate_rejects_unsupported_absolute_promises_and_stale_years(self):
        content = "".join(
            [
                *(f"<h2>Section {idx}</h2><p>SomeSome helps people find better random video chat conversations. {'word ' * 260}</p>" for idx in range(1, 6)),
                "<p>SomeSome is worth trying if you want more real conversations online.</p>",
            ]
        )
        post = {
            "title": "Best Omegle Alternative: Random Video Chat WITHOUT Bots (2024)",
            "subtitle": "Guaranteed real people tonight",
            "slug": "best-omegle-alternative-no-bots-2024",
            "excerpt": "Bot-free random chat for guaranteed real people.",
            "content": content,
            "image": "https://example.com/chat.jpg",
            "imageAlt": "Friends meeting over random video chat",
        }
        issues = _validate_post(post, config=self.config, brief=self.brief)
        self.assertIn(
            "Avoid unsupported absolute promises like 'no bots', 'bot-free', or guaranteed safety/real people in the title, subtitle, slug, or excerpt.",
            issues,
        )
        self.assertIn(
            "Avoid stale year framing in the title, subtitle, slug, or excerpt unless it matches the current year (2026) or the brief explicitly requires it.",
            issues,
        )

    def test_find_editorial_meta_sentences_and_validate_rejects_revision_leakage(self):
        content = "".join(
            [
                "<p>This article stays focused on conversation quality instead of fake certainty.</p>",
                "<h2>Why this topic matters</h2>",
                "<p>People who search this keyword are usually trying to recover a specific feeling, not just compare feature lists.</p>",
                "<h2>What readers actually want</h2>",
                "<p>The article should keep the product framing simple and honest.</p>",
                *(f"<h2>Section {idx}</h2><p>SomeSome helps people find better random video chat conversations. {'word ' * 80}</p>" for idx in range(1, 4)),
                "<p>If you want the best omegle alternative, SomeSome is worth trying.</p>",
            ]
        )
        repeated = _find_editorial_meta_sentences(content)
        self.assertTrue(repeated)
        post = _apply_post_defaults(
            {
                "title": "Best Omegle Alternative for Real Conversations",
                "content": content,
                "image": "https://example.com/chat.jpg",
                "imageAlt": "Friends meeting over random video chat",
            },
            self.brief,
            self.config,
        )
        issues = _validate_post(post, config=self.config, brief=self.brief)
        self.assertTrue(any("editorial/meta-writing" in issue.lower() for issue in issues))

    def test_validate_clean_publishable_article_does_not_flag_editorial_meta_false_positive(self):
        content = "".join(
            [
                "<p>Late-night random chat still matters because a good conversation can cut through the usual internet numbness, and SomeSome can make sense early when you want a calmer shot at that kind of exchange.</p>",
                "<h2>Why late-night chat feels different</h2><p>SomeSome can feel worth trying when you want a calmer shot at a real exchange instead of another instant skip. " + ("word " * 70) + "</p>",
                "<h2>Why most apps feel hollow</h2><p>Readers usually leave when the pace feels frantic, performative, and disposable, so keeping SomeSome in the frame here helps tie the diagnosis back to a credible recommendation. " + ("word " * 70) + "</p>",
                "<h2>How to judge a better app</h2><p>The best signal is whether conversations have enough room to become funny, awkward, personal, or memorable, and SomeSome stays relevant when that is the standard. " + ("word " * 70) + "</p>",
                "<h2>Why SomeSome fits this mood</h2><p>SomeSome works best here as a modest recommendation for people who miss spontaneous conversations and want another honest shot. " + ("word " * 70) + "</p>",
                "<h2>What to do next</h2><p>If you want the best omegle alternative for real conversations, try SomeSome the next time you want random video chat to feel more human. " + ("word " * 70) + "</p>",
            ]
        )
        post = _apply_post_defaults(
            {
                "title": "Best Omegle Alternative for Real Conversations",
                "content": content,
                "image": "https://example.com/chat.jpg",
                "imageAlt": "Friends meeting over random video chat",
            },
            self.brief,
            self.config,
        )
        issues = _validate_post(post, config=self.config, brief=self.brief)
        self.assertFalse(any("editorial/meta-writing" in issue.lower() for issue in issues))

    def test_validate_outline_rejects_placeholders_fences_and_founder_story(self):
        outline = """```markdown\n## Top apps\n- [Alternative App 1 Name]\n## Why We Built SomeSome\n"""
        issues = _validate_outline(outline)
        self.assertIn("Remove markdown code fences from the outline.", issues)
        self.assertIn("Replace bracketed placeholder text with real outline content.", issues)
        self.assertIn("Remove speculative 'why we built SomeSome' or founder-story sections unless that context was explicitly provided.", issues)

    def test_validate_outline_requires_product_planning(self):
        outline = "# Best Omegle Alternative\n\n## Intro\n- open with frustration\n\n## What to look for\n- pacing\n- curiosity\n"
        issues = _validate_outline(outline)
        self.assertIn(
            "Mention SomeSome somewhere in the outline so the product fit is planned before drafting, not bolted on at the end.",
            issues,
        )
        self.assertIn(
            "Use 5 substantive H2 sections in the outline so the article has enough depth without drifting into filler.",
            issues,
        )

    def test_validate_brief_rejects_invented_product_advantage_claims(self):
        brief = {
            "working_title": "The Best Omegle Alternative When You Actually Want Real Conversations",
            "angle": "SomeSome attracts users who actually want to talk and works better because its culture is more conversation-focused",
            "promise": "Find out why SomeSome works better than other apps",
            "hook": "Most apps are dead, but SomeSome has the right people on it",
            "cta": "Try SomeSome",
        }
        issues = _validate_brief(brief)
        self.assertTrue(any("unsupported claims" in issue.lower() for issue in issues))

    def test_validate_brief_allows_approved_fact_sheet_language(self):
        brief = {
            "working_title": "Best Omegle Alternative for Global Random Chat",
            "angle": "SomeSome is worth considering if you want a random chat app with heavy moderation to keep it SFW and built-in AI translation for cross-language chats.",
            "promise": "Show readers how SomeSome's 60-second extendable calls, direct calls, free messages, and global user base across the Philippines, Colombia, SEA, and Latam may fit their next try.",
            "hook": "If you want random chat that can cross language barriers, SomeSome includes in-app AI translation with live subtitles.",
            "cta": "Try SomeSome next if you want a global random chat app with free messaging and translation.",
        }
        issues = _validate_brief(brief)
        self.assertEqual(issues, [])

    def test_validate_brief_rejects_soft_positioning_and_broad_market_comparison_promises(self):
        brief = {
            "working_title": "The Best Omegle Alternative When You're Tired of 30-Second Conversations",
            "angle": "SomeSome positions itself as the place for different expectations and a better shot at actual conversations",
            "promise": "See which apps still work, starting with SomeSome and the usual suspects",
            "hook": "A few apps still work if you know what to look for",
            "cta": "Try SomeSome",
        }
        issues = _validate_brief(brief)
        self.assertTrue(any("unsupported claims" in issue.lower() for issue in issues))

    def test_validate_outline_rejects_generic_evaluation_headings(self):
        outline = """# Best Omegle Alternative\n\n## Why random chat feels empty now\n- pain\n\n## How to evaluate random chat apps before wasting your time\n- generic checklist\n\n## What to look for in an Omegle alternative\n- more checklist items\n\n## Why SomeSome fits better\n- product fit\n\n## CTA\n- close with SomeSome\n"""
        issues = _validate_outline(outline)
        joined = " ".join(issues)
        self.assertIn("Replace generic filler headings", joined)
        self.assertIn("How to evaluate random chat apps before wasting your time", joined)

    def test_validate_outline_rejects_bloat_and_generic_heading_patterns(self):
        outline = """# Best Omegle Alternative\n\n## Intro\n- frame the problem\n\n## The Psychology Behind Random Chat\n- generic explanation\n\n## Market Landscape Overview\n- broad category talk\n\n## Why Traditional Platforms Keep Failing\n- repetitive theory\n\n## Why SomeSome Fits Better\n- product tie-in\n\n## Future of Random Chat\n- predictions\n"""
        issues = _validate_outline(outline)
        joined = " ".join(issues)
        self.assertIn("Trim the outline to 5 substantive H2 sections", joined)
        self.assertIn("Replace generic filler headings", joined)

    def test_validate_outline_rejects_generic_platform_theory_bullets(self):
        outline = """# Best Omegle Alternative\n\n## Why SomeSome belongs in this decision early\n- frame SomeSome as a grounded next try\n\n## What SomeSome clarifies about disposable chat\n- The Omegle legacy trained people to expect instant exits
- mobile-first design pushed swipe culture into random chat
- volume over depth became the category default
\n## When SomeSome may not be your move\n- honest fit limits\n\n## Why SomeSome still belongs in the shortlist\n- keep the product reasoning grounded\n\n## Why SomeSome is the honest next step\n- CTA\n"""
        issues = _validate_outline(outline)
        self.assertTrue(any("platform theory" in issue.lower() for issue in issues))

    def test_validate_outline_requires_multiple_somesome_touchpoints(self):
        outline = """# Best Omegle Alternative\n\n## Why random chat feels empty now\n- frame the problem\n\n## Why skip culture burns people out\n- more problem context\n\n## Why SomeSome fits better\n- one product section\n\n## Why expectations matter\n- generic cleanup\n\n## Final CTA\n- mention SomeSome once at the end\n"""
        issues = _validate_outline(outline)
        self.assertIn(
            "Plan SomeSome into at least 4 outline touchpoints, including multiple substantive sections, so the draft does not quarantine the product to one late block.",
            issues,
        )

    def test_validate_outline_requires_early_and_repeated_product_headings(self):
        outline = """# Best Omegle Alternative\n\n## Why random chat feels empty now\n- frame the problem\n\n## Why skip culture burns people out\n- more problem context\n\n## Where burnt-out users usually go next\n- still generic\n\n## Why SomeSome might be worth trying after all that frustration finally piles up\n- first product heading comes too late\n\n## Closing thought on trying something else\n- CTA only\n"""
        issues = _validate_outline(outline)
        self.assertIn("Name SomeSome in at least 3 H2 headings so the article structure itself carries the recommendation instead of feeling like generic advice with one product section attached.", issues)
        self.assertIn("Bring SomeSome into one of the first 3 H2 headings so the product fit starts early instead of arriving late.", issues)

    def test_validate_outline_rejects_overlong_h2_headings(self):
        outline = """# Best Omegle Alternative\n\n## Why random chat feels empty when every platform keeps teaching people to disconnect before anything human can happen\n- frame the problem\n\n## Why SomeSome may be worth trying when you are tired of apps that keep training everyone to move too fast\n- product tie-in\n\n## How SomeSome fits when you want conversation instead of another endless parade of instant skips and disposable chats\n- product tie-in\n\n## What changes when you stop chasing speed and start looking for patience in random video chat\n- decision shift\n\n## Why SomeSome is the honest next step when you still want spontaneous conversation online\n- CTA\n"""
        issues = _validate_outline(outline)
        self.assertTrue(any("overly long h2 headings" in issue.lower() for issue in issues))

    def test_validate_outline_rejects_generic_somesome_promo_headings_and_missing_fit_limits(self):
        outline = """# Best Omegle Alternative\n\n## Why random chat became a disconnect factory\n- pain\n\n## What Makes SomeSome Different from High-Volume Chat Apps\n- product intro\n\n## When SomeSome Works Best\n- product fit but only as praise\n\n## Why SomeSome Beats Generic Omegle Clones\n- more promo framing\n\n## Why SomeSome is the honest next step tonight\n- CTA\n"""
        issues = _validate_outline(outline)
        joined = "\n".join(issues)
        self.assertIn("What Makes SomeSome Different from High-Volume Chat Apps", joined)
        self.assertIn("Include one honest outline section or bullet", joined)

    def test_validate_outline_rejects_speculative_somesome_usage_logic_and_late_section_integration(self):
        outline = """# Best Omegle Alternative for Real Conversations\n\n## Random Chat Apps Have Become Skip-Happy Wastelands\n- explain why mainstream apps feel disposable now\n\n## Why SomeSome Might Break Your Skip Addiction\n- SomeSome positions itself as the calmer option\n- the smaller user base means fewer bots and more intentional users\n\n## When SomeSome Won't Fix Your Random Chat Problems\n- off-peak hours are slower and the platform moves at a better pace for real conversations\n\n## If You Miss Actual Online Conversations\n- mention SomeSome near the end of the section after generic advice\n\n## Making the Switch to SomeSome Worth Your Time\n- suggest the best times to use SomeSome for better chats\n"""
        issues = _validate_outline(outline)
        joined = "\n".join(issues)
        self.assertIn("unsupported SomeSome positioning", joined)
        self.assertIn("decision-driving sections", joined)
        self.assertIn("Making the Switch to SomeSome Worth Your Time", joined)

    def test_has_fit_limit_signal_accepts_new_honest_tradeoff_phrases(self):
        self.assertTrue(_has_fit_limit_signal("When SomeSome may not be your move tonight"))
        self.assertTrue(_has_fit_limit_signal("What SomeSome will not solve for you after another dead chat spiral"))
        self.assertTrue(_has_fit_limit_signal("If you want fast novelty, you may be better off elsewhere than SomeSome"))

    def test_env_or_default_honors_configured_free_models(self):
        previous_writer = os.environ.get("WRITER_MODEL")
        previous_reviewer = os.environ.get("REVIEWER_MODEL")
        try:
            os.environ["WRITER_MODEL"] = "meta-llama/llama-3.3-70b-instruct:free"
            os.environ["REVIEWER_MODEL"] = "google/gemma-4-31b-it:free"
            self.assertEqual(_env_or_default("WRITER_MODEL", "google/gemini-2.0-flash-001"), "meta-llama/llama-3.3-70b-instruct:free")
            self.assertEqual(_env_or_default("REVIEWER_MODEL", "anthropic/claude-3.5-haiku"), "google/gemma-4-31b-it:free")
        finally:
            if previous_writer is None:
                os.environ.pop("WRITER_MODEL", None)
            else:
                os.environ["WRITER_MODEL"] = previous_writer
            if previous_reviewer is None:
                os.environ.pop("REVIEWER_MODEL", None)
            else:
                os.environ["REVIEWER_MODEL"] = previous_reviewer

    def test_extract_json_handles_trailing_text_after_object(self):
        payload = extract_json('{"title":"Hello"}\n\nHere is why this title works.')
        self.assertEqual(payload["title"], "Hello")

    def test_extract_json_with_repair_uses_model_when_raw_json_is_broken(self):
        client = FakeClient(
            {
                "writer-primary": OpenRouterError("rate limited", status_code=429),
                "writer-fallback": '{"title": "Broken", "content": "<p class=\'hero\'>oops</p>"}',
            }
        )
        attempts = []
        payload = _extract_json_with_repair(
            client,
            text='{"title": "Broken", "content": "<p class="hero">oops</p>"}',
            role="writer_draft",
            primary_model="writer-primary",
            fallback_models=["writer-fallback"],
            attempts=attempts,
        )
        self.assertEqual(payload["title"], "Broken")
        self.assertIn("hero", payload["content"])
        self.assertEqual(client.calls, ["writer-primary", "writer-fallback"])
        self.assertEqual(len(attempts), 2)
        self.assertEqual(attempts[0].role, "writer_draft_json_repair")
        self.assertFalse(attempts[0].ok)
        self.assertEqual(attempts[1].role, "writer_draft_json_repair")
        self.assertTrue(attempts[1].ok)

    def test_salvage_post_json_recovers_broken_content_field_quotes(self):
        raw = '''
        {
          "title": "Broken",
          "subtitle": "Still usable",
          "slug": "broken",
          "content": "<p class="hero">oops</p>",
          "excerpt": "Short summary",
          "image": "https://example.com/image.jpg",
          "imageAlt": "People chatting online",
          "authorName": "SomeSome Team",
          "authorPosition": "SomeSome Editorial",
          "authorAvatar": "",
          "date": "2026-04-11",
          "draft": false
        }
        '''
        payload = salvage_post_json(raw)
        self.assertIsNotNone(payload)
        self.assertEqual(payload["slug"], "broken")
        self.assertIn('class="hero"', payload["content"])
        self.assertFalse(payload["draft"])

    def test_run_pipeline_end_to_end_with_stubbed_client_writes_publishable_artifacts(self):
        config = BlogPipelineConfig(
            topic="best omegle alternative for people who actually want to talk at night",
            audience="young adults who miss spontaneous late-night conversations online but are tired of bots and instant skips",
            tone="raw, honest, internet-native, useful",
            cta="Naturally invite readers to try SomeSome when they want random chat that feels more human",
            keywords="best omegle alternative, random video chat for real conversations, late night random chat app",
            writer_model="writer-primary",
            reviewer_model="reviewer-primary",
            writer_fallback_models=["writer-fallback"],
            reviewer_fallback_models=["reviewer-fallback"],
        )
        fake_client = EndToEndFakeClient()
        with tempfile.TemporaryDirectory() as tmpdir, patch("hermes_blog.pipeline.OpenRouterClient.from_env", return_value=fake_client):
            run_dir = run_pipeline(config, base_dir=tmpdir)
            self.assertTrue((run_dir / "final_post.json").exists())
            self.assertTrue((run_dir / "summary.json").exists())
            final_post = extract_json((run_dir / "final_post.json").read_text(encoding="utf-8"))
            summary = extract_json((run_dir / "summary.json").read_text(encoding="utf-8"))
            self.assertEqual(_validate_post(final_post, config=config, brief=extract_json((run_dir / "brief.json").read_text(encoding="utf-8"))), [])
            self.assertTrue(summary["reviewer_ready"])
            self.assertEqual(summary["last_review_verdict"], "ready")
            self.assertGreaterEqual(summary["word_count"], 1500)

    def test_run_pipeline_keeps_artifacts_when_validation_issues_exist_by_default(self):
        config = BlogPipelineConfig(
            topic="best omegle alternative for cross-language chats",
            audience="people who want to meet users from other countries",
            tone="raw, honest, useful",
            cta="Invite readers to try SomeSome",
            keywords="best omegle alternative, cross language random chat",
            writer_model="writer-primary",
            reviewer_model="reviewer-primary",
        )
        fake_client = EndToEndFakeClient()
        fake_client.draft["content"] = "<p>Too short.</p><h2>Why SomeSome matters</h2><p>Short.</p>"
        with tempfile.TemporaryDirectory() as tmpdir, patch("hermes_blog.pipeline.OpenRouterClient.from_env", return_value=fake_client):
            run_dir = run_pipeline(config, base_dir=tmpdir)
            summary = extract_json((run_dir / "summary.json").read_text(encoding="utf-8"))
            self.assertTrue((run_dir / "final_post.json").exists())
            self.assertFalse(summary["strict_validation"])
            self.assertTrue(summary["validation_issues"])
            self.assertTrue(any("at least 1500 words" in issue for issue in summary["validation_issues"]))

    def test_run_pipeline_strict_validation_still_raises_on_invalid_final_post(self):
        config = BlogPipelineConfig(
            topic="best omegle alternative for cross-language chats",
            audience="people who want to meet users from other countries",
            tone="raw, honest, useful",
            cta="Invite readers to try SomeSome",
            keywords="best omegle alternative, cross language random chat",
            writer_model="writer-primary",
            reviewer_model="reviewer-primary",
            strict_validation=True,
        )
        fake_client = EndToEndFakeClient()
        fake_client.draft["content"] = "<p>Too short.</p><h2>Why SomeSome matters</h2><p>Short.</p>"
        with tempfile.TemporaryDirectory() as tmpdir, patch("hermes_blog.pipeline.OpenRouterClient.from_env", return_value=fake_client):
            with self.assertRaises(ValueError):
                run_pipeline(config, base_dir=tmpdir)


if __name__ == "__main__":

    unittest.main()
