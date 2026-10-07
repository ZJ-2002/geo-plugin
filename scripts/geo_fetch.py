# 中文注释：GEO/SRA 元数据下载（geo_series_matrix / geo_soft / sra_runinfo /
# geo_suppl / geo_suppl_list 共用）。
# 输入构念：GSE accession（matrix/soft/suppl/list 模式）或 GSE/SRP/SRX/SRR（runinfo 模式）。
# 规则来源：NCBI 官方 HTTPS 镜像与 Run Selector 公开端点，均为公开数据。
# 失败处理：accession 格式不符 → 退出码 2；网络错误指数退避重试 3 次后退出码 1；
# 下载字节 gzip/tar 魔数校验失败 → 退出码 3（防半截文件被当有效输出）；
# list 模式：404（该系列无 suppl 目录）→ 空清单 + log 记录，不算失败；
# 返回 200 但条目行解析不全（整页或局部格式漂移，页面锚点多于解析结果）
# → 退出码 3（宁可失败，不出错清单、不漏文件）。
# 清单大小列 approx_size_bytes 是列表页人类可读标注的换算近似值
# （如 2.5K → 2560；实测 cell_info.txt.gz 列表 2560 vs 实际下载 2528），
# 只用于磁盘预算与清单粗核对，禁止直接作为 file_download 的 expected_bytes；
# 字节终裁以实际下载后的字节数与 sha256 为准。
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


def suppl_dir_url():
    require("GSE\\d+", "GSE series accession (e.g. GSE92742)")
    number = int(ACCESSION[3:])
    prefix = f"GSE{number // 1000}nnn"
    return (
        "https://ftp.ncbi.nlm.nih.gov/geo/series/"
        f"{prefix}/{ACCESSION}/suppl/"
    )


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
        return suppl_dir_url() + SUPPL_FILE
    if MODE == "list":
        return suppl_dir_url()
    fail(2, f"GEO_MODE must be matrix|soft|runinfo|suppl|list, got {MODE!r}")


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


# NCBI HTTPS 目录是 `<pre>` 行式列表：每行一个条目，
# `<a href="名字">名字</a>  日期 时间  大小`；大小是人类标注（1.2T / 99M /
# 26K / 1.4K），目录项为 `-` 且 href 以 / 结尾。
LISTING_ROW = re.compile(
    r'^<a href="([^"]+)">[^<]*</a>\s+'
    r'(\d{4}-\d{2}-\d{2} \d{2}:\d{2})\s+'
    r'(-|[0-9][0-9.,]*[KMGT]?)\s*$'
)
SIZE_MULTIPLIERS = {"K": 1024, "M": 1024**2, "G": 1024**3, "T": 1024**4}
ANCHOR_HREF = re.compile(r'<a\s[^>]*href="([^"]+)"', re.IGNORECASE)


def parse_size(token):
    if token == "-":
        return 0
    multiplier = SIZE_MULTIPLIERS.get(token[-1].upper())
    if multiplier:
        return int(float(token[:-1]) * multiplier)
    return int(token.replace(",", ""))


def file_anchors(html):
    """页面中所有『裸文件名』锚点（不含目录/父目录/查询串）——
    行解析结果的完整性对照集合：每一个都必须被解析成清单条目。"""
    return {
        href
        for href in ANCHOR_HREF.findall(html)
        if "/" not in href and not href.startswith("?") and href != ".."
    }


def parse_listing(html):
    """解析目录页 → (rows, missing)。
    rows = [(filename, approx_size_bytes, last_modified)]，已去重、未排序；
    missing = 存在锚点却未被行解析覆盖的文件名（页面格式局部漂移）。"""
    rows = []
    seen = set()
    for line in html.splitlines():
        row = LISTING_ROW.match(line.strip())
        if row is None:
            continue
        href, modified, size_token = row.groups()
        if href.endswith("/") or href in seen:
            continue
        seen.add(href)
        rows.append((href, parse_size(size_token), modified))
    return rows, file_anchors(html) - seen


def fetch_listing(url):
    """拉目录 HTML。404 → (None, 404)：该系列无 suppl 目录，合法空态。"""
    last_error = None
    for attempt in range(1, RETRIES + 1):
        try:
            with requests.get(url, timeout=TIMEOUT, headers=HEADERS) as response:
                if response.status_code == 404:
                    return None, 404
                response.raise_for_status()
                return response.text, response.status_code
        except Exception as error:  # noqa: BLE001 - 全部网络类异常统一重试
            last_error = error
            time.sleep(2 ** attempt)
    fail(1, f"listing failed after {RETRIES} attempts: {last_error}")


def run_list_mode():
    url = build_url()
    html, status = fetch_listing(url)
    if html is None:
        rows = []
        listing_note = f"{status} (no suppl directory)"
    else:
        rows, missing = parse_listing(html)
        # 完整性闸门：一条条目都没解析出（整页漂移），或页面锚点比解析
        # 结果多（局部漂移会静默漏文件），都拒绝输出清单。
        if not rows or missing:
            detail = f"; unparsed anchors: {sorted(missing)}" if missing else ""
            fail(
                3,
                f"suppl listing incomplete: parsed {len(rows)} entries{detail} "
                "(NCBI listing format may have changed); refusing to emit a manifest",
            )
        rows.sort()
        listing_note = str(status)

    with open(OUT_FILE, "w", encoding="utf-8") as manifest:
        manifest.write("filename\tapprox_size_bytes\tlast_modified\n")
        for href, approx_size, modified in rows:
            manifest.write(f"{href}\t{approx_size}\t{modified}\n")

    with open(OUT_LOG, "w", encoding="utf-8") as log:
        log.write(
            "\n".join(
                [
                    f"mode\t{MODE}",
                    f"accession\t{ACCESSION}",
                    f"url\t{url}",
                    f"listing_status\t{listing_note}",
                    f"files\t{len(rows)}",
                    f"manifest_sha256\t{hashlib.sha256(open(OUT_FILE, 'rb').read()).hexdigest()}",
                    f"completed\t{time.strftime('%Y-%m-%dT%H:%M:%S%z')}",
                ]
            )
            + "\n"
        )


def main():
    if MODE == "list":
        run_list_mode()
        sys.exit(0)

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


if __name__ == "__main__":
    main()
