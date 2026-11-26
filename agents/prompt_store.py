"""Versioned prompts in agents/prompts/<name>_v<N>.md with `## system` and `## user` sections.

Untrusted text is only ever substituted into the user section, inside an <evidence> block (design rule 6).
"""

import re
from dataclasses import dataclass
from functools import cache
from pathlib import Path

PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"
_VERSION = re.compile(r"^[a-z_]+_v\d+$")
_SECTIONS = re.compile(r"^## (system|user)\s*$", re.M)
_PLACEHOLDER = re.compile(r"\{([a-z_]+)\}")


@dataclass(frozen=True)
class Prompt:
    version: str
    system: str
    user_template: str

    def render_user(self, **untrusted: str) -> str:
        """Fill `{name}` placeholders with escaped evidence in one pass, so text inside one value can never be
        treated as a placeholder for another."""
        wanted = set(_PLACEHOLDER.findall(self.user_template))
        if wanted != untrusted.keys():
            raise KeyError(f"{self.version} expects {sorted(wanted)}, got {sorted(untrusted)}")
        return _PLACEHOLDER.sub(lambda m: escape_evidence(untrusted[m.group(1)]), self.user_template)


def escape_evidence(value: str) -> str:
    """Stop untrusted text from closing or opening an evidence block."""
    return re.sub(r"<(/?evidence)", r"&lt;\1", value, flags=re.I)


@cache
def load_prompt(version: str) -> Prompt:
    if not _VERSION.match(version):
        raise ValueError(f"invalid prompt version {version!r}")
    raw = (PROMPTS_DIR / f"{version}.md").read_text()
    parts = _SECTIONS.split(raw)
    sections = {parts[i]: parts[i + 1].strip() for i in range(1, len(parts) - 1, 2)}
    if set(sections) != {"system", "user"}:
        raise ValueError(f"{version} must have exactly '## system' and '## user' sections")
    if "{" in sections["system"]:
        raise ValueError(f"{version}: the system section takes no placeholders")
    return Prompt(version, sections["system"], sections["user"])
