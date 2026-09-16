"""L2: syndicated and near-identical documents collapse into one origin."""

from __future__ import annotations

import hashlib

from datasketch import MinHash, MinHashLSH

from research_agent.agent.text import tokens

_SCHEME = "affine32"


def _normalised(text: str) -> str:
    return " ".join(tokens(text, keep_stopwords=True))


def content_hash(text: str) -> str:
    return hashlib.sha256(_normalised(text).encode()).hexdigest()


def _shingles(text: str, size: int) -> set[bytes]:
    words = tokens(text, keep_stopwords=True)
    if len(words) < size:
        return {" ".join(words).encode()} if words else set()
    return {" ".join(words[i : i + size]).encode() for i in range(len(words) - size + 1)}


def minhash_signature(text: str, *, num_perm: int, shingle_size: int) -> list[int]:
    signature = MinHash(num_perm=num_perm, scheme=_SCHEME)
    for shingle in _shingles(text, shingle_size):
        signature.update(shingle)
    return [int(value) for value in signature.hashvalues]


class OriginIndex:
    """Assigns each document an origin id; near-duplicates share one.

    Exact matches (normalised text hash) are checked first - cheap and certain - then MinHash
    LSH at the configured Jaccard threshold. The first document of a group names the origin.
    """

    def __init__(self, *, threshold: float, num_perm: int, shingle_size: int) -> None:
        self._num_perm = num_perm
        self._shingle_size = shingle_size
        self._lsh = MinHashLSH(threshold=threshold, num_perm=num_perm)
        self._by_hash: dict[str, str] = {}
        self._origin_of: dict[str, str] = {}
        self._hash_of: dict[str, str] = {}
        self.signatures: dict[str, list[int]] = {}

    def content_hash_of(self, doc_id: str) -> str:
        return self._hash_of[doc_id]

    def origin_of(self, doc_id: str) -> str | None:
        return self._origin_of.get(doc_id)

    def restore(
        self, doc_id: str, origin_id: str, signature: list[int], *, content_hash: str
    ) -> None:
        """Re-register a document from a checkpoint without re-reading its text."""
        self._origin_of[doc_id] = origin_id
        self._hash_of[doc_id] = content_hash
        self._by_hash.setdefault(content_hash, origin_id)
        if signature:
            self.signatures[doc_id] = signature
            self._lsh.insert(doc_id, self._minhash(signature))

    def assign(self, doc_id: str, text: str) -> str:
        if doc_id in self._origin_of:
            return self._origin_of[doc_id]
        digest = content_hash(text)
        self._hash_of[doc_id] = digest
        if digest in self._by_hash:
            origin = self._by_hash[digest]
            self._origin_of[doc_id] = origin
            return origin

        signature = minhash_signature(
            text, num_perm=self._num_perm, shingle_size=self._shingle_size
        )
        minhash = self._minhash(signature)
        matches = sorted(self._lsh.query(minhash))
        origin = self._origin_of[matches[0]] if matches else f"o-{doc_id}"
        self._lsh.insert(doc_id, minhash)
        self.signatures[doc_id] = signature
        self._by_hash[digest] = origin
        self._origin_of[doc_id] = origin
        return origin

    def _minhash(self, signature: list[int]) -> MinHash:
        return MinHash(num_perm=self._num_perm, hashvalues=signature, scheme=_SCHEME)
