# 회귀 인벤토리 — baseline 결함 분류

기준선: `main` 고정 커밋 `29abec0ec95369de7ae9e22207349d574f1f46b7`.
검토 근거: Astra 독립 검토(R1–R7) + 채택된 C/H 계열 항목.
머신 정본: `tests/regression_inventory.json` — 이 문서는 같은 내용의 사람용 표다.

## 읽는 법

- 이 저장소는 **재현·공개검증 묶음**이다. 결함은 단계(P1, P2 …)별로 고치고, 아직 열린 항목은 고치지 않는다.
- runner가 녹색(`verdict=ok`, exit 0)이어도 **제품 PASS가 아니다**. 녹색은 "분류가 정확히 동작했다"는 뜻이다.
- `known_baseline_defect` = main에서 재현 확인됐고 아직 `open`인 기저 결함. 각 항목의 수정은 표에 적힌 게이트(P1–P5) 소유.
- `fixed` = 해당 단계에서 수정됐고 지정 회귀 시험이 실제로 통과한다. 기준선 서명은 기록으로 남는다.
- `not_applicable` = 해당 결함이 main에 없는 코드 경로에 속함(PR 전용 모듈/Windows 전용 경로).
- `deferred` = main에 해당하지만 범위 밖 사유로 미재현.

## 1. main에서 재현된 결함 (14건 — P3 결과: 14 fixed / 0 open)

기준선 시험은 결함을 그대로 관찰해 실패로 기록한다. `open` 항목이 실제로 고쳐지면 같은 시험이 통과되며, 이때 runner는 XPASS를 unexpected failure로 올려 재분류를 강제한다. `fixed` 항목의 시험은 일반 회귀 assertion으로 전환돼 통과해야 한다. P1에서 12건, P2에서 C4, P3-C1에서 H9(R6-undercheck 별칭)가 fixed로 전환되어 이 표의 14건 전부가 fixed다. ACCEPTANCE.md r2 F01/F02/H9/R6 표가 명시한 대로 `test_finance_scope.FinanceScopeTests.test_won_unit_fact_is_checked`가 이 전환의 정본 시험이다.

| ID | 결함 | 게이트 | 상태 | 시험 | 기준선 서명 |
| --- | --- | --- | --- | --- | --- |
| C2 | locale 종속 텍스트 I/O(encoding 미지정) | P1 | **fixed** | `tests.test_canonical_flow.TextIoTests.test_locale_independent_text_io` + `tests.test_p1_core.Utf8IoTests` | 배포 스크립트 다수에 `encoding=` 없는 `read_text()/write_text()` |
| C3 | 비문자열 레코드 id가 canonical 오염 | P1 | **fixed** | `tests.test_canonical_flow.CanonicalFlowTests.test_integer_id_rejected_before_store` + `tests.test_p1_core.RecordIdTests` | `apply({'id': 7})`이 쓰기 통과 → 재로드 `ValueError ID/개정번호 오류` |
| C4 | stale `.gg-lock` 회복 경로 없음 | P2 | **fixed** | `tests.test_canonical_flow.LockTests.test_stale_lock_has_no_recovery_path` + `tests.test_p2_core`·`tests.test_p2_mutators` 잠금 계약 시험 | 선행 lock 디렉터리 → `쓰기 잠금 존재` 차단 + `unlock` 명령 부재 |
| C5 | Python ≥3.10 진입 가드 없음 | P1 | **fixed** | `tests.test_canonical_flow.EntryContractTests.test_python_floor_guard_declared` + `tests.test_p1_core.PythonFloorTests` | `gg.py`에 `sys.version_info` 바닥 검사 없음 |
| C6 | Windows Office 자동화가 기존 인스턴스 Quit | P1 | **fixed** | `tests.test_canonical_flow.EntryContractTests.test_office_automation_quit_blocked` + `tests.test_p1_outputs.WindowsComBlockTests` | `EnsureDispatch` 후 무조건 `Quit()`; 사전 차단 없음 |
| H1 | 병합 마커 `<`/`^`를 채워진 셀로 판정 | P1 | **fixed** | `tests.test_xlsx_structure.MergeMarkerTests.test_merge_markers_are_empty` | `_filled_cell('<')`, `_filled_cell('^')` → `True` |
| H2 | 넓은 표가 절 전체를 landscape 회전 가능 | P1 | **fixed** | `tests.test_canonical_flow.EntryContractTests.test_landscape_rotation_suppressed` + `tests.test_p1_outputs.WideTableOrientationTests` | `build_docx.py`가 `allow_landscape=False` 없이 호출 |
| H3 | TOC↔본문 사이 미렌더 노드를 무시 | P1 | **fixed** | `tests.test_canonical_flow.EntryContractTests.test_unrendered_nodes_rejected` + `tests.test_p1_outputs.UnrenderedNodeTests` | 미렌더 노드 거부 경로 없음 |
| H5 | 템플릿 공백 복사 시 wanted 셀 누락 무시 | P1 | **fixed** | `tests.test_canonical_flow.EntryContractTests.test_missing_wanted_cells_rejected` + `tests.test_p1_outputs.MissingTemplateCellTests` | `gg_excel_template.py`에 missing-wanted 거부 없음 |
| H6 | `print_titles`가 definedNames 컨테이너 없이 기록 | P1 | **fixed** | `tests.test_xlsx_structure.PrintTitleTests.test_titles_container_created_when_missing` | workbook.xml에 bare `<definedName>`; openpyxl `print_title_rows` = `None` |
| H7 | 손상 XLSX가 출력 점검을 크래시 | P1 | **fixed** | `tests.test_xlsx_structure.XlsxInspectTests.test_corrupt_xlsx_returns_issue` | `inspect_outputs`가 `zipfile.BadZipFile` 전파 |
| H8 | `unknown` 첫 답변이 reuse로 흐름 | P1 | **fixed** | `tests.test_question_gate.QuestionGateTests.test_unknown_answer_reaches_help` + `tests.test_p1_core.QuestionLadderP1Tests` | `question()`이 attempts=1에서 `reuse` 반환(계약: ask→help) |
| H9 | `원` 단위 금액이 본문 대조에서 누락 | P3 | **fixed** | `tests.test_finance_scope.FinanceScopeTests.test_won_unit_fact_is_checked` + `tests.test_finance_scope.FinanceScopeTests.test_ratio_unit_fact_still_out_of_scope` | `_finance_check_items`가 `천원`만 선별 → `원` 팩트 `([], None)` |
| H15-count | 숨김 시트가 페이지 하한 산정에 포함 | P1 | **fixed** | `tests.test_xlsx_structure.XlsxInspectTests.test_visible_sheet_count` | `workbook_worksheet_count`가 hidden 포함 2 반환 |

## 2. main에 해당 없음 (not_applicable, 8건)

| ID | 결함 | 게이트 | 이유 |
| --- | --- | --- | --- |
| R1 | benchmark-pack record_id 충돌 | P4 | `gg_rda_lookup`/`gg_rda_research`는 PR1 전용 |
| R2 | 모호 가격이 첫 행 선택 | P4 | lookup/propose는 PR1 전용 |
| R3 | builtin 출처 스킵 시 런타임 아티팩트 없음 | P5 | `builtin-sources.json`/`gg_source_intake`는 PR1 전용 |
| R4 | intake 인덱스가 기존 출처 증거 덮어씀 | P5 | `write_intake_index`는 PR1 전용 |
| R5 | unlock이 생산자의 live lock 제거 | P2 | main `gg.py`에 `unlock` 없음. 같은 계열의 main 결함은 C4로 별도 추적 |
| R6-overblock | 비금융 팩트를 재무 오류로 차단 | P3 | `unsupported_unit` 과잉 차단은 PR2/3 전용. 양성 대조 `test_nonfinancial_fact_no_finance_block`이 main에 없음을 확인 |
| R7 | WindowsLock이 자기 잠금 바이트를 제2 핸들로 읽음 | P2 | `WindowsLock`/`_NativeWindowsPort`는 PR3 전용 |
| C1 | Windows 잠금 의미론 미검증 | P2 | main Lock은 POSIX mkdir 전용; 재현할 Windows 경로 자체가 없음 |

## 3. main 해당 — P0 범위 밖 (deferred, 7건)

| ID | 결함 | 사유 |
| --- | --- | --- |
| H4 | 고정 Excel 입력 매핑 제약 | 실제 템플릿 검증 필요 — 설계 §8 deferred |
| H10 | 분기 canonical / 동기화 폴더 발산 | 별도 설계 필요 |
| H11 | 모델 fallback 문서↔명시 고정 불일치 | 런타임 경로가 아닌 문서 계약. P1에서 model-routing.md·SKILL.md에 "사용자 명시 모델 임의 폴백 금지" 문구로 정정 적용 — 시험 대상 아님 |
| H12 | Kordoc 그림 배치 | 실제 Kordoc 설치 필요 |
| H13 | Word attachedTemplate 외부 관계 | 실제 Word 저장 왕복 필요 |
| H14 | 단일 시트 학교명 교집합 오판 | heuristic 유지 결정, 범위 deferred |
| H15-coverage | 시트별 내용의 PDF 페이지 커버리지 | 실제 PDF 렌더 점검 필요 — 개수 절반(H15-count)만 재현 |

## 4. 양성 대조(통과해야 할 시험) 요약

초기화·apply 멤등·request-id 충돌·CAS 개정 보존·UTF-8 왕복·checks/초안 export·CLI 종료 계약·단일 작성자 배타·재무 본문 대조(천원 일치/불일치/전체 빈값)·비금융 팩트 비차단·질문 예산 사다리·import 격리(후보 경로 강제·외국 모듈 제거)·runner 부정 대조(누락 런타임·PYTHONPATH 오염·변조 감지)·print-titles 정상 경로·정상 워크북 비차단. P3-C1 추가: 설치 registry가 gg_core.py 자체 배포 위치 기준으로 해석되고 사용자 프로젝트 루트에서는 결코 해석되지 않음(Lane 28)·reviewKind content/logic/calculation 세 종류가 동일한 freshness 계약을 공유함(Lane 27)· 쓰기 시점 고정 무결성과 현재성 재도출을 모두 요구함(Lane 25)·출력 증거의 신원 바인딩이 무관한 신선 리뷰로 대체되지 않음(Lane 26). 전수 목록은 runner JSON의 `passed` 엔트리.

## 5. 검증 한계

- **네이티브 Windows/Office 미실행**: macOS/Linux에서 구조·정적 검사만 수행. C1/C6/R7의 네이티브 확인은 미래 게이트 소유. Windows CI 잡은 구조 검사만 한다.
- **클라우드 CI 미실행**: `.github/workflows/validation.yml`은 정의됐으나 호스티드 러너에서 아직 실행되지 않았다.
- **Python 3.9 정책 유보**: 지원 바닥은 3.10. C5 진입 가드는 P1에서 추가됐고, 실제 Python 3.9.6(`/usr/bin/python3`)으로 사전 거부 JSON·exit 2·프로젝트 미생성·부수효과 없음을 관측했다(관측 산출물은 배포 저장소에 포함되지 않는다).
- **합성 fixture만 사용**: 실제 사용자 문서·학교 원본·비밀정보 없음. 실물 서식의 정합성은 별도 게이트.
- **녹색 ≠ 제품 PASS**: `open`으로 남은 항목은 열려 있는 main 결함이며, runner는 이를 통과로 바꾸지 않는다. 현재 이 표에 `open` 항목은 없다(P3-C1에서 H9가 마지막으로 닫혔다) — 그렇다고 §5의 다른 검증 한계(네이티브 Windows, 클라우드 CI, 실물 서식)가 닫힌 것은 아니다.

## 6. runner 실행 계약 (repair-N1)

`tools/run_validation.py`는 시험 전에 **현재 인터프리터**의 필수 의존성을 점검한다. 정본은 후보의 `skills/knuaf-doc/scripts/runtime-deps.json`이며, 후보의 `gg_deps.py` 읽기 전용 helper(manifest 검증·in-process import·배포판 버전·선언 범위 비교)로 판정한다. 목록·버전은 코드에 하드코딩되지 않는다.

- 적용 대상 의존성은 import 성공 + dist 버전 확인 + 선언 범위 만족을 모두 요구한다. `missing`/`unsupported-version`/`installed-version-unchecked`/manifest 오류는 준비 실패다. `find_spec` 통과만으로 인정하지 않는다.
- 선언 `platform`이 `sys.platform`과 다른 항목(예: `pywin32`의 `win32`)은 `not_applicable`로 기록하며 결코 누락으로 세지 않는다.
- 준비 실패 시 시험은 실행하지 않는다: `runtime.status=not_run_dependency`, 실행 수 0, 의존성 진단을 JSON/요약에 남긴다. 이는 passed/known/제품 결함으로 집계되지 않는다.

**스킵 정책**: 의존성 준비가 통과한 뒤의 예상 외 unittest skip은 성공을 막는다. XLSX 시험의 dependency-skip은 제거됐다 — import/fixture 오류는 failure/error로 드러난다. inventory `not_applicable`(§2)과 명시된 deferred(§3)는 unittest skip과 다른 분류이며 그대로 유지된다.

**중첩 플래그**: `GG_VALIDATION_NESTED`는 시험이 runner를 재귀 호출할 때 무한 재귀를 막는 내부 접점이다. 기본(최종) 모드에서 이 변수가 비어 있지 않으면 `incomplete`(exit 3). `--runtime-only` 내부 실행에서 정확히 `GG_VALIDATION_NESTED=1`일 때만 아래 유한 목록의 skip이 허용된다 — 다른 값·사유 문자열·ID 접두사·플랫폼 문자열 면제는 없다. 내부 실행의 의존성 누락도 성공 불가다.

허용 중첩 skip 목록(전부 runner subprocess를 생성해 재귀 방지가 필요한 시험):

- `tests.test_runner_negative_controls.RunnerNegativeControlTests.test_missing_runtime_module_detected`
- `tests.test_runner_negative_controls.RunnerNegativeControlTests.test_pythonpath_pollution_does_not_shadow_runtime`
- `tests.test_runner_negative_controls.RunnerNegativeControlTests.test_tampered_runtime_module_detected`
- `tests.test_runner_contract.RunnerContractTests.test_dependency_stub_versions`
- `tests.test_runner_contract.RunnerContractTests.test_injected_skip_blocks_success`
- `tests.test_runner_contract.RunnerContractTests.test_missing_dependencies_block_run`
- `tests.test_runner_contract.RunnerContractTests.test_nested_flag_rejected_in_final_mode`
- `tests.test_runner_contract.RunnerContractTests.test_unexpected_failure_beats_incomplete`
- `tests.test_runner_contract.RunnerContractTests.test_xpass_surfaces_as_unexpected`
- `tests.test_runner_contract.RunnerContractTests.test_fixed_defect_mapping_required`

위 목록은 정확히 10개로 고정된다. bundle 변조 subprocess 통제는 별도 메서드가 아니라 `test_unexpected_failure_beats_incomplete` 안에 결합돼 있어 11번째 ID를 추가하지 않는다 — 변조된 사본의 기본 실행은 `bundle.status=fail`·`verdict=unexpected_failures`·exit 1·`platform_validation_completed=false`를 요구한다.

**판정 우선순위**(P2-R2): bundle 변조/fail 또는 실제 시험의 unexpected failure/error/XPASS·inventory 불일치·framework-회계 불일치 → `unexpected_failures`(exit 1). 그것이 없고 checker 누락·의존성 준비 실패·미승인 skip·최종 모드의 nested flag → `incomplete`(exit 3). 미지원 Python → exit 2. 그 외 기본 모드에서 선언된 외부 게이트가 pending이면 **`ok_platform_scoped`(exit 0)**, pending이 없으면 `ok`(exit 0). 여러 원인이 공존하면 `causes.unexpected`/`causes.incomplete`에 각각 기록한다.

**완료 플래그**(P2-R2): `platform_validation_completed`는 기본 모드 + bundle pass + 의존성 통과 + 시험 완료(tests_run>0) + unexpected/inventory/incomplete/회계 오류 없음일 때만 `true` — `--runtime-only`는 항상 `false`. `full_validation_completed`는 platform이 true이면서 `pending_external_checks`가 비어 있을 때만 `true` — 현재 P2는 P2-L09-native가 pending이므로 항상 `false`다. 두 플래그 모두 실행 무결성 사실이며 제품 PASS가 아니다.

**외부 게이트 규칙**(P2-R2, fail-closed·컴파일 상수): 정확한 테스트 `tests.test_p2_windows.WindowsBandCase.test_native_windows_two_process_contention`가 darwin/linux에서 정확한 사유 `native Windows+NTFS required — withheld`로 skip될 때만 `approved_external_skips`로 인정되고 `pending_external_checks`에 `P2-L09-native`(status `not_run`, support `withheld`, implementation `placeholder_only`)를 기록한다. 같은 ID의 pass/known-baseline 결과는 위조로 간주해 inventory 불일치(exit 1), 다른 사유·다른 플랫폼·중복·부재도 면제되지 않는다. 사유 문자열을 복사한 다른 시험의 skip은 승인되지 않는다. `--runtime-only`에서도 동일하게 분류되며 게이트는 별도 보고되지만 완료 플래그는 항상 `false`다.

**결과 회계**(P2-R2): `runtime.entries`는 시작된 부모 시험마다 정확히 하나의 terminal 분류를 갖고 `tests_run`은 framework `testsRun`과 같다. subTest 결과는 부모의 `events`로 보존된다 — 실패/에러 subTest가 하나라도 있으면 부모는 `failed`(passed/known-baseline 불가), skip subTest만이면 부모는 `skipped`(실패 우선). 클래스/모듈 fixture 결과는 시작된 부모가 없으므로 `fixture_events`에 분리 기록되며 failure/error는 `unexpected_failed`에 계산되고 skip은 미승인 skip이다. framework의 failures/errors/skipped/expectedFailures/unexpectedSuccesses와 기록을 대조하고 불일치는 회계 오류(exit 1)다. `wasSuccessful()==False`이면 exit 0 불가. unittest `expectedFailure`·`unexpectedSuccess`는 둘 다 unexpected 실패로 계산된다 — KNOWN_DEFECTS/inventory 경로만이 기저결함을 인정한다.

## 7. P4 패키징 검증 (2026-09-21, packaging lane — 준비 증거이며 수용 아님)

본 절은 `P4-packaging` 레인(work/g005-p4-packaging-1)이 생성한 패키지보내기에서 실행한 검증 결과다. 상위 레인들의 독립 수용은 별도 문서에 기록됐으며, 여기의 수치는 이 export에서 새로 실행한 독립 영수증에 근거한다.

**실행 스코프 표기**: synthetic fixture 경로와 실제 전국 팩(national)·4개 팩 전체·프로덕션 라이프사이클 범위는 명시적으로 구분된다. 기본 CLI는 설치된 수용 카탈로그를 아직 갖지 않는다 — 이는 파일 복사나 bundle PASS로 해소되지 않는 기존 스코프 제한이다.

- **runner (이 export에서 재실행, `python3 -B tools/run_validation.py --json`)**: verdict=`ok_platform_scoped`, exit=0, tests_run=485, passed=484, skipped=1(승인된 P2-L09-native 외부 게이트 — native Windows+NTFS 미보유, withheld), not_applicable=8, fixed=15, open=0, deferred=7. bundle=pass, dependency=ready.
- **validation bundle**: `build_validation_bundle.py --root public --check` — current, 23 files, deterministic rebuild identical; `check_bundle.py` — status=ok, baseline=55, declared=23, errors=none, manifest sha256 `829b6993699ff5094d3d01f0fd7e86580b3830700547579d6be5cb97622879bf`.
- **D01 (6,055 bijection)**: catalog-transform corrections-1 산출 manifest 4개를 byte-identical로 이식 — 102/365/2600/2988행, audit_key 1:1 검증은 해당 레인의 VERIFY PASS에 근거하며 이 export에서 파일 해시 일치를 재확인.
- **D02/D05 (lookup 경계)**: quarantined(2,950)는 `not_found`/`ambiguous` 계약 유지 — lookup 모듈은 provenance lane 바이트(`9e2a8c17…`)와 동일.
- **D08 (provenance 스냅샷 필드)**: `gg_rda_provenance.py`는 수용된 shared-core 사본 `b23142c7…`에서 왔다 — provenance lane의 구 사본 `4982c1fc…`는 사용하지 않았다(sequential transfer 소유권).
- **캐시 제외(RECV-SC-C3-1)**: export 생성 시 rsync exclude + 사후 재스캔으로 `__pycache__`/`.pyc` 0건. 테스트 중첩 subprocess가 쓴 `scripts/__pycache__` 1건을 제거하고 cache-free export에서 bounded check를 재실행해 PASS 확인(P-3c).
- **미해결·범위 외**: P2-L09-native(네이티브 Windows+NTFS) 게이트는 여전히 pending이며 §5 한계는 유지된다.
