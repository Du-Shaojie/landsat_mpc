"""USGS HTTP downloader.

This downloader intentionally refuses s3:// requester-pays URLs. The project
must not use AWS requester-pays for L1 because that path can generate costs.
"""

from __future__ import annotations

import logging

from .http import HttpDownloader

logger = logging.getLogger(__name__)


class UsgsDownloader(HttpDownloader):
    source = "usgs"

    def download(self, file_info: dict, local_path: str) -> dict:
        href = file_info["href"]
        if href.startswith("s3://"):
            result = self._result(file_info, local_path)
            result["error"] = "拒绝 USGS requester-pays S3 URL"
            logger.error("  拒绝 USGS requester-pays S3 URL：%s", href)
            return result
        return super().download(file_info, local_path)
