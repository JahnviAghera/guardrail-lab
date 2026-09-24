"""S0: deterministic canonicalisation so later detectors see through cheap obfuscation."""
import base64
import binascii
import re
import unicodedata
from dataclasses import dataclass, field

ZERO_WIDTH = re.compile("[​-‏⁠﻿­]")
B64_TOKEN = re.compile(r"[A-Za-z0-9+/]{16,}={0,2}")
LEET_I = str.maketrans({"0": "o", "1": "i", "3": "e", "4": "a", "5": "s", "7": "t", "@": "a", "$": "s"})
LEET_L = str.maketrans({"0": "o", "1": "l", "3": "e", "4": "a", "5": "s", "7": "t", "@": "a", "$": "s"})


@dataclass
class NormalizedText:
    original: str
    text: str
    decoded_segments: list[str] = field(default_factory=list)
    flags: list[str] = field(default_factory=list)

    def variants(self) -> list[str]:
        """Every view of the input that detectors should scan."""
        views = [self.text, self.text.translate(LEET_I), self.text.translate(LEET_L), *self.decoded_segments]
        return list(dict.fromkeys(views))


def _try_b64(token: str) -> str | None:
    try:
        raw = base64.b64decode(token + "=" * (-len(token) % 4), validate=True)
        decoded = raw.decode("utf-8")
    except (binascii.Error, UnicodeDecodeError, ValueError):
        return None
    printable = sum(ch.isprintable() for ch in decoded) / max(len(decoded), 1)
    return decoded if printable > 0.9 and " " in decoded else None


def normalize(text: str) -> NormalizedText:
    flags = []
    out = unicodedata.normalize("NFKC", text)
    if out != text:
        flags.append("unicode_normalized")
    stripped = ZERO_WIDTH.sub("", out)
    if stripped != out:
        flags.append("zero_width_removed")
    out = re.sub(r"[ \t]+", " ", stripped).strip()

    decoded = [d for d in (_try_b64(t) for t in B64_TOKEN.findall(out)) if d]
    if decoded:
        flags.append("base64_decoded")
    if re.search(r"\b\w*[a-z][0-9@$][a-z]\w*\b", out, re.I) and re.search(r"[a-z][0137][a-z]", out, re.I):
        flags.append("possible_leetspeak")
    return NormalizedText(original=text, text=out, decoded_segments=decoded, flags=flags)
