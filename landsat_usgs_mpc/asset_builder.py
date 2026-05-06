"""从 STAC item 的 assets 中提取待下载文件列表。"""

from __future__ import annotations

import logging
import os
from urllib.parse import unquote, urlparse

import config

logger = logging.getLogger(__name__)

# 永久跳过的非数据 asset 或不需要的辅助 asset。
SKIP_ASSETS = {
    "thumbnail",
    "reduced_resolution_browse",
    "full_resolution_browse",
    "rendered_preview",
    "tilejson",
    "ang",
    "st_cdist",
    "cdist",
}

MTL_PATTERNS = ["mtl.txt", "mtl.xml", "mtl.json", "mtl_txt", "mtl_xml", "mtl_json", "mtl"]


def extract_download_files(stac_item, product: str, source: str) -> list[dict]:
    """提取指定产品的下载文件描述。

    返回字段：
    - source: 数据源标记，如 mpc/usgs
    - product: L1 或 L2
    - href: 远程下载地址
    - local_filename: 本地文件名
    - checksum: 可用时的校验值
    - asset_key: STAC asset key
    """
    files = []
    target_bands = config.L1_BANDS if product == "L1" else config.L2_BANDS

    for asset_key, asset in stac_item.assets.items():
        href = asset.href
        key_lower = asset_key.lower()

        if source == "usgs" and href.startswith("s3://"):
            logger.warning("跳过 USGS requester-pays S3 asset：%s", href)
            continue

        if key_lower in SKIP_ASSETS:
            continue

        if product == "L2" and _should_skip_l2_asset(href, asset_key):
            logger.debug("跳过 L2 排除波段：%s", href)
            continue

        if not _is_data_file(href, asset_key):
            continue

        if product == "L1":
            if not _should_download_l1(href, asset_key, target_bands):
                continue
        elif target_bands is not None and not _band_matches(href, asset_key, target_bands):
            continue

        filename = _extract_filename(href)
        if not filename:
            logger.warning("跳过无法提取文件名的 asset：%s", href)
            continue

        files.append({
            "source": source,
            "product": product,
            "href": href,
            "local_filename": filename,
            "checksum": _extract_checksum(asset),
            "asset_key": asset_key,
        })

    logger.info("  %s/%s 提取到 %s 个文件", product, source, len(files))
    return files


def _should_skip_l2_asset(href: str, asset_key: str) -> bool:
    text = f"{href.upper()} {asset_key.upper()}"
    return any(skip.upper() in text for skip in config.L2_SKIP_ASSETS)


def _is_data_file(href: str, asset_key: str) -> bool:
    href_lower = href.lower().split("?", 1)[0]
    if any(skip in href_lower for skip in ["browse", "thumbnail", "preview"]):
        return False
    if href_lower.endswith((".tif", ".tiff", ".txt", ".xml", ".json")):
        return True

    data_keys = [
        "b1", "b2", "b3", "b4", "b5", "b6", "b7", "b8", "b9", "b10", "b11",
        "bqa", "qa_pixel", "qa_radsat", "qa_aerosol", "cloud_qa",
        "red", "green", "blue", "nir08", "swir16", "swir22", "coastal",
        "lwir", "lwir11", "mtl",
    ]
    return any(k in asset_key.lower() for k in data_keys)


def _should_download_l1(href: str, asset_key: str, target_bands: list[str]) -> bool:
    return _is_mtl_file(href, asset_key) or _band_matches(href, asset_key, target_bands)


def _band_matches(href: str, asset_key: str, target_bands: list[str]) -> bool:
    href_upper = href.upper()
    asset_upper = asset_key.upper()
    for band in target_bands:
        band_upper = band.upper()
        if f"_{band_upper}." in href_upper:
            return True
        if asset_upper == band_upper:
            return True
        if band_upper in href_upper and band_upper.startswith("QA"):
            return True
    return False


def _is_mtl_file(href: str, asset_key: str) -> bool:
    text = f"{href.lower()} {asset_key.lower()}"
    return any(pattern in text for pattern in MTL_PATTERNS)


def _extract_filename(href: str) -> str:
    if href.startswith("s3://"):
        path = href[5:].split("/", 1)[1] if "/" in href[5:] else ""
    else:
        path = urlparse(href).path
    return unquote(os.path.basename(path))


def _extract_checksum(asset) -> str | None:
    for key in ["file:checksum", "checksum", "sha256", "md5"]:
        if hasattr(asset, "extra_fields") and key in asset.extra_fields:
            val = asset.extra_fields[key]
            return val if isinstance(val, str) and ":" in val else None
    return getattr(asset, "checksum", None)
