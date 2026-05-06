# Landsat L2-MPC + L1-USGS 混合下载工具

本工程是独立的新下载工具，不修改原始 `Landsatdownload` 目录。

当前设计目标：

- L2 数据从 Microsoft Planetary Computer 下载。
- L1 数据从 USGS STAC 提供的 HTTP/HTTPS asset 下载。
- 明确禁用 USGS/AWS `s3://` requester-pays 下载路径，避免产生 AWS requester-pays 费用。
- 数据选择逻辑、月份顺序、Path/Row 顺序、目录结构和 DB/CSV 状态记录逻辑保持原项目习惯。

## 目录结构

```text
landsat_usgs_mpc/
├── main.py                    # 程序入口
├── config.py                  # 运行配置
├── stac_query.py              # MPC/USGS STAC 查询和场景选择
├── asset_builder.py           # STAC asset 到下载文件列表的转换
├── download_orchestrator.py   # 下载编排
├── db_logger.py               # SQLite/CSV 状态记录
├── benchmark.py               # 测速模块
├── downloaders/               # 下载器实现
├── config/
│   ├── china_boundary.geojson
│   ├── wrs2_path_rows.json
│   └── china_path_rows.csv
└── requirements.txt
```

## 数据存放位置

默认数据根目录在 `config.py` 中配置：

```python
LANDSAT_DATA_ROOT = os.path.join("E:\\Landsatdownload_new", "Landsat_CHINA")
```

下载后的文件仍按原逻辑存放：

```text
E:\Landsatdownload_new\Landsat_CHINA\YYYYMM\PPPRRR\
```

示例：

```text
E:\Landsatdownload_new\Landsat_CHINA\202508\118039\
```

L1 和 L2 文件仍放在同一个 `PPPRRR` 文件夹中，不新增产品子目录。

日志和状态文件：

```text
E:\Landsatdownload_new\Landsat_CHINA\logs\download_usgs_mpc.log
E:\Landsatdownload_new\Landsat_CHINA\logs\download.db
E:\Landsatdownload_new\Landsat_CHINA\logs\download.csv
```

## 安装依赖

在 `landsat_usgs_mpc` 目录下运行：

```powershell
pip install -r requirements.txt
```

## 直接运行

程序的默认运行行为由 `config.py` 控制。配置完成后，直接运行：

```powershell
python main.py
```

默认配置当前为：

```python
DEFAULT_PRODUCTS = "L2"
DEFAULT_DRY_RUN = False
YEAR_MONTHS = ["202508"]
TARGET_PATH_ROWS = None
```

含义：

- 只处理 L2。
- 执行真实下载。
- 处理 2025 年 8 月。
- 使用 `config/wrs2_path_rows.json` 中的中国范围 Path/Row 缓存。

## 修改运行配置

主要配置都在 `config.py` 中。

### 1. 选择下载产品

```python
DEFAULT_PRODUCTS = "L2"
```

可选值：

```python
DEFAULT_PRODUCTS = "L2"    # 只下载 L2，推荐先用这个
DEFAULT_PRODUCTS = "L1"    # 只下载 L1，等 USGS 网络恢复后再用
DEFAULT_PRODUCTS = "both"  # 同时处理 L1 和 L2
```

说明：

- `L2` 使用 Microsoft Planetary Computer。
- `L1` 使用 USGS STAC 的 HTTP/HTTPS asset。
- 如果 USGS 只返回 `s3://` requester-pays asset，程序会跳过或拒绝，不会使用 AWS 下载。

### 2. 选择运行模式

```python
DEFAULT_DRY_RUN = False
```

可选：

```python
DEFAULT_DRY_RUN = True   # 只预览，不下载
DEFAULT_DRY_RUN = False  # 实际下载
```

建议第一次运行先设为：

```python
DEFAULT_DRY_RUN = True
```

确认文件列表和目录逻辑正确后，再改成：

```python
DEFAULT_DRY_RUN = False
```

### 3. 配置月份

方式一：直接指定月份列表。

```python
YEAR_MONTHS = [
    "202508",
    "202509",
]
```

方式二：使用起止月份。

```python
YEAR_MONTHS = []
START_MONTH = "202501"
END_MONTH = "202512"
```

优先级：

- 如果 `YEAR_MONTHS` 非空，使用 `YEAR_MONTHS`。
- 如果 `YEAR_MONTHS` 为空，使用 `START_MONTH` 到 `END_MONTH` 的连续月份。

### 4. 配置下载范围

默认：

```python
TARGET_PATH_ROWS = None
```

表示使用中国范围缓存：

```text
config/wrs2_path_rows.json
```

如果只想测试单个 Path/Row：

```python
TARGET_PATH_ROWS = [(118, 39)]
```

如果想测试多个 Path/Row：

```python
TARGET_PATH_ROWS = [
    (118, 39),
    (119, 38),
]
```

### 5. 云量阈值

```python
MAX_CLOUD_COVER = 50
```

只选择云量不超过该阈值的场景。

### 6. L2 排除波段

`config.py` 中的 `L2_SKIP_ASSETS` 用于排除不需要下载的 L2 asset：

```python
L2_SKIP_ASSETS = {
    "ST_ATRAN",
    "ST_URAD",
    "ST_TRAD",
    "ST_EMSD",
    "ST_EMIS",
    "ST_DRAD",
    "ST_QA",
}
```

这些是地表温度反演辅助波段。当前会被跳过，不会进入 L2 下载清单。

保留下载的典型 L2 文件包括：

```text
SR_B1 ~ SR_B7
ST_B10
QA_PIXEL
QA_RADSAT
SR_QA_AEROSOL
MTL.txt / MTL.xml / MTL.json
```

## 推荐使用流程

### 第一步：先预览 L2

在 `config.py` 中设置：

```python
DEFAULT_PRODUCTS = "L2"
DEFAULT_DRY_RUN = True
YEAR_MONTHS = ["202508"]
TARGET_PATH_ROWS = [(118, 39)]
```

运行：

```powershell
python main.py
```

检查日志中是否列出 L2/MPC 文件，例如：

```text
L2/mpc LC08_L2SP_..._SR_B1.TIF
L2/mpc LC08_L2SP_..._MTL.txt
```

### 第二步：真实下载 L2

确认无误后，将 `config.py` 改为：

```python
DEFAULT_PRODUCTS = "L2"
DEFAULT_DRY_RUN = False
```

运行：

```powershell
python main.py
```

### 第三步：后续单独下载 L1

等 USGS STAC 网络恢复后，将 `config.py` 改为：

```python
DEFAULT_PRODUCTS = "L1"
DEFAULT_DRY_RUN = True
```

先预览：

```powershell
python main.py
```

如果能列出 `L1/usgs` 且 href 是 HTTP/HTTPS asset，再改为：

```python
DEFAULT_DRY_RUN = False
```

执行 L1 下载：

```powershell
python main.py
```

## 命令行临时覆盖配置

虽然推荐直接改 `config.py`，程序仍保留命令行覆盖能力。

只预览单个 Path/Row：

```powershell
python main.py --dry-run --products L2 --path 118 --row 039 --month 202508
```

强制真实下载：

```powershell
python main.py --download --products L2 --path 118 --row 039 --month 202508
```

只处理 L1：

```powershell
python main.py --products L1 --path 118 --row 039 --month 202508
```

重试失败记录：

```powershell
python main.py --retry
```

测速：

```powershell
python main.py --benchmark --path 118 --row 039 --month 202508
```

注意：测速会真实下载样本文件。

## 关于 L1 数据源

当前策略是：

- 使用 USGS STAC 查询 L1。
- 只接受 HTTP/HTTPS asset。
- 拒绝 `s3://` requester-pays asset。

因此如果当前网络无法访问 USGS STAC，或者 USGS 只返回 `s3://` asset，则 L1 不会下载。

这是有意设计，目的是避免产生 AWS requester-pays 费用。

## 关于断点续传和状态记录

下载器会使用：

```text
filename.part
```

作为临时文件。下载完成后再重命名为正式文件。

状态记录在：

```text
download.db
download.csv
```

支持：

- 已下载文件跳过
- 失败记录重试
- 中断后恢复
- 文件级状态记录

## 常见问题

### 1. 直接运行 `python main.py` 会下载什么？

取决于 `config.py`：

```python
DEFAULT_PRODUCTS
DEFAULT_DRY_RUN
YEAR_MONTHS
TARGET_PATH_ROWS
```

### 2. 会不会产生 AWS requester-pays 费用？

当前版本不会主动使用 AWS requester-pays。

程序已经禁用：

- boto3 下载
- AWS credential 配置
- S3 requester-pays fallback

遇到 `s3://` asset 时会跳过或拒绝。

### 3. 为什么 L1 没有下载？

常见原因：

- 当前机器无法访问 `https://landsatlook.usgs.gov/stac-server`
- USGS STAC 没有返回 HTTP/HTTPS asset
- `DEFAULT_PRODUCTS` 当前设置为 `"L2"`

### 4. 为什么建议先下载 L2？

L2 数据量最大，MPC 下载路径相对稳定。先下载 L2 可以完成主要数据部分；L1 后续等 USGS 网络恢复后单独补齐。
