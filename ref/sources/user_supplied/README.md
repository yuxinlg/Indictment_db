# user_supplied — source documents for the reference tables

Files the build scripts (`scripts/ref_regions.py`, `scripts/ref_charges.py`) read from this folder.

## Committed (public domain or government notices)

| file | what it is | status |
|---|---|---|
| `最高人民法院关于执行《中华人民共和国刑法》确定罪名的规定.epub` | 法释〔1997〕9号, Wikisource export | official text, public domain (著作权法 第五条) |
| `…确定罪名的补充规定.epub` … `（七）.epub` | 法释〔2002〕7号 … 法释〔2021〕2号, Wikisource exports | same |
| `2016年第26号：关于批准发布GBT 2260-2007…第1号修改单的公告.docx` | 国家标准委 announcement with the 2016 amendment table | government notice, public domain |

## Not committed (obtain separately and drop into this folder)

| file | why it is excluded | where to get it |
|---|---|---|
| `GB_T 2260-2007 中华人民共和国行政区划代码.pdf` | national standard; copyright held by the standards body | 国家标准全文公开系统 (openstd.samr.gov.cn) or a standards vendor |
| `县以上行政区划代码历史大全（民政部1980年-2025年+老国标+非官方数据）.xlsx` | individual's compilation, no licence stated | https://zhuanlan.zhihu.com/p/564774073 |
| `标准地址库分开版（2025）.xlsx` | provenance unknown; not used by any script | — |
| `医保参保区划编码总览（广东省医保用）.xlsx` | provenance unknown; not used by any script | — |

Without the PDF and the workbook, `ref_regions.py` cannot rebuild `ref/region_codes.csv` or its
validation report; the committed outputs and `ref/region_codes_validation.md` record the last build.
`ref_charges.py` needs only the EPUBs and runs as is.
