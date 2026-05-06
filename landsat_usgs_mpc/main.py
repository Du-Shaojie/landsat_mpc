"""Landsat L2-MPC + L1-USGS 下载器入口。"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
import time

import planetary_computer
from pystac_client import Client

import benchmark
import config
import db_logger
import wrs2_path_rows
from download_orchestrator import DownloadOrchestrator


def setup_logging():
    os.makedirs(config.LOGS_DIR, exist_ok=True)
    root = logging.getLogger()
    root.setLevel(getattr(logging, config.LOG_LEVEL, logging.INFO))
    root.handlers.clear()

    console = logging.StreamHandler(sys.stdout)
    console.setLevel(logging.INFO)
    console.setFormatter(logging.Formatter(config.LOG_FORMAT))
    root.addHandler(console)

    logfile = logging.FileHandler(os.path.join(config.LOGS_DIR, "download_usgs_mpc.log"), encoding="utf-8")
    logfile.setLevel(logging.DEBUG)
    logfile.setFormatter(logging.Formatter(config.LOG_FORMAT))
    root.addHandler(logfile)
    return logging.getLogger(__name__)


def parse_args():
    parser = argparse.ArgumentParser(description="Landsat L2-MPC + L1-USGS downloader")
    parser.add_argument("--dry-run", action="store_true", default=None, help="只查询和列出文件，不下载")
    parser.add_argument("--download", action="store_true", default=None, help="即使 DEFAULT_DRY_RUN=True 也强制真实下载")
    parser.add_argument("--start", type=str, default=None, help="开始月份 YYYYMM")
    parser.add_argument("--end", type=str, default=None, help="结束月份 YYYYMM")
    parser.add_argument("--retry", action="store_true", default=None, help="重试失败记录")
    parser.add_argument("--path", type=int, default=None, help="WRS Path")
    parser.add_argument("--row", type=int, default=None, help="WRS Row")
    parser.add_argument("--month", type=str, default=None, help="单个月份 YYYYMM")
    parser.add_argument("--benchmark", action="store_true", default=None, help="对指定 Path/Row/月做下载测速")
    parser.add_argument(
        "--products",
        choices=["L2", "L1", "both"],
        default=None,
        help="处理产品：L2、L1 或 both",
    )
    return parser.parse_args()


def get_year_months(args) -> list[str]:
    months = config.YEAR_MONTHS[:] if config.YEAR_MONTHS else _month_range(config.START_MONTH, config.END_MONTH)
    start = args.start or config.START_MONTH
    end = args.end or config.END_MONTH
    if start:
        months = [m for m in months if m >= start]
    if end:
        months = [m for m in months if m <= end]
    if args.month:
        months = [args.month]
    return months


def _month_range(start: str | None, end: str | None) -> list[str]:
    if not start or not end:
        return []
    sy, sm = int(start[:4]), int(start[4:6])
    ey, em = int(end[:4]), int(end[4:6])
    months = []
    year, month = sy, sm
    while (year, month) <= (ey, em):
        months.append(f"{year:04d}{month:02d}")
        month += 1
        if month == 13:
            year += 1
            month = 1
    return months


def get_products(args) -> set[str]:
    value = args.products or config.DEFAULT_PRODUCTS
    return {"L1", "L2"} if value == "both" else {value}


def get_dry_run(args) -> bool:
    if args.download:
        return False
    if args.dry_run:
        return True
    return bool(config.DEFAULT_DRY_RUN)


def get_retry(args) -> bool:
    return bool(args.retry) or bool(config.DEFAULT_RETRY_FAILED)


def get_benchmark(args) -> bool:
    return bool(args.benchmark) or bool(config.DEFAULT_BENCHMARK)


def open_clients(products: set[str]):
    mpc_client = _open_client_with_retry(
        config.MPC_STAC_API_URL,
        modifier=planetary_computer.sign_inplace,
    )
    needs_usgs = "L1" in products or config.L2_METADATA_SOURCE == "usgs"
    if needs_usgs:
        logging.getLogger(__name__).info("USGS 元数据查询将直接使用 /search 接口，不初始化根 catalog")
    return mpc_client, None


def _open_client_with_retry(url: str, modifier=None, attempts: int = 3):
    last_error = None
    for attempt in range(1, attempts + 1):
        try:
            kwargs = {"modifier": modifier} if modifier else {}
            return Client.open(url, **kwargs)
        except Exception as exc:
            last_error = exc
            logging.getLogger(__name__).warning("打开 STAC 客户端失败 [%s/%s] %s：%s", attempt, attempts, url, exc)
            if attempt < attempts:
                time.sleep(2 ** attempt)
    raise last_error


async def async_main():
    args = parse_args()
    products = get_products(args)
    dry_run = get_dry_run(args)
    retry_failed = get_retry(args)
    run_benchmark = get_benchmark(args)
    logger = setup_logging()
    logger.info("=" * 60)
    logger.info("Landsat 混合下载器：L2=MPC，L1=USGS")
    logger.info("=" * 60)

    os.makedirs(config.LANDSAT_DATA_ROOT, exist_ok=True)
    os.makedirs(config.LOGS_DIR, exist_ok=True)

    year_months = get_year_months(args)
    if not year_months:
        logger.info("未配置需要处理的月份")
        return

    logger.info("处理产品：%s", ",".join(sorted(products)))
    needs_usgs = "L1" in products or config.L2_METADATA_SOURCE == "usgs"
    logger.info(
        "STAC 地址：MPC=%s USGS=%s",
        config.MPC_STAC_API_URL,
        f"{config.USGS_STAC_API_URL.rstrip('/')}/search" if needs_usgs else "未使用",
    )
    try:
        mpc_client, usgs_client = open_clients(products)
    except Exception as exc:
        logger.error("打开 STAC 客户端失败：%s", exc)
        return

    if run_benchmark:
        bench_path, bench_row = _single_path_row_from_args_or_config(args)
        bench_month = args.month or (year_months[0] if year_months else None)
        if not (bench_path and bench_row and bench_month):
            logger.error("benchmark 需要 --path/--row/--month，或在 config.py 设置单个 TARGET_PATH_ROWS")
            return
        try:
            benchmark.run_benchmark(mpc_client, usgs_client, bench_path, bench_row, bench_month)
        except Exception as exc:
            logger.error("benchmark 失败：%s", exc, exc_info=True)
            logger.error("如果 MPC 签名或限流失败，可尝试配置 PC_SDK_SUBSCRIPTION_KEY")
        return

    db = db_logger.DownloadDB()
    db.reset_downloading_to_pending()

    if retry_failed:
        failed = db.get_failed_records()
        if not failed:
            logger.info("没有需要重试的失败记录")
            return
        logger.info("准备重试失败记录：%s 个", len(failed))
        db.reset_failed_to_pending()
        path_rows = sorted({(int(r["path"]), int(r["row"])) for r in failed}, key=lambda pr: (pr[0], -pr[1]))
        retry_months = {r["year_month"] for r in failed}
        year_months = [m for m in year_months if m in retry_months]
    elif args.path and args.row:
        path_rows = [(args.path, args.row)]
    elif config.TARGET_PATH_ROWS:
        path_rows = [(int(p), int(r)) for p, r in config.TARGET_PATH_ROWS]
    else:
        path_rows = wrs2_path_rows.discover_path_rows()

    logger.info("月份范围：%s ~ %s，共 %s 个月", year_months[0], year_months[-1], len(year_months))
    logger.info("Path/Row 数量：%s", len(path_rows))

    orchestrator = DownloadOrchestrator(db=db, dry_run=dry_run, products=products)
    await orchestrator.run(path_rows, year_months, mpc_client, usgs_client)


def _single_path_row_from_args_or_config(args) -> tuple[int | None, int | None]:
    if args.path and args.row:
        return args.path, args.row
    if config.TARGET_PATH_ROWS and len(config.TARGET_PATH_ROWS) == 1:
        p, r = config.TARGET_PATH_ROWS[0]
        return int(p), int(r)
    return None, None


if __name__ == "__main__":
    asyncio.run(async_main())
    #J4MncxRWNWS2E2YWJuMFV0WXkyemJwSGtRUEJjS0RaSkNNdnBQTHdxQXBweUpxSVFBQUFBJCQAAAAAAAAAAAEAAAAVTKEj1Ly2qMHLtcTM7MzDTQAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAACka-2kpGvtpRG
