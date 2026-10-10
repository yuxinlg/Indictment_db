#!/usr/bin/env python3
"""
M1 — Tier 0 rule library for 检察文书 (起诉书 / 不起诉决定书 and friends).

Pure functions over one document's text. No I/O, no DuckDB. `m1_extract.py` drives this over the
corpus; `test_m1_rules.py` pins the behaviour on real documents.

Document anatomy (segments are separated by TAB in the source):
    院名 · 文书类型 · 文号 · 被告人块 ×N · 本案由(移送) · 经审查查明(事实) · 证据 · 本院认为 · 此致+法院 ·
    署名 · 落款日期 · 附
Every extractor works on a region located by these anchors, never on the whole text, so that e.g.
"检察长批准延长羁押期限" in the procedure paragraph can never become a prosecutor signature.

Conventions
  * Offsets returned anywhere are character offsets into the *normalised* text, which is the same
    length as the original (only 1:1 character translations are applied).
  * Nothing is guessed: a field that cannot be read is None, and the reason is recorded in
    `extract_flags` (bitmask, see FLAG_*).
"""
import re
from datetime import date, timedelta

# --------------------------------------------------------------------------- normalisation
# Full-width digits / letters / stars -> ASCII; NBSP and ideographic space -> space. All 1:1, so
# offsets into the normalised text are offsets into the original.
_TRANS = {ord(c): ord("0") + i for i, c in enumerate("０１２３４５６７８９")}
_TRANS.update({0xA0: 0x20, 0x3000: 0x20, ord("＊"): ord("*"), ord("Ｘ"): ord("X"), ord("ｘ"): ord("x"),
               ord("："): ord("："), ord("\r"): ord(" ")})


def normalise(text: str) -> str:
    return text.translate(_TRANS)


_CN = {c: i for i, c in enumerate("零一二三四五六七八九")}
_CN.update({"〇": 0, "○": 0, "两": 2})


def cn2int(s: str):
    """一百零三 -> 103, 二十 -> 20, 十二 -> 12, 三百四十七 -> 347, 三百0六 -> 306; Arabic passes through;
    None if unparseable."""
    if not s:
        return None
    if s.isdigit():
        return int(s)
    n, num = 0, 0
    for ch in s:
        if ch == "百":
            n += (num or 1) * 100; num = 0
        elif ch == "十":
            n += (num or 1) * 10; num = 0
        elif ch in _CN:
            num = _CN[ch]
        elif ch.isdigit():
            num = int(ch)
        else:
            return None
    return n + num


def cn_year(s: str):
    """二〇一五 -> 2015 (digit-by-digit)."""
    if s.isdigit():
        return int(s)
    try:
        return int("".join(str(_CN[c]) for c in s))
    except KeyError:
        return None


# --------------------------------------------------------------------------- flags
FLAG_NO_PROC = 1            # no 检察院 name in header
FLAG_NO_DOCTYPE = 2         # neither heading nor title nor body gave a type
FLAG_NO_CASE_NO = 4
FLAG_NO_DEFENDANT = 8       # no defendant block found
FLAG_NO_PROCEDURE = 16      # no 本案由 / 移送审查起诉 anchor
FLAG_NO_FACTS = 32
FLAG_NO_OPINION = 64        # no 本院认为
FLAG_NO_CLOSING = 128       # no 此致 (normal for 不起诉决定书)
FLAG_NO_PROSECUTOR = 256
FLAG_CHARGE_UNRESOLVED = 512   # at least one charge string not mapped to the standard table
FLAG_NAME_UNRESOLVED = 1024    # a name in the opinion/referral did not match any defendant block
FLAG_MULTI_CASE_NO = 2048      # >1 distinct 文号 in the header
FLAG_WEAK_BLOCK = 4096         # a block start was rejected for lacking person cues
FLAG_NO_SIGN_DATE_LINE = 8192  # (reserved)

FLAG_NAMES = {v: k[5:] for k, v in globals().items() if k.startswith("FLAG_")}

# --------------------------------------------------------------------------- segments
RE_WS = re.compile(r"\s+")


def split_segments(text: str):
    """[(start, end, segment_text)] split on TAB (newline fallback when a document has no tabs)."""
    sep = "\t" if text.count("\t") >= 3 else "\n"
    out, pos = [], 0
    for part in text.split(sep):
        out.append((pos, pos + len(part), part))
        pos += len(part) + 1
    return out


# --------------------------------------------------------------------------- header
RE_PROC_NAME = re.compile(
    r"([一-龥·]{2,40}?(?:人民检察院|检察院)(?:[一-龥]{0,14}?分院)?)")
RE_PROC_NAME_ALL = re.compile(r"[一-龥]{2,40}?(?:人民检察院|检察院)(?:[一-龥]{0,14}?分院)?")
HEADING_TYPES = [  # (regex on whitespace-stripped segment, doc_type)
    (re.compile(r"附条件不起诉决定书"), "附条件不起诉决定书"),
    (re.compile(r"不起诉决定书|不起诉书|不起诉决定"), "不起诉决定书"),
    (re.compile(r"刑事抗诉书|抗诉书"), "刑事抗诉书"),
    (re.compile(r"刑事申诉复查决定书|申诉复查决定书|复查决定书"), "刑事申诉复查决定书"),
    (re.compile(r"起诉书"), "起诉书"),
]
RE_CASE_NO = re.compile(
    r"([一-龥]{1,24}?)\s*([〔\[【（(［﹝])\s*(\d{4})\s*[〕\]】）)］﹞]\s*第?\s*(\d{1,6})\s*号")
# A header 文号 must contain 检 or end in a known series; the body also carries 文号 of other
# documents (判决书, 鉴定书) that we do not want.
RE_CASE_PREFIX_OK = re.compile(r"检|诉|刑|不诉|抗")


def _is_person_label(label: str) -> bool:
    return "单位" not in label


def parse_header(text: str, segs, title: str):
    """Return dict(proc_name, doc_type, doc_type_source, doc_heading_raw, case_no=dict|None,
    n_case_no, header_end). Header = segments before the first defendant block / 本案由 / 查明 anchor,
    capped at 6 segments and 400 chars."""
    first_block = RE_DEF_START.search(text)
    anchor = RE_PROC_ANCHOR.search(text)
    header_end = min([m.start() for m in (first_block, anchor) if m] + [len(text), 600])
    if header_end < 20:  # a stray label in the first characters; keep a usable header window
        header_end = min(len(text), 600)
    head = text[:header_end]
    head_ns = RE_WS.sub("", head)

    # procuratorate name: first segment ending in 检察院 / 分院 inside the header
    proc_name = None
    for s, e, seg in segs:
        if s >= header_end:
            break
        seg_ns = RE_WS.sub("", seg)
        m = RE_PROC_NAME.search(seg_ns)
        if m and seg_ns.endswith(m.group(1)):
            proc_name = m.group(1); break
    if proc_name is None:
        m = RE_PROC_NAME_ALL.search(head_ns)
        if m:
            proc_name = m.group(0)

    # document type from the heading line(s) in the header, then title, then body cues
    doc_type = doc_type_source = heading_raw = None
    for s, e, seg in segs:
        if s >= header_end:
            break
        seg_ns = RE_WS.sub("", seg)
        if not seg_ns or seg_ns.endswith(("检察院", "分院")):
            continue
        for rx, t in HEADING_TYPES:
            if rx.search(seg_ns):
                doc_type, doc_type_source, heading_raw = t, "heading", seg_ns[:40]; break
        if doc_type:
            break
    if doc_type is None:
        t_ns = RE_WS.sub("", title or "")
        for rx, t in HEADING_TYPES:
            if rx.search(t_ns):
                doc_type, doc_type_source = t, "title"; break
    if doc_type is None:
        if "提起公诉" in text:
            doc_type, doc_type_source = "起诉书", "body"
        elif "不起诉" in text and "决定" in text:
            doc_type, doc_type_source = "不起诉决定书", "body"
        elif "提出抗诉" in text or "抗诉" in text[:800]:
            doc_type, doc_type_source = "刑事抗诉书", "body"

    # 文号
    case_no, seen = None, []
    for m in RE_CASE_NO.finditer(head):
        prefix = RE_WS.sub("", m.group(1))
        if not RE_CASE_PREFIX_OK.search(prefix):
            continue
        full = f"{prefix}{m.group(2)}{m.group(3)}{_close(m.group(2))}{m.group(4)}号"
        if full in seen:
            continue
        seen.append(full)
        if case_no is None:
            if "检" in prefix:
                proc_abbr, series = prefix.split("检", 1)
            else:
                proc_abbr, series = prefix, None
            case_no = dict(case_no_full=full, case_no_proc=proc_abbr or None, case_no_series=series,
                           case_no_year=int(m.group(3)), case_no_seq=int(m.group(4)), case_no_bracket=m.group(2))
    return dict(proc_name=proc_name, doc_type=doc_type, doc_type_source=doc_type_source, doc_heading_raw=heading_raw,
                case_no=case_no, n_case_no=len(seen), header_end=header_end)


def _close(b):
    return {"〔": "〕", "[": "]", "【": "】", "（": "）", "(": ")", "［": "］", "﹝": "﹞"}[b]


# --------------------------------------------------------------------------- anchors
RE_PROC_ANCHOR = re.compile(r"本案由|侦查终结|移送(?:本院)?(?:审查)?起诉|经(?:本院)?依法审查查明|经审查查明|审查查明[：:]")
RE_FACTS_ANCHOR = re.compile(
    r"经(?:本院)?(?:依法)?审查[，,]?(?:查明|表明|认定|认为|证实)(?:的事实)?|审查认定的事实|审查查明[：:]?|本院审查查明|查明[：:]|经审理查明")
RE_EVID_ANCHOR = re.compile(r"认定上述事实的证据|上述(?:犯罪)?事实[，,]?有?(?:下列|以下)?证据|证据如下|证明上述事实的证据|认定上述犯罪事实的证据|上述事实有下列证据|证据(?:有|如下)[：:]")
RE_OPINION_ANCHOR = re.compile(r"本院认为")
RE_CLOSING_ANCHOR = re.compile(r"此\s*致")
RE_ATTACH_ANCHOR = re.compile(r"(?:^|[\t\n])\s*附[：:]?\s*(?:[\t\n]|\d|1|一|被告|本决定|案卷|随)")


def locate_sections(text: str, segs, header_end: int, blocks):
    """Return (sections list of (sect_type, start, end), anchors dict). Anchors are searched in order
    and each must lie after the previous one; a missing anchor leaves its section absent."""
    n = len(text)
    a = {}
    a["defendants"] = blocks[0]["start"] if blocks else None
    pos = (blocks[-1]["end"] if blocks else header_end)
    m = RE_PROC_ANCHOR.search(text, max(pos - 2, 0))
    a["procedure"] = _seg_start(segs, m.start()) if m and not m.group(0).startswith(("经", "审查")) else None
    m2 = RE_FACTS_ANCHOR.search(text, a["procedure"] + 3 if a["procedure"] is not None else pos)
    a["facts"] = _seg_start(segs, m2.start()) if m2 else None
    m3 = RE_EVID_ANCHOR.search(text, a["facts"] + 3 if a["facts"] is not None else pos)
    a["evidence"] = _seg_start(segs, m3.start()) if m3 else None
    start_op = max(x for x in (a["evidence"], a["facts"], a["procedure"], pos) if x is not None)
    m4 = RE_OPINION_ANCHOR.search(text, start_op)
    if m4 is None:  # tolerate opinion before/without evidence anchor
        m4 = RE_OPINION_ANCHOR.search(text, pos)
    a["opinion"] = _seg_start(segs, m4.start()) if m4 else None
    m5 = RE_CLOSING_ANCHOR.search(text, a["opinion"] + 4 if a["opinion"] is not None else pos)
    # the last 此致 in the document is the real closing (the opinion may quote one)
    last = None
    for mm in RE_CLOSING_ANCHOR.finditer(text, a["opinion"] + 4 if a["opinion"] is not None else pos):
        last = mm
    m5 = last or m5
    a["closing"] = _seg_start(segs, m5.start()) if m5 else None
    m6 = RE_ATTACH_ANCHOR.search(text, a["closing"] if a["closing"] is not None else (a["opinion"] or pos))
    a["attachments"] = m6.start() if m6 else None

    order = ["defendants", "procedure", "facts", "evidence", "opinion", "closing", "attachments"]
    pts = [(k, a[k]) for k in order if a[k] is not None]
    sections = [("header", 0, pts[0][1] if pts else min(header_end, n))]
    for i, (k, s) in enumerate(pts):
        e = pts[i + 1][1] if i + 1 < len(pts) else n
        if e > s:
            sections.append((k, s, e))
    return sections, a


def _seg_start(segs, pos):
    """Snap an anchor to the start of its segment when the anchor is at the head of the segment."""
    for s, e, seg in segs:
        if s <= pos < e:
            lead = seg[: pos - s]
            return s if not lead.strip(" 　\xa0：:，,。") else pos
    return pos


# --------------------------------------------------------------------------- defendant blocks
DEF_LABELS = "被告人|被不起诉人|犯罪嫌疑人|被告单位|被不起诉单位|犯罪嫌疑单位|被附条件不起诉人|被告"
# A block starts at a segment / sentence boundary, or after a run of spaces in tab-less headers
# ("…〔2015〕74号   被告人郭某某"). Leading junk: numbering "1." / "一、", underscores.
RE_DEF_START = re.compile(   # possessive quantifiers: long runs of NBSP in 2015 headers must not backtrack
    r"(?:^|(?<=[\t\n。；;\s]))[\d一二三四五六七八九十_＿\-—]*+[、.．]?\s*+(" + DEF_LABELS + r")[：:]?\s*+"
    r"([^，,。；：:\s（(、]{1,25})")
# identity line without any label ("林某某，男，1977年**月**日出生") — only used when no labelled block exists
RE_DEF_UNLABELLED = re.compile(r"(?:^|(?<=[\t\n]))[\s_＿]*+()([一-龥·某\*×X]{2,15})[，,]\s*(?=男|女)")
RE_PERSON_CUE = re.compile(r"男|女|出生|岁|族|身份证|身份号码|文化|文盲|户籍|住")
RE_ALIAS = re.compile(r"[（(]\s*(?:绰号|外号|曾用名|又名|别名|化名|小名|乳名|艺名|网名|原名)[：:]?\s*([^）)]{1,30})[）)]")
RE_MASK = re.compile(r"[某\*×X□○]|^.{1}$")
RE_BIRTH = [  # a bare "2016年4月1日" is NOT a birth date (it is a conviction / detention date)
    re.compile(r"(\d{4})\s*年\s*[\d\*xX×]{1,2}\s*月\s*[\d\*xX×]{1,2}\s*日?\s*(?:出生|生)"),
    re.compile(r"(?:出生于|生于)\s*(\d{4})\s*年"),
    re.compile(r"(\d{4})\s*年\s*(?:出生|生)(?!效)"),                                 # 1972年出生 / 1974年出生于广西
    re.compile(r"(\d{4})\s*年\s*[\*xX×]{1,2}\s*月\s*[\d\*xX×]{1,2}\s*日"),   # masked month = birth line
    re.compile(r"(\d{4})\s*年\s*[\*xX×]{1,2}\s*月"),
    re.compile(r"([一二三四五六七八九〇○零]{4})年[一二三四五六七八九十〇○零\*]{1,3}月"),
]
RE_BIRTH_FIELD = re.compile(r"出生|生于|^\d{4}年|^[\*xX×]{2,4}年|年[\d\*xX×]{1,2}月[\d\*xX×]{1,2}日生?$")
RE_BARE_DATE_FIELD = re.compile(r"^(\d{4})\s*年\s*\d{1,2}\s*月\s*\d{1,2}\s*日$")   # "1985年5月6日" as its own identity field
RE_AGE_FIELD = re.compile(r"^(?:现年|年龄)?[：:]?\s*(\d{1,2})\s*(?:周)?岁$")
RE_SKIP_FIELD = re.compile(r"^(?:出生地|籍贯地|出生于)|前科|劣迹|受过|政治面貌")
RE_ID = re.compile(r"(?:身份证|公民身份|身份号码|身份证件|证件号)[一-龥]{0,6}?[：:]?\s*([0-9X\*x×]{6,20})")
RE_ETHNIC = re.compile(r"^([一-龥]{1,10}族)$")
RE_EDU_WORD = re.compile(r"博士|硕士|研究生|本科|大学|大专|专科|学院|中专|职高|技校|中技|职业高中|职业中专|高中|高职|初中|小学|文盲|半文盲|无文化|不识字|文化程度|学历|肄业|毕业|在读|文化")
RE_POLITICS = re.compile(r"党员|团员|群众|民主党派|无党派|党派|民建|民盟|民进|农工党|致公党|九三学社|台盟")
RE_HUKOU = re.compile(r"^(?:户籍|户口|籍贯|原籍|户籍所在地|户籍地|户藉)")
RE_RESIDE = re.compile(r"^(?:现?住|暂住|居住|现居|经常居住|住址|现址|租住|家住|住所|居所|现在)|住址|住所|住处|居住|无固定住|暂住|租住")
RE_HUKOU_ANY = re.compile(r"((?:户籍所在地|户籍地|户籍|户口所在地|户口址|户口|籍贯|原籍)[：:]?[^，,。；；（）()]{2,40})")
RE_GENDER = re.compile(r"^(男|女)(?:性)?$")
RE_DATE_FULL = re.compile(r"(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日")
RE_DATE_YM = re.compile(r"(\d{4})\s*年\s*(\d{1,2})\s*月")

EDU_LEVEL = [  # first match wins
    ("博士", "doctor"), ("硕士", "master"), ("研究生", "master"), ("本科", "university"), ("大学", "university"),
    ("学院", "university"), ("大专", "college"), ("专科", "college"), ("高职", "college"), ("中专", "vocational"),
    ("职高", "vocational"), ("技校", "vocational"), ("中技", "vocational"), ("职业高中", "vocational"), ("职业中专", "vocational"),
    ("高中", "senior"), ("初中", "junior"), ("小学", "primary"), ("半文盲", "illiterate"), ("文盲", "illiterate"),
    ("无文化", "illiterate"), ("不识字", "illiterate"),
]


def find_blocks(text: str, header_end: int, flags: int):
    """Defendant blocks in the region between the header and the procedure anchor.
    Returns (blocks, flags). Each block: dict(label, name_raw, start, end, text, name_end, n_parts).

    A later paragraph that starts with the same name ("被告人谢某某因涉嫌开设赌场罪，于…取保候审。",
    the Jiangsu layout) is a continuation of that defendant, not a new one: its text is appended to
    the first block. A start whose next 80 characters carry no person cue (男/女/出生/族/身份证…)
    and whose name is new is rejected as a false start."""
    m_anchor = RE_PROC_ANCHOR.search(text, header_end)
    region_end = m_anchor.start() if m_anchor else len(text)
    starts = []   # (start, name_end, label, name, weak)
    for m in RE_DEF_START.finditer(text, 0, region_end + 1):
        label, name = m.group(1), m.group(2)
        if m.start(1) >= region_end:
            break
        if label == "被告" and name.startswith(("人", "单位")):
            continue
        weak = False
        if _is_person_label(label):
            window = text[m.end(): m.end() + 80]
            weak = not RE_PERSON_CUE.search(window)
        starts.append((m.start(1), m.end(), label, name, weak))
    if not starts:
        for m in RE_DEF_UNLABELLED.finditer(text, 0, region_end + 1):
            if m.start(2) >= region_end:
                break
            starts.append((m.start(2), m.end(2), "(无标签)", m.group(2), False))
    blocks, by_name = [], {}
    for i, (s, e_name, label, name, weak) in enumerate(starts):
        end = starts[i + 1][0] if i + 1 < len(starts) else region_end
        # continuation paragraph of an earlier defendant: "被告人谢某某因涉嫌…" captures the name plus
        # the following words, so match on prefix
        prev = name if name in by_name else next((nm for nm in by_name if name.startswith(nm)), None)
        if prev is None and weak and len(blocks) == 1 and re.match(r"^(?:因|曾|于|现|均|系|在|自|已|经|被)", name):
            prev = blocks[0]["name_raw"]   # "被告人因涉嫌盗窃罪，于…" with the name omitted: the sole defendant
        if prev is not None:
            b = blocks[by_name[prev]]
            b["text"] += "\t" + text[s:end]
            b["n_parts"] += 1
            b["spans"].append((s, end))
            continue
        if weak:
            flags |= FLAG_WEAK_BLOCK
            continue
        by_name[name] = len(blocks)
        blocks.append(dict(label=label, name_raw=name, start=s, end=end, text=text[s:end], name_end=e_name,
                           n_parts=1, spans=[(s, end)]))
    if not blocks:
        flags |= FLAG_NO_DEFENDANT
    return blocks, flags


def parse_person_block(b: dict):
    """Fields of one 被告人 block. Returns dict."""
    t = b["text"]
    name = b["name_raw"]
    alias = None
    m = RE_ALIAS.match(t[b["name_end"] - b["start"]:].lstrip())
    if m:
        alias = m.group(1).strip()
    out = dict(name=name, alias=alias, is_name_masked=bool(RE_MASK.search(name)))

    # first sentence = identity line
    first = re.split(r"。", t, maxsplit=1)[0]
    # strip the label+name prefix
    body = first[b["name_end"] - b["start"]:]
    body = RE_ALIAS.sub("", body, count=1)
    fields = [f.strip(" 　:：") for f in re.split(r"[，,；;]", body)]
    fields = [f for f in fields if f]
    gender = birth_year = ethnicity = edu_raw = politics = hukou_text = None
    age_stated = None
    occupation, others = [], []
    seen_edu = False
    for f in fields:
        g = RE_GENDER.match(f)
        if g and gender is None:
            gender = g.group(1); continue
        if RE_BIRTH_FIELD.search(f):
            if birth_year is None:
                birth_year = _birth_year(f)
            continue
        mb = RE_BARE_DATE_FIELD.match(f)
        if mb and birth_year is None and ethnicity is None and 1900 <= int(mb.group(1)) <= 2015:
            birth_year = int(mb.group(1)); continue
        ma = RE_AGE_FIELD.match(f)
        if ma:
            age_stated = int(ma.group(1)); continue
        if "身份" in f or "证号" in f or re.fullmatch(r"[0-9X\*x×]{6,20}", f) or RE_SKIP_FIELD.search(f):
            continue
        e = RE_ETHNIC.match(f)
        if e and ethnicity is None:
            ethnicity = e.group(1); continue
        if RE_EDU_WORD.search(f) and not seen_edu and len(f) <= 14:
            edu_raw = f; seen_edu = True; continue
        if RE_POLITICS.search(f) and len(f) <= 12 and politics is None:
            politics = f; continue
        if RE_HUKOU.match(f) or (f.endswith("人") and re.search(r"省|市|县|区|自治|盟|旗|州", f) and len(f) <= 30):
            if hukou_text is None:
                hukou_text = f
            continue
        if RE_RESIDE.search(f):
            continue
        if re.search(r"\d{4}年|拘留|逮捕|取保|羁押|涉嫌|因", f):
            break  # procedural history starts; identity fields are over
        if len(f) <= 40:
            occupation.append(f)
    if birth_year is None:
        birth_year = _birth_year(t[:300])
    if hukou_text is None:
        mh = RE_HUKOU_ANY.search(t[:400])   # "（户口址：…）" after the residence, in the next sentence
        if mh:
            hukou_text = mh.group(1)
    out.update(gender=gender, birth_year=birth_year, ethnicity=ethnicity, edu_raw=edu_raw,
               edu_level=_edu_level(edu_raw), politics=politics, hukou_text=hukou_text,
               occupation_raw="；".join(occupation) or None, age_stated=age_stated)

    # ID number
    m = RE_ID.search(t)
    id_raw = m.group(1) if m else None
    id_prefix = id_birth_year = None
    id_shape = None
    if id_raw:
        id_shape = re.sub(r"\d", "9", id_raw)
        if re.match(r"\d{6}", id_raw):
            id_prefix = id_raw[:6]
        if re.match(r"\d{10}", id_raw) and 1900 <= int(id_raw[6:10]) <= 2015:
            id_birth_year = int(id_raw[6:10])
    out.update(id_raw=id_raw, id_prefix=id_prefix, id_birth_year=id_birth_year, id_shape=id_shape,
               is_id_unmasked=bool(id_raw and re.fullmatch(r"\d{17}[\dX]", id_raw)))
    if out["birth_year"] is None and id_birth_year:
        out["birth_year"] = id_birth_year
        out["birth_year_source"] = "id"
    else:
        out["birth_year_source"] = "text" if out["birth_year"] else None

    # 涉嫌 charges inside the block (per-defendant attribution for free)
    out["suspect_charges"] = [(mm.group(1), b["start"] + mm.start(1)) for mm in RE_SUSPECT.finditer(t)]
    # coercive measures
    out.update(parse_measures(t))
    return out


def _birth_year(s: str):
    for rx in RE_BIRTH:
        m = rx.search(s)
        if m:
            y = cn_year(m.group(1))
            if y and 1900 <= y <= 2015:
                return y
    return None


def _edu_level(edu_raw):
    if not edu_raw:
        return None
    for k, v in EDU_LEVEL:
        if k in edu_raw:
            return v
    return "other"


# --------------------------------------------------------------------------- coercive measures
# Each keyword is paired with the nearest date to its left inside the same sentence; relative
# dates (同年/同月/同日/次日) are resolved against the last absolute date seen in the block.
RE_MEASURE_TOKEN = re.compile(
    r"(?P<full>(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日)"
    r"|(?P<sy>同年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日)"
    r"|(?P<sm>同月\s*(\d{1,2})\s*日)"
    r"|(?P<same>同日|当日|当天)"
    r"|(?P<next>次日|翌日|第二天)"
    r"|(?P<kw>刑事拘留|刑拘|(?<![行司治])(?<!行政)(?<!司法)(?<!治安)拘留|批准逮捕|决定逮捕|执行逮捕|逮捕|取保候审|监视居住|拘传)"
    r"|(?P<approve>批准|决定)(?=[，,]?\s*(?:同日|当日|次日|于)?[^。，,\d]{0,30}?(?:执行)?逮捕)"
    r"|(?P<eos>[。])")


RE_PRIOR_SENT = re.compile(r"判处|劳动教养|劳教|刑满|释放|前科|曾因|曾于|受过|另案|缓刑|假释|减刑")


def parse_measures(t: str):
    out = dict(date_detain=None, date_arrest_approved=None, date_arrest=None, date_bail=None, date_surveil=None,
               n_bail=0, n_detain=0, detain_kind=None)
    last_abs = None          # last absolute date in the block (for 同年/同月/次日)
    sent_date = None         # most recent date in the current sentence (absolute or resolved)
    # sentences about prior convictions / earlier cases carry their own 拘留/逮捕 dates: skip them
    skip_until = -1
    sent_starts = [0] + [m.end() for m in re.finditer(r"。", t)]
    prior = []
    for i, s in enumerate(sent_starts):
        e = sent_starts[i + 1] if i + 1 < len(sent_starts) else len(t)
        if RE_PRIOR_SENT.search(t[s:e]) and not re.search(r"因涉嫌|因本案", t[s:e]):
            prior.append((s, e))
    for m in RE_MEASURE_TOKEN.finditer(t):
        k = m.lastgroup
        if k == "eos":
            sent_date = None; continue
        in_prior = any(s <= m.start() < e for s, e in prior)
        if k == "full":
            d = _mkdate(m.group(2), m.group(3), m.group(4))
            if d:
                last_abs = sent_date = d
            continue
        if k == "sy":
            if last_abs:
                d = _mkdate(last_abs.year, m.group(6), m.group(7))
                if d:
                    last_abs = sent_date = d
            continue
        if k == "sm":
            if last_abs:
                d = _mkdate(last_abs.year, last_abs.month, m.group(9))
                if d:
                    last_abs = sent_date = d
            continue
        if k == "same":
            if last_abs:
                sent_date = last_abs
            continue
        if k == "next":
            if last_abs:
                sent_date = last_abs = last_abs + timedelta(days=1)
            continue
        if in_prior:
            continue
        kw = m.group("kw") if k == "kw" else None
        if k == "approve":
            if out["date_arrest_approved"] is None and sent_date:
                out["date_arrest_approved"] = sent_date
            continue
        if kw in ("刑事拘留", "刑拘") or kw == "拘留":
            out["n_detain"] += 1
            if out["date_detain"] is None:
                out["date_detain"] = sent_date
                out["detain_kind"] = "criminal" if kw != "拘留" else "bare"
        elif kw in ("批准逮捕", "决定逮捕"):
            if out["date_arrest_approved"] is None:
                out["date_arrest_approved"] = sent_date
        elif kw in ("执行逮捕", "逮捕"):
            if out["date_arrest"] is None:
                out["date_arrest"] = sent_date
        elif kw == "取保候审":
            out["n_bail"] += 1
            if out["date_bail"] is None:
                out["date_bail"] = sent_date
        elif kw == "监视居住":
            if out["date_surveil"] is None:
                out["date_surveil"] = sent_date
    out["detained"] = out["n_detain"] > 0
    out["arrested"] = out["date_arrest"] is not None or bool(re.search(r"(?<!批准)(?<!决定)逮捕", t))
    out["bailed"] = out["n_bail"] > 0
    out["surveilled"] = "监视居住" in t
    if out["date_detain"] and out["date_arrest"]:
        out["days_to_arrest"] = (out["date_arrest"] - out["date_detain"]).days
    else:
        out["days_to_arrest"] = None
    return out


def _mkdate(y, mth, d):
    try:
        y, mth, d = int(y), int(mth), int(d)
        if 1990 <= y <= 2020:
            return date(y, mth, d)
    except (ValueError, TypeError):
        return None
    return None


# --------------------------------------------------------------------------- charges
RE_SUSPECT = re.compile(r"涉嫌([^，。；：\s（）()]{1,60}?罪)")
RE_YINGYI = re.compile(r"应(?:当|该)?(?:依法)?(?:分别)?(?:以)?([^，。；：\s（）()]{1,80}?罪)追究")
RE_GOUCHENG = re.compile(r"构成([^，。；：\s（）()]{1,60}?罪)")
# strings at a charge position that are not charges
JUNK_CHARGES = {"犯罪", "共同犯罪", "其他犯罪", "的犯罪", "以罪", "一罪", "数罪", "同一罪", "本罪", "重罪", "新罪", "该罪",
                "此罪", "两罪", "二罪", "多罪", "前罪", "后罪", "之罪", "何罪", "有罪", "无罪", "罪", "上述罪", "该项罪",
                "新的犯罪", "其它犯罪", "同种罪", "同种犯罪", "其他罪", "数个犯罪", "多个犯罪"}
RE_NAME_CHAIN = re.compile(
    r"(?:被告人|被不起诉人|犯罪嫌疑人|被告单位|被附条件不起诉人)[：:]?\s*((?:[^，,。、；:：\s（）()]{1,25})"
    r"(?:(?:、|[,，]\s*(?=被告人|被不起诉人|犯罪嫌疑人|被告单位))\s*(?:被告人|被不起诉人|犯罪嫌疑人|被告单位)?[^，,。、；:：\s（）()]{1,25})*)")


def charges_from_referral(text, sec_start, sec_end):
    """涉嫌 charges in the 本案由 paragraph with the names in front of each: list of (names, raw, pos)."""
    seg = text[sec_start:sec_end]
    out = []
    for m in RE_SUSPECT.finditer(seg):
        # names: last 被告人-chain before this match with no other 涉嫌 in between
        lead = seg[max(0, m.start() - 160): m.start()]
        cut = lead.rfind("涉嫌")
        if cut >= 0:
            lead = lead[cut + 2:]
        names = []
        for nm in RE_NAME_CHAIN.finditer(lead):
            names = _split_names(nm.group(1))
        out.append((names, m.group(1), sec_start + m.start(1)))
    return out


def charges_from_opinion(text, sec_start, sec_end):
    """Charges stated in 本院认为: list of (names, raw, pos, pattern) per sentence."""
    seg = text[sec_start:sec_end]
    out = []
    pos = 0
    for sent in re.split(r"(?<=。)", seg):
        if not sent:
            continue
        yy = list(RE_YINGYI.finditer(sent))
        hits = [(m.group(1), m.start(1), "yingyi") for m in yy]
        if not yy:
            hits = [(m.group(1), m.start(1), "goucheng") for m in RE_GOUCHENG.finditer(sent)]
        if hits:
            names = []
            for nm in RE_NAME_CHAIN.finditer(sent[: hits[0][1]]):
                names = _split_names(nm.group(1)) if not names else names + _split_names(nm.group(1))
            # a name chain can be repeated ("被告人甲、乙…被告人甲…"); dedupe keeping order
            seen, uniq = set(), []
            for n in names:
                if n not in seen:
                    seen.add(n); uniq.append(n)
            for raw, p, pat in hits:
                out.append((uniq, raw, sec_start + pos + p, pat))
        pos += len(sent)
    return out


def _split_names(chain: str):
    parts = re.split(r"[、,，]", chain)
    out = []
    for p in parts:
        p = re.sub(r"^(?:被告人|被不起诉人|犯罪嫌疑人)", "", p.strip())
        p = re.sub(r"(?:等人?|二人|三人|四人|五人|均|分别|各自)$", "", p)
        if p and len(p) <= 25:
            out.append(p)
    return out


def match_names(names, blocks):
    """Map name strings in the narrative to block indices. Exact match first, then unique prefix
    (黎某甲 vs 黎某甲某 edge cases are rare). Returns (list of indices or None per name)."""
    idx = {}
    for i, b in enumerate(blocks):
        idx.setdefault(b["name"], i)
    out = []
    for n in names:
        if n in idx:
            out.append(idx[n]); continue
        cands = [i for nm, i in idx.items() if nm.startswith(n) or n.startswith(nm)]
        if not cands:   # unit defendant referred to by its short form (古某某公司 for 江西古某某中药饮片有限公司)
            core = re.sub(r"(有限责任公司|有限公司|公司|厂|店|合作社|中心|集团)$", "", n)
            if len(core) >= 2:
                cands = [i for nm, i in idx.items() if core in nm and nm != n]
        out.append(cands[0] if len(cands) == 1 else None)
    return out


def looks_like_name(tok: str) -> bool:
    """Short tokens or masked tokens are names; long clauses picked up by the chain regex are not."""
    return len(tok) <= 5 or bool(re.search(r"[某\*×X]", tok[:4]))


# --------------------------------------------------------------------------- statutes
RE_LAW = re.compile(r"《([^》]{2,40})》|(?<![一-龥])(刑法|刑事诉讼法|刑诉法)(?=第)")
RE_REF = re.compile(   # tolerates 第第一百三十三条 (doubled 第), 第二百八十五第二款 (missing 条), 》二百六十四条 (missing 第)
    r"\s*(?:(?:第\s*)*([一二三四五六七八九十百零〇\d]+)\s*(?:条|(?=\s*第\s*[一二三四五六七八九十零〇\d]+\s*款)|(?=\s*[，,。；、的之]))(?:\s*之\s*([一二三四五六七八九十]+))?"
    r"|第\s*([一二三四五六七八九十零〇\d]+)\s*款"
    r"|第\s*[（(]?\s*([一二三四五六七八九十零〇\d]+)\s*[）)]?\s*项)")
RE_REF_SEP = re.compile(r"\s*(?:[、，,和及与]|以及|或者|或)\s*(?=第)")
RE_REF_TAIL = re.compile(r"\s*(?:之规定|的规定|规定)?")


def norm_law(raw: str):
    r = re.sub(r"\s", "", raw).replace("邢法", "刑法")
    if "刑事诉讼" in r or "刑诉" in r:
        return "刑诉法"
    if "修正案" in r:
        return "other"
    if "刑法" in r or r.endswith(("刑罚", "国刑")) or re.search(r"^中.{1,8}国刑法$", r):
        return "刑法"
    return "other"


def parse_statutes(text: str):
    """All 第X条[之N][第Y款][第Z项] references with the law they belong to: list of dicts."""
    out = []
    for lm in RE_LAW.finditer(text):
        raw = lm.group(1) or lm.group(2)
        law = norm_law(raw)
        pos = lm.end()
        article = sub = None
        first = True
        while True:
            m = RE_REF.match(text, pos)
            if not m:
                if first:
                    break
                sm = RE_REF_SEP.match(text, pos)
                if sm:
                    pos = sm.end(); first = False
                    m = RE_REF.match(text, pos)
                    if not m:
                        break
                else:
                    break
            first = False
            if m.group(1):
                article = cn2int(m.group(1)); sub = cn2int(m.group(2)) if m.group(2) else None
                para = item = None
                # trailing 款 / 项 directly attached
                pos = m.end()
                m2 = RE_REF.match(text, pos)
                while m2 and not m2.group(1):
                    if m2.group(3):
                        para = cn2int(m2.group(3))
                    elif m2.group(4):
                        item = cn2int(m2.group(4))
                    pos = m2.end()
                    m2 = RE_REF.match(text, pos)
                out.append(dict(law=law, law_raw=raw, article=article, sub_article=sub, paragraph=para, item=item,
                                pos=m.start(), ref_raw=text[m.start(): pos].strip()))
            else:
                # 款/项 without a 条: inherits the last article ("第六十七条第一款、第三款")
                if article is None:
                    pos = m.end(); continue
                para = cn2int(m.group(3)) if m.group(3) else (out[-1]["paragraph"] if out else None)
                item = cn2int(m.group(4)) if m.group(4) else None
                pos = m.end()
                m2 = RE_REF.match(text, pos)
                while m2 and m2.group(4):
                    item = cn2int(m2.group(4)); pos = m2.end(); m2 = RE_REF.match(text, pos)
                out.append(dict(law=law, law_raw=raw, article=article, sub_article=sub, paragraph=para, item=item,
                                pos=m.start(), ref_raw=text[m.start(): pos].strip()))
            sm = RE_REF_SEP.match(text, pos)
            if sm:
                pos = sm.end()
            else:
                break
    return out


# --------------------------------------------------------------------------- prosecutors
ROLES = ["代理检察员", "代检察员", "助理检察员", "检察官助理", "员额检察官", "主任检察官", "副检察长", "检察长", "检察员", "检察官", "书记员"]
_role_rx = "|".join("(" + r"\s*".join(r) + ")" for r in ROLES)
_role_any = "|".join(r"\s*".join(r) for r in ROLES)
# the name part stops before the next role label on the same line ("检察员：张三 书记员：李四")
RE_ROLE = re.compile(r"(?:" + _role_rx + r")\s*(?:[：:]|(?=\s)|(?=[一-龥]))\s*((?:(?!" + _role_any + r")[^\t\n]){0,60})")
RE_NAME_TOKEN = re.compile(r"^[一-龥·某\*×X]{2,20}$")
NAME_STOP = {"批准", "决定", "出庭", "依法", "助理", "意见", "签名", "签字", "盖章", "签发", "审批", "审核", "印", "空白"}
RE_ROLE_ANY = re.compile(r"检\s*察\s*(?:员|官|长)|书\s*记\s*员|代\s*理\s*检\s*察\s*员")


def parse_prosecutors(text: str, tail_start: int):
    """Signatures in the tail: list of dict(role, name, is_masked, line_raw, pos)."""
    tail = text[tail_start:]
    out = []
    for m in RE_ROLE.finditer(tail):
        role = next(r for r in ROLES if RE_WS.sub("", m.group(0)).startswith(r))
        rest = m.group(len(ROLES) + 1)
        # cut at the next role label on the same line ("检察员：张三 书记员：李四")
        cut = RE_ROLE_ANY.search(rest)
        if cut:
            rest = rest[: cut.start()]
        rest = re.split(r"\d{4}\s*年|[一二三四五六七八九〇○零]{4}\s*年|附[：:]", rest)[0]
        toks = [t for t in re.split(r"[\s、，,；;/]+", rest.strip()) if t]
        # merge spaced single characters ("李 碧 玉")
        merged, buf = [], ""
        for t in toks:
            if len(t) == 1 and t not in "某":
                buf += t; continue
            if buf:
                merged.append(buf); buf = ""
            merged.append(t)
        if buf:
            merged.append(buf)
        for t in merged:
            t = t.strip("：:。.")
            if RE_NAME_TOKEN.match(t) and t not in NAME_STOP and not any(s in t for s in NAME_STOP) \
                    and not re.search(r"检察|法院|人民|看守所|羁押|起诉|被告|本件|原本|核对|无异|附|案卷|证据|建议", t):
                out.append(dict(role=role, name=t, is_masked=bool(re.search(r"[某\*×X]", t)),
                                line_raw=RE_WS.sub(" ", m.group(0))[:80], pos=tail_start + m.start()))
    return out


# --------------------------------------------------------------------------- court / closing
RE_COURT = re.compile(r"此\s*致\s*[：:]?\s*([^\t\n，。；：]{2,40}?(?:人民法院|法院))")
RE_COURT_LOOSE = re.compile(r"([一-龥]{2,30}?人民法院)")


def parse_court(text: str, closing_start):
    if closing_start is None:
        return None
    m = RE_COURT.search(text, closing_start)
    if m:
        return RE_WS.sub("", m.group(1))
    m = RE_COURT_LOOSE.search(text, closing_start, min(len(text), closing_start + 120))
    return RE_WS.sub("", m.group(1)) if m else None


# --------------------------------------------------------------------------- custody at filing
RE_CUSTODY = re.compile(r"(取保候审|候审|监视居住|在逃|未被羁押|未羁押|已释放|释放|羁押|押于|押在|看守所|在押|拘留所)")


def custody_at_filing(text: str, region_start, blocks):
    """Per block: custody | bail | surveil | released | fugitive | None, from the 附 section (or the
    closing region when there is no 附): "被告人甲现羁押于…看守所；被告人乙现取保候审"."""
    res = [None] * len(blocks)
    if region_start is None or not blocks:
        return res
    att = text[region_start: region_start + 700]
    clauses = re.split(r"[；;。]", att)   # not on TAB: "被告人赵某某、李某乙\t现取保候审" is one clause
    for cl in clauses:
        if not re.search(r"现|住|羁押|押于|押在|看守所|候审|监视居住|在逃|释放", cl):
            continue
        m = RE_CUSTODY.search(cl)
        if not m:
            continue
        kw = m.group(1)
        status = "bail" if kw in ("取保候审", "候审") else "surveil" if kw == "监视居住" else \
            "released" if "释放" in kw or kw.startswith("未") else "fugitive" if kw == "在逃" else "custody"
        named = [i for i, b in enumerate(blocks) if b["name"] and b["name"] in cl]
        targets = named if named else ([0] if len(blocks) == 1 else [])
        for i in targets:
            if res[i] is None:
                res[i] = status
    return res


# --------------------------------------------------------------------------- non-prosecution reason
NONPROS_MAP = {  # (article, paragraph) -> reason; 1996 CPL, 2012 CPL, 2018 CPL numberings
    (142, 1): "法定不起诉", (142, 2): "相对不起诉", (140, 4): "存疑不起诉",
    (173, 1): "法定不起诉", (173, 2): "相对不起诉", (171, 4): "存疑不起诉", (271, None): "附条件不起诉",
    (177, 1): "法定不起诉", (177, 2): "相对不起诉", (175, 4): "存疑不起诉", (282, None): "附条件不起诉",
    (182, None): "特别不起诉",
    (15, None): "法定不起诉", (16, None): "法定不起诉",
}
RE_DECISION_SENT = re.compile(r"[^。]*决定[^。]*不起诉[^。]*。")


def nonpros_reason(text: str, opinion_start, opinion_end, statutes):
    """(reason, article, paragraph, source). Article-based first (decision sentence, then whole opinion),
    keyword fallback last."""
    if opinion_start is None:
        opinion_start, opinion_end = 0, len(text)
    seg = text[opinion_start:opinion_end]
    m = RE_DECISION_SENT.search(seg)
    cands = []
    if m:
        lo, hi = opinion_start + m.start(), opinion_start + m.end()
        cands = [s for s in statutes if s["law"] == "刑诉法" and lo <= s["pos"] < hi]
    if not cands:
        cands = [s for s in statutes if s["law"] == "刑诉法" and opinion_start <= s["pos"] < opinion_end]
    for prefer_para in (True, False):
        for s in cands:
            key = (s["article"], s["paragraph"] if prefer_para else None)
            if key in NONPROS_MAP:
                return NONPROS_MAP[key], s["article"], s["paragraph"], "article"
    # 款 unknown: article alone still tells 法定/相对 apart only for 271/282/182/15/16
    for s in cands:
        if s["article"] in (173, 177, 142):
            if "情节轻微" in seg or "犯罪情节轻微" in seg:
                return "相对不起诉", s["article"], s["paragraph"], "article+keyword"
            return "不起诉(款未注明)", s["article"], s["paragraph"], "article"
    if "附条件不起诉" in seg:
        return "附条件不起诉", None, None, "keyword"
    if "犯罪情节轻微" in seg or "情节轻微" in seg:
        return "相对不起诉", None, None, "keyword"
    if re.search(r"证据不足|不符合起诉条件|事实不清", seg):
        return "存疑不起诉", None, None, "keyword"
    if re.search(r"没有犯罪事实|不负刑事责任|情节显著轻微|不认为是犯罪|不构成犯罪|已过追诉时效|告诉才处理", seg):
        return "法定不起诉", None, None, "keyword"
    return None, None, None, None


# --------------------------------------------------------------------------- offense date / age
def facts_dates(text: str, start, end):
    """(first_date_in_document_order, earliest_date) over [start, end), full dates and 年月 dates
    (day taken as 1), 1980 <= year <= 2019. The first date in the 查明事实 paragraph is normally the
    offence; the earliest date is a conservative bound for the minor gate."""
    if start is None:
        return None, None
    seg = text[start:end]
    first = best = None
    for m in RE_DATE_YM.finditer(seg):
        tail = seg[m.end(): m.end() + 4]
        md = re.match(r"\s*(\d{1,2})\s*日", tail)
        d = _mkdate_loose(m.group(1), m.group(2), md.group(1) if md else 1)
        if d is None:
            continue
        if first is None:
            first = d
        if best is None or d < best:
            best = d
    return first, best


def _mkdate_loose(y, mth, d):
    try:
        y, mth, d = int(y), int(mth), int(d)
        if 1980 <= y <= 2019:
            return date(y, mth, d)
    except (ValueError, TypeError):
        return None
    return None


def minor_status(birth_year, offense: date):
    """Birth month/day are masked, so true age at offense is age or age-1."""
    if birth_year is None or offense is None:
        return None, "undetermined"
    age = offense.year - birth_year
    if age < 0 or age > 110:
        return None, "undetermined"
    if age <= 17:
        return age, "minor"
    if age == 18:
        return age, "ambiguous"
    return age, "adult"
