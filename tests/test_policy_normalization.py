"""X1-04: typography variants, original counts and lexical boundaries."""
import json
import tempfile
import time
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
    def test_pv1_separator_and_jamo_matrix(self):
        # PV1's exact 289-case matrix, plus the four deep-probe codas.
        terms = ("순현재가치", "내부수익률", "편익비용", "비용편익", "할인율", "민감도")
        count = 0
        for term in terms:
            nfd = unicodedata.normalize("NFD", term)
            for separator in (" ", "\u200b", "-", "/", "\n"):
                for i in range(1, len(nfd)):
                    raw = nfd[:i] + separator + nfd[i:]
                    with self.subTest(term=term, separator=separator, i=i):
                        self.assertTrue(any(r.endswith(": " + term) for r in forbidden(raw)))
                    count += 1
        self.assertEqual(count, 285)
        for raw, term in (("ㄴㅐ부수익률", "내부수익률"), ("내ㅂㅜ수익률", "내부수익률"),
                          ("ㅍㅕㄴ익비용", "편익비용"), ("편익ㅂㅣ용", "편익비용"),
                          ("할이ㄴ율", "할인율"), ("순혀ㄴ재가치", "순현재가치"),
                          ("비용펴ㄴ익", "비용편익"), ("펴ㄴ이ㄱ비용", "편익비용")):
            with self.subTest(raw=raw):
                self.assertTrue(any(r.endswith(": " + term) for r in forbidden(raw)))

    def test_pv1_jamo_occurrence_spans_and_columns(self):
        term = unicodedata.normalize("NFD", "할인율")
        row = next(r for r in forbidden(term * 2) if r.endswith(": 할인율"))
        self.assertIn("2회", row)
        self.assertIn("1행 1열 %r" % term, row)
        self.assertIn("1행 10열 %r" % term, row)
        row = next(r for r in forbidden(unicodedata.normalize("NFD", "문장할인율검토"))
                   if r.endswith(": 할인율"))
        self.assertIn("1행 7열 %r" % term, row)

    def test_pv1_supported_mixed_forms_in_all_majors(self):
        initials = "ㄱㄲㄴㄷㄸㄹㅁㅂㅃㅅㅆㅇㅈㅉㅊㅋㅌㅍㅎ"
        finals = "ㄱㄲㄳㄴㄵㄶㄷㄹㄺㄻㄼㄽㄾㄿㅀㅁㅂㅄㅅㅆㅇㅈㅊㅋㅌㅍㅎ"
        compat = {chr(0x1100 + i): c for i, c in enumerate(initials)}
        compat.update({chr(0x1161 + i): chr(0x314f + i) for i in range(21)})
        compat.update({chr(0x11a8 + i): c for i, c in enumerate(finals)})
        for major in MAJORS:
            for term in ("순현재가치", "내부수익률", "편익비용", "비용편익", "할인율", "민감도"):
                nfd = unicodedata.normalize("NFD", term)
                mixed = "".join(compat.get(c, c) if i % 2 else c for i, c in enumerate(nfd))
                forms = (nfd, "".join(compat.get(c, c) for c in nfd), mixed,
                         "할이\u200bᆫ율" if term == "할인율" else term)
                for form in forms:
                    for separator in ("", " ", "\u200b", "\u034f", "\ufe0f", "-", "/", "\n"):
                        raw = separator.join(form)
                        with self.subTest(major=major, term=term, raw=raw):
                            self.assertTrue(any(r.endswith(": " + term) for r in forbidden(raw, major)))

    def test_pv1_original_spans_cover_only_each_syllable(self):
        for raw in (unicodedata.normalize("NFD", "문장할인율검토"),
                    "문장ㅎ / ㅏ / ㄹㅇ / ㅣ / ㄴㅇ / ㅠ / ㄹ검토"):
            compact, spans = doc._compact_term_text(raw)
            self.assertEqual(compact, "문장할인율검토")
            found = doc._forbidden_spans(raw, "할인율", compact, spans)
            self.assertEqual(len(found), 1)
            start, end = found[0]
            self.assertEqual(doc._compact_term_text(raw[start:end])[0], "할인율")
            self.assertGreater(start, 0)
            self.assertLess(end, len(raw))

    def test_pv1_unsupported_spellings_are_not_guessed(self):
        note = doc.load_plain_policy()["forbidden_body_normalization_note"]
        for word in ("옛한글", "남은 자모", "미지원", "검토"):
            self.assertIn(word, note)
        for raw in ("N👩‍🌾PV", "1RR", "IЯR", "ㅎㄹㅇㄴㅇㄹ", "하ᇹ인율"):
            with self.subTest(raw=raw):
                self.assertFalse(forbidden(raw))

    def test_pv1_draft_and_heading_tail_offsets(self):
        for newline in ("\n", "\r\n"):
            for body, column in (("NPV", 1), ("앞 NPV", 3)):
                text = newline.join(("## STATUS", body, "## DRAFT", body,
                                     "## OPEN", "보류"))
                row = next(r for r in forbidden(text) if r.endswith(": NPV"))
                self.assertIn("4행 %d열" % column, row)
                self.assertNotIn("2행", row)
        row = next(r for r in forbidden("가. NPV: NPV") if r.endswith(": NPV"))
        self.assertIn("2회", row)
        for column in (4, 9):
            self.assertIn("1행 %d열" % column, row)

    def test_pv1_offsets_for_markers_indentation_repeated_cells_and_images(self):
        text = "## STATUS\nNPV\n" + doc.START + "\n  NPV\n" + doc.END
        row = next(r for r in forbidden(text) if r.endswith(": NPV"))
        self.assertIn("4행 3열", row)
        self.assertNotIn("2행", row)
        row = next(r for r in forbidden("  ### 가. NPV: NPV") if r.endswith(": NPV"))
        for column in (10, 15):
            self.assertIn("1행 %d열" % column, row)
        text = "  |NPV|NPV|\n  |---|---|\n  |NPV|NPV|\n![NPV](NPV)"
        row = next(r for r in forbidden(text) if r.endswith(": NPV"))
        self.assertIn("6회", row)
        for location in ("1행 4열", "1행 8열", "3행 4열", "3행 8열", "4행 3열", "4행 8열"):
            self.assertIn(location, row)
        text = "N\r\n  P\r\n  V"
        row = next(r for r in forbidden(text) if r.endswith(": NPV"))
        self.assertIn("1행 1열 %r" % text, row)

    def test_pv1_soft_paragraph_scaling(self):
        # Loose relative bound only: no machine-specific absolute timeout.
        timings = []
        for n in (2000, 8000):
            text = "농장 계획을 정리한다.\n" * n
            nodes = doc.parse(text, with_spans=True)
            samples = []
            for _ in range(2):
                start = time.perf_counter()
                units = doc._body_term_units(text, nodes)
                samples.append(time.perf_counter() - start)
                self.assertEqual(len(units), 1)
                self.assertEqual(len(units[0][0]), len(units[0][1]))
            timings.append(min(samples))
        self.assertLess(timings[1] / timings[0], 8)

    def test_pv1_policy_check_scaling_with_and_without_hits(self):
        for fragment in ("농장 계획을 정리한다.\n", "NPV\n\n", "할이ㄴ율\n"):
            timings = []
            for n in (2000, 8000):
                text = fragment * n
                samples = []
                for _ in range(2):
                    start = time.perf_counter()
                    rows = forbidden(text)
                    samples.append(time.perf_counter() - start)
                    if "NPV" in fragment or "할이ㄴ율" in fragment:
                        self.assertIn("%d회" % n, rows[0])
                    else:
                        self.assertFalse(rows)
                timings.append(min(samples))
            with self.subTest(fragment=fragment, seconds=timings):
                self.assertLess(timings[1] / timings[0], 8)

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
