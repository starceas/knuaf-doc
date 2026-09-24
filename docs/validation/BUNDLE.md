# 검증 묶음·무결성 계약

이 문서는 공개 저장소의 검증 묶음(validation bundle)이 무엇을 포함하고,
어떻게 무결성을 검사하는지를 설명한다. 회귀 시험의 결함 목록은
`docs/validation/REGRESSIONS.md`가 담당한다.

## 두 산출물의 분리

- **설치 플러그인**: `skills/knuaf-doc/` 런타임과 `.codex-plugin/`만 포함한다.
  시험·개발 문서·학교 원문 자료는 들어가지 않는다.
- **공개 저장소 검증 묶음**: 위 런타임에 더해 `tests/`, `tools/`,
  `docs/validation/`, `.github/workflows/`, `requirements-validation.txt`를
  포함한다. 시험은 배포 런타임을 직접 import하며 개발 트리로 fallback하지
  않는다.

## 고정 진입점

```text
python3 tools/check_bundle.py --root .        # 무결성 (stdlib만 사용)
python3 tools/run_validation.py [--json out]  # 회귀 시험 한 명령
```

지원 Python은 3.10 이상이다. `check_bundle.py`는 stdlib만 사용한다.

`run_validation.py`는 회귀 시험 전에 **현재 실행 인터프리터**의 필수
의존성(`skills/knuaf-doc/scripts/runtime-deps.json` 기준)을 점검한다.
준비 실패 시 시험은 실행하지 않고 `runtime.status=not_run_dependency`로
보고한다. 판정 우선순위: 예상 외 실패 `unexpected_failures`(exit 1) >
`incomplete`(exit 3: 의존성 준비 실패·미승인 skip·최종 모드의
`GG_VALIDATION_NESTED` 등) > 미지원 Python(exit 2) > `ok`(exit 0).
JSON의 `full_validation_completed`는 실행 무결성 사실이며 제품 PASS가
아니다.

## 무결성 모델

- `tools/baseline-files.json` — 고정 main 스냅숏의 파일 해시 목록(입력 정본의
  그대로 복사). 55개 런타임 파일의 기대 SHA-256이다. **수정 금지.**
- `tools/runtime-changes.json` — 사람이 관리하는 선언 목록(P1~, schema
  `knuaf-doc/runtime-changes@2`). baseline과 다른 runtime 파일만 여기에
  명시한다: 항목별 `before_sha256`(원본 main 해시·신규는 null)·
  `after_sha256`(현재 해시)·`first_changed` 단계·소유·사유·채택 출처.
  상단에는 `stage`, `baseline_main`, `baseline_spec_sha256`,
  `parent_candidate`(직전 수용 단계의 후보 digest), `stage_lineage`(P0→…
  누적 부모 계보)를 둔다. 목록에 없는 baseline 변화는 미승인이다 —
  builder가 온디스크 drift를 자동 승인하지 않는다.
- **신규 파일 계약(P2~)**: baseline에 없는 새 제품 파일은
  `before_sha256: null`과 `first_changed` 단계 표기로만 선언할 수 있고,
  그 경로는 `approved_new_files` allowlist에 사전 명시돼 있어야 한다.
  baseline 파일을 `before=null`로 위장하거나 allowlist 밖 경로를 신규로
  선언하면 거부된다. 신규로 선언된 파일은 manifest `files[]`(검증 계층)에
  들어가지 않는다.
- `tools/validation-bundle.json` — 생성되지만 커밋되는 manifest(schema
  `knuaf-doc/validation-bundle@3`). baseline도 runtime 변경 선언도 아닌
  모든 파일의 SHA-256·provenance를 기록하고, `runtime_changes` 블록으로 위
  선언 목록의 SHA-256·단계·부모 digest·변경 수·신규 파일 수·after digest를
  연결한다. 자기 자신은 해시하지 않는다(순환 방지). 이 파일이 무결성
  검사의 신뢰 뿌리다 — 실제 git 커밋에서 리뷰 대상이 되며, 모든 검사
  출력에 자체 SHA-256을 표시해 외부 고정이 가능하다. CI는 manifest를
  재생성해 바이트 동일성을 비교한다.
- `tools/validation-provenance.json` — 사람이 관리하는 입력. 파일별 기원
  (`new`/`reused`/`transformed`/`pinned-input`/`generated`)과 개발 소스
  해시를 선언한다.

`check_bundle.py`의 판정:

| 상태 | 의미 |
|---|---|
| `ok` | 해시 일치 |
| `declared_change` | `runtime-changes.json`에 선언된 runtime 변경 — `after_sha256`과 디스크 해시가 정확히 일치. **해시 연결 검증일 뿐 변경 내용의 승인이 아니다** |
| `declared_new` | allowlist에 명시된 신규 파일의 선언 — `after_sha256`과 디스크 해시 일치. 역시 해시 연결 검증일 뿐 내용 승인이 아니다 |
| `modified_allowed` | `README.md`만 — baseline과 달라도 되며, 필수 마커(`# knuaf-doc`, `run_validation.py`, `check_bundle.py`)가 남아 있어야 한다 |
| `baseline_missing` / `undeclared_runtime_change` | 런타임 누락·미선언 변조 — 실패 |
| `runtime_change_*` | 선언 목록의 before/after 불일치·미존재 경로·경로 탈출·자가면제·중복 키·noop·검증계층 중복 선언·미허용 신규(before=null이 allowlist 밖)·baseline 위장 신규·신규 단계표기 누락 — 실패 |
| `declared_missing` / `declared_hash_mismatch` | manifest가 고정한 검증 파일이 없거나 변함 — 실패. 합법적 변경이면 `--write-manifest`로 갱신 필요 |
| `undeclared_file` | baseline도 manifest도 아닌 파일 — 실패 |
| `manifest_*` | manifest 스키마/자기참조/baseline 중복/트리 digest·baseline spec SHA·runtime_changes 링크 불일치 — 실패 |

exit code: 정상 0, 무결성 발견 1, 사용 오류 2.

## 재생성과 clean export

```text
python3 tools/build_validation_bundle.py --root . --write-manifest
python3 tools/build_validation_bundle.py --root . --check
python3 tools/build_validation_bundle.py --root . --out <새_디렉터리> --receipt <receipt.json>
```

- `--write-manifest`: 현재 트리 상태로 manifest를 재생성한다.
- `--check`: 커밋된 manifest와 재계산 결과가 바이트로 같은지 확인한다.
- `--out`: 무결성 검사를 통과한 트리만 새 디렉터리로 복사한다(기존 경로
  덮어쓰기 거부). 같은 입력은 항상 같은 `tree_sha256`을 만든다.

## CI

`.github/workflows/validation.yml`은 모든 PR과 main push에서 같은 명령을
실행한다. ubuntu 잡이 실제 시험을 수행하고, windows 잡은 **구조 검사만**
수행한다 — Windows 런타임·바이트 잠금·Office 자동화의 네이티브 검증을
주장하지 않는다. export 결정성 단계는 quoted heredoc 안에서
`os.environ['RUNNER_TEMP']`로 receipt 경로를 읽는다(repair-n1/N2 —
쉘 확장 없이 경로 공백도 안전).

ubuntu 잡은 runner 뒤에 **엄격한 리포트 consumer**를 둔다(P2-R2). 이
단계는 `validation-report.json`을 읽어 다음을 요구한다 — 하나라도
어긋나면 잡 실패이며 `|| true`/반환 코드 무시/예외 면제는 없다:

- `verdict=ok_platform_scoped`·`exit_code=0`·`mode=default`·
  `validation_scope=current_platform`만 수용 (runtime-only 리포트와
  어떤 실패/미완결 판정도 거부)
- `platform_validation_completed=true` 그리고 `full_validation_completed=false`
- `pending_external_checks`에 정확히 하나의 게이트만 — `P2-L09-native`,
  `status=not_run`·`support=withheld`·`implementation=placeholder_only`·
  정확한 test_id·`required_environment="native Windows+NTFS"`. 게이트가
  빠지거나 추가되면 거부
- `approved_external_skips` 정확히 1건(정확한 test_id·사유·gate_id·rule)
  그리고 runtime.entries에 `skipped` 원 기록이 그대로 보존
- `unapproved_skips`·`defect_tests_skipped`·`inventory_warnings`·
  `causes.unexpected`·`causes.incomplete` 전부 빈 값, `unexpected_failed=0`,
  `accounting.errors` 없음, `runtime.status=completed`·`tests_run>0`,
  bundle `pass`·dependency ready

P2-R3에서 consumer는 요약 필드 신뢰를 버리고 리포트를 독립적으로
재계산한다 — `accounting.errors`가 비어 있어도 아래 모순은 거부된다:

- 엄격 JSON: 중복 키·`NaN`/`Infinity` 같은 비유한 literal·비객체
  문서를 거부. 필수 필드는 존재와 타입을 둘 다 요구하고, 수치는 bool
  아닌 0 이상 정수다(문자열·bool·음수 거부).
- framework 회계: `runtime.accounting.framework` 객체 필수 —
  `was_successful`이 정확히 `true`, failures/errors/expectedFailures/
  unexpectedSuccesses 전부 정수 0, `testsRun`이 `runtime.tests_run`과
  `len(entries)` 모두와 일치, framework `skipped`는 정확히 1(유일한
  네이티브 skip).
- entries 재집계: 각 항목은 dict·비어 있지 않은 고유 `test` ID·인식된
  category를 가져야 하고, consumer가 직접 센 category 합계가
  `passed`/`known_baseline_defects`/`skipped`/`unexpected_failed`와
  일치하며 `failed`는 0이다. entry 삭제·복제·추가·숨은 `failed`는
  카운터가 맞아 보여도 거부.
- known-baseline(P2-R5 보수): `known_baseline_defect`는 선언된
  (test, defect, gate) 삼중 — `FinanceScopeTests.
  test_won_unit_fact_is_checked → (H9, P3)` — 만 허용하고, 그 정확한
  test ID는 entry 안에 **정확히 한 번 존재해야 한다**(제공된
  category·카운터와 무관하게). `passed`로 재라벨(메타데이터 유지·
  제거 무관)·완전 누락·동일 카디널리티 치환·중복·위조 모두 거부.
  실제 category 합계와 제공된 `runtime.known_baseline_defects`는
  둘 다 정확히 정수 1(bool·문자열·0·2+ 거부). 다른 시험의 동일
  라벨이나 라벨·게이트 조작은 위조로 거부.
- 이벤트: `fixture_events`는 빈 리스트, `failure_events`는 정수 0.
  entry `events`는 부모 ID와 일치하는 `subtest_success`만 허용 —
  숨은 subtest_failure/error/skip·미지형 이벤트·알 수 없는 이벤트
  타입은 거부.
- subtest 식별(P2-R4 보수): `subtest`는 방출된 전체 형태 `부모 +
  " (" + 비어 있지 않은 설명 + ")"`(unittest `_SubTest.id()`)여야
  한다 — 열린 괄호만 있거나 닫히지 않은 ID·빈 설명·다른 부모 접두는
  거부. Python 표현식 파서는 만들지 않는다.
- 부모-이벤트 정합(P2-R4 보수): 정확한 native withheld skip 부모는
  실행된 subtest가 없으므로 어떤 subtest event도 가질 수 없다 —
  요약·회계·게이트가 전부 정상이어도 이 부모의 위조
  `subtest_success`는 거부. 빈 events/키 부재는 무발생 표현으로
  허용되고, 정당한 성공 이벤트는 완전 성공한 ordinary·
  known-baseline 부모에만 속한다.
- 게이트 문자열: `approved_external_skips[].rule`은 truthy가 아니라
  정확한 문자열 `P2-L09-native accepted external placeholder`와
  같아야 한다.

## runner 리포트 계약 (P2-R2)

`tools/run_validation.py --json` 리포트는 P2-R2에서 필드가 추가됐다:

- `validation_scope`: `current_platform`(기본) 또는
  `development_runtime_only`(--runtime-only).
- `platform_validation_completed`: 기본 모드에서 묶음 통과·의존성 준비·
  suite 완주(tests_run>0)·미예상 실패 0·미승인 skip 0·인벤토리/회계
  불일치 없음일 때만 true. runtime-only에서는 항상 false.
- `full_validation_completed`: 외부 게이트가 하나라도 pending이면 절대
  true가 될 수 없다(현재 P2에서는 항상 false).
- `pending_external_checks`: 이 플랫폼에서 실행할 수 없는 선언 게이트의
  미실시 기록 — 현재 `P2-L09-native` 1건(placeholder 전용, 네이티브
  실행 주장 아님).
- `approved_external_skips`: 컴파일된 fail-closed 규칙이 승인한 정확한
  skip 기록 — `unapproved_skips`와 분리된다.

판정 우선순위: `unexpected_failures`(exit 1) > `incomplete`(exit 3) >
`ok_platform_scoped`(exit 0, 기본 모드·pending 게이트 존재) > `ok`
(exit 0). `ok`는 선언된 외부 게이트가 전부 실제로 닫힐 때만 도달 가능
— placeholder 통과로는 도달 불가. 이전 리포트의 단순 `ok`/`incomplete`
판정과 `full_validation_completed`만 있던 계약은 이 계약으로 대체됐다.

결과 회계(P2-R2): `runtime.entries`는 시작된 부모 시험마다 정확히 하나의
종결 분류를 갖고 `tests_run`은 그 수와 프레임워크 testsRun에 동일.
subtest 결과는 부모의 `events`에 붙고 실패/오류 subtest는 부모를 실패로
만든다. 시작된 부모가 없는 fixture 결과는 `fixture_events`로 분리되며
실패/오류 fixture는 미예상 실패로 계수된다. 프레임워크 결과 목록과 커스텀
기록의 설명되지 않는 불일치는 회계 오류(exit 1)다.

## 미실시 범위 (현재)

- 클라우드 CI: 이 workflow 정의는 아직 호스티드 러너에서 실행되지 않았다.
- 네이티브 Windows/Office, 실제 학교 양식·원문 자료, HWP: 별도 게이트.
  `P2-L09-native`는 미구현 placeholder로 남아 있으며 `ok_platform_scoped`
  판정은 현 플랫폼 범위의 성공일 뿐 네이티브 지원이나 제품 PASS가 아니다.
- `run_validation.py`의 녹색 결과는 제품 PASS가 아니다. 알려진 결함은
  개별 expected-failure로 기록되며, 자세한 목록은 `REGRESSIONS.md`를 본다.
