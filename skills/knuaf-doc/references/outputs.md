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

## 학교 논문 본문 (특용작물)과 표 출처 (`table_sources`)

`gg.py paper <폴더> --input <spec.json> [--out <경로>] --major specialty_crops`는 특용작물 학교 논문 본문(`build/검토전_본문.md` 등)을 생성하고 관리 발행한다.

### 표 출처 입력 계약 (`table_sources`)
본문 생성기(`scripts/gg_school_paper.py`)가 출력하는 6개 표에 표별 실제 출처를 붙이려면 spec의 `table_sources` 필드로 출처 객체를 전달한다:
- **대상 표 ID 6개 (또는 정확한 생성 캡션)**:
  1. `farm_overview`: "표 1. 농장 종합 개요" (Ⅱ.1.가 농장 개요)
  2. `climate`: "표 2. 기상환경" (Ⅱ.1.나.1) 기상환경)
  3. `disasters`: "표 3. 재해" (Ⅱ.1.나.6) 재해)
  4. `shipping_markets`: "표 4. 출하시장 거리" (Ⅱ.1.다.1) 지리적 조건)
  5. `growth_targets`: "표 5. 가족구성 및 성장목표" (Ⅲ.2.가 농장의 가족구성 및 성장목표)
  6. `swot`: "표 6. SWOT 분석" (Ⅲ.2.마 SWOT 분석)
- **입력 형식**:
  ```json
  {
    "major_id": "specialty_crops",
    "table_sources": {
      "farm_overview": {
        "sources": [
          {
            "kind": "interview",
            "source_type": "author_survey",
            "source": "작성자 직접 조사",
            "title": "농장 현황 조사 기록",
            "year": "2026",
            "locator": "조사일지 1쪽",
            "survey_date": "2026-09-10",
            "subject": "농장 부지 및 시설",
            "verified": "user-stated"
          }
        ]
      },
      "climate": {
        "sources": [
          {
            "kind": "stat",
            "source_type": "official_stat",
            "source": "기상청",
            "title": "기상연보",
            "year": "2025",
            "locator": "표 3-1",
            "url": "https://data.kma.go.kr/..."
          }
        ]
      }
    }
  }
  ```
- **허용 kind / source_type 조합 (어댑터 허용 enum)**:
  | kind | source_type | 허용 내용 및 필수 요건 |
  |---|---|---|
  | `stat` | `official_stat` | 국가·공공기관 공식 통계 |
  | `public_data` | `official_public` | 공공 DB, 법령, 사업 지침 |
  | `academic` | `academic_paper` | 학술논문 |
  | `research_report` | `institution_report` | 대학·연구기관 보고서 |
  | `school_material` | `official_school` | 학교 공식 자료 |
  | `textbook` | `formal_textbook` | 정규 교재 |
  | `interview` | `author_survey` | 작성자 직접 조사 (`source="작성자 직접 조사"`, `verified="user-stated"`, 실제 `survey_date`·`subject` 필수. 일반 타인 인터뷰 금지) |

- **필수 및 선택 필드**:
  - 필수 필드: `kind`, `source_type`, `source`, `title`, `year`, `locator`
  - 선택 필드: `url` (평문 HTTP/HTTPS만 허용), `grade` (1 또는 2만 허용, 3/4등급 금지), `survey_date`, `subject`, `verified`, `id`, `revision`
  - 정본 출처 연결: `id`와 `revision`을 함께 지정하면 `project.sources` 레코드와 개정·종류·서지 충돌을 대조 검증한다.
- **인용 필드 문자 허용 부류 및 실질 문자 요건 (`_plain`)**:
  - 인용 필드(`source`, `title`, `year`, `locator` 및 비-URL 서지 필드)는 유한한 허용 문자 부류만 통과한다: NFKC 정규화 후 글자(Unicode `L*`), 숫자(`N*`), 구두점(`P*`), 지정 일반 기호 16종(`_SYMBOLS`: `°©®±×÷−+=∼₩$¥€£¢`), 일반 ASCII 공백(` `)만 허용된다.
  - 보이지 않는 결합 문자(Unicode `M*`: 결합 자모 결합자 `U+034F`, 결합 악센트 등), 변형 선택자(`U+FE00~FE0F`, `U+E0100~E01EF` 계열), 제어문자/형식문자(`C*`, 줄바꿈·탭·U+200B 등), 목록 외 기호(이모지 등)는 명시 거부된다.
  - **표시 문자열 실질 문자 요건**: 결합 마크를 제외한 정규화 표시 문자열(`display`)에 글자(`L*`) 또는 숫자(`N*`)가 반드시 1자 이상 포함되어야 한다 (기호나 구두점만으로 채운 문자열 거부).
  - **자리표시자 판정 기준**: `미정`, `미확인`, `확인필요`, `자료없음`, `n/a` 등 미확인 값 판정도 마크를 제거한 표시 문자열(`display`) 기준으로 검사하므로, 결합 문자나 변형 선택자를 끼워 넣은 위장 자리표시자(`미\u034f정`, `미\ufe0f정`, `미\U000e0100정` 등)도 `미확인 출처 값`으로 즉시 거부된다.
  - **무효 입력 처리 및 6표 무효 시 발행 차단**: 유효하지 않은 출처 입력은 빈 값으로 지워 no-op 처리하지 않고 `invalid_spec`/`not_committed` 오류로 명시 거부된다. 6개 표 모두 보이지 않는 문자 등 무효 입력인 경우 본문 발행 파일이나 발행 영수증이 전혀 생성되지 않는다.
  - **URL 필드**: 평문 HTTP(S) 주소여야 하며, 결합 마크(`M*`)나 공백이 포함되면 거부된다.
- **동작 원칙과 경계**:
  - **정확한 no-op (미제공 시 동작 없음)**: `table_sources`가 없거나 빈 객체이면 원문 텍스트를 그대로 반환한다.
  - **기본 출처 자동 삽입 없음**: spec에 출처가 주어지지 않은 표에는 "본인 작성", "작성자 직접 조사", "확인 필요" 등의 임의 기본 문구를 채워 넣지 않는다. 출처 없는 표는 기존처럼 `gg_document.check`의 `object_credit` 정책 오류가 유지된다.
  - **생성기 코드 보존 (frozen)**: `scripts/gg_school_paper.py` 코드는 일체 변경되지 않는다(frozen 계약 준수). 저수준 함수 직접 호출(`school_paper()`)이나 독립 실행에는 출처가 삽입되지 않으며, 관리 경로(`gg.py paper` → `core.paper`)에서만 관리 발행 직전에 단독 출처 문단으로 삽입된다.
  - **새 revision 필요**: 기존 요청 수령증(receipt)이 존재하는 경우 재호출은 기존 결과를 반환하므로, 표 출처를 새로 적용하거나 변경하려면 새 revision(개정)과 새 출력 경로로 요청해야 한다.
