# 명령 목록

**구현 파일을 열지 말 것.** `scripts/gg_core.py` 등 핵심 구현 파일은 수천 줄 분량이며 전체를 읽으면 컨텍스트 토큰이 가득 찬다.
명령 표면과 JSON 계약은 이 문서가 전부 담는다. 여기에 명시되지 않은 옵션은 `--help`로 확인한다.

## 실행 규칙

```
<파이썬> <스킬>/scripts/gg.py <명령> <폴더> [옵션]
```

- `<파이썬>`: `scripts/gg_deps.py python <폴더>` 또는 현재 환경의 Python 3.10+ 인터프리터. 3.9 이하면 `gg.py`가 차단 JSON을 출력하고 종료한다.
- `<폴더>`: 대상 작업폴더의 경로.
- 출력: 항상 JSON 한 덩어리(`indent=2`). 실패 시에도 JSON 형식으로 반환된다.
- 종료 코드:
  - `0`: 정상 완료.
  - `1`: `check`가 막는 항목(error 수준)을 찾음.
  - `2`: 거절 또는 보류 (`{"status": "blocked"}` 또는 `{"status": "held"}`).

## 전공 계약 (공용 규칙 B)

논문 본문·재무 계산·산출물 출판(export, paper, build_hwpx 등)을 실행하는 모든 경로는 다음 두 조건이 모두 일치해야 한다:
1. 명령 인자의 명시 전공 ID (`--major <전공ID>`)
2. 정본(`project.json`)의 유효한 `common.major_id` 사실 바인딩

하나라도 누락되거나 불일치하면 실행되지 않고 `{"status": "held", "reason": "major_id_required"}` 등으로 보류된다.

## gg.py 명령 표

| 명령 | 주요 옵션 | 하는 일 · 반환 형식 |
| --- | --- | --- |
| `init` | — | `project.json`과 하위 폴더만 만든다. 기존 파일은 건드리지 않아 학생 자료가 든 폴더에도 그대로 쓴다. |
| `doctor` | `[--probe]` | 설치·의존성·잠금 진단. `--probe`는 읽기 전용으로 잠금 상태 점유를 확인한다. |
| `status` | — | `{revision, checks, tasks, completion, skill_ready, guideline_ready, user_finish_pending, professor_approval_pending}`. 한 번에 현재 상태를 본다. |
| `next` | — | 다음에 할 작업 목록을 조회한다. |
| `check` | `[--scope all\|submission]` | 검사 결과 배열을 반환한다. `submission`은 제출 관문(`gate`). 막는 항목이 있으면 종료 코드 1. |
| `apply` | `--change <JSON경로>` `--expected-revision <N>` | 정본(`project.json`)을 수정하는 유일한 경로. 원자적 개정 증가를 보장한다. |
| `export` | `[--kind draft\|review\|submission_candidate]` `--major <전공ID>` | 본문 검토 산출물을 생성한다. `{"status": "generated", "path": "...", "request_id": "..."}`. |
| `question` | `--field <필드ID>` | 질문 상태 추적 `{action: ask\|help\|hold, field, notice}`. 질문 1회+도움 1회 후 해당 항목 보류. |
| `bundle` | `--scope <절ID>` | 한 절의 검토 꾸러미(본문·원자료·사실·규칙·검토축)를 반환한다. |
| `paper` | `--input <spec.json>` `[--out <경로>]` `[--body <본문MD>]` `--major <전공ID>` | 학교 양식 본문을 생성한다. |
| `major-plan` | — | 전공별 질문·문서 계획·근거·재무 능력을 읽기 전용으로 조회한다. |
| `adopt-output` | `--input <spec.json>` `--expected-revision <N>` `--major <전공ID>` | 생성·수정된 출력 파일(DOCX, XLSX 등)을 정본에 정식 산출물로 등록한다. |
| `import` | `--out <새폴더>` `--offline-confirmed` | 기존 폴더의 마크다운/자료를 새 정본 작업폴더로 가져온다. |
| `unlock` | — | 비정상 종료로 남은 잔여 잠금을 해제한다. |
| `lock-upgrade` | — | 오프라인 전환 및 잠금 복구를 수행한다. |
| `finance-tables` | `--xlsx <파일>` `[--out <경로>]` `--major specialty_crops` | 특용작물전공 Ⅳ장 재무표를 전개한다. |
| `tidy` | `[--apply]` | 산출물 폴더 정리를 미리보기하거나 `--apply`로 실행한다. |
| `source-scan` | — | 참고자료 폴더를 스캔하여 증거 목록을 만든다. |
| `source-resolve` | `--source-hash <해시>` | 스캔된 출처의 서지·메타데이터를 확인한다. |
| `source-register` | `--selection <JSON>` | 확인된 출처를 정본 출처로 등록한다. |
| `source-refresh` | `--source <출처ID>` `[--reviewed]` `[--apply]` | 원자료 변경 시 미리보기 및 새 해시 재등록을 수행한다. |
| `rda-lookup` | `--crop <작목>` `--region <지역>` | 농촌진흥청 농축산물 소득자료 지표를 조회한다. |
| `rda-propose` | `--crop <작목>` | 소득자료 기반 시범값 대안을 생성한다. |
| `rda-apply` | `--change <JSON>` | 소득자료 제안을 정본 사실로 반영한다. |
| `rda-candidates` | `--crop <작목>` | 지원 전공·작목의 내장 통계 후보를 조회한다. |

## apply 변경 JSON 계약

정본을 수정할 때는 변경 명세 JSON 파일을 만들어 `gg.py apply <폴더> --change <JSON경로> --expected-revision <N>`으로 전달한다.

### JSON 형식

```json
{
  "request_id": "고유_작업_식별자",
  "ops": [
    {
      "op": "set_fact",
      "fact_id": "common.major_id",
      "value": "specialty_crops",
      "value_type": "text",
      "unit": "",
      "scope": "project",
      "answer_state": "provided",
      "verification": "claim_supported",
      "source_refs": [
        {
          "id": "source-interview-01",
          "revision": 1,
          "locator": "q2-major"
        }
      ]
    }
  ]
}
```

### 주요 연산 종류 (`op`)

- `set_fact`: 사실(fact) 등록 또는 갱신. `fact_id`, `value`, `value_type`, `answer_state`, `verification`, `source_refs` 포함.
- `set_source`: 원자료 출처(source) 등록. `id`, `kind`, `locator`, `sha256` 포함.
- `set_section`: 본문 절(section) 등록 또는 갱신. `section_id`, `title`, `order`, `path`, `claims` 포함.
- `set_rule`: 학교 지침 규칙(rule) 상태 연결. `rule_id`, `applicable`, `locations` 포함.
- `set_task`: 작업 이력(task) 등록. `id`, `source_refs`, `target_refs` 포함.
- `remove_fact` / `remove_source` / `remove_section`: 레코드 제거.

## 주요 보조 스크립트

- `python3 scripts/build_hwpx.py <폴더> --major <전공ID>`: HWPX 검토본 생성 (여백·쪽번호·신명조 후처리).
- `python3 scripts/build_docx.py <폴더> --in <MD> --out <DOCX> --major <전공ID>`: DOCX 빌드.
- `python3 scripts/gg_deps.py ensure <폴더>`: 독립 venv 및 런타임 의존성 확인.
- `python3 scripts/gg_update.py auto`: 자체 업데이트 자동 확인 및 안전 복구.
