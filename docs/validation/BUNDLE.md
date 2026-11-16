# 검증·배포 검사 계약

이 문서는 공개 저장소의 검증 실행과 배포 처분표 검사를 설명한다.
회귀 시험의 결함 목록은 `docs/validation/REGRESSIONS.md`가 담당한다.


## 두 산출물의 분리

- **설치 플러그인**: `skills/knuaf-doc/`와 `.codex-plugin/`을 포함한다.
  현행 `gg_update.py manifest --write`는 스킬 내부 시험·합성 fixture도
  설치 목록에 기록한다. 루트 `tests/`·개발 문서·학교 원 PDF/HWP는
  설치 payload 밖이다. 0.1.5는 공개 승인된 학교 규칙 발췌·공용 지침
  목록·계보 메타데이터를 스킬 내부에 포함한다.
- **공개 저장소 검증 묶음**: 위 런타임에 더해 `tests/`, `tools/`,
  `docs/validation/`, `.github/workflows/`, `requirements-validation.txt`를
  포함한다. 시험은 배포 런타임을 직접 import하며 개발 트리로 fallback하지
  않는다.

## 고정 진입점

```text
python3 tools/check_bundle.py --root .        # 배포 처분표 검사 (stdlib만)
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

시험 수집 범위는 루트 `tests/`와 `skills/knuaf-doc/tests/` 모두다.
스킬 시험은 독립 loader로 수집해 루트 `tests` 패키지와 이름이 충돌하지
않게 하고, 동일한 결과 회계·실패 판정에 합산한다. W2 발행 워크플로는
저장소 전체 사본의 루트에서 `python -m pytest -q skills/knuaf-doc/tests`로도
스킬 시험을 실행한다. pytest는 W2 환경의 도구이며 기본 게이트 의존성은 아니다.

스킬의 `CoreVerificationHookTests`와 snapshot fixture는 정본 스키마·전체
컬렉션 외피·출처 `id`·사실 `revision`을 포함한다. 이 메타데이터 누락으로
claim 복구 생성 전에 발생하던 `KeyError: 'id'`를 시험 입력에서 바로잡았으며,
통계 근거의 값·단위·해시·위치·catalog 판정과 런타임 정본 검증은 바꾸지 않는다.
동적 후보 시험의 등록 루프는 임시 클래스 별칭을 남기지 않는다. 마지막
시험이 별칭으로 중복 수집되던 1회 실행만 제거해, 기존 598회 실행은
동일한 검증 기대값을 가진 고유 597개 시험으로 정리했다. CI의 고유 ID
검사는 완화하지 않는다.

### 스킬 시험의 선택적 외부 입력

`tools/run_validation.py:ALLOWED_SKILL_SKIPS`는 정확한 시험 ID와 사유를
묶은 유한 목록이다. Crossref 실호출(`GG_DOI_LIVE` opt-in) 1건과 배포하지
않는 저장소 밖 승인 catalog가 필요한 24건, 그리고 저장소 밖 비공개 FD-A
원본 통합문서가 필요한 2건만 등록한다. 그 2건은 R-01 오류 흔적 대조
(`tests.test_hort_finance_pipeline.LandErrorRegression.
test_error_footprint_is_scenario_independent`)와 공개문서 누출 감사
(`tests.test_hort_finance_contract.PublicContract.
test_real_h01_audit_reports_no_leak`)이며, 22좌표 잠금과 누출 감사는 그
통합문서 없이는 재현되지 않으므로 시험을 지우거나 입력을 합성으로 바꾸지
않는다. 자료가 있으면 해당 시험은 정상 실행되며 통과·실패를 그대로 기록한다.
다음은 승인하지 않는다:

- 등록하지 않은 시험 ID, 같은 클래스에 새로 추가한 시험, 바뀐 skip 사유
- 필수 런타임 의존성·배포 catalog·payload 누락
- fixture skip 또는 subtest 이벤트를 동반한 skip

승인된 결과도 `passed`로 바꾸지 않는다. `runtime.entries`에 원래
`skipped`·사유를 보존하고 `approved_skill_skips`에 `{test, reason, rule}`을
별도 기록한다. native pending 게이트·기존 nested skip 계약과는 분리된다.

`WINDOW_ONLY_SKILL_SKIPS`는 이 선택적 입력 목록과 별개다. Windows에서
제품 경로가 없는 `OfficeRecoveryTests`의 정확한 7개 시험 ID와 skip 사유만
기록하며, `sys.platform == "win32"`일 때만 승인한다. Linux·macOS에서 같은
시험이 skip되면 승인 밖으로 남아 검증을 incomplete로 한다. Windows에서도
승인 결과는 skipped로 보존하며 passed로 승격하지 않는다.

`POSIX_SKIPPED_WINDOWS_SKILL_TESTS`는 그 반대 방향이다. Windows 전용 제품
경로(고정 디렉터리 핸들의 공유모드)를 확인하는 스킬 시험의 정확한 ID와
skip 사유만 기록하며, `sys.platform != "win32"`일 때만 승인한다. Windows에서
skip되면 승인 밖으로 남는다. `tools/check_validation_report.py`도 같은 목록을
읽어 정확한 ID·사유와 이 목록 전용 rule로만 받는다.


## 무결성 모델

커밋된 파일의 지문은 git이 보유한다. 검증 결과가 어느 커밋의 것인지는
그 커밋을 내려받아 실행한 CI 기록이 증명하며, 로컬 실행 결과는 참고용이다.
학생 컴퓨터에서 받은 파일 대조는 설치 목록이 담당한다.

`check_bundle.py`의 `bundle.status`는 배포 처분표와 실제 배포 파일·설치
목록이 맞는지를 나타낸다. 오류 코드는
`asset_disposition:<처분표 검사기 코드>`다. 파일별 지문 장부와 검증 파일
출처 기록은 `fdaa3d1`까지 있었다.

## CI

`.github/workflows/validation.yml`은 시험 전체를 **5개 조각**(단위: 모듈 통째,
예외 두 모듈은 클래스·메서드)으로 나눠 동시에 돌린다. Windows 잡은 여전히
**구조 검사만** 수행한다 — Windows 런타임·바이트 잠금·Office 자동화를
검증했다고 주장하지 않는다.

| 상황 | `suite (ubuntu, py3.10)` | `suite (ubuntu, py3.14)` |
|---|---|---|
| PR (코드 변경) | 5개 조각 **코드 스코프** 시험 + 합친 보고서 contract 검사 | 빠른 확인(`tools/quick_validation.py`) |
| PR (버전 파일 변경) | 5개 조각 전체 시험 + 합친 보고서 contract 검사 | 빠른 확인(`tools/quick_validation.py`) |
| main push · `workflow_dispatch` | 5개 조각 전체 시험 + contract 검사 | 5개 조각 전체 시험 + contract 검사 |

필수 체크 이름(`suite (ubuntu, py3.10)`·`suite (ubuntu, py3.14)`·
`structural only (windows)`)은 PR·main 모두 그대로 나온다. 두 suite 마무리
잡은 조각 결과가 모두 성공한 경우에만 합친 보고서를 contract 검사한다.

### PR 스코프 (full / code)

`scope` 잡은 PR 변경 경로를 한 번 판정해 조각과 보고서 합치기에 전달한다.

| 변경 | 스코프 | 실행 범위 |
|---|---|---|
| 버전 파일(`skills/knuaf-doc/version.json`, `.codex-plugin/plugin.json`) | `full` | 전체 |
| 그 외 모든 변경 | `code` | 중첩 runner 10단위 제외 |
| 판정 불가 또는 main push·수동 실행 | `full` | 전체 |

경로는 공통 조상과 head 사이에서 계산한다. 공통 조상이 없거나 여럿이면
판정 불가로 전체 시험을 실행한다. 새 경로·모르는 경로도 `code`이며,
전체에서 빠지는 것은 `PR_DEFERRED_UNITS`(= `NESTED_RUNS`) 뿐이다.
보고서 합치기는 조각 간 시험 ID·결과의 일관성을 확인하고, PR scope는
최종 보고서 contract 검사기에 별도로 전달한다.

### 조각 배정과 합치기
두 시험 루트(`tests/`, `skills/knuaf-doc/tests/`)를 discover해 단위별로
5개 조각에 배정한다. 기본 단위는 모듈이며, 저장소 사본에서 중첩 실행을
수행하는 `test_runner_contract.py`와 `test_runner_negative_controls.py`는
클래스 또는 메서드 단위로 나눈다. Fixture가 있는 단위는 함께 둔다.

각 조각은 시험 ID와 결과를 보고하고, 합치기는 저장소에서 시험을 다시
discover해 조각 배정·시험 ID·실행 ID를 대조한다. 불일치가 있으면 실패한다.
중첩 실행 보고서는 저장소 사본 밖 임시 디렉터리에 기록되어 조각별 artifact로
올라간다. 코드 스코프에서 중첩 단위를 실행하지 않아 artifact가 비어 있으면
허용한다.

### 전후 비교 (시험 ID별)

```sh
python tools/compare_validation_reports.py baseline-py3.10.json merged-py3.10.json
python tools/compare_validation_reports.py baseline-py3.14.json merged-py3.14.json
```

두 보고서의 시험 ID 집합과 ID별 결과(category·detail·defect·gate·events)가
같으면 exit 0, 다르면 1, 읽기 실패는 2다.

### Python 3.14 빠른 확인 (PR 전용)

`tools/quick_validation.py`는 구조, Python 파일 컴파일, 시험 모듈 import와
ID 중복 여부, 고정 스모크 시험을 확인한다. 3.14 전체 시험은 main push에서 돈다.

## 미실시 범위 (현재)

- 검증 파일의 외부 원본 추적은 하지 않는다.
- 실행기는 검사 폴더와 커밋의 동일성을 확인하지 않는다.
- 클라우드 CI는 아직 호스티드 러너에서 실행되지 않았다.
- 네이티브 Windows/Office, 실제 학교 양식·원문 자료, HWP는 별도 게이트다.
- `run_validation.py`의 녹색 결과는 제품 PASS가 아니다. 알려진 결함은
  개별 expected-failure로 기록되며, 자세한 목록은 `REGRESSIONS.md`를 본다.
