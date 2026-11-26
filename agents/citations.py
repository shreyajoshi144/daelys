"""Citation checker (design rule 3).

Every sentence shown to users must cite at least one source ([evt:ID] event or [doc:ID] document
chunk), and every cited source must be in the evidence the model was given. Anything else is
dropped, never repaired or rewritten."""

import re
from dataclasses import dataclass, field

MARKER = re.compile(r"\[evt:(\d+)\]")
DOC_MARKER = re.compile(r"\[doc:([0-9a-f-]{36})\]")
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+|(?<=\])\s+(?=[A-Z])")


@dataclass(frozen=True)
class CitedSentence:
    text: str
    event_ids: list[int]
    doc_ids: list[str] = field(default_factory=list)


@dataclass
class CheckedText:
    sentences: list[CitedSentence] = field(default_factory=list)
    dropped: list[str] = field(default_factory=list)


def split_sentences(text_: str) -> list[str]:
    return [s.strip() for s in _SENTENCE_END.split(text_.strip()) if s.strip()]


def check(sentences: list[str], allowed: set[int], allowed_docs: frozenset[str] = frozenset()) -> CheckedText:
    out = CheckedText()
    for raw in sentences:
        for sentence in split_sentences(raw):
            ids = [int(m) for m in MARKER.findall(sentence)]
            docs = DOC_MARKER.findall(sentence)
            cited = bool(ids or docs)
            valid = all(i in allowed for i in ids) and all(d in allowed_docs for d in docs)
            if cited and valid:
                out.sentences.append(
                    CitedSentence(sentence, list(dict.fromkeys(ids)), list(dict.fromkeys(docs)))
                )
            else:
                out.dropped.append(sentence)
    return out
