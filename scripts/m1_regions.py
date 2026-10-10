#!/usr/bin/env python3
"""
M1 — resolve a procuratorate to the administrative-division code table (ref/region_codes.csv).

Two inputs per document, used in this order:
  1. the procuratorate name printed at the top of the document ("深圳市罗湖区人民检察院",
     "河北省蠡县人民检察院", "新疆维吾尔自治区人民检察院喀什分院");
  2. the crawler's prov / muni / county columns, which are dirty ("广东" vs "广东省", "内蒙",
     "上海市浦东新区" in the prov column, "NA", "第二市区检察院") and are only used to supply the
     province when the name omits it, or as a full fallback when the name is missing.

Only codes valid at some point in 2014–2019 are indexed (document era), so a 2015 document from
哈密地区 and a 2017 one from 哈密市 both resolve. Decoding ID-card prefixes is NOT done here — that is a
join on the full time-varying table at analysis time.
"""
import csv
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REGION_CSV = ROOT / "ref" / "region_codes.csv"

ERA_FROM, ERA_TO = 2014, 2019
PROV_ALIASES = {"内蒙": "内蒙古自治区", "内蒙古": "内蒙古自治区", "新疆": "新疆维吾尔自治区", "广西": "广西壮族自治区",
                "宁夏": "宁夏回族自治区", "西藏": "西藏自治区", "兵团": "新疆维吾尔自治区", "新疆生产建设兵团": "新疆维吾尔自治区"}
ETHNIC_GROUPS = ("蒙古|回|藏|维吾尔|苗|彝|壮|布依|朝鲜|满|侗|瑶|白|土家|哈尼|哈萨克|傣|黎|傈僳|佤|畲|高山|拉祜|水|东乡|纳西|景颇|柯尔克孜|土|"
                 "达斡尔|仫佬|羌|布朗|撒拉|毛南|仡佬|锡伯|阿昌|普米|塔吉克|怒|乌孜别克|俄罗斯|鄂温克|德昂|保安|裕固|京|塔塔尔|独龙|鄂伦春|赫哲|"
                 "门巴|珞巴|基诺|各")
# the base name is kept intact: 贡山独龙族怒族自治县 -> 贡山, 黔东南苗族侗族自治州 -> 黔东南 (a greedy [一-龥]{1,4}族 ate the base name)
RE_ETHNIC_SUFFIX = re.compile(r"((?:(?:" + ETHNIC_GROUPS + r")族)+)?(自治州|自治县|自治旗|自治区)$")
RE_SPECIAL = [
    (re.compile(r"铁路"), "railway"), (re.compile(r"林业|林区"), "forestry"), (re.compile(r"农垦|垦区"), "agri"),
    (re.compile(r"军事"), "military"), (re.compile(r"监狱"), "prison"), (re.compile(r"油田"), "oilfield"),
    (re.compile(r"矿区|矿务局"), "mining"), (re.compile(r"开发区|高新区|经开区|工业园区|高新技术"), "devzone"),
    (re.compile(r"兵团"), "corps"),
]
RE_STRIP_PROC = re.compile(r"(人民检察院|检察院|检察分院|分院|检察字|院)$")


def _add(table, key, name, code):
    d = table.setdefault(key, {})
    lst = d.setdefault(name, [])
    if code not in lst:
        lst.append(code)


def _short(name: str, level: str):
    """Short form used in practice: 河北省->河北, 德宏傣族景颇族自治州->德宏, 蠡县->蠡 (dropped: too short)."""
    n = name
    if level == "prov":
        n = re.sub(r"(壮族|回族|维吾尔)?自治区$|特别行政区$|省$|市$", "", n)
    elif level == "city":
        n = RE_ETHNIC_SUFFIX.sub("", n)
        n = re.sub(r"(市|地区|盟)$", "", n)
    else:
        n = RE_ETHNIC_SUFFIX.sub("", n)
        n = re.sub(r"(市|区|县|旗|林区|特区|新区)$", "", n)
    return n if len(n) >= 2 and n != name else None


class RegionIndex:
    def __init__(self, path=REGION_CSV):
        rows = list(csv.DictReader(open(path, encoding="utf-8-sig")))
        self.rows = {}
        era = []
        for r in rows:
            if r["source"] == "workbook_unofficial":
                continue
            yf = int(r["year_from"]) if r["year_from"] else None
            yt = int(r["year_to"]) if r["year_to"] else None
            if (yf is not None and yf > ERA_TO) or (yt is not None and yt <= ERA_FROM):
                continue
            r = dict(r, yf=yf, yt=yt)
            era.append(r)
            self.rows[r["code"]] = r  # later rows (newer windows) overwrite; same code -> same place
        self.prov = {}        # name/short -> code
        # full names and short names are kept apart: a full-name match always beats a short-name
        # match ("衡阳县" is the county 衡阳县, not the city 衡阳市 + "县")
        self.city_full, self.city_short = {}, {}        # prov_code -> {name -> [code]}
        self.county_full, self.county_short = {}, {}    # prov_code -> {name -> [code]}
        self.cbc_full, self.cbc_short = {}, {}          # city_code -> {name -> [code]}
        self.any_city, self.any_county = {}, {}         # full name -> set(code), all provinces (no-hint fallback)
        for r in era:
            lvl = r["level"]
            s = _short(r["name"], lvl)
            if lvl == "prov":
                self.prov.setdefault(r["name"], r["code"])
                if s:
                    self.prov.setdefault(s, r["code"])
            elif lvl == "city":
                if r["name"] in ("市辖区", "县", "省直辖县级行政区划", "自治区直辖县级行政区划"):
                    continue
                _add(self.city_full, r["prov_code"], r["name"], r["code"])
                if s:
                    _add(self.city_short, r["prov_code"], s, r["code"])
                self.any_city.setdefault(r["name"], set()).add(r["code"])
            else:
                if r["name"] in ("市辖区", "县"):
                    continue
                _add(self.county_full, r["prov_code"], r["name"], r["code"])
                if s:
                    _add(self.county_short, r["prov_code"], s, r["code"])
                self.any_county.setdefault(r["name"], set()).add(r["code"])
                if r["city_code"]:
                    _add(self.cbc_full, r["city_code"], r["name"], r["code"])
                    if s:
                        _add(self.cbc_short, r["city_code"], s, r["code"])
        self._prov_full = {r["name"] for r in era if r["level"] == "prov"}
        for a, full in PROV_ALIASES.items():
            if full in self.prov:
                self.prov.setdefault(a, self.prov[full])
        self._prov_keys = sorted(self.prov, key=len, reverse=True)

    # ---- helpers
    def _prefix_match(self, s: str, table: dict):
        """Longest key of `table` that is a prefix of s. Returns (key, codes, rest) or None."""
        best = None
        for k in table:
            if s.startswith(k) and (best is None or len(k) > len(best)):
                best = k
        if best is None:
            return None
        return best, table[best], s[len(best):]

    def norm_prov(self, s: str):
        """Province code for a dirty province string ('广东', '广东省', '上海市浦东新区', '内蒙')."""
        if not s:
            return None
        s = re.sub(r"\s", "", s)
        for k in self._prov_keys:
            if s.startswith(k):
                return self.prov[k]
        return None

    def _match_below_prov(self, rest: str, prov_code: str):
        """City / county inside one province. Full names first, then short names.
        Returns (city_code, county_code, ambiguous)."""
        city_code = county_code = None
        ambiguous = False
        for table in (self.city_full, self.city_short):
            cm = self._prefix_match(rest, table.get(prov_code, {}))
            if not cm:
                continue
            codes, rest2 = cm[1], cm[2]
            # "衡阳县": the short city name 衡阳 matched but the full county name is the better reading
            if table is self.city_short and self._prefix_match(rest, self.county_full.get(prov_code, {})):
                break
            city_code, ambiguous = codes[0], len(codes) > 1
            rest = rest2
            if rest:
                for ct in (self.cbc_full, self.cbc_short):
                    km = self._prefix_match(rest, ct.get(city_code, {}))
                    if km:
                        county_code = km[1][0]; ambiguous = ambiguous or len(km[1]) > 1; rest = km[2]; break
            break
        if county_code is None and rest:
            for table in (self.county_full, self.county_short):
                km = self._prefix_match(rest, table.get(prov_code, {}))
                if km:
                    codes = km[1]
                    county_code, ambiguous = codes[0], ambiguous or len(codes) > 1
                    if city_code is None:
                        city_code = self.rows[county_code]["city_code"] or None
                    break
        return city_code, county_code, ambiguous

    def resolve_location(self, loc: str, prov_hint: str = None):
        """Resolve '深圳市罗湖区' / '河北省蠡县' / '浦东新区' / '吉林市船营区' to
        (prov_code, city_code, county_code, ambiguous)."""
        loc = re.sub(r"\s", "", loc or "")
        if not loc:
            return prov_hint, None, None, False
        # 1. with a province hint, try the string directly inside that province (handles 吉林市 / 海南州
        #    whose names collide with province short names) — unless the string opens with a full
        #    province name (吉林省…)
        opens_with_full_prov = any(loc.startswith(k) for k in self._prov_full)
        if prov_hint and not opens_with_full_prov:
            cc, kc, amb = self._match_below_prov(loc, prov_hint)
            if cc or kc:
                return prov_hint, cc, kc, amb
        # 2. province named in the string
        m = self._prefix_match(loc, self.prov)
        if m:
            prov_code, rest = m[1], m[2]
            if not rest:
                return prov_code, None, None, False
            cc, kc, amb = self._match_below_prov(rest, prov_code)
            return prov_code, cc, kc, amb
        # 3. no usable province: unique full city / county name anywhere in the country
        for table in (self.any_city, self.any_county):
            best = None
            for k in table:
                if loc.startswith(k) and (best is None or len(k) > len(best)):
                    best = k
            if best and len(table[best]) == 1:
                code = next(iter(table[best]))
                r = self.rows[code]
                if table is self.any_city:
                    cc, kc, amb = self._match_below_prov(loc, r["prov_code"])
                    return r["prov_code"], cc or code, kc, amb
                return r["prov_code"], r["city_code"] or None, code, False
        return prov_hint, None, None, False

    def resolve_proc(self, proc_name: str, prov_col: str = None, muni_col: str = None, county_col: str = None):
        """Full resolution for one document. Returns a dict of region_* and proc_* fields."""
        out = dict(region_code=None, region_level=None, prov_code=None, prov_std=None, city_code=None, muni_std=None,
                   county_code=None, county_std=None, region_source=None, region_ambiguous=False,
                   proc_special=None, proc_level=None)
        prov_hint = self.norm_prov(prov_col)
        name = re.sub(r"\s", "", proc_name or "")
        special = next((t for rx, t in RE_SPECIAL if rx.search(name)), None)
        out["proc_special"] = special
        pc = cc = kc = None
        amb = False
        src = None
        if name:
            if name.startswith("最高"):
                out["proc_level"] = "supreme"; return out
            # split "X人民检察院Y分院" into location X and suffix Y
            m = re.match(r"^(.*?)(?:人民检察院|检察院)(.*)$", name)
            loc, suffix = (m.group(1), m.group(2)) if m else (name, "")
            loc = RE_STRIP_PROC.sub("", loc)
            is_branch = "分院" in suffix or "分院" in name
            pc, cc, kc, amb = self.resolve_location(loc, prov_hint)
            if loc and pc is None:
                # the column gave no province; the name alone did not resolve either
                pass
            if loc and cc is None and kc is None and not self._prefix_match(loc, self.prov) and not is_branch:
                # a name that is not a territorial division (建三江人民检察院): province from the column
                # only, level unknown
                out["proc_level"] = "special" if special else "unresolved"
            if is_branch and suffix and cc is None and pc:
                sfx = re.sub(r"(铁路运输|铁路|林区|农垦|垦区|分院|检察)", "", suffix)
                if sfx:
                    _, cc2, kc2, amb2 = self.resolve_location(sfx, pc)
                    cc, kc, amb = cc2, kc2, amb or amb2
            if special == "railway" and kc is None:
                # 广州铁路运输检察院 -> the city named before 铁路
                m2 = re.match(r"^(.*?)铁路", loc)
                if m2 and m2.group(1):
                    p2, c2, k2, a2 = self.resolve_location(m2.group(1), pc or prov_hint)
                    pc, cc, kc = pc or p2, cc or c2, kc or k2
            src = "proc_name"
            if is_branch:
                out["proc_level"] = "branch"
        if pc is None and prov_hint:
            # columns fallback
            pc = prov_hint
            src = "columns"
            if muni_col and muni_col != "NA":
                _, cc, kc, amb = self.resolve_location(re.sub(r"检察院|分院", "", muni_col), pc)
            if county_col and county_col != "NA":
                _, cc2, kc2, amb2 = self.resolve_location(re.sub(r"检察院|院$", "", county_col), pc)
                cc, kc, amb = cc or cc2, kc or kc2, amb or amb2
        if pc is None:
            out["proc_level"] = "special" if special else None
            return out
        out.update(prov_code=pc, prov_std=self.rows[pc]["name"], region_source=src, region_ambiguous=amb)
        if cc and cc in self.rows:
            out.update(city_code=cc, muni_std=self.rows[cc]["name"])
        if kc and kc in self.rows:
            out.update(county_code=kc, county_std=self.rows[kc]["name"])
            if out["muni_std"] is None and self.rows[kc]["city_name"]:
                out["muni_std"] = self.rows[kc]["city_name"]
        if kc:
            out.update(region_code=kc, region_level="county")
        elif cc:
            out.update(region_code=cc, region_level="city")
        else:
            out.update(region_code=pc, region_level="prov")
        if out["proc_level"] is None:
            if special:
                out["proc_level"] = "special"
            elif kc or re.search(r"第[一二三四五六]市区", name):   # 东莞/中山 district offices are basic-level
                out["proc_level"] = "basic"
            elif cc:
                out["proc_level"] = "municipal"
            else:
                out["proc_level"] = "provincial"
        return out


if __name__ == "__main__":
    ix = RegionIndex()
    for n, p in [("深圳市罗湖区人民检察院", "广东"), ("河北省蠡县人民检察院", "河北"), ("浙江省三门县人民检察院", "浙江"),
                 ("秦皇岛市海港区人民检察院", "河北"), ("云南省蒙自市人民检察院", "云南"), ("佛山市顺德区人民检察院", "广东省"),
                 ("上海市浦东新区人民检察院", "上海市浦东新区"), ("新疆维吾尔自治区人民检察院喀什分院", "新疆"),
                 ("天津市人民检察院第一分院", "天津"), ("广州铁路运输检察院", "广东"), ("德宏傣族景颇族自治州芒市人民检察院", "云南"),
                 ("蒙自市人民检察院", "云南"), ("北京市朝阳区人民检察院", "北京市朝阳区"), ("哈密地区人民检察院", "新疆"),
                 ("山西省人民检察院", "山西"), ("晋城市城区人民检察院", "山西"), ("城区人民检察院", "山西"),
                 ("黑龙江省人民检察院农垦分院", "黑龙江"), ("建三江人民检察院", "黑龙江"), ("内蒙古自治区兴安盟人民检察院", "内蒙"),
                 ("衡阳县人民检察院", "湖南"), ("吉林市船营区人民检察院", "吉林"), ("黄石港区人民检察院", "湖北省"),
                 ("喀什市人民检察院", "新疆"), ("衡阳市人民检察院", "衡阳市"), ("濮阳县人民检察院", "河南省"),
                 ("大连经济技术开发区人民检察院", "辽宁"), ("海南省儋州市人民检察院", "海南"), ("广东省东莞市第三市区人民检察院", "广东"),
                 ("海南藏族自治州人民检察院", "青海"), ("吉林省人民检察院", "吉林"), ("湖北省武汉东湖新技术开发区人民检察院", "湖北")]:
        r = ix.resolve_proc(n, p)
        print(f"{n:<28} {p:<10} -> {r['prov_std']} / {r['muni_std']} / {r['county_std']}  {r['region_code']} {r['region_level']} level={r['proc_level']} special={r['proc_special']} amb={r['region_ambiguous']}")
