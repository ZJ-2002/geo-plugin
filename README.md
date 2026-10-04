# geo

GEO/SRA 公共数据下载源节点。补齐引擎缺口：原 registry 无任何 GEO/SRA
下载工厂，主计划的数据集获取（GSE 系列矩阵、SOFT 元数据、SRA run 表）
此前只能宿主机 curl。2026-10-04 增补 `geo_suppl`：系列 suppl/ 目录下
任意具名补充文件（LINCS/CMap 的 GCT/GCTx 矩阵由此进 DAG，方案 §18.2）。

## 节点（全部零输入、Egress、幂等下载）

| kind | 输出 | 上游 |
|---|---|---|
| `geo_series_matrix` | `series_matrix.txt.gz` | `ftp.ncbi.nlm.nih.gov/geo/series/GSEnnn/GSExxx{n}nnn/matrix/` |
| `geo_soft` | `family.soft.tgz` | 同上 `soft/` |
| `sra_runinfo` | `runinfo.tsv` | NCBI Run Selector `run_new?acc=` |
| `geo_suppl` | `suppl_file`（原名字节，不解压） | `…/GSEnnn/GSExxx{n}nnn/suppl/{suppl_file}` |

参数：`accession`（string，必填；GSE\\d+ / SRR\\d+|SRP\\d+|PRJNA\\d+，
脚本内正则校验，格式错 → 退出 2）；`geo_suppl` 另有 `suppl_file`
（suppl/ 下确切文件名，仅裸文件名——含 `/` 或 `..` 拒绝；`.gz` 后缀时
强制 gzip 魔数校验，HTML 响应拒绝）。`geo_suppl` 超时 28800s（LINCS
Level5 全量 gctx.gz 为 GB 级）。

## 布局

```text
geo/
├── manifest.toml
├── scripts/geo_fetch.py   # 单文件自足：三个节点经 GEO_MODE 复用
├── Dockerfile
└── README.md
```

镜像：`localhost/autonomics/biotools-py@sha256:21e14c1582a9d4c261c0a23a11864293313ee2b49b9b7d85bcabc89210b48c7e`。

## 实现说明（引擎契约踩坑）

- `script_file` 被内联成容器内单一脚本，`scripts/` 目录**不挂载**——
  共享模块 import 不可行，故三个节点共用一份自足脚本、以 `GEO_MODE`
  环境变量分派。
- 3 次指数退避重试；边下边算 sha256；gzip 魔数校验拒绝半截文件
  （退出 3）；log 自锚 URL/状态码/字节数/摘要，可直接喂 `file_hashes`
  形成投递链。

## 冒烟记录

- 实网下载 GSE18832：HTTP 200、863080 字节、gzip 合法、sha256
  `0e9995eb…` 自锚入 log。
- `geo_suppl`（2026-10-04）：GSE92742_Broad_LINCS_gene_info.txt.gz →
  200 / 216692 B / sha256 `741216cc…`，gzip 解压后即 LINCS Entrez↔符号
  映射表（cmap_connectivity 的行标识符映射上游）。
