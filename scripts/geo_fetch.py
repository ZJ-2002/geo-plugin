# 中文注释：GEO/SRA 元数据下载（geo_series_matrix / geo_soft / sra_runinfo 共用）。
# 输入构念：GSE accession（matrix/soft 模式）或 GSE/SRP/SRX/SRR（runinfo 模式）。
# 规则来源：NCBI 官方 HTTPS 镜像与 Run Selector 公开端点，均为公开数据。
# 失败处理：accession 格式不符 → 退出码 2；网络错误指数退避重试 3 次后退出码 1；
# 下载字节 gzip/tar 魔数校验失败 → 退出码 3（防半截文件被当有效输出）。
# 下载与 sha256 同时流式计算，log 自带内容锚，可直接引用进运行账本（§26.1）。
# 解释边界：本节点只取原始字节，不解析、不清洗；多平台 gz 内含多个 series 块，
# SOFT 字段语义、供者↔样本映射的核验责任在下游节点与分析注册表。
import gzip
import hashlib
import os
import re
import sys
import time

import requests

MODE = os.environ.get("GEO_MODE", "matrix")
ACCESSION = os.environ.get("GEO_ACCESSION", os.environ.get("SRA_ACCESSION", ""))
SUPPL_FILE = os.environ.get("GEO_SUPPL_FILE", "")
OUT_FILE = os.environ["AUTONOMICS_OUTPUT0"]
OUT_LOG = os.environ["AUTONOMICS_OUTPUT1"]

RETRIES = 3
TIMEOUT = (30, 300)
HEADERS = {"User-Agent": "autonomics-geo-fetch/1.0"}


def fail(code, message):
    print(message, file=sys.stderr)
    sys.exit(code)


def build_url():
    if MODE == "matrix":
        require("GSE\\d+", "GSE series accession (e.g. GSE130563)")
        number = int(ACCESSION[3:])
        prefix = f"GSE{number // 1000}nnn"
        return (
            "https://ftp.ncbi.nlm.nih.gov/geo/series/"
            f"{prefix}/{ACCESSION}/matrix/{ACCESSION}_series_matrix.txt.gz"
        )
    if MODE == "soft":
        require("GSE\\d+", "GSE series accession (e.g. GSE130563)")
        number = int(ACCESSION[3:])
        prefix = f"GSE{number // 1000}nnn"
        return (
            "https://ftp.ncbi.nlm.nih.gov/geo/series/"
            f"{prefix}/{ACCESSION}/soft/{ACCESSION}_family.soft.tgz"
        )
    if MODE == "runinfo":
        require("(GSE|SRP|SRX|SRR)\\d+", "GSE/SRP/SRX/SRR accession")
        return f"https://www.ncbi.nlm.nih.gov/Traces/sra-db-be/run_new?acc={ACCESSION}"
    if MODE == "suppl":
        require("GSE\\d+", "GSE series accession (e.g. GSE92742)")
        # suppl 文件名按上游发布原名传参：只允许单段文件名，杜绝路径拼接注入。
        if not re.fullmatch(r"[A-Za-z0-9._+-]+", SUPPL_FILE or ""):
            fail(2, f"GEO_SUPPL_FILE must be a bare filename (no / or ..): {SUPPL_FILE!r}")
        number = int(ACCESSION[3:])
        prefix = f"GSE{number // 1000}nnn"
        return (
            "https://ftp.ncbi.nlm.nih.gov/geo/series/"
            f"{prefix}/{ACCESSION}/suppl/{SUPPL_FILE}"
        )
    fail(2, f"GEO_MODE must be matrix|soft|runinfo|suppl, got {MODE!r}")


def require(pattern, label):
    if not re.fullmatch(pattern, ACCESSION or ""):
        fail(2, f"{label} must match {pattern}, got {ACCESSION!r}")


def check_magic(path):
    # matrix/soft 产物必须是 gzip；suppl 按后缀（.gz → 必须 gzip，
    # 其余不设硬魔数——gctx/gct/tar 原名直传）；runinfo 是 TSV 文本。
    with open(path, "rb") as handle:
        magic = handle.read(2)
    if MODE in ("matrix", "soft"):
        if magic != b"\x1f\x8b":
            fail(3, f"downloaded bytes are not gzip (magic={magic!r}); refusing to publish")
    elif MODE == "suppl":
        if SUPPL_FILE.endswith(".gz") and magic != b"\x1f\x8b":
            fail(3, f"suppl file {SUPPL_FILE!r} should be gzip (magic={magic!r}); refusing to publish")
        if magic[:1] == b"<":
            fail(3, "suppl endpoint returned HTML (likely wrong filename); refusing to publish")
    else:
        if magic == b"\x1f\x8b" or magic[:1] == b"<":
            fail(3, "runinfo endpoint returned gzip/html instead of TSV; refusing to publish")


def download(url):
    last_error = None
    for attempt in range(1, RETRIES + 1):
        try:
            with requests.get(url, stream=True, timeout=TIMEOUT, headers=HEADERS) as response:
                response.raise_for_status()
                hasher = hashlib.sha256()
                size = 0
                with open(OUT_FILE, "wb") as handle:
                    for block in response.iter_content(chunk_size=1024 * 1024):
                        handle.write(block)
                        hasher.update(block)
                        size += len(block)
            return size, hasher.hexdigest(), response.status_code
        except Exception as error:  # noqa: BLE001 - 全部网络类异常统一重试
            last_error = error
            time.sleep(2 ** attempt)
    fail(1, f"download failed after {RETRIES} attempts: {last_error}")


url = build_url()
size, digest, status = download(url)
check_magic(OUT_FILE)

with open(OUT_LOG, "w", encoding="utf-8") as log:
    lines = [
        f"mode\t{MODE}",
        f"accession\t{ACCESSION}",
    ]
    if MODE == "suppl":
        # 输出文件名固定为 suppl_file，原始名只记录在 log 里自锚。
        lines.append(f"suppl_file\t{SUPPL_FILE}")
    lines += [
        f"url\t{url}",
        f"http_status\t{status}",
        f"bytes\t{size}",
        f"sha256\t{digest}",
        f"completed\t{time.strftime('%Y-%m-%dT%H:%M:%S%z')}",
    ]
    log.write("\n".join(lines) + "\n")
