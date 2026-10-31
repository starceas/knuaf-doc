# 출력 지원 한계

SKILL.md "출력 지원 한계" 절의 상세다. 출력 생성·등록 경로는 SKILL.md와 [section-ledger.md](section-ledger.md)를 따른다.

## DOCX

`build_docx.py <폴더> --in <본문.md> --out <새.docx> --major <전공ID>`는 제목·문단·pipe 표·로컬 그림·강조를 변환한다. 새 학교 원고 CLI는 `school_profile`의 `forms_1_to_4` 배치를 기본으로 사용한다. 표지·제출면·인준서는 각각 한 쪽, 목차는 실제 본문 북마크의 쪽번호를 참조한다. 프로필의 제목·학생·학과·지도교수·제출일·졸업일은 기존 답변에서 스킬이 구성하고, 제출일과 졸업일을 임의로 복제하지 않는다. 제목이 양식 영역을 넘으면 잘라내지 않고 보완을 알린다. 기존 Markdown과 저수준 라이브러리의 옛 배치는 호환을 위해 보존한다. 글꼴 가용성·모든 페이지 렌더·HWP 변환은 생성과 별도로 검증한다. 65자를 넘는 URL은 하이퍼링크로 넣되 **표시 글자도 URL 그대로** 둔다 — 인쇄·PDF에 주소가 남아야 참고문헌을 대조할 수 있다. DOCX 생성 후 한글에서 할 일은 (1) 글꼴 신명조 확인·변경 (2) 여백 위20/아래15/머리15/꼬리15/좌30/우30/제본0 mm (3) 페이지 번호다. 이 세 가지는 스킬 작성 완료를 막지 않으며, 사람이 확인하기 전까지 완료로 표시하지 않는다.

`--no-toc`는 앞머리 없는 본문에 자동 추가하는 목차 제목·필드·쪽 나눔만 생략하며, 이 경우 매니페스트의 `toc_update`는 `not_applicable`이다. 학교 앞머리가 있는 원고는 필수 목차를 그대로 유지한다. 이 옵션은 `--major`와 정본 전공 바인딩 검사를 생략하지 않는다.

매니페스트 `<출력.docx>.manifest.json`의 `file_hash`는 **빌드 직후 파일** 기준이고 `file_hash_stage: "build"`로 표시된다. 한글에서 쪽 나눔·여백을 고쳐 다시 저장했다면 그 해시는 낡는다 — `build_docx.py <폴더> --rehash <출력.docx>`로 다시 계산하면 `file_hash_stage: "post_processed"`와 `rehash` 이력(이전 해시·이후 해시·시각)이 남는다. 재해시는 본문·입력 해시를 바꾸지 않고, 매니페스트가 없거나 이 도구가 만든 것이 아니면 거절한다.

폴더 인자는 절대 경로를 권장한다. 현재 폴더(cwd)를 읽을 수 없는 권한 오류(`EPERM`)에서는 절대 경로가 정상 동작하고, 상대 경로(`.`)는 셸의 `PWD`로 계산하며 그 사실을 stderr에 알린다. 둘 다 불가능하면 절대 경로를 쓰라는 오류로 멈춘다.

## HWPX

`build_hwpx.py <폴더> --in <본문.md> --out <새.hwpx> --major <전공ID>`는 DOCX와 같은 입력 Markdown에서 학교 규격 HWPX(한글 문서)를 만든다(기본 `--in build/본문_통합.md`, `--out build/검토전_논문.hwpx`). 내부적으로 kordoc `generate`(마크다운 → HWPX)로 변환한 뒤 여백(위20/아래15/머리15/꼬리15/좌30/우30/제본0 mm)과 쪽번호를 학교 규격으로 후처리하고 글꼴을 신명조로 맞춘다. 그림은 기본적으로 입력 마크다운이 있는 폴더에서 찾고, 그림이 없거나 그 폴더 밖을 가리키면 발행 전에 거절한다(`--image-dir`로 지정). 생성 뒤 `kordoc validate`(HWPX 구조)와 HWPX 재파싱 표 수치 대조(DOCX와 같은 수치인지)를 거치며, 검증이 실패·미확인이면 발행하지 않고 `blocked`로 멈춘다(`--no-verify`는 검증을 생략한다). `<출력파일 전체이름>.manifest.json`(예: `out.hwpx.manifest.json`)에 kordoc 버전·후처리·검증·표 대조 결과와 미검증 표기를 남긴다. 표 폭은 kordoc 기본값(155.2 mm)이 아니라 **본문 폭**(A4 210 mm − 좌30 − 우30 = 150 mm, 42520 HWPUNIT)에 맞춰 다시 쓰고, 장 제목 문단(Ⅰ~Ⅵ)에는 쪽 나눔을 넣는다. 두 값은 생성 뒤 후처리 단계에서 적용하고, 적용 결과를 `kordoc validate`·재파싱·표 수치 대조로 **다시 검증한 뒤에만** 발행하며 매니페스트 `hwpx.layout`에 본문 폭·바꾼 표 수·이전 폭·쪽 나눔을 넣은 장 목록을 남긴다. 앞부분(표지·제출면·인준서·목차)은 kordoc `--plain`이 평문 문단으로만 내보내므로 쪽 나눔·가운데 정렬·목차 실제 쪽번호를 재현하지 않는다 — 매니페스트 `school_frontmatter.human_finish_required`에 남기고 한글에서 사람이 마무리한다. 이 목록에는 표 폭과 장 시작 쪽 나눔의 **확인** 항목도 들어간다(자동 적용값을 한글에서 눈으로 확인한다). 글꼴 설치 확인·모든 페이지 렌더·한글 저장/재열기는 아직 확인 전이라 `hwp: not_run`으로 표시한다. HWPX는 검토본이며 최종 제출본이 아니고, 사람이 확인하기 전까지 완료로 표시하지 않는다.

## XLSX

`scripts/gg_excel_template.py inspect|clear`가 제공된 제출 템플릿의 시트·입력 셀을 확인하고 복제한다. 특용작물전공은 교수님 양식(X01, 대식물 계정이 필요하면 X02)을 이 경로로 복제한다. 선배가 채운 재무 예시 01 XLSX는 비교 참고이며 틀이 아니다. 원본 형식·수식·병합·시트명을 보존하고 매핑된 명시적 입력 셀만 비운다. 빈 사본에 실제 값을 넣을 때는 [excel-template](excel-template.md)에 따라 `scripts/gg_excel_fill.py`를 사용한다. 복제 때 보존한 예시 연도·가격·비율도 현재 근거에 맞게 별도 쓰기 매핑으로 교체한다. 원본 예시의 수식 오류가 확인되면 `gg_excel_formula_patch.py`로 근거·기대 수식(또는 명시적으로 검토한 숫자 상수)·변경 수식을 기록해 사본만 수정한다. 교수님 양식 X01·X02에는 이미 검토된 C1 공통 정정 명세가 바인딩되어 있으므로 `scripts/gg_workbook_audit.py materialize-c1 --ref X01 --source <X01 원본> --out <로컬 맵>`(대식물 양식은 `--ref X02`)으로 로컬 패치 맵을 만든 뒤 그 맵으로 `gg_excel_formula_patch.py --project <정본 폴더> --major specialty_crops`를 적용한다(명세·처분: [common-workbooks/corrections/C1.md](common-workbooks/corrections/C1.md)). 같은 명세의 XR-11은 자가 노동비(8. 노무비계획 자가 노동비 소계)를 전액 미지급 평가액으로 보아 현금흐름 지출에서 제외하는 가정(option A, 소유자 결정 2026-09-27)에 의존한다. 실제 지급 여부는 인터뷰 가이드 Q2-2 가족사항 문항에서 한 번만 묻는다([interview-guide.md](interview-guide.md)). 경영주가 아닌 사람(가족 포함)에게 실제로 지급하는 노동은 고용 노동비로 입력하므로 수식 변경 없이 현금지출로 남는다. 경영주에게 지급·인출하는 금액은 기존 경영주 급여·가계비 규칙으로 처리하며, 동일 금액을 중복 반영하지 않는다. 경영주는 Q2-2의 기존 답변으로 판별한다. 이 가정이 거절되면 XR-11은 보류로 되돌린다. 표가 페이지 밖으로 갈라지면 `gg_excel_print.py`의 명시적 인쇄 지도에 따라 사본만 조정하고 실제 PDF에서 가독성을 확인한다. 입력 후 실제 앱 재계산과 본문 대조가 필요하며, 빈 사본 생성만으로 재무 완료를 표시하지 않는다. 특용작물전공에서는 이 생성기가 입구에서 막히고(`held/specialty_template_route_required`) 교수님 양식 경로로 안내한다. 다른 전공은 템플릿이 없을 때만 `build_excel_finance_template.py <폴더> --input <계산계약.json> --out <새.xlsx> --major <전공ID>` 레거시 생성기를 사용한다. 엑셀 템플릿 `clear`, `gg_excel_fill.py`, `gg_excel_formula_patch.py`, `gg_excel_print.py`는 모두 `--project <정본 폴더> --major <전공ID>`가 필요하며 영수증에 확인한 전공 권한(`majorAuthorization`)을 남긴다. [excel-template](excel-template.md)은 해시로 고정된 검증 스냅샷이라 명령 예시에 이 두 인자가 빠져 있으니 실행할 때 붙인다. `inspect`는 전공 없이 실행되지만 출력 권한을 주지 않는다. 원본 해시·X01/X02 선택은 파일 식별일 뿐 전공 확인이 아니다. 전공 전용 통합문서가 있으면 그쪽이 우선이다 — 원예환경시스템전공은 등록된 H01 통합문서가 기본이다. 전공 XLSX 부재가 확인되면 공통 XLSX가 채워 쓸 양식이다: X01은 일반 양식이고 X02는 대식물 계정을 추가한 같은 양식이며, `clear`는 레이아웃 앵커가 x01 형태로 확인될 때만 기본 입력 지도를 적용하고 대식물 배치(X02)는 전용 검토 지도가 있기 전 `manual_map_required`로 거절한다. `layout` 없이 직접 만든 지도는 기본 X01 지도로 취급해 X01이 아닌 배치에서는 거절하고, 이런 지도는 `layout`을 `{mapOrigin: reviewed_custom, variant: <감지된 배치>}`로 선언해야 한다. 레거시 생성물은 학교 제출 양식 대체나 엄격한 원본 양식 검증으로 표시하지 않는다. 두 경로 모두 학교 원문대로 기말 잔여 장부가를 수익에, 경영주 급여가 비용에 없으면 가계비를 투자 비용에 반영한다. 장부가는 시장 처분가격이 아니며 기존 사업 일부 투자에 전체 매출을 사용할 수 없다. 복합·다년생·공유시설·가공·보조금은 원계획을 유지하고 미지원 계산만 보류한다.

## Office 재계산·PDF (macOS·Windows)

세션 가드가 `unknown`·`inflight`로 남아 다음 변환이 `previous_office_session_unknown`(또는 `office_guard_state_unreadable`)로 막히면, 차단 메시지가 그대로 칠 수 있는 복구 명령을 함께 낸다: `python3 <스킬>/scripts/gg_office_session.py --recover word`(`--guard-dir`는 기본 가드 폴더가 아닐 때만 붙는다). 이 명령은 **가드 기록만 idle로** 바꾸고 Office 문서 닫기·앱 종료·잡 폴더/산출물 삭제를 하지 않는다. 다른 세션이 잠금을 잡고 있으면 `busy`로 아무것도 바꾸지 않으며, 복구 이력은 가드 폴더의 `recovery-audit.jsonl`에 남는다. Office에 이 작업의 문서가 남아 있지 않은지 사람이 확인한 뒤 실행한다. 현재 코드는 이번 실행의 작업 사본 경로와 실제 열린 문서의 정확한 대응·현재 저장 상태를 확인한 문서만 닫는다. 기존 사용자 문서·미저장 변경은 건드리지 않는다. 저장·닫기 실패, timeout·dirty·unknown에서는 후속 자동 close/quit 없이 보존 또는 결과 불명으로 기록하며 작업 사본을 유지한다. 닫힘이 이미 확인된 뒤 변환 검사·발행이 실패한 경우에는 확인된 닫힘과 실패 결과를 따로 기록한다. 앱 전체 quit은 없으며 Word/Excel 앱은 유지한다. 이 동작 설명은 실제 앱에서의 내용 보존·변환 성공을 보증하지 않는다.

최신 UI 접근 오류 `-10005`의 진단·해결과 실제 앱 검증은 이번 단위 fixture 검사 범위에서 제외한다.

macOS Excel은 기존 전공 권한·재무 프로필 검증 뒤, 세션 가드 안에서 작업 사본 하나만 열고 `calculate` → 저장 → PDF 내보내기를 수행한다. 이미 열린 Word/Excel 문서가 있으면 해당 앱을 건드리지 않고 `reason: "existing_documents"`로 거절한다(실패 JSON과 `office_session.reason`에 기록). 호출자는 **“열려 있는 다른 Word/Excel 문서를 저장하고 닫은 뒤 다시 눌러 주세요”**로 안내할 수 있다. 문서 목록 확인 실패는 `office_inventory_unknown`으로 구분한다. 정확한 작업 경로·문서 수·읽기 전용·저장 상태를 계속 확인하며 수동 계산 모드는 `manual_calculation_not_supported`로 거부한다. 계산 모드를 바꾸거나 오류 시 강제 닫기·앱 종료를 하지 않는다.

`scripts/gg_office.py word|excel`은 원본을 불변으로 격리 스테이징하여 변환한다. macOS는 Office 전용 그룹 컨테이너(`~/Library/Group Containers/UBF8T346G9.Office/ginseng-goat/`), Windows는 사용자 로컬 앱 데이터의 `ginseng-goat/office-jobs`를 사용한다. Windows는 `pywin32>=306`과 실제 데스크톱 Office가 필요하며 `DispatchEx` 전용 인스턴스만 사용한다. 자기 문서가 닫혀 해당 인스턴스의 문서 수가 0일 때만 종료한다. Excel 저장 후 오류 셀이 있으면 PDF를 발행하지 않는다. 타임아웃·COM 실패로 닫힘을 증명하지 못한 작업은 보존한다.

- Word: `scripts/gg_office.py word <문서.docx> --out-dir .gg-work/<산출물>/<회차> --project <정본 폴더> --major <전공ID>` (인쇄 모양 목차/색인 필드 2회 갱신 및 PDF 동시 출력)
- Excel: `scripts/gg_office.py excel <재무.xlsx> --out-dir .gg-work/<산출물>/<회차> --project <정본 폴더> --major <전공ID> [--spec <명세.json> 또는 --template-receipt <입력영수증.json>]` (권한·프로필 확인 뒤 격리 재계산·저장·PDF, PDF 쪽수·수식/구조 검사 뒤에만 발행)
- 일괄: `scripts/gg_office.py batch <파일1.docx> <파일2.docx> <파일1.xlsx> <파일2.xlsx> --out-dir .gg-work/<산출물>/<회차> --project <정본 폴더> --major <전공ID>` (전체 입력 권한·프로필 사전 확인 유지. 앱별 권한/시간초과·세션 보존 실패 뒤 해당 앱의 후속 변환은 중단)

산출물은 새 목적지 폴더에만 무덮어쓰기로 발행되며, 원본은 유지된다. Office 변환 및 기계 검사 완료는 최종 교수 승인이 아니며 최종 제출 완료로 자동 승격되지 않는다.

### 작업 조회·명시 청소

`scripts/gg_office.py jobs [--workspace <작업영역>]`는 `job-<12자리 hex>-<epoch>` 작업과 크기·파일 수·세션 상태·`cleanup_allowed`를 조회한다. `clean --job <작업ID> [<작업ID> ...] [--workspace <작업영역>]`는 지정한 작업만 삭제한다. `cleanup=closed|not_attempted` 증거가 있어야 하며 미기록·읽기 불가·preserved·unknown 세션, 심볼릭 링크, 외부 경로는 거절한다. 자동 청소·전체 삭제 옵션은 없다. `--recover word|excel`은 청소 승인이 아니며 보존된 작업은 그대로 남긴다.

청소 판정은 작업 기록과 앱 가드를 함께 확인한다. macOS의 `not_attempted`는 **이번 정상 작업이 아직 열리지 않았음**을 뜻하며 앱 가드 증거가 없으면 청소를 허용하지 않는다. 가드가 busy·inflight·unknown·읽기 불가이면 목록은 `cleanup_allowed=false`, 청소는 거절한다. 청소 중에는 앱 가드 잠금을 유지한다. 이전 실행이 unknown·preserved로 남은 같은 작업의 재진입은 그 기록을 덮어쓰지 않으며, 재진입 거절이나 `--recover`만으로 이전 작업이 청소 가능해지지 않는다.

### 의존성·Kordoc 진단

`gg_deps.py ensure`는 POSIX에서 실행 파일 symlink로 독립 Python의 상대 libpython 경로를 유지하고 Windows에서는 복사를 사용한다. pip 및 import/version probe는 부모·자식 UTF-8을 맞춘다.

`gg_kordoc.diagnose(*, cache_dir=None, node=None, kordoc=None)`는 파일·PATH만 읽는 공개 진단이다. 설치·네트워크·Kordoc 실행·캐시 쓰기를 하지 않는다. `installed`는 설치 흔적이며 실행/기능 검증 완료가 아니다(`capability_verified=false`, `reason=capability_unverified|not_installed`). `cache_dir`, `selected_cache_dir`, `cli_path`, `node_path`, `candidates`, `legacy_candidates`를 제공한다. `ensure`는 명시 캐시가 없을 때만 옛 이름/기본 위치의 캐시 후보를 찾고, 현행 고정 버전·소유 표식·패키지·실제 버전·파싱 플래그 및 필요한 generate 기능을 재검증한 후보만 읽기 전용 재사용한다. 구 버전 4.13.1을 현행 버전으로 가장하지 않는다.

실기 Office 변환 성공은 이 단위의 대역 시험으로 증명하지 않는다. **v0.6.0 배포 전 macOS·Windows 각각 실제 Word·Excel로 1회씩 변환 확인이 필요하다.**

## 본문 재무 수치의 자동 대조

정본의 범위·기간·항목·값·단위를 같은 절에 명시한 문장으로 제한한다. 예를 들어 실제 정본 값으로 `농장A 1년차 매출액은 1,000 천원이다.`처럼 작성한다(기간·범위 순서는 바꿀 수 있다). 쉼표·줄바꿈으로 나눈 각 절에 범위·기간을 반복한다. 다른 항목의 숫자·단위, 전년 값, 문서 다른 곳의 범위를 빌려 통과시키지 않는다. 암묵적 문맥·별도 반올림 규칙은 추정하지 않고 대조 실패로 남겨 근거를 확인한다. 대조는 사실의 `field_id`를 본문에서 문자열로 찾으므로, `field_id`가 `finance.own_capital` 같은 기계용 식별자면 본문에 있을 수 없다. 그런 항목은 본문 잘못(`fail`)이 아니라 대조 불가(`blocked`)로 보고하므로, 본문에 쓰는 항목 이름(예: `매출액`)으로 `field_id`를 두어야 대조된다. 이 기계 대조가 독립 내용검토를 대신하지 않는다.

## 학교 논문 본문 (특용작물)과 표 출처 (`table_sources`)

`gg.py paper <폴더> --input <spec.json> [--out <경로>] --major specialty_crops`는 특용작물 학교 논문 본문(`build/검토전_본문.md` 등)을 생성하고 관리 발행한다. `--body <병합 본문.md>`를 주면 생성기의 Ⅰ–Ⅵ 골격 대신 그 본문을 싣고 앞머리(겉표지·표제면·제출서·인준서·목차)만 생성한다. 본문은 `Ⅰ. 머리말`(마크다운 `#` 허용)로 시작해야 하고 비어 있으면 `invalid_spec`으로 거절한다. 요청서에는 `body_sha256`이 함께 기록된다.

에이전트·독립 사용자는 `gg_school_paper.py <폴더> --input <spec.json> --body <병합 본문.md> --out <경로> --major specialty_crops`로 같은 관리 발행을 호출할 수 있다(`--body` 생략 시 생성 골격 사용). 앱의 `paper.generate`는 이 직접 CLI가 아니라 `gg.py paper`를 호출한다. 두 CLI 모두 초기화된 정본 폴더·일치하는 명시 전공·비어 있지 않은 `school_profile.school`과 `department`가 필요하며, `core.paper`에서 gg_lock v2·전공 인증·출판 수령증을 적용한다. 성공은 JSON 결과, 일반 입력/파일 실패는 `status: blocked`·`reason` JSON과 exit 2다. 출판 실패는 `OperationError` 결과의 `commit_state`와 대응 exit 2/3/4를 보존한다.

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
  - 선택 필드: `url` (평문 HTTP/HTTPS만 허용), `grade` (기존 기록 호환용 메타데이터. 생략이 가능하고 1·2는 같은 허용 출처 표식이며 순위나 서지 충돌로 구분하지 않음. 3·4등급 및 무효 표식은 거부), `survey_date`, `subject`, `verified`, `id`, `revision`
  - 정본 출처 연결: `id`와 `revision`을 함께 지정하면 `project.sources` 레코드와 개정·종류·서지 충돌을 대조 검증한다.
- **인용 필드 문자 허용 부류 및 실질 문자 요건 (`_plain`)**:
  - 인용 필드(`source`, `title`, `year`, `locator` 및 비-URL 서지 필드)는 유한한 허용 문자 부류만 통과한다: NFKC 정규화 후 글자(Unicode `L*`), 숫자(`N*`), 구두점(`P*`), 지정 일반 기호 16종(`_SYMBOLS`: `°©®±×÷−+=∼₩$¥€£¢`), 일반 ASCII 공백(` `)만 허용된다.
  - 보이지 않는 결합 문자(Unicode `M*`: 결합 자모 결합자 `U+034F`, 결합 악센트 등), 변형 선택자(`U+FE00~FE0F`, `U+E0100~E01EF` 계열), 제어문자/형식문자(`C*`, 줄바꿈·탭·U+200B 등), 목록 외 기호(이모지 등)는 명시 거부된다.
  - **표시 문자열 실질 문자 요건**: 결합 마크를 제외한 정규화 표시 문자열(`display`)에 글자(`L*`) 또는 숫자(`N*`)가 반드시 1자 이상 포함되어야 한다 (기호나 구두점만으로 채운 문자열 거부).
  - **자리표시자 판정 기준**: `미정`, `미확인`, `확인필요`, `자료없음`, `n/a` 등 미확인 값 판정도 마크를 제거한 표시 문자열(`display`) 기준으로 검사하므로, 결합 문자나 변형 선택자를 끼워 넣은 위장 자리표시자(`미\u034f정`, `미\ufe0f정`, `미\U000e0100정` 등)도 `미확인 출처 값`으로 즉시 거부된다.
  - **무효 입력 처리 및 6표 무효 시 발행 차단**: 유효하지 않은 출처 입력은 빈 값으로 지워 no-op 처리하지 않고 `invalid_spec`/`not_committed` 오류로 명시 거부된다. 6개 표 모두 결합 문자(`U+034F` 등) 무효 입력인 경우 본문 발행 파일이나 발행 영수증이 전혀 생성되지 않는다.
  - **URL 필드**: 평문 HTTP(S) 주소여야 하며, 결합 마크(`M*`)나 공백이 포함되면 거부된다.
- **동작 원칙과 경계**:
  - **정확한 no-op (미제공 시 동작 없음)**: `table_sources`가 없거나 빈 객체이면 원문 텍스트를 그대로 반환한다.
  - **기본 출처 자동 삽입 없음**: 출처 미제공은 그대로 두며 본인 작성·산출·촬영 자료에는 출처가 없어야 한다. 자체 작성 출처 표기는 `object_credit` 오류다.
  - **표시와 검증의 분리**: 외부 기관명(`source`)만 중복 없이 제목 끝 `(출처 : 기관명)`으로 표시한다. 제목과 같은 글자 크기를 쓰며 상세 서지·연도·locator·URL은 입력/정본 기록에 보존한다. 직접 조사(`interview/author_survey`)는 검증하되 출처로 표시하지 않는다. 설명은 객체 아래 별도 `주 :` 줄이다.
  - **검사 한계**: 무출처 객체가 자체 작성인지 외부 자료 누락인지 기계가 추정하지 않는다. 외부 자료의 근거·권리는 사람이 검토한다. 옛 별도 출처 줄은 `object_credit_position` 권고이며, 명시된 미확인 출처 값은 오류다.
  - **렌더러 보존 (frozen)**: `gg_school_paper.paper()`의 생성 로직은 유지한다. 저수준 함수 직접 호출에는 출처가 삽입되지 않으며, 두 CLI(`gg.py paper`, `gg_school_paper.py`)가 연결하는 `core.paper` 관리 경로에서만 발행 전에 캡션 끝 괄호로 붙인다.
  - **새 revision 필요**: 기존 요청 수령증(receipt)이 존재하는 경우 재호출은 기존 결과를 반환하므로, 표 출처를 새로 적용하거나 변경하려면 새 revision(개정)과 새 출력 경로로 요청해야 한다.
