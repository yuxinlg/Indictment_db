#!/usr/bin/env python3
"""Regression tests for the M1 rule library on synthetic snippets. Run: python3 scripts/test_m1_rules.py"""
import sys
import unittest
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import m1_rules as R  # noqa: E402


def doc(*segs):
    return "\t".join(segs)


class Header(unittest.TestCase):
    def test_basic(self):
        t = doc("深圳市罗湖区人民检察院", "起 诉 书", " ", "深罗检刑诉〔2015〕1467号", "被告人曾某某，男，1989年**月**日出生，汉族。", "本案由…侦查终结")
        h = R.parse_header(t, R.split_segments(t), "")
        self.assertEqual(h["proc_name"], "深圳市罗湖区人民检察院")
        self.assertEqual(h["doc_type"], "起诉书")
        c = h["case_no"]
        self.assertEqual((c["case_no_proc"], c["case_no_series"], c["case_no_year"], c["case_no_seq"]), ("深罗", "刑诉", 2015, 1467))

    def test_bracket_variants_and_第(self):
        for s in ("华检刑诉字（2016）第19号", "鄂云检刑诉﹝2016﹞55号", "京朝检公诉刑诉[2017]3号"):
            t = doc("某县人民检察院", "起诉书", s, "被告人张某，男，1980年**月**日出生。", "本案由")
            h = R.parse_header(t, R.split_segments(t), "")
            self.assertIsNotNone(h["case_no"], s)
            self.assertEqual(h["case_no"]["case_no_year"], 2016 if "2016" in s else 2017)

    def test_tabless_guangxi_header(self):
        t = " \xa0\xa0 那坡县人民检察院 起 诉 书 \xa0\xa0\xa0 那检刑诉〔2015〕74号\xa0\xa0 \xa0\xa0\xa0 被告人郭某某，男， 1979年**月**日出生于广西壮族自治区那坡县，身份证号码4526271979********，汉族。\t本案由那坡县公安局侦查终结"
        t = R.normalise(t)
        h = R.parse_header(t, R.split_segments(t), "")
        self.assertEqual(h["proc_name"], "那坡县人民检察院")
        self.assertEqual(h["case_no"]["case_no_seq"], 74)
        blocks, fl = R.find_blocks(t, h["header_end"], 0)
        self.assertEqual([b["name_raw"] for b in blocks], ["郭某某"])

    def test_doc_type_from_title_and_body(self):
        t = doc("某县人民检察院", "", "被不起诉人王某，男。", "本案由", "本院认为…决定对王某不起诉。")
        h = R.parse_header(t, R.split_segments(t), "不起诉决定书（王某盗窃案）")
        self.assertEqual((h["doc_type"], h["doc_type_source"]), ("不起诉决定书", "title"))


class Blocks(unittest.TestCase):
    def test_continuation_paragraph_is_merged(self):
        t = doc("X县人民检察院", "起诉书", "X检刑诉〔2016〕1号",
                "被告人谢某某，男，1985年**月**日生，身份证号码3202811985********，汉族，初中文化，无业，住江阴市**村。",
                "被告人时某某，男，1990年**月**日生，身份证号码3422251990********，汉族，小学文化，无业，住江阴市**幢。",
                "被告人谢某某因涉嫌开设赌场罪，于2016年4月6日被江阴市公安局取保候审。",
                "被告人时某某因涉嫌开设赌场罪，于2016年4月7日被江阴市公安局刑事拘留，同年5月10日经本院批准逮捕，次日由该局执行逮捕。",
                "本案由江阴市公安局侦查终结")
        h = R.parse_header(t, R.split_segments(t), "")
        blocks, fl = R.find_blocks(t, h["header_end"], 0)
        self.assertEqual([b["name_raw"] for b in blocks], ["谢某某", "时某某"])
        self.assertEqual([b["n_parts"] for b in blocks], [2, 2])
        for b in blocks:
            b["name"] = b["name_raw"]
        p0 = R.parse_person_block(blocks[0]); p1 = R.parse_person_block(blocks[1])
        self.assertEqual((p0["gender"], p0["birth_year"], p0["id_prefix"]), ("男", 1985, "320281"))
        self.assertEqual(p0["date_bail"], date(2016, 4, 6))
        self.assertEqual((p1["date_detain"], p1["date_arrest_approved"], p1["date_arrest"]), (date(2016, 4, 7), date(2016, 5, 10), date(2016, 5, 11)))
        self.assertEqual(p1["suspect_charges"][0][0], "开设赌场罪")

    def test_label_without_人_and_underscore(self):
        t = doc("某县人民检察院", "起诉书", "_被告朱**，男，19**年*月18日出生，居民身份证号码362334*******7537，汉族，初中文化，无业。", "本案由")
        h = R.parse_header(t, R.split_segments(t), "")
        blocks, fl = R.find_blocks(t, h["header_end"], 0)
        self.assertEqual([b["name_raw"] for b in blocks], ["朱**"])

    def test_unlabelled_identity_line(self):
        t = doc("某县人民检察院", "起诉书", "瓦检公诉刑诉〔2015〕16号", "林某某，男，****年**月**日出生，居民身份证号码210222********8113，汉族。", "本案由")
        h = R.parse_header(t, R.split_segments(t), "")
        blocks, fl = R.find_blocks(t, h["header_end"], 0)
        self.assertEqual([b["name_raw"] for b in blocks], ["林某某"])

    def test_prior_conviction_dates_ignored_and_birth_not_confused(self):
        t = "被告人张某某，女，****年**月**日出生，身份证号码360122********3023，汉族，初中文化程度，无业，住本市**小区。2016年4月1日因犯盗窃罪被南昌市**区人民法院判处有期徒刑十个月，缓刑一年。因本案，于2018年4月19日被南昌市公安局刑事拘留，同年5月2日被逮捕。"
        b = dict(label="被告人", name_raw="张某某", name="张某某", start=0, end=len(t), text=t, name_end=6, n_parts=1)
        p = R.parse_person_block(b)
        self.assertIsNone(p["birth_year"])
        self.assertEqual(p["date_detain"], date(2018, 4, 19))
        self.assertEqual(p["date_arrest"], date(2018, 5, 2))
        self.assertEqual(p["occupation_raw"], "无业")

    def test_fields(self):
        t = "被告人任某甲，男，1967年**月**日出生，身份证号码：1303211967********，汉族，大专文化，河北省青龙满族自治县**派出所所长，群众。经常居住地：河北省青龙满族自治县**镇（户口址：河北省青龙满族自治县**镇**街**号）。2016年4月6日因涉嫌受贿罪经本院决定被刑事拘留，同年4月22日经秦皇岛市人民检察院决定被依法执行逮捕。"
        b = dict(label="被告人", name_raw="任某甲", name="任某甲", start=0, end=len(t), text=t, name_end=6, n_parts=1)
        p = R.parse_person_block(b)
        self.assertEqual(p["edu_level"], "college")
        self.assertEqual(p["occupation_raw"], "河北省青龙满族自治县**派出所所长")
        self.assertEqual(p["politics"], "群众")
        self.assertEqual(p["date_detain"], date(2016, 4, 6))
        self.assertEqual(p["date_arrest_approved"], date(2016, 4, 22))   # 经…检察院决定被执行逮捕: decided and executed the same day
        self.assertEqual(p["date_arrest"], date(2016, 4, 22))
        self.assertTrue(p["hukou_text"].startswith("户口址"))


class Charges(unittest.TestCase):
    def test_opinion_names_and_yingyi(self):
        t = "本院认为，被告人李某甲、赵某某以非法占有为目的，盗窃他人财物，应当以盗窃罪追究其刑事责任。被告人李某乙明知是犯罪所得而予以收购，应当以掩饰、隐瞒犯罪所得罪追究其刑事责任。"
        out = R.charges_from_opinion(t, 0, len(t))
        blocks = [dict(name=n) for n in ("李某甲", "赵某某", "李某乙")]
        self.assertEqual([(R.match_names(n, blocks), raw) for n, raw, pos, pat in out], [([0, 1], "盗窃罪"), ([2], "掩饰、隐瞒犯罪所得罪")])

    def test_yingyi_variants(self):
        for s, exp in (("应当受贿罪追究", "受贿罪"), ("应该以贩卖毒品罪追究", "贩卖毒品罪"), ("应当分别以抢劫罪、盗窃罪追究", "抢劫罪、盗窃罪"), ("应以故意伤害罪追究", "故意伤害罪")):
            m = R.RE_YINGYI.search(s)
            self.assertEqual(m.group(1), exp, s)

    def test_name_chain_stops_at_comma(self):
        t = "本院认为，被告人曾某某无视国家法律，违反国家对毒品的管理法规，应当以贩卖毒品罪追究其刑事责任。"
        out = R.charges_from_opinion(t, 0, len(t))
        self.assertEqual(out[0][0], ["曾某某无视国家法律"])
        blocks = [dict(name="曾某某")]
        self.assertEqual(R.match_names(out[0][0], blocks), [0])

    def test_referral(self):
        t = "本案由蠡县公安局侦查终结，以被告人李某甲、赵某某涉嫌盗窃罪、被告人李某乙涉嫌掩饰、隐瞒犯罪所得罪，于2017年9月12日向本院移送审查起诉。"
        out = R.charges_from_referral(t, 0, len(t))
        # 涉嫌 capture is non-greedy up to the first 罪 (same regex as the alias build): 掩饰、隐瞒犯罪 resolves by unique prefix
        self.assertEqual([(n, raw) for n, raw, pos in out], [(["李某甲", "赵某某"], "盗窃罪"), (["李某乙"], "掩饰、隐瞒犯罪")])


class Statutes(unittest.TestCase):
    def test_runs(self):
        t = "其行为触犯了《中华人民共和国刑法》第一百三十三条之一第一款第（二）项、第六十七条第三款、第一款之规定。根据《中华人民共和国刑事诉讼法》第一百七十二条的规定，提起公诉。刑法第三百0六条。"
        st = R.parse_statutes(t)
        got = [(s["law"], s["article"], s["sub_article"], s["paragraph"], s["item"]) for s in st]
        self.assertEqual(got, [("刑法", 133, 1, 1, 2), ("刑法", 67, None, 3, None), ("刑法", 67, None, 1, None), ("刑诉法", 172, None, None, None), ("刑法", 306, None, None, None)])

    def test_typo_law_names(self):
        for raw in ("中国人民共和国刑法", "中华人民共和刑法", "中华人民共和国刑罚", "中华人民共和国刑事诉讼", "中华人民共和国刑诉法"):
            self.assertIn(R.norm_law(raw), ("刑法", "刑诉法"), raw)
        self.assertEqual(R.norm_law("中华人民共和国刑法修正案（八）"), "other")


class Prosecutors(unittest.TestCase):
    def test_forms(self):
        t = doc("此致", "深圳市罗湖区人民法院", "检察员:马雪艳 陈艾奇   ", "2015年8月31日", "附：", "1.被告人现羁押在看守所。")
        self.assertEqual([(p["role"], p["name"]) for p in R.parse_prosecutors(t, 0)], [("检察员", "马雪艳"), ("检察员", "陈艾奇")])
        t = doc("此 致", "佛山市顺德区人民法院", "     检  察  员：李碧玉", "二〇一五年十一月二十七日")
        self.assertEqual([(p["role"], p["name"]) for p in R.parse_prosecutors(t, 0)], [("检察员", "李碧玉")])
        t = doc("此致", "福州市晋安区人民法院", "检　察　员　　何某某", "2016年1月1日")
        self.assertEqual([(p["role"], p["name"], p["is_masked"]) for p in R.parse_prosecutors(t, 0)], [("检察员", "何某某", True)])
        t = doc("此致", "某人民法院", "代理检察员：王某 书记员：李四", "2016年1月1日")
        self.assertEqual([(p["role"], p["name"]) for p in R.parse_prosecutors(t, 0)], [("代理检察员", "王某"), ("书记员", "李四")])

    def test_no_signature(self):
        t = doc("此致", "福建省松溪县人民法院", "本件与原本核对无异")
        self.assertEqual(R.parse_prosecutors(t, 0), [])


class Misc(unittest.TestCase):
    def test_nonpros(self):
        t = "本院认为，被不起诉人杨某某实施了《中华人民共和国刑法》第一百三十三条规定的行为，但犯罪情节轻微，依据《中华人民共和国刑法》第一百三十三条、第六十七条第一款及《中华人民共和国刑事诉讼法》第一百七十三条第二款的规定，决定对杨某某不起诉。被不起诉人如不服本决定，可以依据第一百七十七条申诉。"
        st = R.parse_statutes(t)
        self.assertEqual(R.nonpros_reason(t, 0, len(t), st)[:3], ("相对不起诉", 173, 2))
        t2 = "本院认为，依据《中华人民共和国刑事诉讼法》第一百七十五条第四款的规定，决定对张某不起诉。"
        self.assertEqual(R.nonpros_reason(t2, 0, len(t2), R.parse_statutes(t2))[0], "存疑不起诉")

    def test_minor(self):
        self.assertEqual(R.minor_status(2000, date(2017, 5, 1)), (17, "minor"))
        self.assertEqual(R.minor_status(1999, date(2017, 5, 1)), (18, "ambiguous"))
        self.assertEqual(R.minor_status(1990, date(2017, 5, 1)), (27, "adult"))
        self.assertEqual(R.minor_status(None, date(2017, 5, 1)), (None, "undetermined"))

    def test_facts_dates(self):
        t = "2009年3月，被告人任某甲向公司索取款项。2010年1月4日再次索取。2008年12月31日曾担任所长。"
        self.assertEqual(R.facts_dates(t, 0, len(t)), (date(2009, 3, 1), date(2008, 12, 31)))

    def test_custody(self):
        blocks = [dict(name="李某甲"), dict(name="赵某某"), dict(name="李某乙")]
        t = "附：\t1.被告人李某甲现押于蠡县看守所；被告人赵某某、李某乙\t现取保候审在其居所。\t2.案卷材料一册。"
        self.assertEqual(R.custody_at_filing(t, 0, blocks), ["custody", "bail", "bail"])
        self.assertEqual(R.custody_at_filing("附：1.被告人现在居住地候审。", 0, [dict(name="王某")]), ["bail"])

    def test_cn2int(self):
        for s, v in (("一百零三", 103), ("二十", 20), ("十二", 12), ("三百四十七", 347), ("三百0六", 306), ("十", 10), ("264", 264)):
            self.assertEqual(R.cn2int(s), v)


if __name__ == "__main__":
    unittest.main(verbosity=1)
