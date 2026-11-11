# 명령 목록

`scripts/gg.py`의 명령·필수 인자·정본 쓰기 여부를 찾는 목차다. 출처·출력·정본 저장의 상세 형식은 각 행이 가리키는 계약 문서에 있다. 구현 파일(`scripts/gg_core.py` 등)은 수천 줄이라 전체를 읽으면 맥락이 가득 차므로 열지 않는다. 이 목차와 계약 문서에 없는 옵션은 `gg.py --help`로 확인한다.

## 실행 규칙

```
<파이썬> <스킬>/scripts/gg.py <명령> <폴더> [옵션]
```

- `<파이썬>`: `scripts/gg_deps.py python <폴더>` 또는 현재 환경의 Python 3.10+ 인터프리터. 3.9 이하면 `gg.py`가 차단 JSON을 출력하고 끝난다.
- `<폴더>`: 대상 작업폴더. `rda-lookup`·`rda-propose`·`rda-apply`만 폴더 없이 실행한다.
- 출력: 명령이 실행되면 JSON 한 덩어리다. 거절·보류도 JSON이며 `reason`·`cleanup_errors`에서 사유를 확인한다. 명령 이름 오류 같은 인자 파싱 오류와 폴더 누락은 JSON이 아닌 오류 문장으로 끝난다.
- 종료 코드:
  - `0`: 정상 완료(미리보기 포함).
  - `1`: `check`가 막는 항목(error 수준)을 찾음. `source-refresh`가 미리보기·최신·재등록 외의 결과를 냄.
  - `2`: 거절·보류(필수 인자 누락, 전공 보류, `not_committed` 등).
  - `3`(`committed_cleanup_pending`): 그 연산의 대상(`target`)은 반영됐고 정리만 남았다. 대상이 정본이라는 뜻은 아니다.
  - `4`(`indeterminate`): 그 연산의 대상이 반영됐는지 확정하지 못했다.

3·4가 나오면 같은 명령을 바로 다시 실행하지 않는다. 결과 JSON의 `operation`·`target`·`request_id`·`reason`, 남은 정리(`cleanup_errors`), 보존된 경로(`preserved_paths`·`receipt_path`·`publication_state`)를 먼저 읽는다. 무엇을 확인할지는 `target`에 따라 다르다.

| `target` | 대상 | 나오는 명령 | 확인할 것 |
| --- | --- | --- | --- |
| `canonical` | `project.json` | `init`, `apply`, `adopt-output`, `question`, `source-register`, `source-view`, `source-refresh`, `intake-manuscript` | `status`의 `revision`이 결과의 `revision`과 같고, 결과에 `request_id`가 있으면 `project.json`의 `requests`에 그 ID가 있어야 반영된 것이다. `init`은 `project.json`이 revision 0으로 있는지 본다. `source-register`·`source-view`의 `reason`이 `post_publish_verify_failed`·`committed_publication_missing`이면 정본이 아니라 `sources/registered/`의 등록 기록이나 `sources/generated/`의 뷰 파일이 확인 대상이다. |
| `export` | 검토 산출물 발행 | `export` | 정본은 바뀌지 않는다. 정본 개정으로 판정하지 않는다. 발행 폴더 `build/<개정>/<kind>/`와 `cleanup_errors`의 경로를 확인한다. |
| `requested_output` | 요청한 출력 경로 | `paper` | 정본은 바뀌지 않는다. `publication_state: managed_ready`이면 관리 사본은 `preserved_paths`에 있고 요청 경로에만 못 옮긴 것이다. |
| `observation` | 검토 관측 기록 | `observe` | 정본은 바뀌지 않는다. `receipt_path`의 기록을 확인한다. |
| `import_destination` | 새 작업폴더 | `import` | 원본 폴더는 바뀌지 않는다. `--out` 폴더와 `preserved_paths`를 확인한다. |
| `answer_snapshot` | 원답변 보존 파일 | `intake-manuscript` | `state`·`reason`·`cleanup_errors`와 목적/임시 보존 경로를 확인한다. 발행 오류/불확실성이면 선택 facts apply 전 미완료이며 exit 2/3/4다. 앞선 정본 snapshot 등록은 `registration_revision`에 별도로 남는다. [source-intake](source-intake.md#원고-파일-하나-접수) |

`lock-upgrade`의 결과에는 `target`이 없다. `recovery_id`·`receipt_path`·`preserved_paths`를 확인하고, 이어서 진행할 때는 `--resume <recovery_id>`를 쓴다([locking](locking.md)). 남은 임시 파일·잠금·보존 사본은 자동으로 지우지 않는다.

## 결과 `detail` 코드 (v0.4.8)

`paper`가 입력값 형식 때문에 exit 2로 멈출 때 결과 JSON의 `detail`에 어느 칸이 문제인지 담는다. `detail`은 아래 고정 코드와 고정 필드 이름만 남기고, 원문 문자열·경로·학생이 쓴 값은 담지 않는다. 이 절은 v0.4.8 결과 계약이며, `detail`이 없는 결과를 받으면 `reason`만으로 판단한다.

| `detail.code` | `detail`의 나머지 키 | 뜻 |
| --- | --- | --- |
| `spec_field_invalid` | `field`: `start_year` 또는 `writing_year` | 그 연도 칸이 정수가 아니다(문자열·소수·범위 밖) |
| `native_field_invalid` | `missing`: 형식이 틀린 칸 이름 목록 | 숫자만 받는 경제 칸에 글자·단위가 섞였다 |

`native_field_invalid`의 `missing`은 시작·목표 × 면적·매출·소득의 12개 이름(`start_area_m2`/`area_m2`, `target_area_m2`/`goal_area_m2`, `start_sales_thousand`/`sales_thousand`, `target_sales_thousand`/`goal_sales_thousand`, `start_profit_thousand`/`profit_thousand`, `target_profit_thousand`/`goal_profit_thousand`) 중 학생이 실제로 쓴 키만 담는다. 목록에 없는 이름은 지우고, 남는 이름이 없으면 `missing` 키 자체를 넣지 않는다. 여러 칸이 틀리면 위 순서로 담는다.

칸을 특정할 수 없는 `invalid_spec`는 `detail`이 붙지 않는다(결과 `reason` 값이다). 코드별로 학생에게 할 말은 [검사 결과를 학생 말로](outputs.md#검사-결과를-학생-말로)를 쓴다.

## 전공 계약 (공용 규칙 B)

논문 본문·재무표·산출물을 만드는 경로(`export`, `paper`, `adopt-output`, `finance-tables`, `build_hwpx.py`, `build_docx.py`)는 다음 두 조건이 모두 맞아야 실행된다.

1. 명령 인자의 명시 전공 ID(`--major <전공ID>`)
2. 정본(`project.json`)의 유효한 `common.major_id` 사실 바인딩

하나라도 없거나 다르면 아무것도 만들지 않고 exit 2로 멈춘다. `reason`은 `major_id_required`(`--major` 없음), `major_binding_required`(정본 바인딩 없음), `major_binding_mismatch`(둘이 다름) 등이다. 바인딩 등록은 아래 "apply 변경 JSON 계약"의 예제, 전공별 모듈 버전·지원 범위는 [majors](majors.md)를 따른다.

## gg.py 명령 표

"정본 쓰기"는 `project.json`의 개정이 바뀌는지다. 산출물·수령증 파일만 쓰는 명령은 그 위치를 적었다.

| 명령 | 필수 인자 | 선택 인자 | 정본 쓰기 | 하는 일 · 상세 |
| --- | --- | --- | --- | --- |
| `init` | — | — | 예 | `project.json`과 `.gg-lock`만 만든다. 폴더 안 기존 파일은 건드리지 않는다. 정본이 이미 있으면 `existing_canonical`로 거절한다. |
| `import` | `--out <새폴더>` | `--offline-confirmed` | 새 폴더에만 | 기존 Markdown 폴더를 새 작업폴더로 옮긴다. 원본은 바꾸지 않는다. 잠금 프로토콜이 없는 구형 폴더는 `--offline-confirmed`가 있어야 한다. [section-ledger](section-ledger.md) |
| `doctor` | — | `--probe` | 아니오 | 설치·의존성·잠금 진단. `--probe`는 잠금 점유를 읽기 전용으로 확인한다. [locking](locking.md) |
| `status` | — | — | 아니오 | `{revision, checks, tasks, completion, skill_ready, guideline_ready, user_finish_pending, professor_approval_pending, professor_approval}` |
| `next` | — | — | 아니오 | 다음 작업 목록 |
| `check` | — | `--scope all\|submission` | 아니오 | 검사 결과 배열. `submission`은 제출 관문이다. 막는 항목이 있으면 exit 1. |
| `apply` | `--change <JSON경로>` `--expected-revision <N>` | — | 예 | 정본 변경의 일반 경로. 형식은 아래 절과 [section-ledger](section-ledger.md#저장) |
| `question` | `--field <필드ID>` | — | 예(질문 횟수) | `{action, field, notice}`. `action`은 `ask`·`help`·`deferred`·`reuse`다. `ask`·`help`면 질문 횟수를 정본에 기록한다. [mid-work-questions](mid-work-questions.md) |
| `bundle` | `--scope <절ID>` | — | 아니오 | 한 절의 검토 꾸러미(본문·원자료·사실·규칙·검토축) |
| `export` | `--major <전공ID>` | `--kind draft\|review\|submission_candidate`(기본 `review`) | 아니오(산출물 발행) | 검토 산출물을 발행한다. [outputs](outputs.md) |
| `paper` | `--input <폴더 안 spec.json>` `--major <전공ID>` | `--out <폴더 안 경로>` `--body <폴더 안 본문MD>` | 아니오(산출물 발행) | 학교 양식 본문 생성. 기본 출력 `build/검토전_본문.md`. [outputs](outputs.md) |
| `adopt-output` | `--input <폴더 안 spec.json>` `--expected-revision <N>` `--major <전공ID>` | `--request-id <ID>` | 예 | 출력 파일을 `outputs`에 등록하는 유일한 경로. [source-contract](source-contract.md#출력-등록adopt-output과-동반-파일) |
| `observe` | `--input <폴더 안 관측JSON>` `--observer <어댑터ID>` | — | 아니오(관측 기록 발행) | 실행 어댑터의 검토 관측 기록을 남긴다. [section-ledger](section-ledger.md) |
| `unlock` | — | — | 아니오 | 비정상 종료로 남은 잠금 소유 기록만 정리한다. [locking](locking.md) |
| `lock-upgrade` | `--offline-confirmed` | `--resume <복구ID>` | 아니오(잠금 구조) | 구형 잠금의 오프라인 전환·복구. 편집 중인 프로세스가 없다고 사용자가 확인한 뒤에만 쓴다. [locking](locking.md) |
| `major-plan` | — | `--major <전공ID>`(바인딩과 같아야 함) | 아니오 | 바인딩된 전공의 질문·문서 계획·근거·재무 능력. 바인딩이 없거나 무효면 exit 2와 `status: held`. 낙농(dairy)은 축종 스위치(dairy_switch) 상태를, 식량작물(food_crops)은 갈래 스위치(entry_mode, cropping_system) 상태를 포함한다. [majors](majors.md) · [dairy](dairy/README.md) · [food-crops](food-crops/README.md) |
| `insect-plan` | — | `--input <폴더 안 선택.json>` | 아니오 | 산업곤충 바인딩 프로젝트 전용. [industrial-insects](industrial-insects/README.md) |
| `insect-evidence` | `--pdf <R0>` `--xlsx <R1>` | — | 아니오 | 산업곤충 공식 통계 원자료 근거 검토. [industrial-insects](industrial-insects/README.md) |
| `rda-candidates` | `--crop <작목>` `--region <지역>` | `--rda-kind` `--year` `--form` `--unit` `--use-scope` `--audit-key <JSON>` | 아니오 | 정본 바인딩 전공의 승인 후보 조회(작성 경로). [rda-benchmark](rda-benchmark.md) |
| `rda-lookup` | `--crop <작목>` `--region <지역>` (폴더 없음) | `--major` `--rda-kind` `--year` `--form` `--allow-web` `--audit-key <JSON>` | 아니오 | 원자료 감사용 조회. 본문 승인 경로가 아니다. [rda-benchmark](rda-benchmark.md) |
| `rda-propose` | `--input <제안입력JSON경로>` (폴더 없음) | `--packs-dir` `--catalog` | 아니오 | 입력 JSON의 `pack_revision`·`target`·`selection`으로 연구 제안을 만든다. [rda-benchmark](rda-benchmark.md#6-연구-제안--정본-반영-propose--apply) |
| `rda-apply` | `--input <제안JSON경로>` `--expected-revision <팩 개정>` `--target-current <대상JSON>` (폴더 없음) | `--packs-dir` | 아니오 | 제안을 다시 대조해 `ready`·`stale`·`rejected`를 판정한다. `ready`의 `fact`는 정본에 쓰이지 않으므로 반영하려면 `apply`로 따로 제출한다. `--expected-revision`은 정본 개정이 아니라 제안에 묶인 팩 개정이다. |
| `intake-manuscript` | `<선택파일>` `--input <폴더 안 원답변source.json>` `--expected-revision <N>` `--request-id <ID>` | — | 예 | 선택한 원고 하나의 수령증·보존 사본·출처 뷰와 원답변/선택 사실 연결을 기존 엔진으로 순서대로 수행한다. 정본이 없으면 `N=0`으로 같은 폴더에 초기화한다. 선택파일은 작업폴더 기준 또는 절대경로, 원답변 파일은 input의 `path`로 명시한다. `status: incomplete`이면 이전 단계가 보존돼도 접수 완료가 아니다. [source-intake](source-intake.md#원고-파일-하나-접수) |
| `source-scan` | `--root <자료폴더>` `--registry <경로>` `--key-catalog <경로>` `--policy <경로>` | `--requested-use <JSON>` | 아니오(`sources/intake/` 수령증) | 자료 폴더를 스캔해 봉인 수령증을 남긴다. 등록은 하지 않는다. [source-intake](source-intake.md#폴더-등록-방식) |
| `source-resolve` | `--source-hash <sha256>` `--requested-use <JSON>` `--registry <경로>` `--key-catalog <경로>` | `--catalog` `--packs-dir` | 아니오 | 자료가 요청 용도에 쓸 수 있는지 판정한다. [source-intake](source-intake.md#폴더-등록-방식) |
| `source-register` | `--selection <JSON>` `--expected-revision <N>` `--request-id <ID>` `--registry <경로>` `--key-catalog <경로>` | `--external-root`(`scanned_external`이면 필수) `--catalog` `--packs-dir` | 예 | 사용자가 고른 자료만 정본 출처로 등록한다. [source-intake](source-intake.md#폴더-등록-방식) |
| `source-view` | `--request-id <ID>` | — | 아니오(`sources/generated/` 뷰) | 등록 요청의 출처 뷰를 발행한다. [source-intake](source-intake.md#폴더-등록-방식) |
| `source-refresh` | `--source <출처ID>` | `--reviewed` `--apply` `--expected-revision <N>` `--request-id <ID>` | `--reviewed --apply`일 때만 | 원자료 바이트가 바뀐 출처의 미리보기와 재등록. SKILL.md "검사 차단 복구" |
| `finance-tables` | `--xlsx <재계산 통합문서>` `--major specialty_crops` | `--input <폴더 안 각주.json>` `--out <폴더 안 경로>` | 아니오(산출물) | 특용작물 Ⅳ장 재무표 전개. 기본 출력 `build/finance-tables.md`, 기존 파일은 덮어쓰지 않는다. [specialty-crops/finance-flow](specialty-crops/finance-flow.md) |
| `delegate` | `--lane <research|writing|repeat|review>` `--task <작업>` `--output <출력 칸 아래 상대경로>`(반복 가능) `--company <claude|codex>` `--model <ID>` | `--input-ref <출처ID|폴더상대경로|대상절>`(각 값을 `|`로 구분, 반복 가능) `--effort <값>` `--job-id <ID>`; research 레인은 `--research-type` `--question <Qn|목표지표|조건=값;…>` `--route <문서|ID/이름|URL>`(반복 가능) `--keyword <검색어>`(반복 가능) `--output-file <출력 칸 아래 상대경로>` 필수. `--output-file`은 `--output` 하나와 글자 그대로 같아야 한다. 수신 검사: `--check-job <ID>` | 아니오(`.knuaf-gui/jobs/`) | 앱 계약 칸으로 주문서를 만들고, 입력 참조의 실재·SHA-256, 출력 상대경로와 담당 간 파일/부모·자식 출력 소유권 겹침을 쓰기 전에 검사한 뒤 고정한 출력 디렉터리에 원자적으로 쓴다. 쓰기 전에는 디렉터리 체인 및 임시 파일 이름의 no-follow identity와 열린 fd를 대조한다. 이동·교체 탐지 시 게시 후라도 소유 파일 정리를 시도하고, 확인·제거하지 못하면 `cleanup_pending`과 마지막 확인 위치를 보고한다. 링크·재지정 경로로 폴더 밖에 원래 있던 무관한 파일을 바꾸지 않는다. POSIX 폴더 이동·정리 중 이름 교체의 잔여 경합 한도는 아래를 따른다. research 수신 검사는 질문 상태·표 필수 칸·주문 경로 및 번호와 `성공`/`실패` 경로 상태를 대조하고, 수치 조사에서 `증거 확보`·`조건 불일치` 질문마다 단위·조건 행을 요구하며 `자료 없음`·`접근 실패` 질문은 관측값 행 없이 받는다. [research-job](research-job.md) · [model-routing](model-routing.md)
| `lanes-propose` | `--lane-scope <once 또는 continue>` `--quote <학생 원문>` `--lane-request <레인=main 또는 회사:모델[:effort]>`(반복 가능; 최소 1개) | — | 아니오(`.knuaf-gui/lanes-request.json`) | 학생이 말로 지정한 일부 레인 배치를 제안 파일로 옮긴다. 레인은 `design`·`research`·`writing`·`repeat`·`review`; 회사는 `claude`·`codex`. 계약 schema를 쓰고 기존 제안 파일은 원자적으로 바꾼다. 쓰기 전 디렉터리·temp 이름과 fd 대조, 게시 후 실패 정리·위치 보고 및 폴더 밖 기존 무관 파일 보호는 `delegate`와 같다. 기존 배치안 교체를 포함한 POSIX 잔여 경합 한도는 아래를 따른다. [model-routing](model-routing.md)
| `tidy` | — | `--apply` | 아니오(`--apply`는 `.gg-work` 정리) | 산출물 폴더 정리 미리보기. 사용자 확인 뒤에만 `--apply`. [output-hygiene](output-hygiene.md) |

`--selection`·`--requested-use`·`--audit-key`·`--target-current`는 파일 경로가 아니라 JSON 문자열이다.
위임 명령은 실제 AI·웹 서비스 호출을 하지 않는다. 결과 JSON은 생성·제안 파일 경로와 schema 판을 반환한다. JobSpec과 LaneRequest의 schema 및 JobSpec `contractVersion`은 스킬 소유 `delegation.json`에서 읽는다.

**위임 쓰기의 POSIX 잔여 경합:** 마지막 검사와 그다음 시스템 호출 사이에 같은 사용자 권한의 프로세스가 이 명령의 폴더를 통째로 밖으로 옮기면, 그 폴더 안에 이 명령이 쓴 임시 파일이나 정식 결과(기존 배치안 교체 포함)가 남을 수 있다. 탐지해 실패할 때는 고정 dir fd에서 이름의 no-follow inode가 소유 파일과 일치하는지 확인한 뒤 정리를 시도한다. 정리의 identity 검사와 실제 unlink 사이에 같은 사용자 프로세스가 그 이름에 다른 파일을 넣으면 대체 파일이 지워질 수 있다. POSIX에는 inode 조건부 unlink가 없어 이 이름 교체도 받아들이는 남은 위험이다. unlink 성공 뒤에도 보유한 소유 fd의 `fstat().st_nlink == 0`을 확인한다. temp·게시 결과 어느 쪽이든 소유 링크가 남거나 확인에 실패하면 fd를 닫기 전에 위치를 확보한다.

찾지 못함·identity 불일치도 정리 성공으로 보지 않으며, 확인하거나 지우지 못하면 `delegation_write_cleanup_pending:<마지막 확인 위치>` 오류를 낸다. 핸들로 현재 위치를 확인할 수 있으면 그 위치를 쓰고, 확인할 수 없으면 마지막으로 알던 위치를 쓴다. 논문 폴더 밖 위치는 절대경로로 보고한다. 기존 배치안을 교체한 뒤 실패한 경우 이전 배치안을 복원하지는 않는다. Windows는 기존 `gg_winfs.Directory` 조상 핸들이 이동을 막는 설계이며, 삭제 공유 제한 때문에 파일 fd를 unlink 전에 닫으므로 위 POSIX fd의 nlink 검사를 적용하지 않는다. Windows 네이티브 실행은 미검증이며 `P2-L09-native` 외부 게이트로 남는다.

## apply 변경 JSON 계약

변경 객체는 `request_id`와 `ops` 배열이다. 각 op는 `{"collection": <컬렉션>, "value": <레코드 객체>}`이며 레코드 ID는 `value.id`다. 컬렉션은 `sources`·`facts`·`sections`·`questions`·`tasks`·`reviews`·`rules`·`approvals`·`outputs`다. 같은 ID는 새 개정으로 갱신되고, 레코드 삭제 연산은 없다.

- `revision`은 서버가 매긴다. `value`에 적지 않는다.
- `--expected-revision`이 현재 개정과 다르면 `revision_conflict`로 거절한다. 같은 `request_id`·같은 내용의 재실행은 개정을 바꾸지 않고 현재 정본을 돌려주며, 같은 `request_id`에 다른 내용은 `request_id_conflict`로 거절한다.
- 성공하면 반영된 정본 전체를 JSON으로 출력한다. 거절은 exit 2와 `reason`을 가진 JSON이고 정본은 바뀌지 않는다.
- `sources`는 `path` 파일의 해시를, `sections`는 DRAFT 경계 안 본문의 해시를 서버가 계산한다. 경로는 작업폴더 기준 상대경로다.
- `outputs`는 새로 만들거나 고칠 수 없다(`adopt_output_required`). `adopt-output`을 쓴다. 기존 출력에 `superseded_by`만 처음 연결하는 갱신은 예외다.
- 사실·규칙·검토·승인 레코드의 필드 조건은 [section-ledger](section-ledger.md), 출처 레코드는 [source-contract](source-contract.md#출처-레코드), 재무 사실은 [finance-semantics](finance-semantics.md)를 따른다.

### 예제: 새 작업폴더에 전공 바인딩 등록

학생 원답변 파일 `interview/major-answer.txt`(경로는 예시)가 작업폴더 안에 있다.

```text
전공: 특용작물전공 (specialty_crops)
```

변경 파일 `bind-major.json`:

```json
{
  "request_id": "bind-major-specialty-crops",
  "ops": [
    {
      "collection": "sources",
      "value": {
        "id": "major-answer",
        "path": "interview/major-answer.txt",
        "claims": {
          "major": {
            "field_id": "common.major_id",
            "value": "specialty_crops",
            "unit": "",
            "period": null,
            "scope": "project",
            "answer_state": "provided",
            "kind": "reported_fact",
            "locator": "line 1",
            "quote": "전공: 특용작물전공 (specialty_crops)"
          }
        }
      }
    },
    {
      "collection": "facts",
      "value": {
        "id": "selected_major",
        "field_id": "common.major_id",
        "kind": "reported_fact",
        "value": "specialty_crops",
        "value_type": "text",
        "unit": "",
        "period": null,
        "scope": "project",
        "answer_state": "provided",
        "verification": "claim_supported",
        "module_version": "1.0.0",
        "source_refs": [
          {"id": "major-answer", "revision": 1, "locator": "line 1", "claim_id": "major"}
        ]
      }
    }
  ]
}
```

새 폴더에서 실행한다. `apply`의 `--expected-revision`은 `init` 직후라 0이다.

```sh
python3 <스킬>/scripts/gg.py init <폴더>
python3 <스킬>/scripts/gg.py apply <폴더> --change bind-major.json --expected-revision 0
python3 <스킬>/scripts/gg.py major-plan <폴더>
```

`major-plan`의 `binding.major_id`가 `specialty_crops`이면 바인딩된 것이다. `module_version`은 [majors](majors.md)의 해당 전공 모듈 버전과 같아야 한다. 사실의 `source_refs`는 원답변 출처의 현재 개정·위치·`claim_id`를 가리키고, 출처의 `claims`는 사실의 항목·값·단위·기간·범위·답변 상태·종류와 같아야 한다. 이 예제만으로는 `check`의 `claim_review` 차단이 남는다. 원문 대조 검토를 출처의 `claim_review`로 연결해야 풀리며, 그 검토 기록은 지어내지 않는다([finance-semantics](finance-semantics.md)).

## 주요 보조 스크립트

- `python3 scripts/build_hwpx.py <폴더> --major <전공ID>`: HWPX 검토본 생성(여백·쪽번호·신명조 후처리). 논문 HWPX는 이 경로로만 생성하며, kordoc `generate`의 보고서·기안문·계획서 프리셋은 공문서용이라 논문에 쓰지 않는다. [outputs](outputs.md)
- `python3 scripts/build_docx.py <폴더> --in <MD> --out <DOCX> --major <전공ID>`: DOCX 검토본 빌드. 목차·표 목차·그림 목차 쪽번호는 필드(PAGEREF·TOC)와 `gg_office.py word` 재조판·필드 갱신으로만 채우며 손으로 적지 않는다. [outputs](outputs.md)
- `python3 scripts/gg_toc_check.py <DOCX> --json <증거경로>`: DOCX 목차 필드 및 고정 쪽번호 검사 도구. 탭 뒤 일반 글자 고정 번호나 미갱신 필드(`?`)를 검출한다(결과 finding id: `TOC-PAGE-NUMBERS`). [outputs](outputs.md) · [cross-review](cross-review.md)
- `python3 scripts/gg_deps.py ensure <폴더>`: 작업폴더 venv와 런타임 의존성 준비.
- `<gg_deps.py python <폴더>가 돌려준 인터프리터> scripts/gg_read.py <파일> --out <폴더 안 새.md>`: DOCX·PDF·XLSX 첫 읽기(읽기 전용, 원본·기존 결과 불변). `status: read`만 성공이며 스캔·손상 복구가 필요한 PDF는 성공으로 보지 않는다. 연결된 읽기 도구가 없을 때 Kordoc·Node 준비보다 먼저 쓴다. [parser-setup](parser-setup.md)
- `python3 scripts/gg_update.py auto`: 업데이트 확인·적용·복구. [update](update.md)
