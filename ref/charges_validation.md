# Standard charge table — build and validation report

Generated 2026-10-07 by scripts/ref_charges.py.

## 1. Instruments applied (in order)

```
补充规定（八）.epub 法释〔2024〕3号       effective 2024-01-30: +0 charges, 0 names cancelled
  法释〔2002〕7号: implicit rename '盗窃、抢夺枪支、弹药、爆炸物罪' -> '盗窃、抢夺枪支、弹药、爆炸物、危险物质罪' (same article 127)
  法释〔2002〕7号: implicit rename '抢劫枪支、弹药、爆炸物罪' -> '抢劫枪支、弹药、爆炸物、危险物质罪' (same article 127)
  法释〔2002〕7号: implicit rename '伪造、变造、转让金融机构经营许可证罪' -> '伪造、变造、转让金融机构经营许可证、批准文件罪' (same article 174)
  法释〔2002〕7号: implicit rename '编造并传播证券交易虚假信息罪' -> '编造并传播证券、期货交易虚假信息罪' (same article 181)
  法释〔2002〕7号: implicit rename '诱骗投资者买卖证券罪' -> '诱骗投资者买卖证券、期货合约罪' (same article 181)
  法释〔2002〕7号: implicit rename '操纵证券交易价格罪' -> '操纵证券、期货交易价格罪' (same article 182)
  法释〔2002〕7号: cancelled name not found in table: '国家机关工作人员徇私舞弊罪' (block 第397条)
  法释〔2002〕7号: cancelled name not found in table: '枉法追诉、裁判罪' (block 第399条第1款)
罪名的补充规定.epub 法释〔2002〕7号       effective 2002-03-15: +21 charges, 15 names cancelled
补充规定（二）.epub 法释〔2003〕12号      effective 2003-08-15: +7 charges, 3 names cancelled
  法释〔2007〕16号: cancelled '用账外客户资金非法拆借、发放贷款罪' matched to '用帐外客户资金非法拆借、发放贷款罪' (fuzzy)
补充规定（三）.epub 法释〔2007〕16号      effective 2007-10-25: +21 charges, 8 names cancelled
补充规定（四）.epub 法释〔2009〕13号      effective 2009-10-14: +13 charges, 4 names cancelled
补充规定（五）.epub 法释〔2011〕10号      effective 2011-04-27: +10 charges, 3 names cancelled
  法释〔2015〕20号: cancelled '组织、利用会道门、邪教组织、利用迷信致人死亡罪' matched to '组织、利用会道门、邪教组织利用迷信致人死亡罪' (fuzzy)
补充规定（六）.epub 法释〔2015〕20号      effective 2015-10-30: +31 charges, 14 names cancelled
补充规定（七）.epub 法释〔2021〕2号       effective 2021-02-26: +25 charges, 10 names cancelled
```

Result: **540** charge names ever in force; **483** in force after 法释〔2021〕2号 (the published figure for the current Criminal Law is 483); 54 duty-crime names (chapters 8–9).

Transcription errata corrected (Wikisource text vs official wording, verified against corpus usage):

- `伪造、变造、买卖国家机关公文文、证件、印章罪` → `伪造、变造、买卖国家机关公文、证件、印章罪`
- `为境外窃取、剌探、收买、非法提供国家秘密、情报罪` → `为境外窃取、刺探、收买、非法提供国家秘密、情报罪`
- `虚开增值税专用发票、用于骗取出口退税、抵押税款发票罪` → `虚开增值税专用发票、用于骗取出口退税、抵扣税款发票罪`

Cancellations the text states under a name that never existed in the 最高法 1997 list (they were names in the 最高检 1997 list, unified by 法释〔2002〕7号) are logged above as 'not found' and need no action.

## 2. Alias map built from the corpus

Every string at the 应以…罪追究 and 涉嫌…罪 positions in the 2.4M documents was segmented into official names.

| method | share of charge mentions |
|---|---|
| exact | 84.26% |
| segmented | 11.68% |
| normalised | 3.63% |
| partial | 0.01% |
| unresolved residue | 0.42% |

exact = the official name as written; segmented = several names in one clause or a selective sub-name (贩卖毒品罪 → 走私、贩卖、运输、制造毒品罪); normalised = wrapper removed (犯有X罪, X犯罪, X罪罪); partial = a clause only part of which could be resolved.

## 3. Top unresolved residues (manual queue)

| residue | documents |
|---|---|
| 犯罪 | 5,529 |
| 的犯罪 | 1,045 |
| XX罪 | 447 |
| ***罪 | 412 |
| 贩卖罪 | 407 |
| 危险罪 | 342 |
| 且系共同罪 | 327 |
| ****罪 | 308 |
| **罪 | 213 |
| 毒品犯罪 | 206 |
| 非法持有罪 | 179 |
| 同一罪 | 175 |
| 故意罪 | 166 |
| 非法制造发票罪 | 166 |
| 环境污染罪 | 163 |
| 非法占有农用地罪 | 161 |
| 容留他人罪 | 156 |
| 故意毁坏公私财物罪 | 155 |
| 信用卡罪 | 153 |
| 过失损坏军事通信罪 | 137 |
| 国有公司人员失职罪 | 134 |
| 伤害罪 | 133 |
| 贩毒罪 | 131 |
| 故意损坏财物罪 | 130 |
| 生产、销售不符合食品安全标准的食品罪 | 130 |
