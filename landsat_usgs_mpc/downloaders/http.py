"""HTTP downloader with HEAD checks, Range resume, retries, and checksum verification."""

from __future__ import annotations

import logging
import os
import time

import requests

import config
from .base import BaseDownloader

logger = logging.getLogger(__name__)


class HttpDownloader(BaseDownloader):
    source = "http"

    def download(self, file_info: dict, local_path: str) -> dict:
        result = self._result(file_info, local_path)
        href = file_info["href"]
        checksum = file_info.get("checksum")
        os.makedirs(os.path.dirname(local_path), exist_ok=True)

        remote_size = self._get_remote_size(href)
        if os.path.exists(local_path) and remote_size:
            local_size = os.path.getsize(local_path)
            if local_size == remote_size:
                result.update(success=True, size=local_size, checksum_ok=self._verify_checksum(local_path, checksum))
                return result
            if local_size > remote_size:
                os.remove(local_path)

        last_error = None
        for attempt in range(1, config.MAX_DOWNLOAD_RETRIES + 1):
            try:
                logger.info("  下载 [%s/%s] %s", attempt, config.MAX_DOWNLOAD_RETRIES, os.path.basename(local_path))
                started = time.time()
                size = self._download_stream(href, local_path, remote_size)
                elapsed = time.time() - started
                if size <= 0:
                    raise RuntimeError("下载文件大小为 0")
                if remote_size and size != remote_size:
                    raise RuntimeError(f"文件大小不匹配 local={size} remote={remote_size}")

                result.update(
                    success=True,
                    size=size,
                    elapsed=elapsed,
                    speed_mbps=(size / elapsed / (1024 * 1024)) if elapsed > 0 else 0,
                )
                break
            except Exception as exc:
                last_error = str(exc)
                logger.warning("  下载失败，第 %s 次尝试：%s", attempt, exc)
                if attempt < config.MAX_DOWNLOAD_RETRIES:
                    time.sleep(config.RETRY_BACKOFF_BASE ** attempt)

        if not result["success"]:
            result["error"] = last_error
            return result

        result["checksum_ok"] = self._verify_checksum(local_path, checksum)
        if not result["checksum_ok"] and config.CHECKSUM_RETRY_COUNT > 0:
            try:
                os.remove(local_path)
            except OSError:
                pass
            retry_result = self.download({**file_info, "checksum": None}, local_path)
            if retry_result["success"]:
                result.update(retry_result)
                result["checksum_ok"] = self._verify_checksum(local_path, checksum)
                if not result["checksum_ok"]:
                    result["error"] = "checksum_retry_after_retry"
        return result

    def _get_remote_size(self, href: str) -> int | None:
        try:
            resp = requests.head(href, allow_redirects=True, timeout=config.HTTP_TIMEOUT)
            if resp.status_code >= 400:
                return None
            length = resp.headers.get("Content-Length")
            return int(length) if length else None
        except Exception:
            return None

    def _download_stream(self, href: str, local_path: str, remote_size: int | None) -> int:
        part_path = local_path + ".part"
        resume_from = os.path.getsize(part_path) if os.path.exists(part_path) else 0
        headers = {}
        mode = "wb"

        if resume_from > 0 and (not remote_size or resume_from < remote_size):
            headers["Range"] = f"bytes={resume_from}-"
            mode = "ab"
        elif resume_from > 0 and remote_size and resume_from == remote_size:
            os.replace(part_path, local_path)
            return os.path.getsize(local_path)

        with requests.get(href, headers=headers, stream=True, allow_redirects=True, timeout=config.HTTP_TIMEOUT) as resp:
            if resp.status_code == 416:
                os.replace(part_path, local_path)
                return os.path.getsize(local_path)
            if resp.status_code == 200 and resume_from > 0:
                mode = "wb"
                resume_from = 0
            resp.raise_for_status()
            with open(part_path, mode + ("" if "b" in mode else "b")) as f:
                for chunk in resp.iter_content(chunk_size=config.HTTP_CHUNK_SIZE):
                    if chunk:
                        f.write(chunk)

        os.replace(part_path, local_path)
        return os.path.getsize(local_path)
