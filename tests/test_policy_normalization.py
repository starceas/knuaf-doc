"""X1-04: typography variants, original counts and lexical boundaries."""
import json
import tempfile
import unicodedata
from pathlib import Path
from unittest.mock import patch

from tests._harness import ContractCase, runtime

doc = runtime("gg_document")
MAJORS = ("specialty_crops", "fruit_trees", "hort_env_systems",
          "industrial_insects", "unknown_major", None)


def forbidden(text, major=None):
    return [reason for cid, reason in doc.check(text, None, major_id=major)
            if cid == "forbidden_term"]


class PolicyNormalizationTests(ContractCase):
    def test_typographic_bypasses_in_all_majors(self):
        cases = {
            "NPV": ("ＮＰＶ", "N P V", "n.p.v", "N\u200bP\u200dV", "Ｎ－ｐ－Ｖ",
                    "N\u034fP\ufe0fV", "N\U000e0100PV", "ⓃⓅⓋ",
                    "N\tP\u00a0V", "N\nP\nV"),
            "IRR": ("ｉｒｒ", "I R R", "I·r—R", "I\ufeffR\u2060R"),
            "B/C": ("Ｂ／Ｃ", "B / C", "b-c"),
            "BCR": ("ＢＣＲ", "B C R"),
            "순현재가치": ("순 현재 가치", "순·현재·가치"),
            "내부수익률": ("내부 수익률", "내부\u200b수익률"),
            "할인율": ("할 인 율", "할-인-율"),
            "민감도": ("민 감 도",),
        }
        for major in MAJORS:
            for term, variants in cases.items():
                for raw in variants:
                    with self.subTest(major=major, raw=raw):
                        self.assertTrue(any(r.endswith(": " + term)
                                            for r in forbidden(raw + "를 검토했다.", major)))

    def test_financial_phrase_aliases_only(self):
        aliases = ("net present value", "internal rate of return", "discount rate",
                   "benefit-cost ratio", "sensitivity analysis")
        for raw in aliases:
            for variant in (raw, raw.upper(), raw.replace(" ", "\u200b")):
                with self.subTest(raw=variant):
                    self.assertTrue(forbidden(variant + "를 검토했다."))
        self.assertFalse(forbidden("The sensor sensitivity and the shop discount changed."))
        self.assertTrue(forbidden("센서 민감도를 살핀다."))  # existing Korean policy

    def test_unrelated_words_and_identifiers_do_not_fuse(self):
        for raw in ("an PV sensor", "plan P V crop", "NP Variety", "N P Variety",
                    "IR Rye", "ＢＣＲＯＰ", "aNPV", "NPV2", "npv_index",
                    "net present values", "rediscount rate", "discount rates",
                    "sensitivity analyses", "강점을 정리한다.\n가격을 비교한다."):
            with self.subTest(raw=raw):
                self.assertFalse(forbidden(raw))
        # A paragraph/heading or table cell boundary is not an obfuscating space.
        for raw in ("N\n\nP V", "N\n# P V", "|N|P V|\n|---|---|\n|값|값|"):
            self.assertFalse(forbidden(raw))

    def test_original_counts_are_not_doubled(self):
        rows = forbidden("NPV, ＮＰＶ, N P V, npv를 비교했다.")
        npv = [r for r in rows if r.endswith(": NPV")]
        self.assertEqual(len(npv), 1)
        self.assertIn("4회", npv[0])

    def test_nfkc_composes_hangul_before_matching(self):
        for term in ("순현재가치", "내부수익률", "할인율"):
            self.assertTrue(forbidden(unicodedata.normalize("NFD", term)))
        self.assertTrue(forbidden("할이\u11ab율"))

    def test_original_locations_in_tables_images_and_draft_wrappers(self):
        text = ("## STATUS\n준비\n## DRAFT\n  |항목|값|\n  |---|---|\n"
                "  |NPV|ＮＰＶ|\n![N P V](n-p-v.png)\n## OPEN\nNPV\n")
        row = next(r for r in forbidden(text) if r.endswith(": NPV"))
        self.assertIn("4회", row)
        for location in ("6행 4열", "6행 8열", "7행 3열", "7행 10열"):
            self.assertIn(location, row)

    def test_report_keeps_original_spelling_and_position(self):
        text = "# Ⅰ. 서론\n\n계획에서 ＮＰＶ를 검토한다."
        row = next(r for r in forbidden(text) if r.endswith(": NPV"))
        self.assertIn("ＮＰＶ", row)
        self.assertIn("3행 6열", row)

    def test_normalization_does_not_spread_to_limits_or_captions(self):
        rows = doc.check("손 익 분 기 " * 10, None)
        self.assertNotIn("term_limit", {cid for cid, _ in rows})
        rows = doc.check("손익분기 " * 7, None)
        self.assertIn("term_limit", {cid for cid, _ in rows})
        text = "표 1. ＮＰＶ 승 인 조 건\n|항목|값|\n|---|---|\n|작물|산약|\n출처: 본인 작성"
        self.assertNotIn("caption_forbidden", {cid for cid, _ in doc.check(text, None)})

    def test_alias_list_is_validated_and_read_from_policy(self):
        data = doc.load_plain_policy()
        self.assertEqual(data["forbidden_body_term_aliases"], [
            "net present value", "internal rate of return", "discount rate",
            "benefit-cost ratio", "sensitivity analysis"])
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "policy.json"
            for bad in (None, "discount", [7], [""]):
                broken = dict(data, forbidden_body_term_aliases=bad)
                path.write_text(json.dumps(broken), encoding="utf-8")
                with self.assertRaises(ValueError):
                    doc.load_plain_policy(path)
            data["forbidden_body_term_aliases"] = ["test financial phrase"]
            path.write_text(json.dumps(data), encoding="utf-8")
            with patch.object(doc, "POLICY_PATH", path):
                self.assertTrue(forbidden("TEST FINANCIAL PHRASE"))
                self.assertFalse(forbidden("net present value"))
