"""X1-01 (W3-2): horticultural growth-rate/evidence inputs bound to the
official-statistics contract.

Reproduction targets from work/X1-scratch/targeted-results.json:
- cost.material.y1.item1.price_growth "0.37" accepted with source_refs=[]
- cost.overhead.y1.slot3.annual_growth "0.37" accepted with source_refs=[]
- cost.overhead.source_summary / cost.labor.wage_evidence_summary accepting
  "개인 블로그에서 가져온 상승률 37%" free text
"""
import copy
import json
from pathlib import Path
import sys
import unittest
from decimal import Decimal

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "skills/knuaf-doc/scripts"))
import gg_hort_workbook as h
import gg_price_assumptions as _pa

MAT = "cost.material.y1.item1.price_growth"
OVH = "cost.overhead.y1.slot3.annual_growth"
SUM_OVH = "cost.overhead.source_summary"
SUM_WAGE = "cost.labor.wage_evidence_summary"
SUM_MAT = "cost.material.source_summary"
QUOTE = "cost.material.y1.item1.unit_price_krw"
EVID = "cost.labor.y1.slot1.evidence"
MAT_SERIES = "official.kosis.farm_purchase.materials"
OVH_SERIES = "official.kosis.farm_purchase.expenses"
WAGE_SERIES = "official.minimumwage.hourly"


def _specs(*keys):
    allp = json.loads(
        (ROOT / "skills/knuaf-doc/references/hort-env-systems/"
         "finance-params.json").read_text())["parameters"]
    return {k: allp[k] for k in keys}


def _fact(key, value, refs="default", **extra):
    fact = {"id": "f-" + key, "field_id": "hort_env_systems.fin." + key,
            "answer_state": "provided", "value": value, "revision": 1,
            "source_refs": ([{"id": "s1", "revision": 1}]
                            if refs == "default" else refs)}
    fact.update(extra)
    return fact


def _project(facts, sources="default"):
    project = {"facts": {f["id"]: f for f in facts}}
    if sources == "default":
        project["sources"] = {
            "s1": {"revision": 1, "kind": "stat",
                   "source_type": "official_statistics",
                   "source": "KOSIS 농가판매및구입가격조사", "grade": 1}}
    elif sources is not None:
        project["sources"] = sources
    return project


def _assumption(source_id=MAT_SERIES, base_year=2020, unit="지수(2020=100)",
                start=2020, end=2025, **extra):
    a = {"source_id": source_id, "base_year": base_year, "unit": unit,
         "observation": {"start_year": start, "end_year": end}}
    a.update(extra)
    return a


def _cagr():
    return _pa.cagr(100.0, 129.8, 5)


def _student_series(sid="student.kosis.custom", roles=None, obs=None):
    return {"id": sid, "agency": "통계청", "agency_kind": "national_statistics",
            "statistic_name": "테스트 수열",
            "url": "https://kosis.kr/statHtml/statHtml.do?orgId=101&tblId=T",
            "evidence_locator": "테스트 근거 위치", "base_year": 2020,
            "unit": "지수(2020=100)",
            "dimensions": {"region": "전국"},
            "allowed_roles": roles if roles is not None else ["general"],
            "observations": obs if obs is not None else [
                {"year": 2020, "value": 100.0},
                {"year": 2021, "value": 104.0},
                {"year": 2022, "value": 108.0}]}


class SeriesContract(unittest.TestCase):
    """Growth-rate params accept only the bound series' endpoint CAGR."""

    def setUp(self):
        self.params = _specs(MAT, OVH, "sales.y2.growth_rate")

    def test_reproduction_arbitrary_rate_with_empty_refs_held(self):
        """The reported defect: 0.37 and source_refs=[] used to pass."""
        for key in (MAT, OVH):
            with self.subTest(key=key), self.assertRaisesRegex(
                    h.Held, "evidence_source_missing"):
                h._facts(_project([_fact(key, "0.37", refs=[])]), self.params)

    def test_reproduction_arbitrary_rate_without_assumption_held(self):
        for key in (MAT, OVH):
            with self.subTest(key=key), self.assertRaisesRegex(
                    h.Held, "price_contract_missing"):
                h._facts(_project([_fact(key, "0.37")]), self.params)

    def test_arbitrary_assumption_free_fact_value_held(self):
        """Declared 0.37 disagrees with the observed CAGR → held."""
        fact = _fact(MAT, "0.37",
                     price_assumption=_assumption())
        with self.assertRaisesRegex(h.Held, "price_contract_invalid"):
            h._facts(_project([fact]), self.params)

    def test_valid_series_cagr_applied(self):
        rate = _cagr()
        fact = _fact(MAT, str(rate), price_assumption=_assumption())
        values, derivations, _ = h._facts(_project([fact]), self.params)
        self.assertEqual(values[MAT], Decimal(str(rate)))
        self.assertEqual(derivations[0]["rule_id"], h.PRICE_RULE)
        self.assertEqual(derivations[0]["price_source_id"], MAT_SERIES)
        self.assertEqual(derivations[0]["observation"]["end_year"], 2025)
        self.assertAlmostEqual(derivations[0]["declared_rate"], rate)

    def test_missing_and_unknown_source_id(self):
        a = _assumption()
        del a["source_id"]
        fact = _fact(MAT, "0.05", price_assumption=a)
        with self.assertRaisesRegex(h.Held, "price_contract_missing"):
            h._facts(_project([fact]), self.params)
        fact = _fact(MAT, "0.05",
                     price_assumption=_assumption(source_id="no.such.series"))
        with self.assertRaisesRegex(h.Held, "price_contract_invalid"):
            h._facts(_project([fact]), self.params)

    def test_base_year_missing_or_wrong(self):
        a = _assumption()
        del a["base_year"]
        fact = _fact(MAT, "0.05", price_assumption=a)
        with self.assertRaisesRegex(h.Held, "price_contract_missing"):
            h._facts(_project([fact]), self.params)
        fact = _fact(MAT, "0.05", price_assumption=_assumption(base_year=2019))
        with self.assertRaisesRegex(h.Held, "price_contract_invalid"):
            h._facts(_project([fact]), self.params)

    def test_observation_period_variants(self):
        for obs in (None, "2020-2025", {},
                    {"start_year": 2025, "end_year": 2020},
                    {"start_year": "2020", "end_year": 2025},
                    {"start_year": 2020, "end_year": 2030},
                    {"start_year": 2021, "end_year": 2021}):
            a = _assumption()
            if obs is None:
                del a["observation"]
            else:
                a["observation"] = obs
            fact = _fact(MAT, "0.05", price_assumption=a)
            with self.subTest(obs=obs), self.assertRaisesRegex(
                    h.Held, "price_contract_(missing|invalid)"):
                h._facts(_project([fact]), self.params)

    def test_role_and_unit_mismatch(self):
        # wage-only series on a general-role param
        fact = _fact(MAT, "0.05", price_assumption=_assumption(
            source_id=WAGE_SERIES, base_year=None, unit="원/시간"))
        with self.assertRaisesRegex(h.Held, "price_contract_invalid"):
            h._facts(_project([fact]), self.params)
        # right series, wrong declared unit
        fact = _fact(MAT, "0.05", price_assumption=_assumption(unit="원/kg"))
        with self.assertRaisesRegex(h.Held, "price_contract_invalid"):
            h._facts(_project([fact]), self.params)

    def test_fewer_than_two_observations(self):
        fact = _fact(MAT, "0.04", price_assumption=_assumption(
            source_id="student.kosis.custom",
            series=_student_series(obs=[{"year": 2020, "value": 100.0}])))
        with self.assertRaisesRegex(h.Held, "price_contract_invalid"):
            h._facts(_project([fact]), self.params)

    def test_declared_rate_within_tolerance_ok(self):
        rate = _cagr()
        fact = _fact(MAT, rate + 0.00005, price_assumption=_assumption())
        values, _, _ = h._facts(_project([fact]), self.params)
        self.assertEqual(values[MAT], Decimal(str(rate)))

    def test_status_not_applied_and_unknown_field_held(self):
        fact = _fact(MAT, "0.05", price_assumption=_assumption(
            status="not_applied", reason="no inflation"))
        with self.assertRaisesRegex(h.Held, "price_contract_invalid"):
            h._facts(_project([fact]), self.params)
        fact = _fact(MAT, "0.05",
                     price_assumption=_assumption(hack="x"))
        with self.assertRaisesRegex(h.Held, "price_contract_invalid"):
            h._facts(_project([fact]), self.params)

    def test_student_declared_series_valid_path(self):
        series = _student_series()
        fact = _fact(MAT, str(_pa.cagr(100.0, 108.0, 2)),
                     price_assumption=_assumption(
                         source_id="student.kosis.custom", start=2020,
                         end=2022, series=series))
        values, _, _ = h._facts(_project([fact]), self.params)
        self.assertAlmostEqual(float(values[MAT]), 0.0392304845, places=6)

    def test_conflicting_student_series_id_held(self):
        a = _student_series(sid="dup.id")
        b = _student_series(sid="dup.id", obs=[{"year": 2020, "value": 50.0},
                                               {"year": 2021, "value": 55.0}])
        project = _project([_fact(MAT, "0.05", price_assumption=_assumption(
            source_id="dup.id", series=a))],
            sources={"s1": {"revision": 1, "kind": "stat",
                            "source_type": "official_statistics",
                            "source": "KOSIS", "grade": 1,
                            "price_series": b}})
        with self.assertRaisesRegex(h.Held, "price_source_conflict"):
            h._facts(project, self.params)

    def test_application_base_year_checked(self):
        params = dict(self.params)
        params["plan.start_year"] = _specs("plan.start_year")["plan.start_year"]
        base_facts = [_fact("plan.start_year", 2027)]
        fact = _fact(MAT, str(_cagr()), price_assumption=_assumption(
            application_base_year=2026))
        h._facts(_project(base_facts + [fact]), params)
        fact["price_assumption"]["application_base_year"] = 2024
        with self.assertRaisesRegex(h.Held, "price_contract_invalid"):
            h._facts(_project(base_facts + [fact]), params)

    def test_sales_production_growth_not_bound(self):
        """sales.yN.growth_rate is production growth — no series contract."""
        params = dict(self.params)
        params["sales.y2.m1.production_qty"] = _specs(
            "sales.y2.m1.production_qty")["sales.y2.m1.production_qty"]
        facts = [_fact("sales.y2.m1.production_qty", 10),
                 _fact("sales.y2.growth_rate", "0.10", refs=[])]
        values, _, _ = h._facts(_project(facts, sources=None), params)
        self.assertEqual(values["sales.y2.growth_rate"], Decimal("0.10"))


class SourceContract(unittest.TestCase):
    """Quote/evidence/summary params require classified admissible sources."""

    def setUp(self):
        self.params = _specs(QUOTE, EVID, SUM_OVH, SUM_WAGE, SUM_MAT)

    def test_quote_requires_resolvable_classified_source(self):
        # no sources map at all
        with self.assertRaisesRegex(h.Held, "evidence_source_missing"):
            h._facts(_project([_fact(QUOTE, 5000)], sources=None), self.params)
        # unresolvable ref id
        fact = _fact(QUOTE, 5000, refs=[{"id": "sX", "revision": 1}])
        with self.assertRaisesRegex(h.Held, "evidence_source_missing"):
            h._facts(_project([fact]), self.params)
        # revision drift
        fact = _fact(QUOTE, 5000, refs=[{"id": "s1", "revision": 9}])
        with self.assertRaisesRegex(h.Held, "evidence_source_revision"):
            h._facts(_project([fact]), self.params)

    def test_forbidden_and_unclassified_source_kinds(self):
        for source in ({"kind": "blog", "source": "개인 블로그"},
                       {"kind": "news"}, {"kind": "interview",
                        "source": "인터넷 인터뷰"},
                       {"kind": "ai_generated"}, {"kind": "stat", "grade": 3},
                       {"kind": "file"}, {"source": "분류 없음"},
                       {"kind": "file", "source_type": "external_blog"},
                       {"kind": "stat", "source_type": "external_shop"}):
            project = _project([_fact(QUOTE, 5000)],
                               sources={"s1": dict(source, revision=1)})
            with self.subTest(source=source), self.assertRaisesRegex(
                    h.Held, "evidence_source_(forbidden|unclassified)"):
                h._facts(project, self.params)

    def test_author_survey_interview_admissible(self):
        for source in ({"kind": "interview", "source_type": "author_survey"},
                       {"kind": "interview", "source": "작성자 직접 조사"}):
            # QUOTE activates its material group, which requires SUM_MAT.
            project = _project([_fact(QUOTE, 5000), _fact(SUM_MAT, "근거")],
                               sources={"s1": dict(source, revision=1)})
            values, _, _ = h._facts(project, self.params)
            self.assertEqual(values[QUOTE], 5000)

    def test_evidence_ref_param_auto_contract(self):
        """EVIDENCE_REF params are contract-scoped without a declared field."""
        assert "price_contract" not in self.params[EVID]
        project = _project([_fact(EVID, "근거 문서")],
                           sources={"s1": {"revision": 1, "kind": "blog"}})
        with self.assertRaisesRegex(h.Held, "evidence_source_forbidden"):
            h._facts(project, self.params)


class SummaryGeneration(unittest.TestCase):
    """Displayed summaries are generated; student free text never ships."""

    def setUp(self):
        self.params = _specs(SUM_OVH, SUM_MAT, SUM_WAGE, OVH, MAT)

    def test_reproduction_free_text_never_displayed(self):
        """The reported defect: blog-sourced free text used to pass through."""
        for key in (SUM_OVH, SUM_WAGE):
            fact = _fact(key, "개인 블로그에서 가져온 상승률 37%")
            with self.subTest(key=key):
                values, derivations, _ = h._facts(_project([fact]), self.params)
                self.assertNotIn("블로그", values[key])
                self.assertNotIn("37%", values[key])
                self.assertEqual(
                    [d for d in derivations if d["rule_id"] == h.SUMMARY_RULE
                     ][0]["derived_value"], values[key])

    def test_summary_generated_with_series_footnote(self):
        facts = [
            _fact(OVH, str(_pa.cagr(100.0, 130.2, 5)),
                  price_assumption=_assumption(source_id=OVH_SERIES)),
            _fact(SUM_OVH, "원본 2009~2018·3%"),
        ]
        values, _, _ = h._facts(_project(facts), self.params)
        text = values[SUM_OVH]
        self.assertIn(OVH_SERIES, text)
        self.assertIn("관측", text)
        self.assertIn("연평균", text)
        self.assertNotIn("원본", text)
        self.assertNotIn("2009", text)

    def test_student_marks_in_generated_summary(self):
        for mark, source in (
                ("학생 견적", {"kind": "stat", "source": "학생 견적"}),
                ("작성자 직접 조사", {"kind": "interview",
                                   "source_type": "author_survey"}),
                ("학생 견적", {"kind": "interview", "source": "학생 견적",
                              "source_type": "author_survey"})):
            fact = _fact(SUM_OVH, "자유 문구")
            project = _project([fact],
                               sources={"s1": dict(source, revision=1)})
            with self.subTest(mark=mark, source=source):
                values, _, _ = h._facts(project, self.params)
                self.assertEqual(values[SUM_OVH], mark)
                self.assertNotIn("자유 문구", values[SUM_OVH])

    def test_official_source_label_and_absence_sentinel(self):
        project = _project([_fact(SUM_OVH, "자유 문구")])
        values, _, _ = h._facts(project, self.params)
        self.assertIn("KOSIS", values[SUM_OVH])
        # '해당 없음' declared absence passes through the hold path unchanged
        fact = _fact(SUM_OVH, "해당 없음", refs=[])
        values, _, _ = h._facts(_project([fact], sources=None), self.params)
        self.assertEqual(values[SUM_OVH], "해당 없음")

    def test_material_summary_includes_growth_family(self):
        facts = [
            _fact(MAT, str(_cagr()), price_assumption=_assumption()),
            _fact(SUM_MAT, "아무 문구"),
        ]
        values, _, _ = h._facts(_project(facts), self.params)
        self.assertIn(MAT_SERIES, values[SUM_MAT])


if __name__ == "__main__":
    unittest.main()
