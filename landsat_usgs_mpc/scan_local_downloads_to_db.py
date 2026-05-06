"""扫描本地 Landsat 数据目录，并把已存在文件登记到新下载状态数据库。

功能：
1. 扫描 config.LANDSAT_DATA_ROOT 下的 {YYYYMM}/{PPPRRR}/ 文件结构。
2. 只处理实际存在且文件大小大于 0 的 Landsat 文件。
3. 根据文件名解析 Path、Row、产品类型、scene_id、波段名。
4. 写入 config.DB_PATH 指向的新数据库，状态统一记录为 downloaded。
5. 同步生成 config.CSV_PATH，便于后续主程序跳过已下载文件。

该脚本不依赖旧 download.db，不迁移 failed/pending/downloading 记录，
也不迁移旧 scene_selection 缓存，避免旧 AWS 版缓存污染新流程。
"""

from __future__ import annotations

import argparse
import logging
import os
import re
from dataclasses import dataclass

import config
import db_logger

LOG_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
logger = logging.getLogger(__name__)

LANDSAT_FILE_RE = re.compile(
    r"^(?P<sat>L[COTEM]\d{2})_"
    r"(?P<level>L1\w+|L2\w+)_"
    r"(?P<pathrow>\d{6})_"
    r"(?P<acq>\d{8})_"
    r"(?P<proc>\d{8})_"
    r"(?P<collection>\d{2})_"
    r"(?P<tier>T\d)"
    r"(?:_(?P<suffix>.+))?$",
    re.IGNORECASE,
)

DATA_EXTENSIONS = {".tif", ".tiff", ".txt", ".xml", ".json"}


@dataclass(frozen=True)
class LocalRecord:
    path: int
    row: int
    year_month: str
    scene_id: str
    product: str
    source: str
    band_name: str
    filename: str
    local_dir: str
    file_size_bytes: int


def setup_logging():
    logging.basicConfig(level=logging.INFO, format=LOG_FORMAT)


def parse_args():
    parser = argparse.ArgumentParser(description="扫描本地 Landsat 文件并写入新下载状态数据库")
    parser.add_argument("--root", default=config.LANDSAT_DATA_ROOT, help="待扫描的数据根目录")
    parser.add_argument("--dry-run", action="store_true", help="只统计，不写入数据库")
    return parser.parse_args()


def main():
    setup_logging()
    args = parse_args()
    root = os.path.abspath(args.root)

    logger.info("开始扫描本地数据目录：%s", root)
    records = list(scan_local_records(root))
    logger.info("扫描完成，可登记文件数：%s", len(records))

    by_month: dict[str, int] = {}
    by_product: dict[str, int] = {}
    for record in records:
        by_month[record.year_month] = by_month.get(record.year_month, 0) + 1
        by_product[record.product] = by_product.get(record.product, 0) + 1

    logger.info("按月份统计：%s", dict(sorted(by_month.items())))
    logger.info("按产品统计：%s", dict(sorted(by_product.items())))

    if args.dry_run:
        logger.info("dry-run 模式，不写入数据库")
        return

    db = db_logger.DownloadDB()
    inserted = write_records(db, records)
    db.sync_csv_if_dirty()
    logger.info("写入完成，已登记 downloaded 记录：%s", inserted)
    logger.info("数据库：%s", config.DB_PATH)
    logger.info("CSV：%s", config.CSV_PATH)


def scan_local_records(root: str):
    if not os.path.isdir(root):
        raise RuntimeError(f"数据根目录不存在：{root}")

    for year_month in sorted(os.listdir(root)):
        month_dir = os.path.join(root, year_month)
        if not _is_year_month(year_month) or not os.path.isdir(month_dir):
            continue

        for pathrow in sorted(os.listdir(month_dir)):
            pr_dir = os.path.join(month_dir, pathrow)
            if not _is_pathrow(pathrow) or not os.path.isdir(pr_dir):
                continue

            for filename in sorted(os.listdir(pr_dir)):
                file_path = os.path.join(pr_dir, filename)
                if not os.path.isfile(file_path):
                    continue
                if os.path.getsize(file_path) <= 0:
                    continue
                if os.path.splitext(filename)[1].lower() not in DATA_EXTENSIONS:
                    continue

                record = parse_landsat_file(year_month, pathrow, pr_dir, filename, os.path.getsize(file_path))
                if record:
                    yield record


def parse_landsat_file(year_month: str, pathrow_dir_name: str, local_dir: str, filename: str, file_size: int) -> LocalRecord | None:
    stem = os.path.splitext(filename)[0]
    match = LANDSAT_FILE_RE.match(stem)
    if not match:
        logger.debug("跳过无法识别的文件名：%s", filename)
        return None

    pathrow = match.group("pathrow")
    path = int(pathrow[:3])
    row = int(pathrow[3:])

    expected_pathrow = f"{path:03d}{row:03d}"
    if expected_pathrow != pathrow_dir_name:
        logger.warning("文件 Path/Row 与目录不一致，仍按文件名记录：目录=%s 文件=%s", pathrow_dir_name, filename)

    level = match.group("level").upper()
    if level.startswith("L1"):
        product = "L1"
        source = config.L1_SOURCE
        scene_id = _scene_id_for_l1(match)
    elif level.startswith("L2"):
        product = "L2"
        source = config.L2_SOURCE
        scene_id = _scene_id_for_l2(match)
    else:
        return None

    return LocalRecord(
        path=path,
        row=row,
        year_month=year_month,
        scene_id=scene_id,
        product=product,
        source=source,
        band_name=match.group("suffix") or "",
        filename=filename,
        local_dir=local_dir,
        file_size_bytes=file_size,
    )


def _scene_id_for_l1(match: re.Match) -> str:
    return "_".join([
        match.group("sat"),
        match.group("level"),
        match.group("pathrow"),
        match.group("acq"),
        match.group("proc"),
        match.group("collection"),
        match.group("tier"),
    ])


def _scene_id_for_l2(match: re.Match) -> str:
    # MPC item id 通常不包含处理日期：LC08_L2SP_118039_20250830_02_T1。
    # 主程序使用该形式查询和记录 L2 scene_id，因此这里按相同规则登记。
    return "_".join([
        match.group("sat"),
        match.group("level"),
        match.group("pathrow"),
        match.group("acq"),
        match.group("collection"),
        match.group("tier"),
    ])


def write_records(db: db_logger.DownloadDB, records: list[LocalRecord]) -> int:
    inserted = 0
    for record in records:
        db.mark_file_downloaded(
            path=record.path,
            row=record.row,
            year_month=record.year_month,
            scene_id=record.scene_id,
            product=record.product,
            filename=record.filename,
            local_dir=record.local_dir,
            source=record.source,
            remote_href="local_scan",
            band_name=record.band_name,
            file_size_bytes=record.file_size_bytes,
        )
        inserted += 1
        if inserted % 1000 == 0:
            logger.info("已登记 %s 条记录", inserted)
            db.sync_csv_if_dirty()
    return inserted


def _is_year_month(value: str) -> bool:
    return bool(re.fullmatch(r"\d{6}", value))


def _is_pathrow(value: str) -> bool:
    return bool(re.fullmatch(r"\d{6}", value))


if __name__ == "__main__":
    main()
