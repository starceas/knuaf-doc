# 출력 지원 한계

SKILL.md "출력 지원 한계" 절의 상세다. 출력 생성·등록 경로는 SKILL.md와 [section-ledger.md](section-ledger.md)를 따른다.

## DOCX

`build_docx.py <폴더> --in <본문.md> --out <새.docx> --major <전공ID>`는 제목·문단·pipe 표·로컬 그림·강조를 변환한다. 새 학교 원고 CLI는 `school_profile`의 `forms_1_to_4` 배치를 기본으로 사용한다. 표지·제출면·인준서는 각각 한 쪽, 목차는 실제 본문 북마크의 쪽번호를 참조한다. 프로필의 제목·학생·학과·지도교수·제출일·졸업일은 기존 답변에서 스킬이 구성하고, 제출일과 졸업일을 임의로 복제하지 않는다. 제목이 양식 영역을 넘으면 잘라내지 않고 보완을 알린다. 기존 Markdown과 저수준 라이브러리의 옛 배치는 호환을 위해 보존한다. 글꼴 가용성·모든 페이지 렌더·HWP 변환은 생성과 별도로 검증한다. DOCX 생성 후 한글에서 할 일은 (1) 글꼴 신명조 확인·변경 (2) 여백 위20/아래15/머리15/꼬리15/좌30/우30/제본0 mm (3) 페이지 번호다. 이 세 가지는 스킬 작성 완료를 막지 않으며, 사람이 확인하기 전까지 완료로 표시하지 않는다.

## XLSX

`scripts/gg_excel_template.py inspect|clear`가 제공된 제출 템플릿의 시트·입력 셀을 확인하고 복제한다. 서민서 XLSX처럼 실제 템플릿이 있으면 이 경로를 우선한다. 원본 형식·수식·병합·시트명을 보존하고 매핑된 명시적 입력 셀만 비운다. 빈 사본에 실제 값을 넣을 때는 [excel-template](excel-template.md)에 따라 `scripts/gg_excel_fill.py`를 사용한다. 복제 때 보존한 예시 연도·가격·비율도 현재 근거에 맞게 별도 쓰기 매핑으로 교체한다. 원본 예시의 수식 오류가 확인되면 `gg_excel_formula_patch.py`로 근거·기대 수식(또는 명시적으로 검토한 숫자 상수)·변경 수식을 기록해 사본만 수정한다. 서민서 템플릿(`seo-minseo-finance-xlsx`)에는 이미 검토된 C1 공통 정정 명세가 바인딩되어 있으므로 `scripts/gg_workbook_audit.py materialize-c1 --ref SEO --source <서민서 원본> --out <로컬 맵>`으로 로컬 패치 맵을 만든 뒤 그 맵으로 `gg_excel_formula_patch.py --project <정본 폴더> --major specialty_crops`를 적용한다(명세·처분: [common-workbooks/corrections/C1.md](common-workbooks/corrections/C1.md)). 같은 명세의 XR-11은 자가 노동비(8. 노무비계획 자가 노동비 소계)를 전액 미지급 평가액으로 보아 현금흐름 지출에서 제외하는 가정(option A, 소유자 결정 2026-09-27)에 의존한다. 실제 지급 여부는 인터뷰 가이드 Q2-2 가족사항 문항에서 한 번만 묻는다([interview-guide.md](interview-guide.md)). 경영주가 아닌 사람(가족 포함)에게 실제로 지급하는 노동은 고용 노동비로 입력하므로 수식 변경 없이 현금지출로 남는다. 경영주에게 지급·인출하는 금액은 기존 경영주 급여·가계비 규칙으로 처리하며, 동일 금액을 중복 반영하지 않는다. 경영주는 Q2-2의 기존 답변으로 판별한다. 이 가정이 거절되면 XR-11은 보류로 되돌린다. 표가 페이지 밖으로 갈라지면 `gg_excel_print.py`의 명시적 인쇄 지도에 따라 사본만 조정하고 실제 PDF에서 가독성을 확인한다. 입력 후 실제 앱 재계산과 본문 대조가 필요하며, 빈 사본 생성만으로 재무 완료를 표시하지 않는다. 템플릿이 없을 때만 `build_excel_finance_template.py <폴더> --input <계산계약.json> --out <새.xlsx> --major <전공ID>` 레거시 생성기를 사용한다. 엑셀 템플릿 `clear`, `gg_excel_fill.py`, `gg_excel_formula_patch.py`, `gg_excel_print.py`는 모두 `--project <정본 폴더> --major <전공ID>`가 필요하며 영수증에 확인한 전공 권한(`majorAuthorization`)을 남긴다. [excel-template](excel-template.md)은 해시로 고정된 검증 스냅샷이라 명령 예시에 이 두 인자가 빠져 있으니 실행할 때 붙인다. `inspect`는 전공 없이 실행되지만 출력 권한을 주지 않는다. 원본 해시·X01/X02 선택은 파일 식별일 뿐 전공 확인이 아니다. 전공 전용 통합문서가 있으면 그쪽이 우선이다 — 원예환경시스템전공은 등록된 H01 통합문서가 기본이다. 전공 XLSX 부재가 확인되면 공통 XLSX가 채워 쓸 양식이다: X01은 일반 양식이고 X02는 대식물 계정을 추가한 같은 양식이며, `clear`는 레이아웃 앵커가 x01 형태로 확인될 때만 기본 입력 지도를 적용하고 대식물 배치(X02)는 전용 검토 지도가 있기 전 `manual_map_required`로 거절한다. `layout` 없이 직접 만든 지도는 기본 X01 지도로 취급해 X01이 아닌 배치에서는 거절하고, 이런 지도는 `layout`을 `{mapOrigin: reviewed_custom, variant: <감지된 배치>}`로 선언해야 한다. 레거시 생성물은 학교 제출 양식 대체나 엄격한 원본 양식 검증으로 표시하지 않는다. 두 경로 모두 학교 원문대로 기말 잔여 장부가를 수익에, 경영주 급여가 비용에 없으면 가계비를 투자 비용에 반영한다. 장부가는 시장 처분가격이 아니며 기존 사업 일부 투자에 전체 매출을 사용할 수 없다. 복합·다년생·공유시설·가공·보조금은 원계획을 유지하고 미지원 계산만 보류한다.

## macOS Office 재계산·PDF

`scripts/gg_office.py`는 macOS Office 샌드박스 파일 접근을 지원하기 위해 전용 그룹 컨테이너(`~/Library/Group Containers/UBF8T346G9.Office/ginseng-goat/`) 작업영역에서 원본을 불변으로 격리 스테이징하여 변환한다. **이 단계는 macOS만 지원한다 — Windows는 작성·검토본(DOCX·XLSX 생성, 검토)까지 지원하고 Office 자동 재계산·PDF 변환은 지원하지 않는다.**

- Word: `scripts/gg_office.py word <문서.docx> --out-dir <새폴더> --project <정본 폴더> --major <전공ID>` (인쇄 모양 목차/색인 필드 2회 갱신 및 PDF 동시 출력)
- Excel: `scripts/gg_office.py excel <재무.xlsx> --out-dir <새폴더> --project <정본 폴더> --major <전공ID> [--spec <명세.json> 또는 --template-receipt <입력영수증.json>]` (전 시트 used range 재계산 및 단일 세션 PDF 내보내기)
- 일괄: `scripts/gg_office.py batch <파일1.docx> <파일2.docx> <파일1.xlsx> <파일2.xlsx> --out-dir <새폴더> --project <정본 폴더> --major <전공ID>` (UI 클릭 없는 연속 실행, 앱별 최초 권한/시간초과 시 즉시 중단)

산출물은 새 목적지 폴더에만 무덮어쓰기로 발행되며, 원본은 유지된다. Office 변환 및 기계 검사 완료는 최종 교수 승인이 아니며 최종 제출 완료로 자동 승격되지 않는다.

## 본문 재무 수치의 자동 대조

정본의 범위·기간·항목·값·단위를 같은 절에 명시한 문장으로 제한한다. 예를 들어 실제 정본 값으로 `농장A 1년차 매출액은 1,000 천원이다.`처럼 작성한다(기간·범위 순서는 바꿀 수 있다). 쉼표·줄바꿈으로 나눈 각 절에 범위·기간을 반복한다. 다른 항목의 숫자·단위, 전년 값, 문서 다른 곳의 범위를 빌려 통과시키지 않는다. 암묵적 문맥·별도 반올림 규칙은 추정하지 않고 대조 실패로 남겨 근거를 확인한다. 이 기계 대조가 독립 내용검토를 대신하지 않는다.
