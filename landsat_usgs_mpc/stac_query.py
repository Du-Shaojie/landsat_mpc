"""STAC 查询与 L1/L2 场景匹配。

本文件只负责元数据查询和最优场景选择：
- L2 元数据优先使用 USGS STAC /search，按 Path/Row 精确查询；
- L2 文件下载地址仍会改写为 MPC Blob 地址；
- L1 元数据使用 USGS STAC /search，且只接受 HTTP/HTTPS asset。
"""

from __future__ import annotations

import json
import logging
import re
import threading
from datetime import date, timedelta
from urllib.parse import urlparse

import requests
from pystac import Item
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

import config

logger = logging.getLogger(__name__)

_boundary_geometry = None
_usgs_l2_month_cache = {}
_usgs_l2_month_cache_lock = threading.Lock()
_http_session = None


def _get_http_session():
    global _http_session
    if _http_session is None:
        retry = Retry(
            total=3,
            connect=3,
            read=3,
            backoff_factor=1,
            status_forcelist=(429, 500, 502, 503, 504),
            allowed_methods=("GET", "POST"),
        )
        adapter = HTTPAdapter(max_retries=retry, pool_connections=32, pool_maxsize=32)
        session = requests.Session()
        session.headers.update(
            {
                "User-Agent": "landsat-usgs-mpc-downloader/1.0",
                "Accept": "application/geo+json, application/json",
            }
        )
        session.mount("https://", adapter)
        session.mount("http://", adapter)
        _http_session = session
    return _http_session


def get_boundary_geometry():
    global _boundary_geometry
    if _boundary_geometry is None:
        with open(config.BOUNDARY_GEOJSON, "r", encoding="utf-8") as f:
            _boundary_geometry = json.load(f)["geometry"]
    return _boundary_geometry


def year_month_to_range(year_month: str) -> tuple[str, str]:
    year = int(year_month[:4])
    month = int(year_month[4:6])
    start = f"{year:04d}-{month:02d}-01"
    if month == 12:
        last_day = date(year + 1, 1, 1) - timedelta(days=1)
    else:
        last_day = date(year, month + 1, 1) - timedelta(days=1)
    return start, last_day.isoformat()


def _day_15(year_month: str) -> date:
    return date(int(year_month[:4]), int(year_month[4:6]), 15)


def select_best_scene(mpc_client, usgs_client, path: int, row: int, year_month: str, include_l1: bool = True) -> dict | None:
    """选择指定 Path/Row/月内云量最低且日期最接近月中的 L2 场景，并匹配同日 L1。"""
    start_date, end_date = year_month_to_range(year_month)
    day15 = _day_15(year_month)
    month_start = date(int(year_month[:4]), int(year_month[4:6]), 1)
    month_end = date.fromisoformat(end_date)

    logger.info(
        "查询 L2 场景：Path=%03d Row=%03d 月份=%s 元数据源=%s",
        path,
        row,
        year_month,
        config.L2_METADATA_SOURCE,
    )
    try:
        if config.L2_METADATA_SOURCE == "usgs":
            all_items = _search_l2_items_usgs(path, row, start_date, end_date)
        else:
            all_items = _search_l2_items_mpc(mpc_client, path, row, start_date, end_date)
    except Exception as exc:
        logger.error("STAC 查询失败：%s", exc)
        return None

    candidates = []
    for item in all_items:
        props = item.properties
        if _get_path(props) != path or _get_row(props) != row:
            continue

        cloud = _get_cloud(props)
        if cloud > config.MAX_CLOUD_COVER:
            continue

        if item.datetime:
            acq_date = item.datetime.date()
            if not (month_start <= acq_date <= month_end):
                continue

        candidates.append(item)

    if not candidates:
        logger.warning("  未找到满足 Path/Row、月份和云量条件的 L2 场景")
        return None

    def sort_key(item):
        item_date = item.datetime.date() if item.datetime else day15
        return (_get_cloud(item.properties), abs((item_date - day15).days))

    best_l2 = sorted(candidates, key=sort_key)[0]
    cloud_cover = _get_cloud(best_l2.properties)
    acquisition_date = best_l2.datetime.date().isoformat() if best_l2.datetime else best_l2.properties.get("datetime", "")[:10]

    if config.L2_METADATA_SOURCE == "usgs":
        _rewrite_l2_assets_to_mpc(best_l2)

    logger.info("  选中 L2 场景：%s 云量=%.1f 日期=%s", best_l2.id, cloud_cover, acquisition_date)

    l1_item = find_corresponding_l1(best_l2, path, row) if include_l1 else None
    if l1_item:
        logger.info("  匹配到 L1/USGS 场景：%s", l1_item.id)
    elif include_l1:
        logger.warning("  未匹配到同日同 Path/Row 的 L1/USGS 场景，本次仅处理 L2")

    return {
        "l2_item": best_l2,
        "l1_item": l1_item,
        "scene_id_l2": best_l2.id,
        "scene_id_l1": l1_item.id if l1_item else None,
        "cloud_cover": cloud_cover,
        "acquisition_date": acquisition_date,
    }


def _search_l2_items_usgs(path: int, row: int, start_date: str, end_date: str):
    logger.info("  使用 USGS STAC /search 按 Path/Row 精确查询 L2")
    items = _search_usgs_path_row_items_direct(
        config.USGS_COLLECTION_L2,
        path,
        row,
        start_date,
        end_date,
        max_items=200,
    )
    if items:
        logger.info("  USGS Path/Row L2 查询返回 %s 个场景", len(items))
        return items

    cache_key = (start_date, end_date)
    with _usgs_l2_month_cache_lock:
        if cache_key not in _usgs_l2_month_cache:
            logger.warning("  Path/Row 服务器端查询无结果，降级为中国范围整月查询后本地按 Path/Row 精确过滤")
            _usgs_l2_month_cache[cache_key] = _search_usgs_items_direct(
                config.USGS_COLLECTION_L2,
                {
                    "datetime": _datetime_interval(start_date, end_date),
                    "intersects": get_boundary_geometry(),
                },
                max_items=config.STAC_MAX_ITEMS_PER_MONTH,
            )
            logger.info("  USGS 中国范围整月 L2 查询返回 %s 个场景", len(_usgs_l2_month_cache[cache_key]))
        return _usgs_l2_month_cache[cache_key]


def _search_usgs_path_row_items_direct(collection: str, path: int, row: int, start_date: str, end_date: str, max_items: int):
    query_variants = [
        {
            "landsat:wrs_path": {"eq": f"{path:03d}"},
            "landsat:wrs_row": {"eq": f"{row:03d}"},
        },
        {
            "landsat:wrs_path": {"eq": path},
            "landsat:wrs_row": {"eq": row},
        },
        {
            "landsat:wrs:path": {"eq": f"{path:03d}"},
            "landsat:wrs:row": {"eq": f"{row:03d}"},
        },
        {
            "wrs:path": {"eq": f"{path:03d}"},
            "wrs:row": {"eq": f"{row:03d}"},
        },
    ]

    for query in query_variants:
        try:
            items = _search_usgs_items_direct(
                collection,
                {
                    "datetime": _datetime_interval(start_date, end_date),
                    "query": query,
                },
                max_items=max_items,
            )
            filtered = [item for item in items if _get_path(item.properties) == path and _get_row(item.properties) == row]
            if filtered:
                return filtered
        except requests.HTTPError as exc:
            status_code = exc.response.status_code if exc.response is not None else None
            if status_code in {400, 422}:
                logger.warning("  USGS 不支持当前 Path/Row 查询字段组合，继续尝试下一组字段：%s", exc)
                continue
            raise
        except Exception as exc:
            raise RuntimeError(f"USGS Path/Row 查询网络失败：{exc}") from exc
    return []


def _search_usgs_items_direct(collection: str, payload: dict, max_items: int):
    url = f"{config.USGS_STAC_API_URL.rstrip('/')}/search"
    body = dict(payload)
    body["collections"] = [collection]
    body.setdefault("limit", min(1000, max_items))

    features = []
    next_url = url
    next_body = body
    while len(features) < max_items:
        response = _get_http_session().post(next_url, json=next_body, timeout=config.STAC_TIMEOUT)
        response.raise_for_status()
        data = response.json()
        features.extend(data.get("features", []))

        next_link = next((link for link in data.get("links", []) if link.get("rel") == "next"), None)
        if not next_link:
            break
        if next_link.get("method", "GET").upper() != "POST" or not next_link.get("body"):
            break
        next_url = next_link.get("href", url)
        next_body = next_link["body"]

    return [Item.from_dict(feature) for feature in features[:max_items]]


def _datetime_interval(start_date: str, end_date: str) -> str:
    return f"{start_date}T00:00:00Z/{end_date}T23:59:59Z"


def _search_l2_items_mpc(mpc_client, path: int, row: int, start_date: str, end_date: str):
    query_variants = [
        {
            "landsat:wrs_path": {"eq": f"{path:03d}"},
            "landsat:wrs_row": {"eq": f"{row:03d}"},
            "eo:cloud_cover": {"lte": config.MAX_CLOUD_COVER},
        },
        {
            "landsat:wrs_path": {"eq": path},
            "landsat:wrs_row": {"eq": row},
            "eo:cloud_cover": {"lte": config.MAX_CLOUD_COVER},
        },
        {
            "landsat:wrs:path": {"eq": f"{path:03d}"},
            "landsat:wrs:row": {"eq": f"{row:03d}"},
            "eo:cloud_cover": {"lte": config.MAX_CLOUD_COVER},
        },
        {
            "wrs:path": {"eq": f"{path:03d}"},
            "wrs:row": {"eq": f"{row:03d}"},
            "eo:cloud_cover": {"lte": config.MAX_CLOUD_COVER},
        },
    ]

    last_error = None
    for query in query_variants:
        try:
            kwargs = {
                "collections": [config.MPC_COLLECTION_L2],
                "datetime": [start_date, end_date],
                "query": query,
                "max_items": 100,
            }
            if not config.MPC_QUERY_BY_PATH_ROW_ONLY:
                kwargs["intersects"] = get_boundary_geometry()
            results = mpc_client.search(**kwargs)
            items = list(results.items())
            if items:
                return items
        except Exception as exc:
            last_error = exc
            logger.warning("MPC Path/Row 查询失败，继续尝试下一组字段：%s", exc)

    if last_error:
        logger.warning("MPC Path/Row 查询均失败，降级为月份查询：%s", last_error)
    results = mpc_client.search(
        collections=[config.MPC_COLLECTION_L2],
        datetime=[start_date, end_date],
        max_items=500,
    )
    return list(results.items())


def _rewrite_l2_assets_to_mpc(item):
    for asset in item.assets.values():
        rewritten = _usgs_l2_href_to_mpc_href(asset.href)
        if rewritten:
            asset.href = rewritten


def _usgs_l2_href_to_mpc_href(href: str) -> str | None:
    parsed = urlparse(href)
    path = parsed.path
    match = re.search(r"/(?:data/)?collection02/(level-2/.+)$", path)
    if not match:
        return None
    return f"https://landsateuwest.blob.core.windows.net/landsat-c2/{match.group(1)}"


def find_corresponding_l1(l2_item, path: int, row: int):
    parts = l2_item.id.split("_")
    if len(parts) < 4:
        return None

    satellite = parts[0]
    acq_date = parts[3]
    iso_date = f"{acq_date[:4]}-{acq_date[4:6]}-{acq_date[6:8]}"

    try:
        items = _search_usgs_path_row_items_direct(
            config.USGS_COLLECTION_L1,
            path,
            row,
            iso_date,
            iso_date,
            max_items=200,
        )
        for item in items:
            props = item.properties
            if _get_path(props) != path or _get_row(props) != row:
                continue
            item_parts = item.id.split("_")
            if len(item_parts) >= 4 and item_parts[0] == satellite and item_parts[3] == acq_date:
                return item
    except Exception as exc:
        logger.warning("L1/USGS 查询失败：%s", exc)
    return None


def fetch_item(client, scene_id: str, collection: str):
    if not scene_id:
        return None
    try:
        if collection in {config.USGS_COLLECTION_L1, config.USGS_COLLECTION_L2}:
            items = _search_usgs_items_direct(collection, {"ids": [scene_id]}, max_items=1)
            return items[0] if items else None
        if client is None:
            return None
        results = client.search(collections=[collection], ids=[scene_id], max_items=1)
        items = list(results.items())
        return items[0] if items else None
    except Exception:
        return None


def _get_path(props: dict) -> int:
    return int(props.get("landsat:wrs_path", props.get("landsat:wrs:path", props.get("wrs:path", 0))) or 0)


def _get_row(props: dict) -> int:
    return int(props.get("landsat:wrs_row", props.get("landsat:wrs:row", props.get("wrs:row", 0))) or 0)


def _get_cloud(props: dict) -> float:
    return float(props.get("landsat:cloud_cover_land", props.get("eo:cloud_cover", 999)) or 999)
