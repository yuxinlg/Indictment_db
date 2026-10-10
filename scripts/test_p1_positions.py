#!/usr/bin/env python3
"""Regression tests for the P1 position parser on synthetic strings. Run: python3 scripts/test_p1_positions.py"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import p1_positions as P  # noqa: E402
from m1_regions import RegionIndex  # noqa: E402

LP = P.LevelParser(RegionIndex())


def parse(s):
    rows = P.parse_occupation(s, LP)
    P.choose_offense_time(rows)
    return rows


def tup(d):
    return (d["position_sys"], d["position_level"], d["position_rank"], d["position_title"], d["position_org"])


class Fragments(unittest.TestCase):
    def test_non_positions_dropped(self):
        for s in ("曾用名雷某某", "绰号“老扁”", "捕前住四川省攀枝花市东区**路**号", "证件号码：H4*****(*)", "非人大代表、政协委员",
                  "汉族", "*族", "陕西西安人", "澳大利亚籍人", "粗识字", "系原案被害人", "副科级", "联系方式：189********", "中共党员"):
            self.assertFalse(P.is_position_fragment(s), s)
        for s in ("农民", "无业", "**", "原任**", "**公司法定代表人", "负责人", "工人"):
            self.assertTrue(P.is_position_fragment(s), s)

    def test_status_rows(self):
        r = parse("农民")
        self.assertEqual(len(r), 1)
        self.assertEqual((r[0]["position_sys"], r[0]["position_title"], r[0]["is_status"]), ("无公职", "农民", True))
        self.assertIsNone(r[0]["position_level"]); self.assertIsNone(r[0]["position_rank"])
        self.assertEqual(parse("个体工商户")[0]["position_sys"], "民营企业")
        self.assertEqual(parse("系个体")[0]["position_sys"], "民营企业")
        self.assertEqual(parse("个体经营建筑")[0]["position_sys"], "民营企业")
        self.assertEqual(parse("系黑龙江省富锦市**镇**村农民")[0]["position_sys"], "无公职")

    def test_masked_only(self):
        r = parse("职务**")
        self.assertEqual(len(r), 1)
        self.assertTrue(r[0]["position_title_masked"]); self.assertIsNone(r[0]["position_org"])
        r = parse("原任**")
        self.assertEqual((r[0]["position_time"], r[0]["position_title_masked"]), ("原任", True))


class Systems(unittest.TestCase):
    def test_plan_example(self):
        r = parse("原任广州市公安局番禺区分局大龙派出所民警")[0]
        self.assertEqual(tup(r), ("公检法司", "区", "一般人员", "民警", "广州市公安局番禺区分局大龙派出所"))
        self.assertEqual(r["position_time"], "原任")
        self.assertEqual(r["place_str"], "广州市")

    def test_village(self):
        self.assertEqual(tup(parse("原任海南省文昌市**镇**村委会党支部书记")[0])[:3], ("村级组织", "村社区", "正职"))
        self.assertEqual(tup(parse("凯里市**镇**村村委会原副主任")[0])[:3], ("村级组织", "村社区", "副职"))
        self.assertEqual(parse("凯里市**镇**村村委会原副主任")[0]["position_time"], "原任")
        self.assertEqual(tup(parse("村支书")[0])[:3], ("村级组织", "村社区", "正职"))
        self.assertEqual(tup(parse("原合肥市包河区**街道**社居委主任")[0])[:3], ("村级组织", "村社区", "正职"))
        self.assertEqual(tup(parse("**苏木**嘎查委员会嘎查达")[0])[:3], ("村级组织", "村社区", "正职"))
        self.assertEqual(tup(parse("系杭州市余杭区**街道**社区**组组长")[0])[:3], ("村级组织", "村社区", "正职"))
        self.assertEqual(tup(parse("北京市丰台区**乡**村民")[0])[:3], ("无公职", None, None))

    def test_village_institutions_are_not_village_orgs(self):
        self.assertEqual(parse("**镇**村小学教师")[0]["position_sys"], "事业单位")
        self.assertEqual(parse("**镇**村卫生室医生")[0]["position_sys"], "事业单位")
        self.assertEqual(tup(parse("济南市长清区**街道办事处社区卫生服务中心工作人员")[0])[:2], ("事业单位", "乡镇街道"))

    def test_party(self):
        self.assertEqual(tup(parse("原任凉城县县委副书记")[0])[:3], ("党委系统", "县", "副职"))
        self.assertEqual(tup(parse("中共张家港市委原常委")[0])[:3], ("党委系统", "县", "一般人员"))
        self.assertEqual(tup(parse("**镇党委书记")[0])[:3], ("党委系统", "乡镇街道", "正职"))
        self.assertEqual(parse("内蒙古自治区乌兰察布市组织部工作人员")[0]["position_sys"], "党委系统")
        # attached organs take their host's system
        self.assertEqual(parse("原系岳池县**局党组书记")[0]["position_sys"], "政府部门")
        self.assertEqual(parse("**公司党委书记")[0]["position_sys"], "企业")
        self.assertEqual(parse("**县公安局**分局局长")[0]["position_sys"], "公检法司")
        self.assertEqual(parse("**县税务局**分局局长")[0]["position_sys"], "政府部门")

    def test_people_congress(self):
        r = parse("中国人民政治协商会议**县委员会秘书长")[0]
        self.assertEqual(tup(r)[:3], ("人大政协", "县", "正职"))
        self.assertEqual(tup(parse("本届**县**人大代表")[0])[:3], ("人大政协", "县", "一般人员"))
        self.assertEqual(parse("现任南召县人大代表")[0]["position_time"], "现任")

    def test_government_and_pla(self):
        self.assertEqual(tup(parse("**县人民政府办公室副主任")[0])[:3], ("政府部门", "县", "副职"))
        self.assertEqual(tup(parse("**县民政局局长")[0])[:3], ("政府部门", "县", "正职"))
        self.assertEqual(tup(parse("兰州市**区住房和城乡建设局副局长")[0])[:3], ("政府部门", "区", "副职"))
        self.assertEqual(tup(parse("原任霍尔果斯市人民政府副市长(副县级)")[0])[:3], ("政府部门", "县", "副职"))
        self.assertEqual(tup(parse("**县**镇挂职副镇长")[0])[:3], ("政府部门", "乡镇街道", "副职"))
        self.assertEqual(tup(parse("合肥市**监狱**科科长")[0])[:3], ("公检法司", "市", "正职"))
        self.assertEqual(tup(parse("洮南市人民法院民事一庭审判员")[0])[:3], ("公检法司", "县", "一般人员"))
        self.assertEqual(tup(parse("德州市公安局交通警察支队武城大队**中队队员（公益岗民警）")[0])[:3], ("公检法司", "市", "一般人员"))

    def test_institutions_and_finance(self):
        self.assertEqual(tup(parse("中国农业发展银行友谊县支行信贷员")[0])[:3], ("金融机构", "县", "一般人员"))
        self.assertEqual(parse("原系中国农业银行股份有限公司*****街心分理处信贷员")[0]["position_sys"], "金融机构")
        self.assertEqual(tup(parse("原系常熟市**中心小学校长")[0])[:3], ("事业单位", "县", "正职"))
        self.assertEqual(tup(parse("系常州市**人民医院影像科副主任")[0])[:3], ("事业单位", "市", "副职"))
        self.assertEqual(parse("哈密公路管理局**收费站收费员")[0]["position_sys"], "事业单位")
        self.assertEqual(parse("小学教师")[0]["position_sys"], "事业单位")

    def test_firms(self):
        self.assertEqual(parse("中国**股份有限公司**销售分公司**经营部**加油站**")[0]["position_sys"], "国企")
        self.assertEqual(parse("国网河南省电力公司鹤壁供电公司********")[0]["position_sys"], "国企")
        self.assertEqual(parse("江苏**建设有限公司实际经营人")[0]["position_sys"], "民营企业")
        self.assertEqual(parse("系黑龙江省逊克县**店经营者")[0]["position_sys"], "民营企业")
        self.assertEqual(parse("原**公司**")[0]["position_sys"], "企业")          # resolved later by charge
        self.assertEqual(parse("原系成都铁路局机关服务所行管科协理（正科级）")[0]["position_sys"], "国企")
        # a place name containing a keyword (金山店镇) must not fire 店
        self.assertEqual(parse("原大冶市金山店镇党委**")[0]["position_sys"], "党委系统")


class Levels(unittest.TestCase):
    def test_county_level_city(self):
        self.assertEqual(parse("靖江市**局原副局长")[0]["position_level"], "县")
        self.assertEqual(parse("合肥市**监狱")[0]["position_level"], "市")
        self.assertEqual(parse("黑龙江省**监狱")[0]["position_level"], "省")
        self.assertEqual(parse("北京市**局")[0]["position_level"], "省")
        self.assertEqual(parse("**厅**处处长")[0]["position_level"], "省")

    def test_places(self):
        r = parse("内蒙古自治区兴安盟科尔沁右翼前旗**镇**嘎查**")[0]
        self.assertEqual((r["position_level"], r["place_str"]), ("村社区", "内蒙古自治区兴安盟科尔沁右翼前旗"))
        self.assertEqual(parse("新疆特克斯县**镇**村原村委会副主任")[0]["place_str"], "新疆特克斯县")
        self.assertEqual(parse("郸城县公安局城郊乡派出所协警")[0]["position_level"], "乡镇街道")
        self.assertEqual(parse("乌海市海南区**局监察员")[0]["place_str"], "乌海市海南区")
        self.assertEqual(parse("**市场监督管理局科员")[0]["position_level"], None)
        self.assertEqual(parse("**县市场监督管理局科员")[0]["position_level"], "县")
        self.assertEqual(parse("**县乡镇企业局局长")[0]["position_level"], "县")
        self.assertEqual(parse("**村镇银行行长")[0]["position_level"], None)
        self.assertEqual(parse("**市政府办公室主任")[0]["position_level"], "市")


class Multi(unittest.TestCase):
    def test_inherit_org_backward(self):
        r = parse("城镇居民；原系岳池县**局党组书记、局长；广安市政协、岳池县政协委员")
        self.assertEqual([d["position_title"] for d in r], ["城镇居民", "书记", "局长", "政协委员", "政协委员"])
        self.assertEqual(r[2]["position_org"], r[1]["position_org"])
        self.assertEqual(r[3]["position_sys"], "人大政协")
        self.assertTrue(r[1]["is_offense_time"]); self.assertFalse(r[0]["is_offense_time"])
        r = parse("峡江县第二中学教师兼出纳")
        self.assertEqual([tup(d)[:3] for d in r], [("事业单位", "县", "一般人员"), ("事业单位", "县", "一般人员")])
        r = parse("原攀枝花**有限公司经理、攀枝花**责任公司党总支书记、工会主席")
        self.assertEqual(len(r), 3)
        self.assertEqual(r[2]["position_org"], "攀枝花**责任公司党总支工会")

    def test_inherit_title_forward(self):
        r = parse("系万载县**有限公司、江西**有限公司、**有限公司实际控制人")
        self.assertEqual([d["position_title"] for d in r], ["实际控制人"] * 3)
        self.assertEqual({d["position_sys"] for d in r}, {"民营企业"})

    def test_work_at(self):
        r = parse("在洛阳**置业有限公司工作；任董事长兼法定代表人")
        self.assertEqual([d["position_title"] for d in r], [None, "董事长", "法定代表人"])
        self.assertEqual({d["position_org"] for d in r}, {"洛阳**置业有限公司"})

    def test_no_inherit_for_territorial_heads(self):
        r = parse("原任**县**局局长；现任县长")
        self.assertIsNone(r[1]["position_org"])
        self.assertEqual((r[1]["position_sys"], r[1]["position_level"]), ("政府部门", "县"))

    def test_offense_time(self):
        r = parse("案发前系辽宁省**研究所研究员；曾任辽宁省**研究所所长")
        self.assertEqual([d["is_offense_time"] for d in r], [True, False])
        r = parse("农民；原蒙阴县**办事处**村村委委员兼会计")
        self.assertEqual([d["is_offense_time"] for d in r], [False, True, False])
        r = parse("抚远县**乡人大代表；抚远县**乡**村**")     # a 人大代表 membership never beats a job
        self.assertEqual([d["is_offense_time"] for d in r], [False, True])
        self.assertFalse(any(d["time_ambiguous"] for d in r))
        r = parse("现任咸阳市渭城区**学校工会主席兼后勤主任")
        self.assertEqual([d["position_time"] for d in r], ["现任", "现任"])
        self.assertEqual(r[1]["position_sys"], "事业单位")
        r = parse("案发前曾任东**县教育局电教仪器站站长、电教馆馆长")
        self.assertEqual([d["position_time"] for d in r], ["案发时", "案发时"])
        self.assertEqual(r[1]["position_level"], "县")
        r = parse("原任**局局长；现任**局副局长")
        self.assertEqual([d["is_offense_time"] for d in r], [True, False])
        self.assertFalse(r[0]["time_ambiguous"])
        r = parse("系原伊春市**中心**一科**")
        self.assertEqual(r[0]["position_time"], "原任")
        r = parse("中国农业发展银行友谊县支行（以下简称友谊县农发行）原**")
        self.assertEqual((r[0]["position_time"], r[0]["position_title_masked"]), ("原任", True))

    def test_review_fixes(self):
        self.assertEqual(parse("原吐鲁番市高昌区计生委党组书记")[0]["position_sys"], "政府部门")
        self.assertEqual(parse("安徽**医院西药房工作人员")[0]["position_sys"], "事业单位")
        self.assertEqual(parse("垦利县**办事处**村民")[0]["position_sys"], "无公职")
        self.assertEqual(parse("永和县住房保障和城乡建设管理局副局长")[0]["position_level"], "县")
        self.assertEqual(parse("原威海市人力资源公共服务中心**科正科级职员")[0]["position_title"], "职员")
        r = parse("原绵竹市人民政府市长、市委副书记")
        self.assertEqual((r[1]["position_sys"], r[1]["position_level"]), ("党委系统", "县"))
        r = parse("曾任黑龙江省北安监狱**监区**分监区副分监区长")[0]
        self.assertEqual((r["position_sys"], r["position_level"], r["position_rank"]), ("公检法司", "省", "副职"))
        self.assertEqual(parse("私人包工头")[0]["position_sys"], "民营企业")
        self.assertEqual(parse("宜章县**乡**村原党支部书记")[0]["position_time"], "原任")
        self.assertEqual(parse("系黑龙江省松滨监狱八监区管教员（副主任科员；一级警员）")[0]["position_level"], "省")
        self.assertEqual(parse("武功县工业园区管理委员会一般工作人员")[0]["position_level"], "县")
        self.assertEqual(parse("系内蒙古自治区包头市达茂联合旗**局**所**")[0]["position_level"], "县")
        self.assertEqual(parse("原木垒县**镇**村委会副主任兼妇女主任")[1]["position_sys"], "村级组织")
        self.assertIsNone(parse("原系德清县***院**科主任")[0]["position_sys"])
        self.assertEqual(parse("甘肃某某国家级自然保护区管理局产业科科长")[0]["position_level"], None)
        self.assertEqual(parse("中共党员、政协遵义市汇川区委员会第四届政协委员")[0]["position_sys"], "人大政协")
        self.assertEqual(len(parse("中共党员、政协遵义市汇川区委员会第四届政协委员")), 1)

    def test_time_guard(self):
        self.assertEqual(parse("原平市**局局长")[0]["position_org"], "原平市**局")
        self.assertEqual(parse("任丘市**镇**村党支部书记")[0]["position_level"], "村社区")


class Ranks(unittest.TestCase):
    def test_rank(self):
        self.assertEqual(parse("**局副局长")[0]["position_rank"], "副职")
        self.assertEqual(parse("**局常务副局长")[0]["position_rank"], "副职")
        self.assertEqual(parse("**公司总经理助理")[0]["position_rank"], "一般人员")
        self.assertEqual(parse("**县**局主任科员")[0]["position_rank"], "一般人员")
        self.assertEqual(parse("**县**局副主任科员")[0]["position_rank"], "一般人员")
        self.assertEqual(parse("**医院主任医师")[0]["position_rank"], "一般人员")
        self.assertEqual(parse("**公司项目经理")[0]["position_rank"], "一般人员")
        self.assertEqual(parse("案发前系**餐饮部**、**长")[1]["position_rank"], "正职")
        self.assertEqual(parse("**局副**")[0]["position_rank"], "副职")
        self.assertEqual(parse("**局**员")[0]["position_rank"], "一般人员")
        self.assertEqual(parse("**收费站收费班班长")[0]["position_rank"], "正职")
        self.assertEqual(parse("**公司法定代表人")[0]["position_rank"], "正职")


if __name__ == "__main__":
    unittest.main(verbosity=1)
