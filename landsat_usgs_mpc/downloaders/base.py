"""Shared downloader interface and checksum helpers."""

from __future__ import annotations

import hashlib
import os
from abc import ABC, abstractmethod


class BaseDownloader(ABC):
    source = "base"

    @abstractmethod
    def download(self, file_info: dict, local_path: str) -> dict:
        """Download one file descriptor into local_path."""

    def _result(self, file_info: dict, local_path: str) -> dict:
        return {
            "success": False,
            "source": file_info.get("source", self.source),
            "href": file_info.get("href"),
            "local_path": local_path,
            "size": 0,
            "checksum_ok": None,
            "error": None,
            "elapsed": 0,
            "speed_mbps": 0,
        }

    def _verify_checksum(self, local_path: str, checksum: str | None) -> bool:
        if not checksum:
            return True

        try:
            if ":" in checksum:
                algo, expected = checksum.split(":", 1)
                algo = algo.lower().replace("sha2-", "sha").replace("sha-", "sha")
            else:
                algo = "md5"
                expected = checksum

            if algo in ("sha256", "sha"):
                actual = self._compute_hash(local_path, hashlib.sha256())
            elif algo == "sha512":
                actual = self._compute_hash(local_path, hashlib.sha512())
            elif algo == "md5":
                actual = self._compute_hash(local_path, hashlib.md5())
            else:
                return True
            return actual.lower() == expected.lower()
        except Exception:
            return False

    @staticmethod
    def _compute_hash(local_path: str, hasher) -> str:
        with open(local_path, "rb") as f:
            for chunk in iter(lambda: f.read(8 * 1024 * 1024), b""):
                hasher.update(chunk)
        return hasher.hexdigest()
