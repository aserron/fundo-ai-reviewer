"""Untrusted bank description sanitizer and adversarial prompt-injection defense."""

import re
from typing import Tuple

# Regex to detect suspicious LLM-hijacking patterns in counterparty memos
INJECTION_PATTERNS = [
    re.compile(r"(?i)\bignore\s+(?:all\s+)?previous(?:\s+instructions?)?\b"),
    re.compile(r"(?i)\bsystem\s*override\b"),
    re.compile(r"(?i)\bdisregard\s+(?:all\s+)?rules\b"),
    re.compile(r"(?i)\bsystem\s*:"),
    re.compile(r"(?i)\bmark\s+as\s+revenue\b"),
    re.compile(r"(?i)\blabel\s+as\s+revenue(?:_verification)?\b"),
    re.compile(r"(?i)\bconfidence\s*=\s*0\.\d+\b"),
]

# Tags commonly targeted for delimiter escapes
XML_TAG_PATTERN = re.compile(r"<(/?[a-zA-Z0-9_\-]+(?:(?:\s+[^>]*)|/?))>")


def sanitize_bank_description(raw_text: str) -> str:
    """Normalize untrusted bank transaction memos.

    1. Collapse repeated whitespace and strip leading/trailing spaces.
    2. Remove non-printable control characters.
    3. Neutralize XML/HTML tags by entity-escaping `<` and `>` so untrusted
       counterparty memos cannot break out of prompt delimiters like
       `<untrusted_bank_memo>...</untrusted_bank_memo>`.
    """
    if not raw_text:
        return ""

    # Remove unprintable/control characters
    cleaned = "".join(ch for ch in raw_text if ch.isprintable() or ch in " \t\n")

    # Collapse repeated whitespace
    cleaned = re.sub(r"\s+", " ", cleaned).strip()

    # Escape XML delimiters
    cleaned = cleaned.replace("<", "&lt;").replace(">", "&gt;")

    return cleaned


def inspect_for_injection(raw_text: str) -> Tuple[str, bool]:
    """Sanitize description and determine whether it contains an injection attempt."""
    sanitized = sanitize_bank_description(raw_text)
    
    has_injection = any(pattern.search(raw_text) for pattern in INJECTION_PATTERNS)
    
    return sanitized, has_injection
