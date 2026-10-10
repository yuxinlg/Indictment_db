#!/usr/bin/env python3
"""
P1 — rule-based parsing of one defendant's position string (defendants.occupation_raw) into
position rows, following 条块共谋网络方案 v1 §4–5 and §11.

Pure functions over one string; no I/O except loading the two lexicons once:
  ref/position_lexicon.csv  — organisation keywords → system (条); SOE / private cues
  ref/position_titles.csv   — title words → rank (正职 / 副职 / 一般人员) and a system hint;
                               status words (农民, 无业, 个体 …) that are not positions

One occupation_raw string → list of position dicts, one per position, in the order described
(`position_seq` 0 = first). The caller (p1_build.py) adds the charge-based firm-ownership
fallback and the region codes.

Pipeline per string:
  1. split on "；" (M1 joined the identity fields with it) → fragments
  2. drop fragments that are not positions (曾用名, 捕前住, 证件, 非人大代表, 汉族, hukou-like "陕西西安人")
  3. split a fragment into pieces on "、" / "兼" / "并任"; a titled piece with no organisation
     inherits the previous organisation ("党组书记、局长", "教师兼出纳"); an organisation-only piece
     followed by a titled piece inherits that title ("A公司、B公司实际控制人")
  4. per piece: time marker (原任 / 现任 / 案发时 / 不明) → organisation | title split (title = the
     last title word, verbatim) → system from the rightmost organisation keyword (attached organs
     such as 党组 / 工会 / 分局 take their host's system; keywords inside a place name are ignored)
     → level from the deepest place token (named tokens with a coded suffix are validated against
     ref/region_codes.csv) → rank from the title.
"""
import csv
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LEX_CSV = ROOT / "ref" / "position_lexicon.csv"
TITLE_CSV = ROOT / "ref" / "position_titles.csv"

SYS_VALUES = ["党委系统", "政府部门", "人大政协", "公检法司", "国企", "事业单位", "村级组织", "金融机构", "民营企业", "无公职"]
LEVEL_VALUES = ["省", "市", "县", "区", "乡镇街道", "村社区"]
RANK_VALUES = ["正职", "副职", "一般人员"]
TIME_VALUES = ["原任", "现任", "案发时", "不明"]

RE_MASK_RUN = re.compile(r"[\*×X□○某]+")
RE_ALL_MASK = re.compile(r"^[\*×X□○某甲乙丙丁]+$")


# --------------------------------------------------------------------------- lexicons
def _load_lexicon():
    kw, soe, private = {}, [], []
    for r in csv.DictReader(open(LEX_CSV, encoding="utf-8-sig")):
        k, kind, v = r["keyword"], r["kind"], (r["value"] or None)
        if kind == "soe":
            soe.append(k)
        elif kind == "private":
            private.append(k)
        else:
            kw[k] = (kind, v)
    return kw, soe, private


def _load_titles():
    titles, status = {}, {}
    for r in csv.DictReader(open(TITLE_CSV, encoding="utf-8-sig")):
        t = r["title"]
        if r["kind"] == "status":
            status[t] = r["sys_hint"] or None
        elif r["kind"] == "title":
            titles[t] = (r["rank"] or None, r["sys_hint"] or None)
    return titles, status


KW, SOE_CUES, PRIVATE_CUES = _load_lexicon()
TITLES, STATUS = _load_titles()
ATTACHED = {k for k, (kind, v) in KW.items() if kind == "attached"}

RE_KW = re.compile("|".join(map(re.escape, sorted(KW, key=len, reverse=True))))
RE_KW_ALL = re.compile("(?=(" + "|".join(map(re.escape, sorted(KW, key=len, reverse=True))) + "))")   # overlapping starts
RE_SOE = re.compile("|".join(map(re.escape, sorted(SOE_CUES, key=len, reverse=True))))
RE_PRIVATE = re.compile("|".join(map(re.escape, sorted(PRIVATE_CUES, key=len, reverse=True))))
_PRE = r"(?P<pre>(?:常务|第一|执行|代理|代|兼职|专职|挂职|名誉|荣誉|临时|助理|副)*)"
RE_TITLE = re.compile(r"^(?P<org>.*?)" + _PRE + r"(?P<title>" + "|".join(map(re.escape, sorted(TITLES, key=len, reverse=True))) + r")$")
RE_TITLE_STATUS = re.compile(r"^(?P<org>.+?)(?P<title>" + "|".join(map(re.escape, sorted(STATUS, key=len, reverse=True))) + r")$")
RE_TITLE_MASKED = re.compile(r"^(?P<org>.*?)(?P<pre>副)?(?P<title>[\*×X□○某]+(?:长|员|主任|书记|经理|科员|委员|代表|局长|所长)?)$")
RE_TITLE_SUFFIX = re.compile(r"^(?P<org>.+?)(?P<pre>副)?(?P<title>[一-龥]{1,3}(?:长|员|师|工|手))$")
RE_ORGISH_END = re.compile(r"(?:[局委办厅处科室股部站所队院校司厂场会社馆台园库店心组点班连团线行区镇乡村道查木庄寨社厅矿井场段期号]|[\*×X□○某]|\d)$")
NOT_TITLE_SUFFIX = {"成长", "增长", "特长", "专长", "生长", "党员", "团员", "会员", "学员", "演员", "船员", "官员", "议员", "家长",
                    "乘员", "全员", "雇员", "成员", "人员", "员"}
BARE_LEVEL = {"省": "省", "市": "市", "县": "县", "区": "区", "镇": "乡镇街道", "乡": "乡镇街道", "街道": "乡镇街道", "村": "村社区", "社区": "村社区", "嘎查": "村社区"}
TITLE_LEVEL = {"省长": "省", "市长": "市", "县长": "县", "区长": "区", "镇长": "乡镇街道", "乡长": "乡镇街道", "村长": "村社区",
               "嘎查达": "村社区", "村支书": "村社区", "村书记": "村社区", "村主任": "村社区", "村干部": "村社区", "社区干部": "村社区",
               "网格员": "村社区"}
NO_INHERIT_TITLES = set(TITLE_LEVEL) | {"人大代表", "政协委员", "村民", "村民代表"}


# --------------------------------------------------------------------------- fragment filtering
RE_DROP = re.compile(
    r"^(?:曾用名|绰号|别名|外号|化名|又名|小名|乳名|艺名|网名|原名|英文名|笔名|曾用|姓名|姓)"
    r"|^(?:捕前住|现住|暂住|居住|住|户籍|户口|籍贯|原籍|家住|住址|住所|租住|现居|经常居住|无固定住|暂居)"
    r"|证件|护照|身份|联系方式|电话|手机|邮箱|微信号"
    r"|^非(?:人大|政协|公职|中共)|不是人大|^无$|^无党派|^(?:中共)?(?:党员|团员|群众|预备党员)$"
    r"|^[一-龥\*×X□○]{0,4}族$|^(?:粗识字|识字|文盲|半文盲)|^系?原案|被害人|证人$|^(?:男|女)(?:性)?$"
    r"|^(?:正|副|享受)?(?:科|处|厅|部|股|局|县|乡|镇|国)级(?:干部|待遇)?$"
    r"|^\d+$|^[\d\-]+号?$|^(?:无|不详|不明)$|^(?:已|未)(?:婚|故)|^(?:前科|劣迹|曾因|受过)"
    r"|文化$|学历|毕业|肄业|缓刑|有期徒刑|拘役|判处|刑满|释放|^非[×\*X某]*(?:委员|代表)|^非党|^原住|^原籍|^原户籍|^无党"
    r"|^现年|岁$|同年|本院|批准|逮捕|拘留|^行政级别|^级别[：:]|^并住|^并居|年\d+月|编制$|^[一二三四五六七八九十\d]+级[一-龥]{0,3}$")
RE_GRADE = re.compile(r"(?:正|副|享受)?(?:科|处|厅|局|股|部|国|县|乡|镇|市|省)级(?:干部|待遇|领导|职务)?")
RE_INNER_FORMER = re.compile(r"(?<=[村镇乡县市区局委会司厂校院所站处科队部\*×X某])原(?!料|油|煤|木|材|始|则|因|告|野|生|子|产|有|来|址)(?=[一-龥\*×X某])")
RE_HUKOU_PERSON = re.compile(r"^[一-龥\*×X某]{2,10}(?:籍)?人$")
RE_PERSON_TITLE_TAIL = re.compile(r"(?:负责人|代表人|法人|经营人|控制人|承包人|工人|人员|军人|证人|保证人|介绍人|中间人|合伙人|经纪人|代理人|"
                                  r"发起人|持有人|投资人|申请人|监护人|委托人|联系人|牵头人|带头人|主持人|管理人|责任人|经手人|继承人|"
                                  r"主任人|受托人|出资人|借款人|担保人|保荐人|营人)$")
RE_PAREN = re.compile(r"[（(][^（）()]*[）)]")
RE_PAREN_OPEN = re.compile(r"[（(][^（）()]*$")   # unclosed "(以下简称…" at the end
RE_LEAD_NOISE = re.compile(r"^(?:本届|第[\d\*×X一二三四五六七八九十]+届|先后担任|先后任|历任|担任|现担任|曾担任|原担任|职务[：:]?|职业[：:]?|工作单位[：:]?|单位[：:]?|行政级别为|级别[：:]?|身份[：:]?|系统[：:]?)")
RE_TAIL_NOISE = re.compile(r"(?:（职务）|职务|一职|等职务|等职|工作|之职|任职|职位)$")
RE_SPLIT_FRAG = re.compile(r"[；;]")
RE_SPLIT_PIECE = re.compile(r"、|兼任|并任|并兼|兼(?!职|容|并|营)")

RE_TIME = re.compile(
    r"^(?:(?P<offense>案发前|案发时|案发期间|作案时|作案前|时任|捕前|被捕前|归案前|到案前|案发当时|犯罪时|事发时|事发前)(?:系|任|为|担任|在|是)?"
    r"|(?P<former>原任|原系|原为|原在|原是|原|曾任|曾系|曾为|曾在|曾是|曾|退休前(?:系|任|为)?|离任前(?:系|任|为)?|先后担任|先后任|历任|曾经)"
    r"|(?P<current>现任|现系|现为|现在|现是|现|目前(?:系|任|为)?|系现)"
    r"|(?P<neutral>系|任|为|担任|在|是))")
TIME_GUARD = ("原平市", "原阳县", "原州区", "任县", "任丘", "任城区", "曾都区", "现代", "系统", "原种场", "原煤", "在编", "系列", "原始")
RE_WORK_AT = re.compile(r"^在(?P<org>.+?)(?:工作|任职|上班|打工|就职|就业|供职|务工|从事[一-龥]*)$")
RE_TRAIL_FORMER = re.compile(r"(?:原|原任|原系|时任|前任|前)$")


def _strip_parens(s: str) -> str:
    s = RE_PAREN.sub("", s)
    s = RE_PAREN_OPEN.sub("", s)
    return s.replace("“", "").replace("”", "").replace('"', "").replace(" ", "").replace("　", "")


def is_position_fragment(f: str) -> bool:
    """False for identity leftovers that are not positions."""
    if not f or RE_DROP.search(f):
        return False
    if RE_HUKOU_PERSON.match(f) and not RE_PERSON_TITLE_TAIL.search(f):
        return False
    return True


# --------------------------------------------------------------------------- place tokens / level
PLACE_SUFFIXES = [  # (suffix, level, coded)  — multi-char suffixes first
    ("自治区", "省", True), ("自治州", "市", True), ("自治县", "县", True), ("自治旗", "县", True), ("地区", "市", True),
    ("街道", "乡镇街道", False), ("苏木", "乡镇街道", False), ("办事处", "乡镇街道", False),
    ("社区", "村社区", False), ("嘎查", "村社区", False),
    ("省", "省", True), ("市", "市", True), ("县", "县", True), ("旗", "县", True), ("州", "市", True), ("盟", "市", True),
    ("区", "区", True), ("镇", "乡镇街道", False), ("乡", "乡镇街道", False), ("村", "村社区", False),
]
RE_FUNC_ZONE = re.compile(r"(?:开发区|高新区|新区|矿区|林区|景区|园区|示范区|保护区|经开区|工业区|旅游区|度假区|风景区|试验区|新城区|产业区|工业园区|农业园区|管理区|垦区|保税区|自贸区|库区|灌区)$")
ORG_CHARS = set("局委办厅处室司院队所")          # a place name never contains these
NAME_STOP_CHARS = set("和与及的")               # … nor these, when the suffix cannot be validated (住房和城乡)
RE_ADMIN_ZONE = re.compile(r"(?:开发区|高新区|新区|经开区|园区|工业园区|农业园区|示范区|产业区|工业区|旅游区|度假区|管理区|新城区|保税区|自贸区)$")
RE_NATURAL_ZONE = re.compile(r"(?:矿区|林区|景区|保护区|风景区|试验区|垦区|库区|灌区)$")
STOP_TOKENS = ["农村", "新农村", "乡村", "村镇", "城乡", "乡镇", "城市", "都市", "超市", "门市", "集市", "夜市", "闹市", "市场", "本村", "本镇", "本县", "本市", "本省", "全村", "全镇", "全县", "全市", "全省"]
_SKIP_AFTER = {  # suffix -> chars that must not follow
    "州": "市", "市": "场容民级属区辖郊界", "县": "级域属", "区": "域级段间", "省": "级直属际内外部",
    "镇": "级区", "乡": "镇级下", "村": "镇庄寨级", "社区": "卫生",
}
_PREV_STOP = {"镇": "乡村城", "村": "农新乡", "市": "城都超门集夜闹", "乡": "城", "区": "社小片厂校灌库辖郊市地园"}
_MASKED_MARKET = "超门集夜闹城都"


class LevelParser:
    """Finds place tokens in an organisation string. With a RegionIndex (m1_regions) named tokens with a
    coded suffix are validated and 县级市 are recognised; without one, tokens are accepted on shape only."""

    def __init__(self, region_index=None):
        self.ix = region_index
        self.short_city, self.short_county, self.city_names = set(), set(), set()
        self.prov_keys = []
        if region_index is not None:
            for d in region_index.city_short.values():
                self.short_city.update(d)
            for d in region_index.county_short.values():
                self.short_county.update(d)
            self.prov_keys = sorted((k for k in region_index.prov if len(k) >= 2 and "兵团" not in k), key=len, reverse=True)   # 新疆生产建设兵团 is an organisation
            self.city_names = set(region_index.any_city)
            self.county_names = set(region_index.any_county)

    def _valid(self, name, suffix, level):
        if self.ix is None:
            return True
        full = name + suffix
        if level == "省":
            return full in self.ix.prov or name in self.ix.prov
        if level == "市":
            return full in self.city_names or full in self.county_names or name in self.short_city or name in self.short_county
        if level in ("县", "区"):
            if RE_FUNC_ZONE.search(full):
                return True
            return full in self.county_names or name in self.short_county
        return True

    def _level_of_named(self, name, suffix, level):
        """县级市 → 县; prefecture-level 区 (none) stays."""
        if self.ix is None or suffix != "市":
            return level
        full = name + suffix
        if full in self.county_names and full not in self.city_names:
            return "县"
        if full not in self.city_names and name in self.short_county and name not in self.short_city:
            return "县"
        return level

    def tokens(self, org: str):
        """List of dicts: start, end, name, suffix, level, masked, coded."""
        out = []
        i, n = 0, len(org)
        while i < n:
            tok = self._token_at(org, i, out)
            if tok:
                out.append(tok)
                i = tok["end"]
            else:
                i += 1
        return out

    def _prov_at_start(self, org):
        for k in self.prov_keys:
            if not org.startswith(k):
                continue
            rest = org[len(k):]
            if k.endswith(("省", "自治区")) or k in ("北京市", "上海市", "天津市", "重庆市"):
                return dict(start=0, end=len(k), name=k, suffix="", level="省", masked=False, coded=True)
            if rest[:1] and (rest[:1] in "省市州县区镇乡村盟旗" or rest.startswith(("自治", "地区"))):
                continue   # 吉林市 / 海南州 / 广西… are longer tokens
            return dict(start=0, end=len(k), name=k, suffix="", level="省", masked=False, coded=True)
        return None

    def _city_at_start(self, org):
        """Prefecture short name opening the string: 哈密公路管理局, 福州**科技有限公司."""
        if self.ix is None:
            return None
        for L in (4, 3, 2):
            k = org[:L]
            if len(k) == L and k in self.short_city and k not in self.ix.prov:
                rest = org[L:]
                if rest[:1] and (rest[:1] in "省市州县区镇乡村盟旗" or rest.startswith(("自治", "地区"))):
                    continue
                if rest[:1] and (RE_MASK_RUN.match(rest) or RE_KW.search(rest[:6])):
                    return dict(start=0, end=L, name=k, suffix="", level="市", masked=False, coded=True)
        return None

    def _token_at(self, org, i, prev):
        if i == 0:
            t = self._prov_at_start(org) or self._city_at_start(org)
            if t:
                return t
        n = len(org)
        for L in range(0, 13):
            if i + L > n:
                break
            if L and org[i + L - 1] in ORG_CHARS:
                break
            name = org[i:i + L]
            for suffix, level, coded in PLACE_SUFFIXES:
                if not org.startswith(suffix, i + L):
                    continue
                end = i + L + len(suffix)
                nxt = org[end:end + 1]
                if nxt and nxt in _SKIP_AFTER.get(suffix, ""):
                    continue
                if suffix == "社区" and org.startswith(("卫生", "服务中心", "医院", "学校", "警务", "矫正"), end):
                    continue
                if L == 0:
                    if i > 0 and org[i - 1] in _PREV_STOP.get(suffix, ""):
                        continue
                    if i > 0 and not (prev and prev[-1]["end"] == i):
                        continue
                    after = org[end:end + 6]
                    if not after or any(after.startswith(s) for s, _, _ in PLACE_SUFFIXES):
                        continue
                    if not RE_KW.search(after):
                        continue
                    return dict(start=i, end=end, name="", suffix=suffix, level=level, masked=False, coded=False)
                masked = bool(RE_MASK_RUN.search(name))
                tok = name + suffix
                prev_tok = prev[-1] if prev and prev[-1]["end"] == i else None
                if masked:
                    if suffix == "市" and name[-1:] in _MASKED_MARKET:
                        continue
                    if suffix == "区" and name[-1:] in "片小厂校灌库辖郊社监":
                        continue
                    if len(RE_MASK_RUN.sub("", name)) > 2:      # "北安监狱**监" is not a masked place name
                        continue
                    if any(tok.endswith(w) for w in STOP_TOKENS):
                        continue
                    return dict(start=i, end=end, name=name, suffix=suffix, level=level, masked=True, coded=coded)
                if any(c in ORG_CHARS for c in name):
                    continue
                if not coded and (any(c in NAME_STOP_CHARS for c in name) or any(tok.endswith(w) for w in STOP_TOKENS)):
                    continue
                if coded:
                    if self._valid(name, suffix, level):
                        lvl = self._level_of_named(name, suffix, level)
                        if suffix == "区" and RE_NATURAL_ZONE.search(tok):
                            lvl = None                      # 自然保护区 / 矿区 are not administrative levels
                        elif suffix == "区" and RE_ADMIN_ZONE.search(tok):
                            lvl = prev_tok["level"] if prev_tok and prev_tok["coded"] and prev_tok["name"] and prev_tok["level"] in ("市", "县", "区") else "区"
                        return dict(start=i, end=end, name=name, suffix=suffix, level=lvl, masked=False, coded=True)
                    if suffix in ("县", "旗", "区") and len(name) <= 6 and prev_tok and prev_tok["coded"] and prev_tok["name"] \
                            and prev_tok["level"] == "市" and not RE_FUNC_ZONE.search(tok) and name[-1:] not in "片小厂校灌库辖郊社监":
                        # 达茂联合旗 after 包头市: a county the code table does not list under that spelling
                        return dict(start=i, end=end, name=name, suffix=suffix, level=level, masked=False, coded=False)
                    continue
                if len(name) <= 8:
                    return dict(start=i, end=end, name=name, suffix=suffix, level=level, masked=False, coded=False)
        return None


# --------------------------------------------------------------------------- one piece
def _split_title(core: str):
    """core (time marker removed) -> (org, title, title_masked, pre, method)."""
    ms = RE_TITLE_STATUS.match(core)
    if ms and len(ms.group("org")) <= 4 and not RE_KW.search(ms.group("org")):      # 私人包工头
        return ms.group("org"), ms.group("title"), False, "", "status"
    m = RE_TITLE.match(core)
    if m:
        pre, title = m.group("pre") or "", m.group("title")
        if pre.endswith("副") and ("副" + title) in TITLES:
            pre, title = pre[:-1], "副" + title
        return m.group("org"), title, False, pre, "lexicon"
    m = RE_TITLE_STATUS.match(core)
    if m:
        return m.group("org"), m.group("title"), False, "", "status"
    m = RE_TITLE_MASKED.match(core)
    if m:
        return m.group("org"), None, True, m.group("pre") or "", "masked"
    if core[-1:] in "长员师工手" and len(core) >= 3 and not core.endswith(("成长", "增长", "特长", "专长", "生长")):
        for n in (2, 1, 3):   # 材料员 / 点长 / 林管护员 — the organisation must end at a unit-like boundary
            title, org = core[-(n + 1):], core[:-(n + 1)]
            pre = ""
            if org.endswith("副"):
                pre, org = "副", org[:-1]
            if org and RE_ORGISH_END.search(org) and title not in NOT_TITLE_SUFFIX and not RE_KW.fullmatch(title) and "族" not in title:
                if RE_MASK_RUN.search(title):
                    return org, None, True, pre, "masked"
                return org, title, False, pre, "suffix"
        if len(core) <= 6 and not RE_KW.search(core) and core not in NOT_TITLE_SUFFIX:
            return "", core, False, "", "suffix"
    return core, None, False, "", None


def _rank(title, pre, masked, title_method, core):
    if pre and "副" in pre:
        return "副职", "prefix:副"
    if title is None:
        if masked:
            tail = re.sub(r"^[\*×X□○]+", "", core)
            if tail.endswith(("长", "主任", "书记", "经理", "局长", "所长")):
                return "正职", "masked:" + tail[-1]
            if tail.endswith(("员", "委员", "代表", "科员")):
                return "一般人员", "masked:" + tail[-1]
        return None, None
    if title in TITLES:
        r = TITLES[title][0]
        return r, ("title" if r else None)
    if title_method == "suffix":
        if title.endswith("长"):
            return "正职", "suffix:长"
        return "一般人员", "suffix:" + title[-1]
    return None, None


_SCAN_MASK = ("新农村", "农村", "乡村", "村镇", "城乡", "乡镇企业", "城市", "都市", "超市", "门市", "集市", "夜市", "市场", "城中村")


def scan_keywords(org: str, tokens=None):
    """Lexicon matches in org as (start, end, keyword, kind, value). Matches that fall inside the name
    part of a place token (金山店镇 → 店) and inside 农村 / 城乡 … bigrams are ignored."""
    s = org
    for w in _SCAN_MASK:
        s = s.replace(w, "\x00" * len(w))
    spans = [(t["start"], t["start"] + len(t["name"])) for t in (tokens or []) if t["name"] and not t["masked"]]
    found = []
    for m in RE_KW_ALL.finditer(s):
        k = m.group(1)
        if any(a <= m.start() and m.start() + len(k) <= b for a, b in spans):
            continue
        found.append((m.start(), m.start() + len(k), k))
    # resolve overlaps: longest keyword wins (人民政府 over 民政, 政治协商会议 over 民政, 县委员会 over 县委)
    taken, out = [], []
    for st, en, k in sorted(found, key=lambda x: (-(x[1] - x[0]), -x[1])):   # ties: the later-ending word (北安监狱 → 监狱, not 安监)
        if any(not (en <= a or st >= b) for a, b in taken):
            continue
        taken.append((st, en))
        kind, v = KW[k]
        out.append((st, en, k, kind, v))
    out.sort()
    return out


def classify_sys(org, title, title_hint=None, tokens=None):
    """(sys, rule). '企业' is an intermediate value: the caller resolves it (SOE / private cue, then charge)."""
    kws = scan_keywords(org, tokens) if org else []
    strong = [k for k in kws if k[3] in ("strong", "attached")]
    units = [k for k in kws if k[3] == "unit" and k[4]]
    units_all = [k for k in kws if k[3] == "unit"]
    places = [k for k in kws if k[3] == "place"]
    if strong:
        k = strong[-1]
        if k[4] == "企业" and k[2] in ("公司", "分公司", "总公司", "集团") and any(h[4] == "金融机构" for h in strong):
            return "金融机构", f"org:{k[2]}<金融"
        if k[3] == "attached":
            hosts = [h for h in kws if h[1] <= k[0] and (h[3] == "strong" or (h[3] == "unit" and h[4]))]
            if hosts:
                h = hosts[-1]
                return _resolve_firm(h[4], org, title, f"attached:{k[2]}<{h[2]}")
            return _resolve_firm(k[4], org, title, f"attached:{k[2]}")
        return _resolve_firm(k[4], org, title, f"org:{k[2]}")
    if units:
        k = units[-1]
        return k[4], f"unit:{k[2]}"
    if title_hint:
        return _resolve_firm(title_hint, org, title, f"title:{title}")
    if places and not units_all:
        return "政府部门", f"place:{places[-1][2]}"
    return None, None


def _resolve_firm(sys, org, title, rule):
    if sys != "企业":
        return sys, rule
    m = RE_SOE.search(org or "")
    if m:
        return "国企", rule + f";soe:{m.group(0)}"
    m = RE_PRIVATE.search(org or "")
    if m:
        return "民营企业", rule + f";private:{m.group(0)}"
    if title and TITLES.get(title, (None, None))[1] == "民营企业":
        return "民营企业", rule + f";private_title:{title}"
    return "企业", rule


def _new_row(raw):
    return dict(position_raw=raw, position_time="不明", position_sys=None, position_sys_rule=None, position_level=None,
                position_level_source=None, position_rank=None, position_rank_rule=None, position_title=None,
                position_title_masked=False, position_org=None, is_status=False, place_str=None, parsed=False, tokens=[])


def parse_piece(piece: str, lp: LevelParser):
    """One position description -> dict (region codes are added by the caller)."""
    out = _new_row(piece)
    s = RE_GRADE.sub("", _strip_parens(piece)).strip(" 　:：,，。")
    time = "不明"
    for _ in range(3):   # "系原…", "先后担任…", "职务：原任…"
        s2 = RE_LEAD_NOISE.sub("", s)
        if not s2.startswith(TIME_GUARD):
            m = RE_TIME.match(s2)
            if m:
                if m.group("offense"):
                    time = "案发时"
                elif m.group("former") and time != "案发时":       # 案发前曾任 stays 案发时
                    time = "原任"
                elif m.group("current") and time == "不明":
                    time = "现任"
                s2 = s2[m.end():]
        if s2 == s:
            break
        s = s2
    s = RE_TAIL_NOISE.sub("", s).strip(" 　:：,，。")
    out["position_time"] = time
    if not s:
        return out
    if RE_ALL_MASK.match(s):
        out.update(position_title_masked=True, parsed=True)
        return out
    if s in STATUS:
        out.update(position_title=s, is_status=True, parsed=True, position_sys=STATUS[s], position_sys_rule=f"status:{s}")
        return out
    if s.startswith(("个体", "私营")) and not RE_KW.search(s[2:]):
        out.update(position_title=s, is_status=True, parsed=True, position_sys="民营企业", position_sys_rule=f"status:{s[:2]}")
        return out
    if s.startswith("从事") and len(s) <= 8:      # 从事建筑业
        out.update(position_title=s, is_status=True, parsed=True, position_sys_rule="status:从事")
        return out
    m = RE_WORK_AT.match(s)
    if m:
        org, title, masked, pre, tm = m.group("org"), None, False, "", None
    else:
        org, title, masked, pre, tm = _split_title(s)
    org = org.strip(" 　,，、")
    mt = RE_TRAIL_FORMER.search(org)
    if mt and mt.start() > 0:
        org = org[:mt.start()]
        if time == "不明":
            time = "原任"
    mi = RE_INNER_FORMER.search(org)
    if mi:                                          # 宜章县**乡**村原党支部书记 / 局原副局长
        org = org[:mi.start()] + org[mi.end():]
        if time == "不明":
            time = "原任"
    org = org.rstrip("的之")
    if masked and org in STATUS:     # 退休**
        out.update(position_title=org, is_status=True, parsed=True, position_sys=STATUS[org], position_sys_rule=f"status:{org}",
                   position_title_masked=False, position_time=time)
        return out
    toks = lp.tokens(org) if org else []
    if org and not toks and org in BARE_LEVEL:
        toks = [dict(start=0, end=len(org), name="", suffix=org, level=BARE_LEVEL[org], masked=False, coded=False)]
    title_hint = TITLES[title][1] if title in TITLES else (STATUS.get(title) if tm == "status" else None)
    sys, rule = classify_sys(org, title, title_hint, toks)
    if tm == "status":
        sys, rule = STATUS.get(title), f"status:{title}"
    elif title in TITLES and TITLES[title][1] == "无公职":          # 村民 after a place is still 无公职
        sys, rule = "无公职", f"title:{title}"
    rank, rank_rule = _rank(title, pre, masked, tm, s)
    level, lsrc, place_str = None, None, None
    if toks:
        deep = toks[-1]
        level = deep["level"]
        lsrc = "suffix_masked" if deep["masked"] else ("token" if deep["coded"] and deep["name"] else "suffix")
        named = []
        for t in toks:
            if t["masked"] or not t["coded"] or (not t["name"]):
                break
            if named and t["start"] != named[-1]["end"]:
                break
            named.append(t)
        if named:
            place_str = org[named[0]["start"]:named[-1]["end"]]
    if sys == "村级组织":
        level, lsrc = "村社区", lsrc if level == "村社区" else "sys"
    if level is None and org:
        kws = scan_keywords(org, toks)
        if any(k[2] == "厅" for k in kws):
            level, lsrc = "省", "org_kw"
    if level is None and title in TITLE_LEVEL:
        level, lsrc = TITLE_LEVEL[title], "title"
    if sys == "无公职":
        level, lsrc, rank, rank_rule = None, None, None, None
    if tm == "status":
        rank, rank_rule = None, None
    parsed = bool(title or masked or (org and (toks or RE_KW.search(org))))
    out.update(position_sys=sys, position_sys_rule=rule, position_level=level, position_level_source=lsrc,
               position_rank=rank, position_rank_rule=rank_rule, position_title=title, position_title_masked=masked,
               position_org=org or None, place_str=place_str, position_time=time, parsed=parsed, tokens=toks,
               is_status=(tm == "status"))
    return out


# --------------------------------------------------------------------------- one string
def split_fragments(occupation_raw: str):
    return [f.strip() for f in RE_SPLIT_FRAG.split(occupation_raw or "") if f.strip()]


def split_pieces(fragment: str):
    """-> list of (piece, separator) where separator is '' for the first piece, '、' or '兼' otherwise."""
    f = _strip_parens(fragment).replace("）", "").replace("（", "").replace(")", "").replace("(", "")
    out, last = [], 0
    for m in RE_SPLIT_PIECE.finditer(f):
        out.append((f[last:m.start()], "兼" if "兼" in m.group(0) or "并" in m.group(0) else "、"))
        last = m.end()
    out.append((f[last:], ""))
    res = []
    for i, (p, _) in enumerate(out):
        p = p.strip(" 　,，")
        if p:
            res.append((p, out[i - 1][1] if i else ""))
    return res


def _reclassify(d, org, lp):
    toks = lp.tokens(org)
    hint = TITLES.get(d["position_title"], (None, None))[1]
    d["position_org"] = org
    d["position_sys"], d["position_sys_rule"] = classify_sys(org, d["position_title"], hint, toks)
    if d["position_sys"] is None and hint:
        d["position_sys"], d["position_sys_rule"] = hint, f"title:{d['position_title']}"
    d["tokens"] = toks
    if toks:
        d["position_level"], d["position_level_source"] = toks[-1]["level"], "inherited"
    if d["position_sys"] == "村级组织":
        d["position_level"], d["position_level_source"] = "村社区", "sys"
    if d["position_sys"] == "无公职":
        d["position_level"] = d["position_rank"] = None


def parse_occupation(occupation_raw: str, lp: LevelParser):
    """-> list of position dicts in description order (position_seq assigned)."""
    rows = []
    last = None     # last row with an organisation (any fragment)
    pos = 0
    for frag in split_fragments(occupation_raw):
        start = occupation_raw.find(frag, pos)
        if start < 0:
            start = pos
        pos = start + len(frag)
        if not is_position_fragment(frag):
            continue
        parsed = []
        for pc, sep in split_pieces(frag):
            if not is_position_fragment(pc):          # 中共党员、政协委员
                continue
            d = parse_piece(pc, lp)
            d["raw_start"], d["raw_end"], d["sep"] = start, start + len(frag), sep
            parsed.append(d)
        # backward organisation inheritance: "党组书记、局长", "教师兼出纳", "党委**、**", "经理、工会主席"
        for j, d in enumerate(parsed):
            if d["is_status"] or d["position_title"] in NO_INHERIT_TITLES:
                continue
            prev = parsed[j - 1] if j else last
            if prev is None or not prev["position_org"]:
                continue
            titled = bool(d["position_title"]) or d["position_title_masked"]
            if titled and not d["position_org"]:
                _reclassify(d, prev["position_org"], lp)
                if j:
                    d["position_level"], d["position_level_source"], d["place_str"] = prev["position_level"], prev["position_level_source"], prev["place_str"]
                if d["position_time"] == "不明":
                    d["position_time"] = prev["position_time"]
                d["position_sys_rule"] = (d["position_sys_rule"] or "") + (";org_inherited" if j else ";org_inherited_xfrag")
                d["parsed"] = True
            elif j and titled and d["position_org"] in ATTACHED:
                _reclassify(d, prev["position_org"] + d["position_org"], lp)
                d["position_level"], d["position_level_source"], d["place_str"] = prev["position_level"], prev["position_level_source"], prev["place_str"]
                d["position_sys_rule"] = (d["position_sys_rule"] or "") + ";host_inherited"
            elif j and d["position_org"] and len(d["position_org"]) <= 4 and not d["tokens"] and not RE_KW.search(d["position_org"]):
                # 副主任兼妇女主任 / 工会主席兼后勤主任: a sub-unit of the previous organisation
                _reclassify(d, prev["position_org"] + d["position_org"], lp)
                d["position_level"], d["position_level_source"], d["place_str"] = prev["position_level"], prev["position_level_source"], prev["place_str"]
                d["position_sys_rule"] = (d["position_sys_rule"] or "") + ";subunit_inherited"
            elif j and d["position_org"]:
                bare_only = all(not t["name"] and not t["masked"] for t in d["tokens"])
                if prev["position_level"] and (not d["tokens"] or (bare_only and d["position_sys"] != "村级组织")):
                    d["position_level"], d["position_level_source"] = prev["position_level"], "inherited"
                    if not d["place_str"]:
                        d["place_str"] = prev["place_str"]
                if d["position_sys"] is None and not d["is_status"] and prev["position_sys"] and not d["tokens"]:
                    d["position_sys"], d["position_sys_rule"] = prev["position_sys"], (d["position_sys_rule"] or "") + ";sys_inherited"
        for j in range(1, len(parsed)):                 # 现任A兼B / 案发前系A、B: the time marker covers the list
            if parsed[j]["position_time"] == "不明" and parsed[j - 1]["position_time"] != "不明" and not parsed[j]["is_status"]:
                parsed[j]["position_time"] = parsed[j - 1]["position_time"]
        # forward title inheritance: "A公司、B公司实际控制人"
        for j in range(len(parsed) - 2, -1, -1):
            d, nxt = parsed[j], parsed[j + 1]
            if d["position_title"] is None and not d["position_title_masked"] and d["position_org"] and not d["is_status"] \
                    and nxt["position_title"] and nxt["position_org"] and not nxt["is_status"] and nxt["position_title"] not in TITLE_LEVEL:
                d["position_title"] = nxt["position_title"]
                d["position_rank"], d["position_rank_rule"] = nxt["position_rank"], nxt["position_rank_rule"]
                d["position_sys_rule"] = (d["position_sys_rule"] or "") + ";title_inherited"
                if d["position_sys"] in (None, "企业") and nxt["position_sys"] not in (None, "企业"):
                    d["position_sys"] = nxt["position_sys"]
                if d["position_time"] == "不明":
                    d["position_time"] = nxt["position_time"]
                d["parsed"] = True
        for d in parsed:
            if d["position_org"]:
                last = d
            rows.append(d)
    for i, d in enumerate(rows):
        d["position_seq"] = i
    return rows


def choose_offense_time(rows):
    """Marks is_offense_time on exactly one row (plan §5 时间性): 案发时 > 原任 > 不明 > 现任, then first
    described; status rows (农民, 无业 …) only when nothing else is parsed. time_ambiguous when the choice
    is an unmarked row among several real positions."""
    for d in rows:
        d["is_offense_time"] = False
        d["time_ambiguous"] = False
    if not rows:
        return
    order = {"案发时": 0, "原任": 1, "不明": 2, "现任": 3}
    jobs = [d for d in rows if d["parsed"] and not d["is_status"] and d["position_title"] not in ("人大代表", "政协委员", "村民代表")]
    real = jobs or [d for d in rows if d["parsed"] and not d["is_status"]]
    cands = real or [d for d in rows if d["parsed"]] or rows
    best = min(cands, key=lambda d: (order[d["position_time"]], d["position_seq"]))
    best["is_offense_time"] = True
    if len(real) > 1 and best["position_time"] == "不明":
        for d in rows:
            d["time_ambiguous"] = True


if __name__ == "__main__":
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from m1_regions import RegionIndex
    lp = LevelParser(RegionIndex())
    tests = sys.argv[1:] or [
        "原任广州市公安局番禺区分局大龙派出所民警", "原任林州市合涧镇小付街村党支部书记；原林州市合涧镇人大代表",
        "村人口主任兼出纳员", "原任凉城县县委副书记；内蒙古自治区乌兰察布市组织部工作人员", "农民；原蒙阴县**办事处**村村委委员兼会计",
        "城镇居民；原系岳池县**局党组书记、局长；广安市政协、岳池县政协委员", "系万载县**有限公司、江西**有限公司、**有限公司、**有限公司实际控制人",
        "靖江市**局原副局长", "原任霍尔果斯市人民政府副市长(副县级)", "济南市长清区**街道办事处社区卫生服务中心工作人员",
        "中共张家港市委原常委", "系黑龙江省富锦市**镇**村农民", "曾用名雷某某；原台安县**商店店员", "原四川省江安县**科学技术局（以下简称县经商局）局长；中国人民政治协商会议**县委员会秘书长",
        "陕西西安人", "非人大代表、政协委员", "兰州市**区住房和城乡建设局副局长", "在洛阳**置业有限公司工作；任董事长兼法定代表人",
        "原河南省高级人民法院监察局副局长（副处级）", "合肥市**监狱**科科长", "中国农业发展银行友谊县支行信贷员", "原**公司**",
        "原大冶市金山店镇党委**、**", "原攀枝花**有限公司经理、攀枝花**责任公司党总支书记、工会主席", "**县**镇挂职副镇长兼**村党总支书记",
        "哈密公路管理局**收费站收费员", "原合肥市包河区**街道**社居委主任", "职务**", "系原伊春市**中心**一科**", "案发前系**餐饮部**、**长",
    ]
    for t in tests:
        rows = parse_occupation(t, lp)
        choose_offense_time(rows)
        print("##", t)
        for d in rows:
            print("   ", d["position_seq"], d["position_time"], "*" if d["is_offense_time"] else " ", d["position_sys"], d["position_level"],
                  d["position_rank"], "|", d["position_title"], "|", d["position_org"], "|", d["place_str"], "|", d["position_sys_rule"], d["position_level_source"])
