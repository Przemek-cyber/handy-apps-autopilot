"""Agenci oparci o Claude API: redaktor (tworzy treść) i kontroler jakości (sprawdza ją)."""
from __future__ import annotations

import json
import os

import anthropic

from common import apps, brand

MODEL = os.environ.get("CLAUDE_MODEL", "claude-sonnet-5-5")

SLIDE_SCHEMA = {
    "type": "object",
    "properties": {
        "kind": {"type": "string", "enum": ["cover", "point", "cta"]},
        "title": {"type": "string", "description": "Max 8 words."},
        "body": {"type": "string", "description": "Max 30 words. Empty for cover if subtitle is enough."},
    },
    "required": ["kind", "title", "body"],
}

SCENE_SCHEMA = {
    "type": "object",
    "properties": {
        "text": {"type": "string", "description": "On-screen text, max 12 words."},
        "seconds": {"type": "number", "description": "2.5 to 5 seconds."},
        "visual": {"type": "string", "enum": ["card", "clip"]},
        "clip": {"type": "string", "description": "File name from the available clips list, only when visual=clip."},
    },
    "required": ["text", "seconds", "visual"],
}

CONTENT_SCHEMA = {
    "type": "object",
    "properties": {
        "hook": {"type": "string", "description": "First line that stops the scroll, max 10 words."},
        "slides": {"type": "array", "items": SLIDE_SCHEMA,
                   "description": "Carousel only: 6-8 slides, first 'cover', last 'cta'."},
        "scenes": {"type": "array", "items": SCENE_SCHEMA,
                   "description": "Reel only: 5-7 scenes, total 18-30 s, first scene is the hook, last is the CTA."},
        "caption": {"type": "string", "description": "Instagram caption, 400-1200 characters, line breaks allowed, ends with the CTA."},
        "hashtags": {"type": "array", "items": {"type": "string"}, "description": "5-10 hashtags without spaces, each starting with #."},
        "alt_text": {"type": "string", "description": "Accessibility description of the post, max 200 characters."},
        "sources_used": {"type": "array", "items": {"type": "string"},
                         "description": "Which configured sources the advice relies on; empty if none."},
    },
    "required": ["hook", "caption", "hashtags", "alt_text", "sources_used"],
}

QA_SCHEMA = {
    "type": "object",
    "properties": {
        "verdict": {"type": "string", "enum": ["pass", "fix", "reject"]},
        "issues": {"type": "array", "items": {"type": "string"}},
        "fixed": {**CONTENT_SCHEMA, "description": "Full corrected content when verdict=fix."},
    },
    "required": ["verdict", "issues"],
}


def _client() -> anthropic.Anthropic:
    return anthropic.Anthropic()  # czyta ANTHROPIC_API_KEY


def _call_tool(system: str, user: str, tool_name: str, schema: dict) -> dict:
    resp = _client().messages.create(
        model=MODEL,
        max_tokens=4000,
        system=system,
        tools=[{"name": tool_name, "description": "Return the result.", "input_schema": schema}],
        tool_choice={"type": "tool", "name": tool_name},
        messages=[{"role": "user", "content": user}],
    )
    for block in resp.content:
        if block.type == "tool_use":
            return block.input
    raise RuntimeError("Model nie zwrócił wyniku narzędzia")


def _product_brief(app_key: str) -> str:
    a = apps()[app_key]
    facts = "\n".join(f"- {f}" for f in a.get("facts", []) if f)
    sources = "\n".join(f"- {s}" for s in a.get("sources", [])) or "- (none configured)"
    return (
        f"PRODUCT: {a['name']}\nOne-liner: {a['one_liner']}\nAudience: {a['audience']}\n"
        f"Approved product facts (the ONLY claims allowed about the product):\n{facts}\n"
        f"Reference sources for advice:\n{sources}\n"
        f"Call to action: {a['cta']}\nDM keyword (optional, 'Comment {a['dm_keyword']}' style): {a['dm_keyword']}"
    )


def _rules() -> str:
    b = brand()
    return (
        f"You write for the Instagram account '{b['name']}' — a faceless, product-focused account by an indie maker "
        "who builds practical apps with AI. Language: English only.\n"
        f"Tone: {b['voice']['tone']}\n"
        "Hard rules:\n"
        "- Claim nothing about the product beyond the approved facts. No invented features, prices, numbers of users or sales.\n"
        "- Preparedness advice must be general, widely agreed guidance consistent with official civil-protection agencies; "
        "no medical dosing, no weapons, nothing dangerous. When unsure, give less detail, not more.\n"
        "- Never use fear, doom or disaster clickbait. Calm and empowering.\n"
        "- Value first: the post must be useful even to someone who never buys. Product mention is light except in 'product' pillar posts.\n"
        "- Faceless: no references to a presenter's face or voice; on-screen text carries the message."
    )


def write_content(app_key: str, fmt: str, topic: dict, clips: list[str], recent: list[str]) -> dict:
    fmt_rules = (
        "FORMAT: carousel. Return 'slides' (6-8): slide 1 kind=cover (title = the hook, body = a short subtitle), "
        "middle slides kind=point (one idea each), last slide kind=cta. Do not return 'scenes'."
        if fmt == "carousel" else
        "FORMAT: reel (vertical video, no voice, text on screen, music bed). Return 'scenes' (5-7, total 18-30 s). "
        "Scene 1 = hook, last scene = CTA. Use visual=clip only with a file name from the available clips list; "
        "otherwise visual=card. Do not return 'slides'."
    )
    user = (
        f"{_product_brief(app_key)}\n\nTOPIC: {topic['topic']}\nPILLAR: {topic['pillar']}\n\n{fmt_rules}\n\n"
        f"Available clips: {', '.join(clips) if clips else '(none)'}\n"
        f"Recent topics to avoid repeating angles: {'; '.join(recent) if recent else '(none)'}\n\n"
        "Write the post."
    )
    return _call_tool(_rules(), user, "post_content", CONTENT_SCHEMA)


def review_content(app_key: str, fmt: str, content: dict) -> dict:
    system = (
        "You are a strict quality editor for an Instagram account. You check a draft post before it is shown to the owner.\n"
        + _rules()
        + "\nCheck: (1) every product claim is in the approved facts; (2) advice is accurate, general and safe; "
        "(3) no fear-mongering; (4) English, clear, no typos; (5) structure matches the format "
        "(carousel 6-8 slides cover..cta, slide titles <= 8 words, bodies <= 30 words; reel 5-7 scenes, 18-30 s, "
        "scene text <= 12 words, clips only from the list); (6) caption 400-1200 characters ending with the CTA; "
        "(7) 5-10 hashtags.\n"
        "verdict=pass if publishable as is; verdict=fix if small corrections make it publishable — then return the FULL "
        "corrected content in 'fixed'; verdict=reject if the idea itself is unsafe or off-brand."
    )
    user = f"{_product_brief(app_key)}\n\nFORMAT: {fmt}\n\nDRAFT:\n{json.dumps(content, ensure_ascii=False, indent=2)}"
    return _call_tool(system, user, "qa_result", QA_SCHEMA)


def new_topic(app_key: str, used: list[str]) -> dict:
    """Gdy bank tematów się wyczerpie — nowy temat w tych samych filarach."""
    schema = {
        "type": "object",
        "properties": {"topic": {"type": "string"},
                       "pillar": {"type": "string", "enum": ["value", "product", "community"]}},
        "required": ["topic", "pillar"],
    }
    user = (f"{_product_brief(app_key)}\n\nAlready used topics:\n- " + "\n- ".join(used) +
            "\n\nPropose ONE new topic. Prefer 'value' (60%), then 'product', then 'community'.")
    return _call_tool(_rules(), user, "topic", schema)
