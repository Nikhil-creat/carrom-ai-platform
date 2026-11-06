"""RAG knowledge base: Carrom rules + tactical shot library.

Embeddings default to a deterministic hashing embedder (no model download,
works offline, good enough for short tactical snippets). Swap in
`SentenceTransformerEmbedder` for semantic recall when the model is available.
Storage defaults to in-memory; `database.vector_store.PgVectorStore` has the
same `upsert` / `search` surface for PostgreSQL + pgvector.

IMPORTANT: the seed documents below are *paraphrased summaries written for
this project*, not the official ICF rulebook text and without official rule
numbers. Ingest the real rulebook with `RagPipeline.ingest()` and keep
`rules.py` consistent with it.
"""
from __future__ import annotations

import hashlib
import math
import re
from dataclasses import dataclass, field
from typing import Any, Protocol

import numpy as np

from config import settings


@dataclass
class Document:
    id: str
    kind: str                # "rule" | "tactic"
    title: str
    text: str
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass
class Hit:
    doc: Document
    score: float

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.doc.id, "kind": self.doc.kind, "title": self.doc.title,
                "score": round(self.score, 3), "meta": self.doc.meta}


class Embedder(Protocol):
    dim: int

    def embed(self, text: str) -> np.ndarray: ...


_TOKEN = re.compile(r"[a-z0-9]+")


class HashingEmbedder:
    """Signed feature hashing over word unigrams/bigrams + char trigrams."""

    def __init__(self, dim: int | None = None) -> None:
        self.dim = dim or settings.embedding_dim

    def _feats(self, text: str):
        words = _TOKEN.findall(text.lower())
        for w in words:
            yield w, 1.0
            padded = f"^{w}$"
            for i in range(len(padded) - 2):
                yield "c:" + padded[i:i + 3], 0.35
        for a, b in zip(words, words[1:]):
            yield f"{a}_{b}", 0.7

    def embed(self, text: str) -> np.ndarray:
        v = np.zeros(self.dim, dtype=np.float32)
        for feat, wgt in self._feats(text):
            h = hashlib.blake2b(feat.encode(), digest_size=8).digest()
            n = int.from_bytes(h, "little")
            v[n % self.dim] += wgt if (n >> 63) & 1 else -wgt
        norm = float(np.linalg.norm(v))
        return v / norm if norm > 0 else v


class SentenceTransformerEmbedder:  # pragma: no cover - optional heavy dependency
    def __init__(self, model: str = "sentence-transformers/all-MiniLM-L6-v2") -> None:
        from sentence_transformers import SentenceTransformer

        self.model = SentenceTransformer(model)
        self.dim = self.model.get_sentence_embedding_dimension()

    def embed(self, text: str) -> np.ndarray:
        v = self.model.encode(text, normalize_embeddings=True)
        return np.asarray(v, dtype=np.float32)


class VectorStore(Protocol):
    def upsert(self, doc: Document, vec: np.ndarray) -> None: ...
    def search(self, vec: np.ndarray, k: int, kind: str | None) -> list[Hit]: ...


class InMemoryVectorStore:
    def __init__(self) -> None:
        self.docs: list[Document] = []
        self.mat: np.ndarray | None = None

    def upsert(self, doc: Document, vec: np.ndarray) -> None:
        for i, d in enumerate(self.docs):
            if d.id == doc.id:
                self.docs[i] = doc
                self.mat[i] = vec  # type: ignore[index]
                return
        self.docs.append(doc)
        self.mat = vec[None, :] if self.mat is None else np.vstack([self.mat, vec])

    def search(self, vec: np.ndarray, k: int, kind: str | None) -> list[Hit]:
        if self.mat is None:
            return []
        sims = self.mat @ vec
        order = np.argsort(-sims, kind="stable")
        out: list[Hit] = []
        for i in order:
            if kind and self.docs[i].kind != kind:
                continue
            out.append(Hit(self.docs[i], float(sims[i])))
            if len(out) >= k:
                break
        return out


# --------------------------------------------------------------------- seed data
SEED_RULES = [
    Document("rule.baseline", "rule", "Striker placement",
             "The striker must be placed on your own baseline between the end circles and "
             "must not overlap a coin. Shots are taken from the baseline."),
    Document("rule.own_coin", "rule", "Pocketing coins",
             "Pocketing one of your own coins scores a point and you shoot again. "
             "Pocketing an opponent coin credits the opponent and normally ends your turn."),
    Document("rule.queen_cover", "rule", "Queen covering",
             "The queen is worth more than a coin but must be covered: pocket one of your own "
             "coins on the same shot or the immediately following strike, otherwise the queen "
             "returns to the centre and your turn ends.", {"strategy": "QUEEN_COVER"}),
    Document("rule.striker_foul", "rule", "Striker pocketed foul",
             "If the striker is pocketed it is a foul: coins pocketed on that shot return to the "
             "centre, one previously pocketed coin is returned as a penalty and the turn passes."),
    Document("rule.last_coin", "rule", "Last coin before queen",
             "Pocketing your last coin before the queen is resolved does not win; the coin is "
             "returned to the centre of the board."),
]

SEED_TACTICS = [
    Document("tac.direct_open", "tactic", "Direct pot on an open line",
             "Open clear line from striker to coin and from coin to a corner pocket; small cut "
             "angle. Take the direct shot with medium force, aim the ghost ball behind the coin.",
             {"strategy": "DIRECT_SHOT", "bias": 1.0}),
    Document("tac.thin_cut", "tactic", "Thin cut angle",
             "Large cut angle needs a softer controlled strike; avoid when the striker could drift "
             "into a pocket. Prefer repositioning along the baseline for a fuller hit.",
             {"strategy": "DIRECT_SHOT", "bias": -0.4}),
    Document("tac.bank_blocked", "tactic", "Bank shot when direct line is blocked",
             "Blocking coin on the direct line to the pocket: rebound the coin off a cushion into "
             "the pocket, an indirect bank shot using a mirrored pocket target. Needs extra force "
             "for cushion loss.", {"strategy": "INDIRECT_BANK_SHOT", "bias": 1.4}),
    Document("tac.double_bank", "tactic", "Double bank trick shot",
             "Coin hugging the cushions near a corner with blockers everywhere: double rebound "
             "across two cushions into the far pocket. High risk, high reward, only with "
             "precise aim.", {"strategy": "DOUBLE_BANK_SHOT", "bias": 1.1}),
    Document("tac.break", "tactic", "Opening break",
             "At the start with the full rosette untouched, strike the centre cluster from the "
             "middle of the baseline with firm force to spread coins and open lines.",
             {"strategy": "BREAK_SHOT", "bias": 1.2}),
    Document("tac.safety", "tactic", "Defensive safety placement",
             "No makeable scoring shot: play a soft shot that leaves few clean lines for the "
             "opponent and keeps the striker away from pockets. Block the opponent's best coins.",
             {"strategy": "SAFETY_PLACEMENT", "bias": 0.9}),
    Document("tac.queen_follow", "tactic", "Queen with a cover coin",
             "Queen is exposed near a pocket and one of your coins sits close to the same pocket: "
             "pot the queen first then cover immediately with your coin.",
             {"strategy": "QUEEN_COVER", "bias": 1.0}),
    Document("tac.block_clear", "tactic", "Clear a blocker",
             "A coin blocks several lines: strike the cluster to scatter blockers and "
             "open pockets for the next visit.", {"strategy": "BLOCK_CLEAR", "bias": 0.5}),
]


class RagPipeline:
    def __init__(self, store: VectorStore | None = None, embedder: Embedder | None = None,
                 seed: bool = True) -> None:
        self.embedder = embedder or HashingEmbedder()
        self.store = store or InMemoryVectorStore()
        if seed:
            for d in SEED_RULES + SEED_TACTICS:
                self.ingest(d)

    def ingest(self, doc: Document) -> None:
        self.store.upsert(doc, self.embedder.embed(f"{doc.title}. {doc.text}"))

    def retrieve(self, query: str, k: int = 3, kind: str | None = None) -> list[Hit]:
        return self.store.search(self.embedder.embed(query), k, kind)

    @staticmethod
    def describe_situation(a: dict[str, Any]) -> str:
        """Turn the Analyst's board analysis into a retrieval query."""
        parts = []
        if a.get("opening"):
            parts.append("opening break rosette untouched centre cluster")
        if a.get("direct_open"):
            parts.append("open clear line direct pot small cut angle")
        else:
            parts.append("direct line blocked by coin")
        if a.get("blocked_ratio", 0) > 0.5:
            parts.append("many blockers rebound cushion bank shot mirrored pocket")
        if a.get("near_wall_coins"):
            parts.append("coin near cushion double rebound")
        if not a.get("has_scoring_candidate"):
            parts.append("no makeable shot defensive safety placement")
        if a.get("queen_on_board") and a.get("queen_exposed"):
            parts.append("queen exposed cover coin")
        if a.get("queen_pending"):
            parts.append("queen cover pending own coin pocket")
        return " ".join(parts)
