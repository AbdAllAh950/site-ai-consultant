"""Knowledge base from Markdown: every '## ' heading starts a chunk. Small bases go to the model whole;
large ones are narrowed to the chunks that match the question."""
from __future__ import annotations

import re
from dataclasses import dataclass

WHOLE_KB_LIMIT = 9000          # characters; below this the whole base is cheaper than a wrong answer
_WORD = re.compile(r"\w+", re.UNICODE)
_STOP = {
    "а", "в", "во", "и", "к", "ли", "на", "не", "но", "о", "об", "по", "с", "со", "у", "я", "вы", "вас",
    "вам", "ваш", "мне", "мы", "что", "как", "это", "есть", "можно", "для", "до", "сколько", "какой",
    "какие", "какая", "где", "когда", "если", "у", "the", "a", "an", "is", "are", "do", "you", "i", "how",
    "what", "to", "of", "in", "for", "can", "في", "من", "على", "هل", "كم", "ما", "عن",
}


def stems(text: str) -> set[str]:
    """Lowercase words without stop-words, cut to 5 letters: a crude stemmer that works for Russian."""
    return {w[:5] for w in _WORD.findall(text.lower()) if w not in _STOP and len(w) > 1}


@dataclass(frozen=True)
class Chunk:
    title: str
    body: str

    @property
    def text(self) -> str:
        return f"## {self.title}\n{self.body}".strip()


class KnowledgeBase:
    def __init__(self, markdown: str):
        self.intro, self.chunks = self._parse(markdown)
        self._index = [(stems(c.title), stems(c.body)) for c in self.chunks]

    @staticmethod
    def _parse(markdown: str) -> tuple[str, list[Chunk]]:
        intro, chunks, title, lines = [], [], None, []
        for line in markdown.splitlines():
            if line.startswith("## "):
                if title is not None:
                    chunks.append(Chunk(title, "\n".join(lines).strip()))
                title, lines = line[3:].strip(), []
            elif title is None:
                if not line.startswith("# "):
                    intro.append(line)
            else:
                lines.append(line)
        if title is not None:
            chunks.append(Chunk(title, "\n".join(lines).strip()))
        return "\n".join(intro).strip(), chunks

    def search(self, query: str, k: int = 5) -> list[tuple[float, Chunk]]:
        q = stems(query)
        if not q:
            return []
        scored = []
        for chunk, (title, body) in zip(self.chunks, self._index):
            score = 2 * len(q & title) + len(q & body)
            if score:
                scored.append((score / len(q), chunk))
        scored.sort(key=lambda pair: pair[0], reverse=True)
        return scored[:k]

    def select(self, query: str) -> tuple[str, list[tuple[float, Chunk]], bool]:
        """(context for the model, ranked hits, whether the whole base was sent)."""
        hits = self.search(query)
        full = "\n\n".join([self.intro, *(c.text for c in self.chunks)]).strip()
        if len(full) <= WHOLE_KB_LIMIT:
            return full, hits, True
        if not hits:
            # no word in common — typically a visitor writing in English or Arabic to a Russian base:
            # better as many sections as fit than the intro alone (evals/: keyword hit@3 is 0 for en/ar)
            parts, size = [self.intro], len(self.intro)
            for chunk in self.chunks:
                if size + len(chunk.text) + 2 > WHOLE_KB_LIMIT:
                    break
                parts.append(chunk.text)
                size += len(chunk.text) + 2
            return "\n\n".join(parts).strip(), hits, False
        return "\n\n".join([self.intro, *(c.text for _, c in hits)]).strip(), hits, False

    def context_for(self, query: str) -> str:
        return self.select(query)[0]

    def best_answer(self, query: str, min_score: float = 0.5) -> str | None:
        hits = self.search(query, k=1)
        if hits and hits[0][0] >= min_score:
            return hits[0][1].body
        return None
