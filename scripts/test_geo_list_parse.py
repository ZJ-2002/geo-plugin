# 中文注释：geo_fetch.py list 模式解析逻辑的离线自测（不联网）。
# 覆盖：大小标注换算（含近似语义）、整页/局部格式漂移拒出、
# 目录项与父目录锚点排除、清单列名。
# 运行：python3 scripts/test_geo_list_parse.py（退出 0 = 全绿）。
import importlib.util
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))

# geo_fetch 顶层读取输出环境变量；给占位值以便无副作用导入。
_TMP = tempfile.mkdtemp(prefix="geo-selftest-")
os.environ.setdefault("AUTONOMICS_OUTPUT0", os.path.join(_TMP, "out0"))
os.environ.setdefault("AUTONOMICS_OUTPUT1", os.path.join(_TMP, "out1"))
os.environ.setdefault("GEO_ACCESSION", "GSE92742")
os.environ.setdefault("GEO_MODE", "list")

_spec = importlib.util.spec_from_file_location(
    "geo_fetch", os.path.join(HERE, "geo_fetch.py")
)
geo = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(geo)


def expect(cond, message):
    if not cond:
        print(f"FAIL: {message}", file=sys.stderr)
        sys.exit(1)


# --- 大小标注换算：人类可读标注 → 近似字节 ---
expect(geo.parse_size("-") == 0, "目录项大小为 0")
expect(geo.parse_size("123") == 123, "裸数字直通")
expect(geo.parse_size("1,234") == 1234, "千分位剥离")
expect(geo.parse_size("2.5K") == 2560, "2.5K -> 2560（近似：2528 实测会差）")
expect(geo.parse_size("99M") == 99 * 1024**2, "99M")
expect(geo.parse_size("20G") == 20 * 1024**3, "20G")
expect(geo.parse_size("1.2T") == int(1.2 * 1024**4), "1.2T")

# --- 干净页面：两条文件记录全部解析 ---
# 注：Apache autoindex 每条目一行（浏览器只是视觉换行）；长名不折行。
CLEAN = """<html><body>
<h1>Index of /geo/series/GSE92nnn/GSE92742/suppl</h1><hr>
<pre><a href="../">../</a>
<a href="cell_info.txt.gz">cell_info.txt.gz</a>      2024-01-02 03:04   2.5K
<a href="GSE92742_Broad_LINCS_Level5_COMPZ.MODZ_n473647x12328.gctx.gz">GSE92742_Broad_LINCS_Level5_COMPZ.MODZ_n473647x12328.gctx.gz</a>  2024-01-02 03:04   20G
</pre><hr></body></html>
"""
rows, missing = geo.parse_listing(CLEAN)
expect(missing == set(), f"干净页面无缺失锚点，got {missing}")
expect(len(rows) == 2, f"两条文件记录，got {len(rows)}")
names = sorted(name for name, _, _ in rows)
expect("cell_info.txt.gz" in names and len(names) == 2, f"两条文件名，got {names}")
expect(dict((n, s) for n, s, _ in rows)["cell_info.txt.gz"] == 2560, "2.5K 换算")

# --- 局部漂移：一条记录换格式 → 锚点仍在但行解析缺失 → 必须拒绝 ---
DRIFT = """<html><body><pre>
<a href="cell_info.txt.gz">cell_info.txt.gz</a>      2024-01-02 03:04   2.5K
<a href="pert_info.txt">pert_info.txt</a>            2024-01-02 03:04
</pre></body></html>
"""
rows, missing = geo.parse_listing(DRIFT)
expect(
    missing == {"pert_info.txt"},
    f"局部漂移必须暴露缺失锚点，got rows={rows} missing={missing}",
)

# --- 整页漂移：锚点在、行格式全变 → 拒绝（而非空清单） ---
TOTAL = """<html><body><pre>
<li><span class="name"><a href="a.txt">a.txt</a></span><span class="size">1K</span></li>
</pre></body></html>
"""
rows, missing = geo.parse_listing(TOTAL)
expect(rows == [] and missing == {"a.txt"}, f"整页漂移：rows={rows} missing={missing}")

# --- 引号风格：单引号/无引号 href 都要被行解析覆盖（解析器提取
#     锚点集合后，引号盲区不再是行解析与锚点对照的共同盲区）---
SINGLE = """<html><body><pre>
<a href='cell_info.txt.gz'>cell_info.txt.gz</a>      2024-01-02 03:04   2.5K
<a href="other.txt.gz">other.txt.gz</a>              2024-01-02 03:04   1.4K
</pre></body></html>
"""
rows, missing = geo.parse_listing(SINGLE)
expect(
    missing == set() and len(rows) == 2,
    f"单引号 href 必须被解析，got rows={rows} missing={missing}",
)

BARE = """<html><body><pre>
<a href=cell_info.txt.gz>cell_info.txt.gz</a>        2024-01-02 03:04   2.5K
</pre></body></html>
"""
rows, missing = geo.parse_listing(BARE)
expect(
    missing == set() and len(rows) == 1,
    f"无引号 href 必须被解析，got rows={rows} missing={missing}",
)

# 行正则不认识的写法（属性顺序变化）→ HTML 解析器仍看到锚点 → 闸门暴露
REORDERED = """<html><body><pre>
<a href="cell_info.txt.gz">cell_info.txt.gz</a>      2024-01-02 03:04   2.5K
<a class="row" href="pert_info.txt">pert_info.txt</a> 2024-01-02 03:04   900
</pre></body></html>
"""
rows, missing = geo.parse_listing(REORDERED)
expect(
    missing == {"pert_info.txt"},
    f"属性重排必须暴露缺失锚点（闸门兜底），got rows={rows} missing={missing}",
)

# --- 清单列名带 approx 前缀（防止近似大小被当精确字节使用）---
orig_fetch = geo.fetch_listing
try:
    geo.fetch_listing = lambda url: (CLEAN, 200)
    with tempfile.TemporaryDirectory(prefix="geo-list-out-") as out_dir:
        manifest = os.path.join(out_dir, "suppl_manifest.tsv")
        log = os.path.join(out_dir, "log.txt")
        geo.OUT_FILE, geo.OUT_LOG = manifest, log
        geo.run_list_mode()
        header = open(manifest).readline().rstrip("\n")
        expect(
            header == "filename\tapprox_size_bytes\tlast_modified",
            f"清单表头必须是 approx_size_bytes，got {header!r}",
        )
        expect("files\t2" in open(log).read(), "log 记录文件数")
finally:
    geo.fetch_listing = orig_fetch

print("all green")
