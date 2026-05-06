"""Load or discover WRS-2 Path/Row coverage for China."""

from __future__ import annotations

import csv
import json
import logging
import os

from pystac_client import Client

import config
from stac_query import year_month_to_range

logger = logging.getLogger(__name__)

_CACHE_FILE = os.path.join(config.PROJECT_ROOT, "config", "wrs2_path_rows.json")
_CSV_FILE = os.path.join(config.PROJECT_ROOT, "config", "china_path_rows.csv")


def load_cached_path_rows() -> list[tuple[int, int]] | None:
    if not os.path.exists(_CACHE_FILE):
        return None
    with open(_CACHE_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)
    path_rows = sorted([tuple(item) for item in data], key=lambda pr: (pr[0], -pr[1]))
    logger.info("从缓存加载 WRS-2 Path/Row：%s 组", len(path_rows))
    return path_rows


def discover_path_rows() -> list[tuple[int, int]]:
    cached = load_cached_path_rows()
    if cached is not None:
        if not os.path.exists(_CSV_FILE):
            _export_path_rows_csv(cached)
        return cached

    logger.info("通过 MPC L2 STAC 发现 WRS-2 Path/Row")
    with open(config.BOUNDARY_GEOJSON, "r", encoding="utf-8") as f:
        boundary = json.load(f)["geometry"]

    client = Client.open(config.MPC_STAC_API_URL)
    path_rows = set()
    for ym in ["202501", "202504", "202507"]:
        start, end = year_month_to_range(ym)
        results = client.search(
            collections=[config.MPC_COLLECTION_L2],
            intersects=boundary,
            datetime=[start, end],
            max_items=500,
        )
        for item in results.items():
            props = item.properties
            p = props.get("landsat:wrs_path", props.get("wrs:path"))
            r = props.get("landsat:wrs_row", props.get("wrs:row"))
            if p is not None and r is not None:
                path_rows.add((int(p), int(r)))

    result = sorted(path_rows, key=lambda pr: (pr[0], -pr[1]))
    if not result:
        raise RuntimeError("无法发现 WRS-2 Path/Row")
    save_path_rows_cache(result)
    _export_path_rows_csv(result)
    return result


def save_path_rows_cache(path_rows: list[tuple[int, int]]):
    os.makedirs(os.path.dirname(_CACHE_FILE), exist_ok=True)
    with open(_CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(path_rows, f)


def _export_path_rows_csv(path_rows: list[tuple[int, int]]):
    with open(_CSV_FILE, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["Path", "Row"])
        writer.writerows(path_rows)
