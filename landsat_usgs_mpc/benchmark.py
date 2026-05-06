"""Small mixed-source throughput benchmark for one Path/Row/month."""

from __future__ import annotations

import json
import logging
import os
import shutil
import time

import config
from asset_builder import extract_download_files
from downloaders import MpcDownloader, UsgsDownloader
from stac_query import select_best_scene

logger = logging.getLogger(__name__)


def run_benchmark(mpc_client, usgs_client, path: int, row: int, year_month: str) -> dict:
    os.makedirs(config.LOGS_DIR, exist_ok=True)
    if os.path.exists(config.BENCHMARK_TMP_DIR):
        shutil.rmtree(config.BENCHMARK_TMP_DIR)
    os.makedirs(config.BENCHMARK_TMP_DIR, exist_ok=True)

    report = {
        "path": path,
        "row": row,
        "year_month": year_month,
        "samples": [],
        "summary": {},
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }

    scene = select_best_scene(mpc_client, usgs_client, path, row, year_month)
    if scene is None:
        raise RuntimeError("测速场景选择未返回可用场景")

    l2_files = extract_download_files(scene["l2_item"], "L2", config.L2_SOURCE)
    l1_files = extract_download_files(scene["l1_item"], "L1", config.L1_SOURCE) if scene["l1_item"] else []
    samples = _pick_samples(l2_files, l1_files)
    downloaders = {"mpc": MpcDownloader(), "usgs": UsgsDownloader()}

    for file_info in samples:
        local_path = os.path.join(config.BENCHMARK_TMP_DIR, file_info["local_filename"])
        logger.info("测速下载 %s/%s %s", file_info["product"], file_info["source"], file_info["local_filename"])
        result = downloaders[file_info["source"]].download(file_info, local_path)
        sample = {
            "product": file_info["product"],
            "source": file_info["source"],
            "filename": file_info["local_filename"],
            "success": result["success"],
            "size": result.get("size", 0),
            "elapsed": result.get("elapsed", 0),
            "speed_mbps": result.get("speed_mbps", 0),
            "error": result.get("error"),
        }
        report["samples"].append(sample)

    report["summary"] = _summarize(report["samples"])
    with open(config.BENCHMARK_JSON, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)

    try:
        shutil.rmtree(config.BENCHMARK_TMP_DIR)
    except OSError:
        logger.warning("清理测速临时目录失败：%s", config.BENCHMARK_TMP_DIR)

    logger.info("测速报告：%s", config.BENCHMARK_JSON)
    logger.info("测速摘要：%s", report["summary"])
    return report


def _pick_samples(l2_files: list[dict], l1_files: list[dict]) -> list[dict]:
    samples = []
    l2_tifs = [f for f in l2_files if f["local_filename"].lower().endswith((".tif", ".tiff"))]
    l2_mtls = [f for f in l2_files if "mtl" in f["local_filename"].lower()]
    l1_priority = [
        f for f in l1_files
        if any(token in f["local_filename"].upper() for token in ["_B8.", "_B10.", "_B11.", "_BQA."])
    ]
    l1_mtls = [f for f in l1_files if "mtl" in f["local_filename"].lower()]

    if l2_tifs:
        samples.append(l2_tifs[0])
    if l2_mtls:
        samples.append(l2_mtls[0])
    if l1_priority:
        samples.append(l1_priority[0])
    if l1_mtls:
        samples.append(l1_mtls[0])
    if len(samples) < 2:
        raise RuntimeError("未找到足够的测速样本文件")
    return samples


def _summarize(samples: list[dict]) -> dict:
    ok = [s for s in samples if s["success"]]
    speeds = [s["speed_mbps"] for s in ok if s["speed_mbps"] > 0]
    avg_speed = sum(speeds) / len(speeds) if speeds else 0
    max_speed = max(speeds) if speeds else 0
    failures = len(samples) - len(ok)
    suggested_scenes = config.MAX_CONCURRENT_SCENES
    if avg_speed > 20:
        suggested_scenes = min(config.MAX_CONCURRENT_SCENES + 4, 16)
    elif failures:
        suggested_scenes = max(2, config.MAX_CONCURRENT_SCENES // 2)
    return {
        "success": len(ok),
        "failed": failures,
        "avg_speed_mbps": avg_speed,
        "max_speed_mbps": max_speed,
        "suggested_max_concurrent_scenes": suggested_scenes,
        "suggested_single_source_parallelism": suggested_scenes,
    }
