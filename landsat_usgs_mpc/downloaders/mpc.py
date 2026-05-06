"""Microsoft Planetary Computer 下载器。

MPC asset URL 带 SAS 签名，签名有有效期。批量任务运行时间较长时，
STAC 查询时拿到的 URL 可能在真正下载前已经过期。因此每个文件下载前
都重新调用 planetary_computer.sign_url 生成新签名。
"""

from urllib.parse import urlsplit, urlunsplit

import planetary_computer

from .http import HttpDownloader


class MpcDownloader(HttpDownloader):
    source = "mpc"

    def download(self, file_info: dict, local_path: str) -> dict:
        refreshed = dict(file_info)
        refreshed["href"] = planetary_computer.sign_url(_strip_query(file_info["href"]))
        return super().download(refreshed, local_path)


def _strip_query(url: str) -> str:
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))
