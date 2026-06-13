import hashlib

import cachetools


class SemanticCache:
    def __init__(self, maxsize: int = 512):
        self._cache = cachetools.LRUCache(maxsize=maxsize)

    @staticmethod
    def _make_key(
        workspace_id: str, acl_cohort_hash: str, embedding_model_id: str, query_embedding: bytes
    ) -> str:
        return hashlib.sha256(
            f"{workspace_id}:{acl_cohort_hash}:{embedding_model_id}:{query_embedding}".encode()
        ).hexdigest()

    def get(
        self,
        workspace_id: str,
        acl_cohort_hash: str,
        embedding_model_id: str,
        query_embedding: bytes,
    ) -> str | None:
        key = self._make_key(workspace_id, acl_cohort_hash, embedding_model_id, query_embedding)
        return self._cache.get(key)

    def set(
        self,
        workspace_id: str,
        acl_cohort_hash: str,
        embedding_model_id: str,
        query_embedding: bytes,
        value: str,
    ) -> None:
        key = self._make_key(workspace_id, acl_cohort_hash, embedding_model_id, query_embedding)
        self._cache[key] = value
