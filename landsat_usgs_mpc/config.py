"""Landsat L2-MPC + L1-USGS 混合下载工具配置文件。

原则：
1. 直接运行 `python main.py` 时，程序完全使用本文件中的默认配置。
2. 命令行参数只作为临时覆盖配置使用。
3. 当前版本明确禁用 AWS requester-pays 下载路径，避免产生 AWS 费用。
"""

import os

from dotenv import load_dotenv

# ========== 工程路径 ==========

# 当前代码工程根目录。
PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))

# 读取本工程目录下的 .env 文件。当前版本不需要 AWS 密钥；
# 该入口主要保留给 Planetary Computer token 等未来配置使用。
load_dotenv(os.path.join(PROJECT_ROOT, ".env"))

# 数据保存根目录。下载结果会存放到：
#   {LANDSAT_DATA_ROOT}/{YYYYMM}/{PPPRRR}/{filename}
# 例如：
#   E:\Landsatdownload_new\Landsat_CHINA\202508\118039\xxx.TIF
LANDSAT_DATA_ROOT = os.path.join("E:\\Landsatdownload_new", "Landsat_CHINA")

# 日志目录仍放在数据根目录下，便于统一查看。
LOGS_DIR = os.path.join(LANDSAT_DATA_ROOT, "logs")

# 本混合下载工具使用独立 DB/CSV，避免复用旧 AWS 版 download.db 中的
# scene_selection 缓存。旧缓存中的 scene id 可能无法直接对应 MPC item id，
# 会导致大量“缓存场景无法获取 / 无可用场景”。
DB_PATH = os.path.join(LOGS_DIR, "download_usgs_mpc.db")
CSV_PATH = os.path.join(LOGS_DIR, "download_usgs_mpc.csv")

# ========== 空间范围 ==========

# 中国边界 GeoJSON。该文件从原工程复制而来。
CHINA_GEOJSON = os.path.join(PROJECT_ROOT, "config", "china_boundary.geojson")

# 当前实际使用的边界文件。如需切换范围，在这里改。
BOUNDARY_GEOJSON = CHINA_GEOJSON

# ========== 数据源 ==========

# Microsoft Planetary Computer STAC API。
# 当前用于查询和下载 L2 数据。
MPC_STAC_API_URL = "https://planetarycomputer.microsoft.com/api/stac/v1"

# USGS Landsat STAC API。
# 当前仅用于查询 L1 数据。只接受 HTTP/HTTPS asset，不使用 s3:// requester-pays。
USGS_STAC_API_URL = "https://landsatlook.usgs.gov/stac-server"

# MPC 中 Landsat Collection 2 Level-2 数据集名称。
MPC_COLLECTION_L2 = "landsat-c2-l2"

# USGS 中 Landsat Collection 2 Level-1 数据集名称。
USGS_COLLECTION_L1 = "landsat-c2l1"

# USGS 中 Landsat Collection 2 Level-2 Surface Reflectance 数据集名称。
# 仅用于场景发现和选择，不用于下载。
USGS_COLLECTION_L2 = "landsat-c2l2-sr"

# L2 场景元数据来源。
# "usgs"：推荐。沿用原 AWS 版的 USGS STAC 选择逻辑，减少“无可用场景”。
# "mpc" ：直接使用 MPC STAC 查询，部分 Path/Row/月可能查询超时。
L2_METADATA_SOURCE = "usgs"

# 内部数据源标记。通常不需要修改。
L2_SOURCE = "mpc"
L1_SOURCE = "usgs"

# ========== 默认运行模式 ==========

# 直接运行 `python main.py` 时处理哪些产品。
# 可选值：
#   "L2"   只处理 L2，使用 MPC。推荐先用这个。
#   "L1"   只处理 L1，使用 USGS HTTP/HTTPS asset。
#   "both" 同时处理 L2 和 L1。
DEFAULT_PRODUCTS = "L2"

# 是否只预览待下载文件，不实际下载。
# True  = dry-run，只打印待下载文件列表。
# False = 真实下载。
DEFAULT_DRY_RUN = False

# 是否重试 download.db 中 status='failed' 的记录。
DEFAULT_RETRY_FAILED = False

# 是否运行测速。测速会真实下载样本文件。
DEFAULT_BENCHMARK = False

# ========== 时间范围 ==========

# 优先使用 YEAR_MONTHS。
# 如果 YEAR_MONTHS 非空，程序只处理这里列出的月份。
YEAR_MONTHS = [
    "202509","202511","202512","202601","202602","202603",
]

# 如果 YEAR_MONTHS 为空，则使用 START_MONTH 到 END_MONTH 的连续月份。
# 格式均为 YYYYMM，例如 "202501"。
START_MONTH = None
END_MONTH = None

# ========== Path/Row 范围 ==========

# None 表示使用 config/wrs2_path_rows.json 中缓存的中国范围 Path/Row。
TARGET_PATH_ROWS = None

# 只测试单个 Path/Row 时可改为：
# TARGET_PATH_ROWS = [(118, 39)]

# 测试多个 Path/Row 时可改为：
# TARGET_PATH_ROWS = [
#     (118, 39),
#     (119, 38),
# ]

# ========== 场景筛选 ==========

# 最大允许云量百分比。候选场景云量超过该值会被过滤。
MAX_CLOUD_COVER = 50

# ========== 并发与 HTTP 下载 ==========

# 同一个月份内同时处理多少个 Path/Row。
MAX_CONCURRENT_SCENES = 4

# HTTP 流式下载每次读取的块大小。
HTTP_CHUNK_SIZE = 4 * 1024 * 1024

# HTTP 请求超时时间，单位秒。
HTTP_TIMEOUT = 120

# STAC 查询超时时间，单位秒。
STAC_TIMEOUT = 60

# 每个月从 USGS STAC 拉取的最大 L2 场景数。
# 使用“中国边界 + 月份”查询后在本地按 Path/Row 过滤。
STAC_MAX_ITEMS_PER_MONTH = 2000

# MPC 查询策略：
# True  = 只按 Path/Row 和月份查询，不附加中国边界 intersects。推荐。
# False = 同时附加中国边界。中国范围较大时容易触发 MPC 查询超时。
MPC_QUERY_BY_PATH_ROW_ONLY = True

# 单个文件最大下载重试次数。
MAX_DOWNLOAD_RETRIES = 3

# checksum 校验失败后额外重下次数。
CHECKSUM_RETRY_COUNT = 1

# 指数退避基数。第 n 次重试前等待 RETRY_BACKOFF_BASE ** n 秒。
RETRY_BACKOFF_BASE = 2

# ========== 波段选择 ==========

# L1 只下载这些波段和 MTL 文件。
L1_BANDS = ["B8", "BQA", "B10", "B11"]

# L2 下载全部数据 asset 和 MTL 文件。None 表示不过滤波段。
L2_BANDS = None

# L2 中需要排除的 asset key 或文件名片段。
# 这里排除的是 Landsat L2 地表温度反演辅助波段。
# 不影响 SR_B1~SR_B7、ST_B10、QA_PIXEL、QA_RADSAT、SR_QA_AEROSOL 和 MTL 文件。
L2_SKIP_ASSETS = {
    "ST_ATRAN",
    "ST_URAD",
    "ST_TRAD",
    "ST_EMSD",
    "ST_EMIS",
    "ST_DRAD",
    "ST_QA",
}

# ========== 测速输出 ==========

# benchmark 临时文件目录和报告路径。
BENCHMARK_TMP_DIR = os.path.join(LOGS_DIR, "benchmark_tmp")
BENCHMARK_JSON = os.path.join(LOGS_DIR, "benchmark.json")

# ========== 日志 ==========

LOG_LEVEL = "INFO"
LOG_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
