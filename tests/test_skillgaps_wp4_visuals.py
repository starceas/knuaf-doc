"""WP-4 시각자료 문서 계약 (G2-09, G1-14)."""
import re
from pathlib import Path
import unittest

REF = Path(__file__).resolve().parent.parent / "skills" / "knuaf-doc" / "references"


def read(name):
    return (REF / name).read_text(encoding="utf-8")


def section(text, heading):
    lines = text.splitlines()
    start = next(
        (i for i, line in enumerate(lines) if line.startswith(heading)), None)
    if start is None:
        raise AssertionError(f"heading not found: {heading}")
    level = len(heading) - len(heading.lstrip("#"))
    for i in range(start + 1, len(lines)):
        match = re.match(r"^(#+)\s", lines[i])
        if match and len(match.group(1)) <= level:
            return "\n".join(lines[start:i])
    return "\n".join(lines[start:])


class FigurePolicyContract(unittest.TestCase):
    def setUp(self):
        self.text = read("figure-photo-policy.md")

    def test_public_plate_license_evidence_is_required(self):
        evidence = section(self.text, "## 6. 공공 도판 이용조건 증거 대조")
        for token in ("이용조건", "조회일", "SHA-256", "대조 실패"):
            self.assertIn(token, evidence)
        self.assertIn("삽입하지 않는다", evidence)
        self.assertIn("원본", evidence)

    def test_chapter_checklist_records_missing_plates(self):
        checklist = section(self.text, "## 7. 장별 공공도판 체크리스트")
        for token in ("후보 도판", "삽입 위치", "출처·이용조건 증거", "삽입/미삽입 사유"):
            self.assertIn(token, checklist)
        self.assertIn("장별 발주·보고", checklist)
        self.assertIn("사진 자리", checklist)


class LinkContract(unittest.TestCase):
    def test_relative_links_resolve(self):
        path = REF / "figure-photo-policy.md"
        for match in re.finditer(r"\]\(([^)]+)\)", path.read_text(encoding="utf-8")):
            target = match.group(1).split("#")[0]
            if not target or target.startswith(("http", "mailto")):
                continue
            self.assertTrue((path.parent / target).exists(), target)


if __name__ == "__main__":
    unittest.main()
