"""Async orchestration for month-ordered, Path/Row-concurrent downloads."""

from __future__ import annotations

import asyncio
import logging
import os
import time

import config
import db_logger
from asset_builder import extract_download_files
from downloaders import MpcDownloader, UsgsDownloader
from stac_query import fetch_item, find_corresponding_l1, select_best_scene

logger = logging.getLogger(__name__)


class DownloadOrchestrator:
    def __init__(self, db: db_logger.DownloadDB, dry_run: bool = False, products: set[str] | None = None):
        self.db = db
        self.dry_run = dry_run
        self.products = products or {"L1", "L2"}
        self._semaphore = asyncio.Semaphore(config.MAX_CONCURRENT_SCENES)
        self._downloaders = {
            "mpc": MpcDownloader(),
            "usgs": UsgsDownloader(),
        }
        self._stats = {
            "total_scenes": 0,
            "scenes_completed": 0,
            "files_total": 0,
            "files_downloaded": 0,
            "files_skipped": 0,
            "files_failed": 0,
            "no_scene": 0,
        }

    async def run(self, path_rows: list[tuple[int, int]], year_months: list[str], mpc_client, usgs_client):
        total = len(path_rows) * len(year_months)
        self._stats["total_scenes"] = total
        logger.info("需要检查的场景数量：%s", total)
        started = time.time()

        for ym in year_months:
            logger.info("开始处理月份 %s，Path/Row 数量：%s", ym, len(path_rows))
            coros = [self._process_scene(mpc_client, usgs_client, path, row, ym) for path, row in path_rows]
            await asyncio.gather(*coros, return_exceptions=True)
            self.db.sync_csv_if_dirty()
            logger.info("月份 %s 处理完成", ym)

        self.db.sync_csv_if_dirty()
        elapsed = time.time() - started
        s = self._stats
        logger.info("=" * 60)
        logger.info("下载流程结束，耗时 %.0f 秒", elapsed)
        logger.info("场景统计：总计=%s 完成=%s 无可用场景=%s", s["total_scenes"], s["scenes_completed"], s["no_scene"])
        logger.info("文件统计：待处理=%s 已下载=%s 已跳过=%s 失败=%s", s["files_total"], s["files_downloaded"], s["files_skipped"], s["files_failed"])
        logger.info("=" * 60)

    async def _process_scene(self, mpc_client, usgs_client, path: int, row: int, year_month: str):
        tag = f"[Path={path:03d} Row={row:03d} {year_month}]"
        async with self._semaphore:
            try:
                await self._do_process_scene(mpc_client, usgs_client, path, row, year_month)
            except Exception as exc:
                logger.error("%s 处理异常：%s", tag, exc, exc_info=True)

    async def _do_process_scene(self, mpc_client, usgs_client, path: int, row: int, year_month: str):
        tag = f"[Path={path:03d} Row={row:03d} {year_month}]"
        cached = self.db.get_scene_selection(path, row, year_month)

        if cached:
            scene_id_l2 = cached["scene_id_l2"]
            scene_id_l1 = cached.get("scene_id_l1")
            cloud_cover = cached.get("cloud_cover", -1)
            logger.info("%s 使用已缓存的场景选择，云量=%.1f", tag, cloud_cover)
            l2_item = await asyncio.to_thread(fetch_item, mpc_client, scene_id_l2, config.MPC_COLLECTION_L2)
            l1_item = await asyncio.to_thread(fetch_item, usgs_client, scene_id_l1, config.USGS_COLLECTION_L1) if scene_id_l1 else None
            if not l2_item:
                logger.warning("%s 缓存的 L2 场景无法获取，重新查询", tag)
                cached = None
            elif "L1" in self.products and not l1_item:
                l1_item = await asyncio.to_thread(find_corresponding_l1, usgs_client, l2_item, path, row)
                if l1_item:
                    scene_id_l1 = l1_item.id
                    self.db.save_scene_selection(path, row, year_month, scene_id_l2, scene_id_l1, cloud_cover)
                    logger.info("%s 找到 L1/USGS 场景：%s", tag, scene_id_l1)

        if not cached:
            logger.info("%s 查询最优混合数据源场景", tag)
            scene_info = await asyncio.to_thread(
                select_best_scene,
                mpc_client,
                usgs_client,
                path,
                row,
                year_month,
                "L1" in self.products,
            )
            if scene_info is None:
                logger.warning("%s 无可用场景", tag)
                self._stats["no_scene"] += 1
                return
            l2_item = scene_info["l2_item"]
            l1_item = scene_info["l1_item"]
            scene_id_l2 = scene_info["scene_id_l2"]
            scene_id_l1 = scene_info["scene_id_l1"]
            self.db.save_scene_selection(path, row, year_month, scene_id_l2, scene_id_l1, scene_info["cloud_cover"])

        l2_files = await asyncio.to_thread(extract_download_files, l2_item, "L2", config.L2_SOURCE) if "L2" in self.products else []
        l1_files = await asyncio.to_thread(extract_download_files, l1_item, "L1", config.L1_SOURCE) if "L1" in self.products and l1_item else []
        all_files = [(f, scene_id_l2, "L2") for f in l2_files] + [(f, scene_id_l1, "L1") for f in l1_files]
        if not all_files:
            logger.warning("%s 没有可下载文件", tag)
            return

        pr_dir = os.path.join(config.LANDSAT_DATA_ROOT, year_month, f"{path:03d}{row:03d}")
        to_download = []
        for file_info, scene_id, product in all_files:
            filename = file_info["local_filename"]
            if not self.dry_run and self.db.is_file_downloaded(path, row, year_month, scene_id, product, filename):
                self._stats["files_skipped"] += 1
            else:
                to_download.append((file_info, scene_id, product, pr_dir))

        skipped = len(all_files) - len(to_download)
        if not to_download:
            logger.info("%s 所有文件已下载，跳过", tag)
            self._stats["scenes_completed"] += 1
            return

        self._stats["files_total"] += len(to_download)
        if self.dry_run:
            logger.info("%s [预览模式] 需要处理 %s 个文件", tag, len(to_download))
            for f, _, _, _ in to_download:
                logger.info("    - %s/%s %s", f["product"], f["source"], f["local_filename"])
            self._stats["files_downloaded"] += len(to_download)
            self._stats["scenes_completed"] += 1
            return

        scene_success = True
        for file_info, scene_id, product, scene_dir in to_download:
            filename = file_info["local_filename"]
            source = file_info["source"]
            href = file_info["href"]
            band_name = file_info.get("asset_key", "")
            local_path = os.path.join(scene_dir, filename)

            self.db.mark_file_downloading(path, row, year_month, scene_id, product, filename, scene_dir, source, href, band_name)
            downloader = self._downloaders[source]
            result = await asyncio.to_thread(downloader.download, file_info, local_path)

            if result["success"]:
                self.db.mark_file_downloaded(
                    path, row, year_month, scene_id, product, filename, scene_dir,
                    source=source, remote_href=href, band_name=band_name, file_size_bytes=result.get("size"),
                )
                self._stats["files_downloaded"] += 1
                logger.info("%s 下载成功 %s/%s %s（%.1f MB，%.2f MB/s）", tag, product, source, filename, result.get("size", 0) / 1048576, result.get("speed_mbps", 0))
            else:
                self.db.mark_file_failed(path, row, year_month, scene_id, product, filename, source, href, band_name, result.get("error", "unknown"))
                self._stats["files_failed"] += 1
                scene_success = False
                logger.error("%s 下载失败 %s/%s %s：%s", tag, product, source, filename, result.get("error", "unknown"))
                await asyncio.sleep(30)

        self.db.sync_csv_if_dirty()
        if scene_success:
            self._stats["scenes_completed"] += 1
            logger.info("%s 完成（处理 %s，跳过 %s）", tag, len(to_download), skipped)
