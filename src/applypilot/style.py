"""Writing rules shared by every prompt that produces text a person will read.

Cover letters, tailored resumes, form answers and content ideas all go out
under the applicant's name. Recruiters skim hundreds of AI-written letters a
week and spot the patterns fast, so the rules below name those patterns, and
`ai_tells()` catches the ones a regex can find so the text gets rewritten.
"""

from __future__ import annotations

import re

HUMAN_STYLE = """WRITING STYLE (applies to every sentence you write):
Write like the applicant typing a quick note to someone they respect: plain, concrete, a little terse.
Short sentences. Simple words. Facts, numbers, tool names, what got built and for whom.

Never use these patterns:
- Negation-reversal: "it isn't X, it's Y", "not just X but Y", "rather than X, Y". Just say Y.
- Punchy closing fragments: "That's the job.", "Simple as that.", "And it worked."
- Hype and superlatives: unlock, game-changing, seamless, robust, cutting-edge, dramatically, incredibly,
  "the single biggest", leverage, empower, elevate, transform, revolutionize, world-class.
- Filler intensifiers: honestly, genuinely, truly, deeply, deliberately, really, "the nice part", "the key is".
- Lists of three for rhythm ("fast, reliable and scalable"). Use two items, or four, or say the one that matters.
- Corporate openers and closers: "I am writing to express", "I am thrilled/excited", "I'd love the opportunity",
  "Thank you for your consideration", "I look forward to hearing from you", "Happy to walk through".
- AI vocabulary: delve, tapestry, landscape, realm, journey, navigate, foster, pivotal, testament, showcase,
  intricate, nuanced, embark, beacon, resonate, align with, at the intersection of, in today's fast-paced world.
- Em dashes. Use a comma or a full stop.
- Emoji, exclamation marks, rhetorical questions.
- Narrating yourself: "This shows...", "This demonstrates...", "which reflects my..."
If a sentence would fit in anyone's application, cut it."""

_TELL_PATTERNS: list[tuple[str, str]] = [
    (r"\b(isn't|is not|wasn't|aren't|not)\s+(just|only|merely|simply)\b[^.]{0,80}\b(but|it's|it is)\b", "negation-reversal"),
    (r"\b(it|this|that)\s+(isn't|is not|wasn't)\b[^.]{0,60}[,;]\s*(it's|it is|it was)\b", "negation-reversal"),
    (r"\brather than\b", "negation-reversal"),
    (r"\b(unlock\w*|game[- ]chang\w*|seamless\w*|robust|cutting[- ]edge|world[- ]class|revolutioni\w+)\b", "hype"),
    (r"\b(leverag\w+|empower\w*|elevat\w+|supercharg\w+)\b", "hype"),
    (r"\b(dramatically|incredibly|tremendous\w*|single biggest)\b", "hype"),
    (r"\b(honestly|genuinely|truly|deeply|deliberately|ruthlessly)\b", "filler"),
    (r"\b(delve\w*|tapestry|landscape|realm|embark\w*|beacon|testament to|showcas\w+|pivotal|intricate)\b", "ai-vocabulary"),
    (r"\b(fast-paced world|at the intersection of|align\w* (perfectly )?with)\b", "ai-vocabulary"),
    (r"\bi am (writing to|thrilled|excited|delighted)\b|\bi'?d love (the|to)\b|\bthrilled\b", "stock-opener"),
    (r"\b(look forward to hearing|thank you for (your )?consideration|happy to walk through)\b", "stock-closer"),
    (r"\bthis (shows|demonstrates|reflects|highlights)\b", "self-narration"),
    (r"[—–]", "em-dash"),
    (r"!", "exclamation"),
    (r"[\U0001F300-\U0001FAFF☀-➿]", "emoji"),
]


def ai_tells(text: str) -> list[str]:
    """Return the AI-writing patterns found in `text` (empty list = clean)."""
    found = []
    low = text.lower()
    for pattern, label in _TELL_PATTERNS:
        m = re.search(pattern, low if label != "emoji" else text)
        if m:
            found.append(f"{label}: '{m.group(0).strip()[:40]}'")
    return found
