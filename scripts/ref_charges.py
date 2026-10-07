#!/usr/bin/env python3
"""
R — build the standard charge table (标准罪名表) and the alias map used to normalise charge
strings found in the corpus.

Inputs (ref/sources/user_supplied/*.epub, Wikisource exports of the official texts):
  最高人民法院关于执行《中华人民共和国刑法》确定罪名的规定            法释〔1997〕9号   base list
  两高 …确定罪名的补充规定 (一)…(七)                                   2002 … 2021       amendments

Outputs (ref/):
  charges_std.csv          one row per charge name ever in force: article, chapter, validity,
                           which instrument added / abolished it, duty-crime flag
  charge_aliases.csv       raw string (as written in the corpus) -> charge_std, with counts
  charges_unmatched.csv    residues the alias builder could not resolve, by frequency (manual queue)

Semantics
  * A charge is identified by its exact official name. When an instrument renames a charge
    (e.g. 强制猥亵妇女罪 -> 强制猥亵、侮辱罪) the old row is closed (valid_to, abolished_by,
    replaced_by) and a new row opened. Documents from 2015 still use old names, so both rows
    are needed and both are legal aliases of the concept.
  * is_duty_crime = chapter 8 (贪污贿赂) or chapter 9 (渎职). Commercial bribery (ch. 3) is excluded.
  * The alias map is built from the corpus: every charge string at the 应以…罪追究 and 涉嫌…罪
    positions is segmented into known charge names by longest match. Anything left over goes to
    charges_unmatched.csv for manual review; nothing is guessed.

Run:  python3 scripts/ref_charges.py            (--no-corpus skips the alias build)
"""
import argparse
import csv
import html
import re
import sys
import zipfile
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
US = ROOT / "ref" / "sources" / "user_supplied"
OUT_STD = ROOT / "ref" / "charges_std.csv"
OUT_ALIAS = ROOT / "ref" / "charge_aliases.csv"
OUT_UNMATCHED = ROOT / "ref" / "charges_unmatched.csv"
PARQUET = Path.home() / "indictment_work" / "raw.parquet" / "pub_year=*" / "*.parquet"

CHAPTERS = [  # article range -> chapter number and title (刑法分则)
    (102, 113, 1, "危害国家安全罪"), (114, 139, 2, "危害公共安全罪"),
    (140, 231, 3, "破坏社会主义市场经济秩序罪"), (232, 262, 4, "侵犯公民人身权利、民主权利罪"),
    (263, 276, 5, "侵犯财产罪"), (277, 367, 6, "妨害社会管理秩序罪"), (368, 381, 7, "危害国防利益罪"),
    (382, 396, 8, "贪污贿赂罪"), (397, 419, 9, "渎职罪"), (420, 451, 10, "军人违反职责罪"),
]

_CN = {c: i for i, c in enumerate("零一二三四五六七八九")}


def cn2int(s: str) -> int:
    """一百二十 -> 120, 四百五十一 -> 451, 十 -> 10, 二十三 -> 23. Arabic passes through."""
    if s.isdigit():
        return int(s)
    n, num = 0, 0
    for ch in s:
        if ch == "百":
            n += (num or 1) * 100; num = 0
        elif ch == "十":
            n += (num or 1) * 10; num = 0
        else:
            num = _CN[ch]
    return n + num


def epub_text(path: Path) -> str:
    z = zipfile.ZipFile(path)
    out = []
    for n in z.namelist():
        if n.startswith("OPS/c") and n.endswith(".xhtml"):
            h = z.read(n).decode("utf-8", "ignore")
            h = re.sub(r"<style.*?</style>|<script.*?</script>", "", h, flags=re.S)
            t = html.unescape(re.sub(r"<[^>]+>", "\n", h))
            out.append(re.sub(r"\n\s*\n+", "\n", t).strip())
    return "\n".join(out)


def chapter_of(article: int):
    for lo, hi, n, title in CHAPTERS:
        if lo <= article <= hi:
            return n, title
    return None, ""


# Transcription errors in the Wikisource texts, corrected here and logged. Official wording verified
# against the corpus (the corpus never uses the erroneous forms).
ERRATA = {
    "伪造、变造、买卖国家机关公文文、证件、印章罪": "伪造、变造、买卖国家机关公文、证件、印章罪",
    "为境外窃取、剌探、收买、非法提供国家秘密、情报罪": "为境外窃取、刺探、收买、非法提供国家秘密、情报罪",
    "虚开增值税专用发票、用于骗取出口退税、抵押税款发票罪": "虚开增值税专用发票、用于骗取出口退税、抵扣税款发票罪",
}


def article_key(ref: str) -> str:
    """'第125条　第2款' -> '125.2'; '第一百二十条之二' -> '120之2'; '第127条　第1款、第2款' -> '127.1,2';
    '第114条、第115条　第1款' -> '114;115.1'. Used to tell apart charges that share a 条 number."""
    parts = []
    for seg in re.split(r"[；;]", ref):
        for m in re.finditer(r"第([0-9一二三四五六七八九十百]+)条(?:之([一二三四五六七八九]))?((?:[\s　]*第[0-9一二三四五六七八九十]+款[、，]?)*)", seg):
            k = str(cn2int(m.group(1))) + (f"之{cn2int(m.group(2))}" if m.group(2) else "")
            kuan = [str(cn2int(x)) for x in re.findall(r"第([0-9一二三四五六七八九十]+)款", m.group(3) or "")]
            if kuan:
                k += "." + ",".join(kuan)
            parts.append(k)
    return ";".join(parts)


RE_ART_LINE = re.compile(r"^第[0-9一二三四五六七八九十百]+条")
RE_ART_NO = re.compile(r"第([0-9一二三四五六七八九十百]+)条")
RE_FASHI = re.compile(r"法释〔(\d{4})〕(\d+)号")
RE_DATE = re.compile(r"(\d{4})年(\d{1,2})月(\d{1,2})日")
RE_CANCEL = re.compile(r"取消(.+?罪)(?:罪名)?[）)]?$")


def split_charges(line: str):
    """A line may carry several charge names: 'A罪、B罪' or, where a line break was lost in the
    transcription, 'A罪B罪'. Split after every 罪 that is followed by more text, except when the
    罪 is part of the word 犯罪 inside a name (掩饰、隐瞒犯罪所得…, 帮助犯罪分子逃避处罚罪)."""
    parts, buf = [], ""
    for i, ch in enumerate(line):
        buf += ch
        if ch == "罪" and i + 1 < len(line) and line[i - 1] != "犯" and line[i + 1] not in "名）)":
            parts.append(buf); buf = ""
    if buf:
        parts.append(buf)
    return [p.strip("、，; ") for p in parts if p.strip("、，; ").endswith("罪")]


# --------------------------------------------------------------------------- 1997 base
def parse_base(text: str):
    lines = [l.strip() for l in text.split("\n")]
    fashi = RE_FASHI.search(text)
    instrument = f"法释〔{fashi.group(1)}〕{fashi.group(2)}号"
    start = next(i for i, l in enumerate(lines) if l.startswith("第一章"))
    chapter = section = None
    rows, art_block, chg_block = [], [], []

    def flush():
        nonlocal art_block, chg_block
        if chg_block:
            pairs = list(zip(art_block, chg_block)) if len(art_block) == len(chg_block) else [("；".join(art_block), c) for c in chg_block]
            for a, c in pairs:
                nums = sorted({cn2int(n) for n in RE_ART_NO.findall(a)}) or sorted({cn2int(n) for n in RE_ART_NO.findall("；".join(art_block))})
                rows.append(dict(charge_std=c, article_nos=nums, article_ref=a, chapter_title=chapter, section=section, added_by=instrument))
        art_block, chg_block = [], []

    for l in lines[start:]:
        if not l:
            continue
        if re.match(r"^第[一二三四五六七八九十]+章", l):
            flush(); chapter = l; section = None; continue
        if re.match(r"^第[一二三四五六七八九十]+节", l):
            flush(); section = l; continue
        if l in ("刑法条文", "罪名"):
            continue
        if RE_ART_LINE.match(l) or re.match(r"^第\d+款", l):
            if chg_block:
                flush()
            if re.match(r"^第\d+款", l) and art_block:  # continuation line "第2款"
                art_block.append(re.match(r"(第\d+条)", art_block[-1]).group(1) + "　" + l)
            else:
                art_block.append(l)
            continue
        if l.endswith("罪"):
            chg_block.append(ERRATA.get(l, l)); continue
        if l.startswith(("本规定", "本作品", "附")):
            flush(); break
    flush()
    return rows, instrument


# --------------------------------------------------------------------------- supplements
def parse_supplement(text: str):
    """Return (instrument, date, blocks). block = dict(article_ref, article_nos, source, added[], cancelled[])."""
    fashi = RE_FASHI.search(text)
    instrument = f"法释〔{fashi.group(1)}〕{fashi.group(2)}号"
    d = RE_DATE.search(text)
    eff = f"{d.group(1)}-{int(d.group(2)):02d}-{int(d.group(3)):02d}"
    lines = [l.strip() for l in text.split("\n") if l.strip()]
    blocks, cur = [], None
    for l in lines:
        if l.startswith("本作品是由"):
            break
        if RE_ART_LINE.match(l) or l.startswith("《决定》"):
            cur = dict(article_ref=l, article_nos=sorted({cn2int(n) for n in RE_ART_NO.findall(l)}), source="", added=[], cancelled=[])
            blocks.append(cur); continue
        if cur is None:
            continue
        if re.match(r"^[（(]《", l):
            cur["source"] = l.strip("（）()"); continue
        # charge line, possibly with inline cancellation: 逃税罪（取消偷税罪罪名）
        m = re.match(r"^(.*?罪)\s*[（(]取消(.+?罪)(?:罪名)?[）)]$", l)
        if m:
            cur["added"] += split_charges(m.group(1)); cur["cancelled"] += split_cancel(m.group(2)); continue
        m = RE_CANCEL.match(l.strip("（）()"))
        if m:
            cur["cancelled"] += split_cancel(m.group(1)); continue
        for part in re.split(r"[；;]", l):
            part = part.strip()
            if part.endswith("罪"):
                cur["added"] += split_charges(part)
    return instrument, eff, blocks


def split_cancel(s: str):
    """'出售、非法提供公民个人信息罪和非法获取公民个人信息罪' -> both names; also strips '刑法原第155条第3项'."""
    s = re.sub(r"^刑法原第\d+条第\d+项", "", s)
    s = ERRATA.get(s, s)
    parts = [p for p in re.split(r"罪和|罪、(?=[^、]+罪$)", s) if p]
    if len(parts) == 1 and s.count("罪") > 1:  # run-together 'A罪B罪'
        parts = [p[:-1] if p.endswith("罪") else p for p in split_charges(s)]
    out = []
    for p in parts:
        p = p if p.endswith("罪") else p + "罪"
        out.append(p)
    return out


# --------------------------------------------------------------------------- assemble
def assemble():
    base_path = next(US.glob("最高人民法院关于执行*确定罪名的规定.epub"))
    base_rows, base_instr = parse_base(epub_text(base_path))
    table = {}  # charge_std -> row (open)
    closed = []
    order = []
    for r in base_rows:
        ch_no, ch_title = chapter_of(r["article_nos"][0]) if r["article_nos"] else (None, "")
        row = dict(charge_std=r["charge_std"], article_nos="|".join(map(str, r["article_nos"])), article_ref=r["article_ref"],
                   article_key=article_key(r["article_ref"]), chapter_no=ch_no, chapter=ch_title or (r["chapter_title"] or ""), section=(r["section"] or ""),
                   valid_from="1997-12-16", valid_to="", added_by=base_instr, added_source="",
                   abolished_by="", replaced_by="", is_duty_crime=int(ch_no in (8, 9)) if ch_no else 0)
        table[row["charge_std"]] = row; order.append(row["charge_std"])

    supps = sorted(US.glob("*确定罪名的补充规定*.epub"), key=lambda p: ("（" not in p.name, p.name))
    # order: (一) [file without number] first, then （二）…（七）
    seq = {"（二）": 2, "（三）": 3, "（四）": 4, "（五）": 5, "（六）": 6, "（七）": 7}
    supps.sort(key=lambda p: next((v for k, v in seq.items() if k in p.name), 1))
    log = []
    for p in supps:
        instrument, eff, blocks = parse_supplement(epub_text(p))
        n_add = n_cancel = 0
        for b in blocks:
            for name in b["added"]:
                if name in table and not table[name]["valid_to"]:
                    # re-listed under a new article (e.g. renumbered) — update article, keep row
                    table[name]["article_nos"] = "|".join(map(str, b["article_nos"])); table[name]["article_ref"] = b["article_ref"]
                    table[name]["article_key"] = article_key(b["article_ref"])
                    continue
                ch_no, ch_title = chapter_of(b["article_nos"][0]) if b["article_nos"] else (None, "")
                row = dict(charge_std=name, article_nos="|".join(map(str, b["article_nos"])), article_ref=b["article_ref"],
                           article_key=article_key(b["article_ref"]), chapter_no=ch_no, chapter=ch_title, section="", valid_from=eff, valid_to="", added_by=instrument,
                           added_source=b["source"], abolished_by="", replaced_by="", is_duty_crime=int(ch_no in (8, 9)) if ch_no else 0)
                table[name] = row; order.append(name); n_add += 1
            for old in b["cancelled"]:
                r = table.get(old)
                if r is None or r["valid_to"]:
                    # transcription variants (帐/账, missing 、): fall back to the closest open name
                    import difflib
                    cands = difflib.get_close_matches(old, [n for n, x in table.items() if not x["valid_to"]], n=1, cutoff=0.8)
                    if cands:
                        log.append(f"  {instrument}: cancelled {old!r} matched to {cands[0]!r} (fuzzy)")
                        r = table[cands[0]]
                    else:
                        log.append(f"  {instrument}: cancelled name not found in table: {old!r} (block {b['article_ref']})")
                        continue
                r["valid_to"] = eff; r["abolished_by"] = instrument; r["replaced_by"] = "|".join(b["added"])
                closed.append(old); n_cancel += 1
            # implicit renames: the instrument re-states an article with a new name and no 取消 line
            # (e.g. 国有公司、企业人员失职罪 -> 国有公司、企业、事业单位人员失职罪). Close the old
            # name when an older open row sits on exactly the same article and the names are similar.
            import difflib
            for new in b["added"]:
                for old, r in list(table.items()):
                    if old == new or r["valid_to"] or r["added_by"] == instrument or old in b["added"]:
                        continue
                    if r["article_key"] and r["article_key"] == article_key(b["article_ref"]) and \
                            difflib.SequenceMatcher(None, old, new).ratio() >= 0.6:
                        r["valid_to"] = eff; r["abolished_by"] = instrument + " (implicit)"; r["replaced_by"] = new
                        closed.append(old); n_cancel += 1
                        log.append(f"  {instrument}: implicit rename {old!r} -> {new!r} (same article {r['article_nos']})")
        log.append(f"{p.name[-12:]:<12} {instrument:<16} effective {eff}: +{n_add} charges, {n_cancel} names cancelled")
    rows = [table[n] for n in order]
    return rows, log


# --------------------------------------------------------------------------- aliases from corpus
SEP = "、，,和及与或"


VERBS = {"走私", "贩卖", "运输", "制造", "生产", "销售", "提供", "引诱", "容留", "介绍", "窝藏", "包庇", "持有", "私藏",
         "伪造", "变造", "买卖", "盗窃", "抢夺", "抢劫", "邮寄", "储存", "出售", "购买", "收购", "加工", "转让", "骗购",
         "编造", "传播", "投放", "组织", "领导", "参加", "强迫", "雇用", "盗用", "使用", "拐卖", "收买", "掩饰", "隐瞒",
         "隐匿", "销毁", "毁坏", "采伐", "盗伐", "滥伐", "猎捕", "杀害", "出租", "出借", "倒卖", "虚开", "骗取", "窃取",
         "刺探", "拆借", "发放", "挪用", "侵占", "侮辱", "诽谤", "煽动", "资助", "宣扬", "破坏", "损坏", "挟持", "劫持",
         "绑架", "偷越", "运送", "扰乱", "聚众", "聚众扰乱", "冲击", "妨害", "侵入", "搜查", "拘禁", "猥亵", "拒绝",
         "逃避", "抗拒", "虐待", "遗弃", "拐骗", "教唆", "欺骗", "协助", "经营", "占用", "捕捞", "狩猎", "处置", "设立",
         "发行", "进口", "吸收", "毁灭", "制作", "复制", "出版", "盗掘", "转移", "隐藏", "损毁", "帮助", "纵容", "私分",
         "泄露", "披露", "报道", "持有", "填平", "篡改", "伪报", "瞒报", "拒不", "强令", "冒充", "抢占", "哄抢",
         "印制", "买卖", "盗窃", "抢夺", "毁灭", "伪造", "变造", "转让", "走私", "贩卖", "运输", "制造", "引诱", "容留",
         "介绍", "强迫", "组织", "领导", "参加", "包庇", "纵容", "非法持有", "非法制造", "非法买卖", "非法生产",
         "非法收购", "非法出售", "非法运输", "非法采伐", "非法经营", "非法转让", "非法占用", "非法捕捞", "非法狩猎",
         "非法处置", "非法侵入", "非法搜查", "非法拘禁", "非法吸收", "非法批准", "非法猎捕", "非法杀害", "非法拆借",
         "非法发放", "非法提供", "非法获取", "非法控制", "非法使用", "非法生产", "非法持有", "擅自设立", "擅自发行",
         "擅自进口", "变相吸收", "过失损坏", "违规制造", "违规销售", "违法发放", "违法运用", "背信运用",
         "利用", "强制猥亵", "猥亵", "侮辱", "编造", "故意传播", "投放", "组织", "强迫", "引诱", "容留", "介绍",
         "获取", "控制", "非法获取", "非法控制"}

# Wording variants that are neither truncations nor selective sub-names. Hand-curated from the
# residue list; each maps a form used in practice to the official name.
MANUAL_ALIASES = {
    "容留他人吸食毒品罪": "容留他人吸毒罪",
    "非法制造注册商标标识罪": "非法制造、销售非法制造的注册商标标识罪",
    "销售非法制造的注册商标标识罪": "非法制造、销售非法制造的注册商标标识罪",
    "掩饰、隐瞒犯罪所得收益罪": "掩饰、隐瞒犯罪所得、犯罪所得收益罪",
    "帮助毁灭证据罪": "帮助毁灭、伪造证据罪",
    "帮助伪造证据罪": "帮助毁灭、伪造证据罪",
    "妨碍公务罪": "妨害公务罪",
    "虚开增值税专用发票、用于骗取出口退税、抵押税款发票罪": "虚开增值税专用发票、用于骗取出口退税、抵扣税款发票罪",
}


def bracket_variants(name: str):
    """偷越国（边）境罪 -> 偷越国境罪, 偷越边境罪, 偷越国边境罪, 偷越国(边)境罪."""
    if "（" not in name:
        return set()
    out = set()
    for a, b in (("（边）", ""), ("国（边）境", "边境"), ("（边）", "边"), ("（", "("), ):
        v = name.replace(a, b) if a != "（" else name.replace("（", "(").replace("）", ")")
        if v != name:
            out.add(v)
    return out
MODIFIERS = ("非法", "违法", "擅自", "过失", "违规", "变相", "背信")
_VERBS_BY_LEN = sorted(VERBS, key=len, reverse=True)


def parse_selective(name: str):
    """'非法制造、买卖、运输、邮寄、储存枪支、弹药、爆炸物罪' -> ('非法', [制造,买卖,运输,邮寄,储存], [枪支,弹药,爆炸物]).
    Returns None when the name is not a 、-coordinated selective charge."""
    if "、" not in name or not name.endswith("罪"):
        return None
    items = name[:-1].split("、")
    mod = ""
    for m in MODIFIERS:
        if items[0].startswith(m) and len(items[0]) > len(m):
            mod, items[0] = m, items[0][len(m):]
            break
    verbs, objects = [], []
    for it in items:
        if not objects:
            if it in VERBS:
                verbs.append(it); continue
            glue = next((v for v in _VERBS_BY_LEN if it.startswith(v) and len(it) > len(v)), None)
            if glue:
                verbs.append(glue); objects.append(it[len(glue):]); continue
        objects.append(it)
    if not verbs:
        return None
    return mod, verbs, objects


def expand_selective(name: str):
    """Sub-names actually used in charging practice for a selective charge:
       any ordered subset of verbs × (one object, with the shared head of the first object or the
       shared tail of the last object optionally distributed to it | several objects joined by 、),
       with and without the leading modifier. Returns a set not containing `name`."""
    parsed = parse_selective(name)
    if not parsed:
        return set()
    mod, verbs, objects = parsed
    from itertools import combinations
    from itertools import permutations
    vsubs = [c for k in range(1, len(verbs) + 1) for c in combinations(verbs, k)]
    if len(vsubs) > 128:
        vsubs = [c for k in (1, 2, len(verbs)) for c in combinations(verbs, k)]
    vsubs += [pm for c in vsubs if 2 <= len(c) <= 3 for pm in permutations(c) if pm != c]   # 介绍、容留卖淫罪
    obj_forms = []  # list of lists of object strings
    if objects:
        first, last = objects[0], objects[-1]
        for i, o in enumerate(objects):
            forms = {o}
            if i > 0:
                forms |= {first[:k] + o for k in range(1, len(first))}          # shared head: 国家机关 + 证件
            if i < len(objects) - 1:
                forms |= {o + last[-k:] for k in range(1, len(last))}           # shared tail: 公司 + 印章
            obj_forms.append(sorted(forms))
        osubs = [[o] for forms in obj_forms for o in forms]                     # single object, with distributed forms
        for k in range(2, len(objects) + 1):                                    # multi-object: plain, head on first, tail on last
            for c in combinations(range(len(objects)), k):
                chosen = [objects[i] for i in c]
                osubs.append(chosen)
                if c[0] > 0:
                    osubs += [[first[:j] + chosen[0]] + chosen[1:] for j in range(1, len(first))]
                if c[-1] < len(objects) - 1:
                    osubs += [chosen[:-1] + [chosen[-1] + last[-j:]] for j in range(1, len(last))]
    else:
        osubs = [[]]
    out = set()
    for it in verbs + objects:                                                  # each item alone: 非法控制计算机信息系统罪
        if len(it) >= 4:
            out.add(it + "罪"); out.add(mod + it + "罪")
    for vs in vsubs:
        for os_ in osubs:
            body = "、".join(list(vs[:-1]) + [vs[-1] + os_[0]] + os_[1:]) if os_ else "、".join(vs)
            for m in {mod, ""}:
                s = m + body + "罪"
                if s != name:
                    out.add(s)
    return out


def normalise_raw(s: str) -> str:
    s = re.sub(r"^(犯有|犯了|犯|了|构成|系|涉嫌)", "", s)
    s = re.sub(r"罪(罪|共同犯罪|的共同犯罪|既遂|未遂|中止|预备)$", "罪", s)
    return s.replace("帐", "账")


def build_aliases(rows):
    import duckdb

    std_names = {r["charge_std"] for r in rows}
    D = {}      # 、-insensitive form -> canonical standard name
    for n in std_names:
        D.setdefault(n.replace("、", ""), n)
    n_sel = 0
    for n in sorted(std_names):
        for a in expand_selective(n) | bracket_variants(n):
            k = a.replace("、", "")
            if k not in D:
                D[k] = n; n_sel += 1
    for a, n in MANUAL_ALIASES.items():
        if n in std_names:
            D.setdefault(a.replace("、", ""), n)
    maxlen = max(len(k) for k in D)
    con = duckdb.connect()
    con.execute(f"CREATE VIEW raw AS SELECT * FROM read_parquet('{PARQUET}', hive_partitioning=true)")
    q = r"""
      SELECT stage, s, count(*) n FROM (
        SELECT '应以' AS stage, unnest(regexp_extract_all(content, '应当?以([^，。；：\s（）()]{1,80}?罪)追究', 1)) AS s FROM raw
        UNION ALL
        SELECT '涉嫌', unnest(regexp_extract_all(content, '涉嫌([^，。；：\s（）()]{1,60}?罪)', 1)) FROM raw
      ) GROUP BY 1, 2"""
    strings = con.execute(q).fetchall()

    def segment(t: str):
        """Longest-match over D on a 、-stripped string. Returns (names, residue_or_None)."""
        i, found = 0, []
        while i < len(t):
            if t[i] in "，,和及与或":
                i += 1; continue
            hit = None
            for L in range(min(maxlen, len(t) - i), 1, -1):
                if t[i:i + L] in D:
                    hit = t[i:i + L]; break
            if hit is None:
                rest = t[i:]
                if rest.endswith("罪") and len(rest) >= 3:        # truncated tail unique prefix: 掩饰隐瞒犯罪 -> …犯罪所得…
                    cands = {D[k] for k in D if k.startswith(rest[:-1])}
                    if len(cands) == 1:
                        return found + [cands.pop()], None
                return found, rest
            found.append(D[hit]); i += len(hit)
        return found, None

    alias_rows, unmatched = [], Counter()
    for stage, s, n in strings:
        best = None
        variants = [(s, "exact"), (normalise_raw(s), "normalised")]
        if re.search(r"犯罪$", normalise_raw(s)):                       # 盗窃犯罪 -> 盗窃罪, tried last so that
            variants.append((re.sub(r"犯罪$", "罪", normalise_raw(s)), "normalised"))  # 掩饰、隐瞒犯罪 keeps its truncation match
        if "、" in s and s.endswith("罪"):
            pieces = [pc if pc.endswith("罪") else pc + "罪" for pc in s[:-1].split("、")]
            if all(pc.replace("、", "") in D for pc in pieces) and len(pieces) > 1:
                variants.insert(1, ("，".join(pieces), "segmented"))
        for variant, method in variants:
            found, residue = segment(variant.replace("、", ""))
            if found and residue is None:
                best = (found, None, method); break
            if found and (best is None or (best[1] is not None and len(residue) < len(best[1]))):
                best = (found, residue, method)
        if best is None:
            unmatched[s] += n; continue
        found, residue, method = best
        if len(found) == 1 and found[0] == s:
            method = "exact"
        elif residue is None and method == "exact":
            method = "segmented"
        if residue is None:
            alias_rows.append((s, "|".join(found), method, stage, n))
        else:
            alias_rows.append((s, "|".join(found), "partial", stage, n)); unmatched[residue] += n
    print(f"alias dictionary: {len(std_names)} standard names + {n_sel:,} selective sub-names")
    return alias_rows, unmatched


# --------------------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-corpus", action="store_true")
    args = ap.parse_args()

    rows, log = assemble()
    cols = ["charge_std", "article_nos", "article_key", "article_ref", "chapter_no", "chapter", "section", "valid_from", "valid_to",
            "added_by", "added_source", "abolished_by", "replaced_by", "is_duty_crime"]
    with open(OUT_STD, "w", newline="", encoding="utf-8-sig") as f:  # BOM so Excel opens it as UTF-8
        w = csv.DictWriter(f, fieldnames=cols); w.writeheader(); w.writerows(rows)
    print("\n".join(log))
    active = sum(1 for r in rows if not r["valid_to"])
    print(f"\nwrote {OUT_STD.relative_to(ROOT)}: {len(rows)} charge names ever in force, {active} in force after the last instrument, "
          f"{sum(r['is_duty_crime'] for r in rows)} duty-crime names")

    if args.no_corpus:
        return
    alias_rows, unmatched = build_aliases(rows)
    with open(OUT_ALIAS, "w", newline="", encoding="utf-8-sig") as f:  # BOM so Excel opens it as UTF-8
        w = csv.writer(f); w.writerow(["raw", "charge_std", "method", "stage", "n_docs"]); w.writerows(sorted(alias_rows, key=lambda r: -r[4]))
    with open(OUT_UNMATCHED, "w", newline="", encoding="utf-8-sig") as f:  # BOM so Excel opens it as UTF-8
        w = csv.writer(f); w.writerow(["residue", "n_docs"]); w.writerows(unmatched.most_common())
    tot = sum(r[4] for r in alias_rows) + sum(unmatched.values())
    cov = sum(r[4] for r in alias_rows if r[2] != "partial")
    print(f"wrote {OUT_ALIAS.relative_to(ROOT)}: {len(alias_rows):,} raw strings mapped; "
          f"{100*cov/tot:.2f}% of charge mentions fully resolved; {len(unmatched):,} residues in {OUT_UNMATCHED.name} "
          f"({100*sum(unmatched.values())/tot:.2f}% of mentions)")
    from datetime import date
    meth = Counter()
    for r in alias_rows:
        meth[r[2]] += r[4]
    R = [f"# Standard charge table — build and validation report\n", f"Generated {date.today().isoformat()} by scripts/ref_charges.py.\n",
         "## 1. Instruments applied (in order)\n", "```", *log, "```", "",
         f"Result: **{len(rows)}** charge names ever in force; **{active}** in force after 法释〔2021〕2号 "
         f"(the published figure for the current Criminal Law is 483); {sum(r['is_duty_crime'] for r in rows)} duty-crime names (chapters 8–9).\n",
         "Transcription errata corrected (Wikisource text vs official wording, verified against corpus usage):\n",
         *[f"- `{a}` → `{b}`" for a, b in ERRATA.items()], "",
         "Cancellations the text states under a name that never existed in the 最高法 1997 list (they were names in the "
         "最高检 1997 list, unified by 法释〔2002〕7号) are logged above as 'not found' and need no action.\n",
         "## 2. Alias map built from the corpus\n",
         "Every string at the 应以…罪追究 and 涉嫌…罪 positions in the 2.4M documents was segmented into official names.\n",
         "| method | share of charge mentions |", "|---|---|",
         *[f"| {k} | {100*v/tot:.2f}% |" for k, v in meth.most_common()],
         f"| unresolved residue | {100*sum(unmatched.values())/tot:.2f}% |", "",
         "exact = the official name as written; segmented = several names in one clause or a selective sub-name "
         "(贩卖毒品罪 → 走私、贩卖、运输、制造毒品罪); normalised = wrapper removed (犯有X罪, X犯罪, X罪罪); "
         "partial = a clause only part of which could be resolved.\n",
         "## 3. Top unresolved residues (manual queue)\n", "| residue | documents |", "|---|---|",
         *[f"| {k} | {v:,} |" for k, v in unmatched.most_common(25)], ""]
    (ROOT / "ref" / "charges_validation.md").write_text("\n".join(R), encoding="utf-8")
    print("wrote ref/charges_validation.md")


if __name__ == "__main__":
    main()
