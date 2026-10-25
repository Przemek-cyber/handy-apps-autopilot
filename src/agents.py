"""Agenci oparci o Claude API: redaktor (tworzy treść) i kontroler jakości (sprawdza ją)."""
from __future__ import annotations

import json
import os

import anthropic

from common import apps, brand, schedule

MODEL = os.environ.get("CLAUDE_MODEL") or "claude-sonnet-5-5"  # pusta zmienna w GitHub = domyślny model

SLIDE_SCHEMA = {
    "type": "object",
    "properties": {
        "kind": {"type": "string", "enum": ["cover", "point", "cta"]},
        "title": {"type": "string", "description": "Max 8 words. Wrap 1-3 key words in *asterisks* to highlight them in orange."},
        "body": {"type": "string", "description": "Max 18 words, no asterisks. For cover: a short subtitle (max 12 words). For cta: empty or max 10 words."},
        "clip": {"type": "string", "description": "Optional, point slides only: clip file name whose app screen illustrates this slide."},
    },
    "required": ["kind", "title", "body"],
}

SCENE_SCHEMA = {
    "type": "object",
    "properties": {
        "text": {"type": "string", "description": "On-screen text, max 12 words. Wrap 1-3 key words in *asterisks* to highlight them in orange."},
        "seconds": {"type": "number", "description": "Hook 1.8-2.2 s, other scenes 2.2-3.2 s."},
        "visual": {"type": "string", "enum": ["card", "clip"]},
        "clip": {"type": "string", "description": "File name from the available clips list, only when visual=clip."},
    },
    "required": ["text", "seconds", "visual"],
}

CONTENT_SCHEMA = {
    "type": "object",
    "properties": {
        "format": {"type": "string", "enum": ["carousel", "reel"], "description": "The format you chose for this post."},
        "hook": {"type": "string", "description": "First line that stops the scroll, max 10 words."},
        "slides": {"type": "array", "items": SLIDE_SCHEMA,
                   "description": "Carousel only: 4 slides, first 'cover', last 'cta'."},
        "scenes": {"type": "array", "items": SCENE_SCHEMA,
                   "description": "Reel only: 4 scenes, total 8-12 s, first scene is the hook, last is the CTA."},
        "caption": {"type": "string", "description": "Caption, 300-900 characters. Line 1 = keyword-rich hook "
                    "(the words people search for, e.g. 'bikepacking water planning'). Then short value. Then the QUESTION "
                    "on its own line. Last line = the call to action."},
        "question": {"type": "string", "description": "One easy, specific question that invites a short comment "
                     "(answerable in 1-5 words, e.g. 'How many litres do you have at home right now?'). Max 12 words. "
                     "Shown on the final video frame and in the caption."},
        "hashtags": {"type": "array", "items": {"type": "string"}, "description": "3-5 specific niche hashtags (no generic "
                     "ones like #love or #instagood), without spaces, each starting with #."},
        "alt_text": {"type": "string", "description": "Accessibility description of the post, max 200 characters."},
        "sources_used": {"type": "array", "items": {"type": "string"},
                         "description": "Which configured sources the advice relies on; empty if none."},
    },
    "required": ["hook", "caption", "question", "hashtags", "alt_text", "sources_used"],
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


def _json_from_text(text: str) -> dict | None:
    """Awaryjnie: wyciąga obiekt JSON z odpowiedzi tekstowej."""
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        return json.loads(text[start:end + 1])
    except json.JSONDecodeError:
        return None


def _call_tool(system: str, user: str, tool_name: str, schema: dict) -> dict:
    """Wynik jako wywołanie narzędzia (tool_choice=auto — nowsze modele nie przyjmują wymuszenia),
    z awaryjnym odczytem JSON z tekstu i jedną ponowną próbą."""
    system = (system + f"\n\nAlways deliver your final answer by calling the `{tool_name}` tool exactly once, "
              "with every required field filled. Do not answer in plain text.")
    messages = [{"role": "user", "content": user}]
    for _ in range(2):
        resp = _client().messages.create(
            model=MODEL,
            max_tokens=8000,
            system=system,
            tools=[{"name": tool_name, "description": "Return the result.", "input_schema": schema}],
            tool_choice={"type": "auto"},
            messages=messages,
        )
        text = ""
        for block in resp.content:
            if block.type == "tool_use" and block.name == tool_name:
                return block.input
            if block.type == "text":
                text += block.text
        if (data := _json_from_text(text)) and all(k in data for k in schema.get("required", [])):
            return data
        messages = messages + [{"role": "assistant", "content": resp.content},
                               {"role": "user", "content": f"Please call the `{tool_name}` tool now with the result."}]
    raise RuntimeError("Model nie zwrócił wyniku narzędzia")


def _product_brief(app_key: str) -> str:
    a = apps()[app_key]
    facts = "\n".join(f"- {f}" for f in a.get("facts", []) if f)
    sources = "\n".join(f"- {s}" for s in a.get("sources", [])) or "- (none configured)"
    return (
        f"PRODUCT: {a['name']}\nOne-liner: {a['one_liner']}\nAudience: {a['audience']}\n"
        f"Approved product facts (the ONLY claims allowed about the product):\n{facts}\n"
        f"Reference sources for advice:\n{sources}\n"
        + (f"Call to action (MANDATORY, last line of the caption, keep this meaning): Comment \"{a['dm_keyword']}\" and "
           f"I'll send you the link in DM — or tap the link in bio. (A comment-to-DM automation sends the checkout link "
           f"automatically.) Do not put the keyword in the final scene/slide text — the design adds a "
           f"'Comment {a['dm_keyword']} → link in DM' button there."
           if a.get("dm_enabled") and a.get("dm_keyword") else
           f"Call to action (last line of the caption): {a['cta']}\n"
           "Do NOT ask people to comment a keyword or promise to send DMs — there is no DM automation here.")
        + _platform_note(app_key)
    )


def _platform_note(app_key: str) -> str:
    plat = (schedule().get("platforms") or {}).get(app_key, "instagram")
    if plat == "tiktok":
        return ("\nPLATFORM: this post is published on TikTok (not Instagram). Write the caption for TikTok: "
                "short punchy first line (length rules unchanged), no 'link in DM'. Links in captions are not clickable — "
                "point people to the website name instead. Hashtags: 3-5, popular on TikTok in this niche.")
    return "\nPLATFORM: this post is published on Instagram."


def _rules() -> str:
    b = brand()
    return (
        f"You write for the Instagram/TikTok account '{b['name']}' — a faceless, product-focused account by an indie maker "
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


def _clips_text(clips: dict[str, str]) -> str:
    return "\n".join(f"- {name}: {desc}" for name, desc in clips.items()) or "(none)"


CAROUSEL_RULES = (
    "FORMAT: carousel. Return 'slides' (exactly 4): slide 1 kind=cover (title = the hook, body = a short subtitle), "
    "middle slides kind=point (one idea each), last slide kind=cta (title = short benefit line, the design adds "
    "the app logo, tagline and a call-to-action button). Give 1-3 point slides a 'clip' whose app screen matches "
    "the slide (product pillar: most point slides). Do not return 'scenes'. NOTE: the slides are published as a "
    "video slideshow with music (each slide is on screen for only 2-3 seconds; the whole video is 8-12 s), so keep slide bodies short — max 18 words."
)
REEL_RULES = (
    "FORMAT: reel (vertical video, no voice, text on screen, music bed). Return 'scenes' (exactly 4, total 8-12 s; short videos get watched to the end). The hook must be understood in under 1 second: max 7 words. "
    "Scene 1 = hook (visual=card, a bold statement or question). Last scene = CTA (visual=card; short benefit line — "
    "the design adds app logo, tagline and a call-to-action button). Middle scenes: mostly visual=clip, showing the app "
    "screen that matches the scene text (choose by clip description, avoid repeating a clip); visual=card only for a "
    "standalone fact. Do not return 'slides'."
)


def write_content(app_key: str, fmt: str, topic: dict, clips: dict[str, str], recent: list[str],
                  recent_formats: list[str] | None = None, hook_style: str = "") -> dict:
    """fmt = 'carousel' | 'reel' | 'auto' (agent wybiera format sam)."""
    if fmt == "auto":
        recent_formats = recent_formats or []
        banned = recent_formats[-1] if len(recent_formats) >= 2 and recent_formats[-1] == recent_formats[-2] else None
        fmt_rules = (
            "CHOOSE THE FORMAT yourself and set 'format'. Pick what fits the topic best: a reel when showing the app "
            "in action or a quick, punchy idea; a carousel for step-by-step lists, numbers, checklists or saveable tips.\n"
            f"Recent formats for this app (oldest first): {', '.join(recent_formats) or '(none)'}."
            + (f" You MUST NOT choose '{banned}' this time (never 3 in a row)." if banned else "")
            + f"\n\nIf carousel — {CAROUSEL_RULES}\n\nIf reel — {REEL_RULES}"
        )
    else:
        fmt_rules = CAROUSEL_RULES if fmt == "carousel" else REEL_RULES
    user = (
        f"{_product_brief(app_key)}\n\nTOPIC: {topic['topic']}\nPILLAR: {topic['pillar']}\n\n{fmt_rules}\n\n"
        + (f"HOOK STYLE (mandatory for the 'hook', the first scene/cover title and the first caption line): {hook_style}\n\n"
           if hook_style else "")
        + f"Available clips (real app screen recordings):\n{_clips_text(clips)}\n"
        f"Recent topics to avoid repeating angles: {'; '.join(recent) if recent else '(none)'}\n\n"
        "Write the post."
    )
    content = _call_tool(_rules(), user, "post_content", CONTENT_SCHEMA)
    if fmt != "auto":
        content["format"] = fmt
    elif content.get("format") not in ("carousel", "reel"):
        content["format"] = "reel" if content.get("scenes") else "carousel"
    return content


def review_content(app_key: str, fmt: str, content: dict, clips: dict[str, str] | None = None) -> dict:
    system = (
        "You are a strict quality editor for a social media account (Instagram or TikTok, see PLATFORM). You check a draft post before it is published automatically — there is no other human check, so be strict.\n"
        + _rules()
        + "\nCheck: (1) every product claim is in the approved facts; (2) advice is accurate, general and safe; "
        "(3) no fear-mongering; (4) English, clear, no typos; (5) structure matches the format "
        "(carousel 4 slides cover..cta, slide titles <= 8 words, bodies <= 18 words; reel 4 scenes, 8-12 s, "
        "scene text <= 12 words, clips only from the list and matching the text); highlight markup with *asterisks* "
        "only in slide titles / scene texts, never in caption; (6) caption 300-900 characters: keyword-rich first line, "
        "the question on its own line, ends with the exact call to action from the brief; (7) 'question' is easy to answer "
        "in a few words; (8) 3-5 specific niche hashtags, each spelled correctly and actually used on that platform (no typos, no invented compound words).\n"
        "verdict=pass if publishable as is; verdict=fix if small corrections make it publishable — then return the FULL "
        "corrected content in 'fixed'; verdict=reject if the idea itself is unsafe or off-brand."
    )
    user = (f"{_product_brief(app_key)}\n\nAvailable clips:\n{_clips_text(clips or {})}\n\nFORMAT: {fmt}\n\n"
            f"DRAFT:\n{json.dumps(content, ensure_ascii=False, indent=2)}")
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
