# 경제 수치 의미 검토

## 본문 전 재무 게이트 기록

[workflow-order.md](workflow-order.md#재무-게이트)의 판정은 기존 `facts`와
`questions`에 `gg.py apply --expected-revision`으로 등록한다. 스키마와 계산기는
추가하지 않는다. 판정용 수치는 기존 재무 계산 결과 또는 전공별 학교 엑셀
재계산값을 원자료로 등록하고 목표연도 값을 그대로 옮긴다. 연도별 숫자는
그 결과 원문과 인터뷰 질문에 남긴다.

| field_id | 값·형식 | 근거 |
|---|---|---|
| `finance.gate.state` | text: `surplus` / `deficit_pending` / `deficit_accepted` / `not_computable`; unit `""`, scope `project`, period 목표연도(계산 불가면 null 가능) | 계산 결과·판정 원문 source_refs; `not_computable`에는 입력 부족·미지원 사유를 reason에 기록 |
| `finance.gate.target_profit` | decimal 문자열, unit `원`, scope `annual`, period 계획 마지막 연도(state와 동일) | 기존 계산 결과의 목표연도 연간 손익; `kind: observation`, `finance_role: context`, `meaning_id: sales.net_profit`, `measure: {kind: monetary_total, currency: KRW, unit: 원}` |
| `finance.gate.acceptance` | text `proceed_with_deficit`, unit `""`, scope `project`, `kind: reported_fact` | 숫자를 보고 적자를 알고 진행한다는 학생 원답변; 모름은 unknown/value null, 거부는 withheld/value null+reason, 조정·거절은 provided의 다른 값 |

손익 기록의 `kind`, `finance_role`, `meaning_id`, `measure`가 위 형태 중 하나라도 맞지 않으면 유효한 손익 기록이 없는 것으로 보고 `missing`으로 판정한다.
철회한 사실(`answer_state: not_provided` 또는 `verification: superseded`)은 게이트에서 기록 없음으로 본다.

각 field는 한 사실 ID를 새 개정으로 갱신한다(같은 field의 여러 ID는 모호하므로
통과하지 않는다). 제공된 판정·손익·수용은 `answer_state: provided`,
`verification: claim_supported`, 원자료의 현재 revision·locator·claim_id를 가진
source_refs와 일치하는 source.claims·claim_review가 필요하다. 자체 상태 문자열만으로
수용을 증명하지 않는다. `derived`는 기존 계산식·input_refs 규칙도 그대로 따른다.

게이트용 손익 사본은 인터뷰 판단 맥락(`context`)이므로 해당 field의 context
사본을 본문 재무 주장 필수 목록에 중복으로 넣지 않는다. 본문용 손익·매출 등
별도 plan 사실의 수치 대조와 경제 의미 검사는 기존대로 적용한다.

수용 전 `gg.py question <폴더> --field finance.gate.acceptance`를 실행한다.
질문과 답은 같은 인터뷰 원문에 먼저 보존하고 질문 레코드의 `source_ref`에
`{id, revision, locator}`로 되물음 위치를 연결한다. 질문의 기존 `attempts`는
1 이상, `decision_revision`은 현재 결정 버전이어야 한다. 수용 사실에는
`question_ref: {"id": "finance.gate.acceptance", "decision_revision": 1}`처럼
해당 버전을 기록하고 source_refs로 같은 인터뷰 원문의 학생 답 위치·claim을 연결한다.
질문을 만들기만 했거나 수용 사실만 있거나 버전이 다르면 `deficit_pending`이다.
질문의 숫자·조정 후보·학생 답의 의미는 원문 대조로 확인한다.

`next`의 작업 ID와 `check`/`status`의 검사 ID는 `finance_gate`다. 검사 evidence의
`gate_state`는 위 4상태 또는 기록 없음·불완전함을 나타내는 `missing`이다.
목표연도 손익 > 0이면 `surplus`, ≤ 0이면 실제 질문·답 연결에 따라 수용/미수용을
판정하므로 `deficit_accepted` 라벨만 적어도 통과하지 않는다. `not_computable`로
적었어도 유효한 손익 값이 있으면 그 값으로 재판정한다. 본문이 없을 때 미충족
안내는 next가 맡으며 검사 행을 추가하지 않는다. 실제 절 DRAFT가 있으면
error/fail로 제출 후보를 막는다.
`not_computable`의 절별 선별 보류는 기존 입력 의존 규칙을 따른다.

입력이 바뀌면 작성자가 새 재무 결과와 게이트 기록을 갱신하고 필요한 경우
기존 질문 재개 규칙에 따라 수용 답을 다시 연결한다. 입력 변화에 대한 게이트
stale 자동 감지와 원고 쓰기 시점의 물리적 차단은 현재 지원하지 않는다.

## 경제 사실·검토의 의미 연결

사실이 "경제 범위"에 속하는지는 사실 레코드에 `finance_role`·`meaning_id`·`measure` 셋 중 하나라도 값이 있는지로 정한다. 판정은 존재 여부(`is not None`)이며, 빈 문자열 같은 비정상 메타데이터도 경제 범위에 남는다 — 비정상이라고 검토에서 제외되지 않는다. 이 판정은 `gg_core.py`의 `_review_targets_economic_fact` 하나가 읽기·쓰기 경로에서 공유한다. 의미 registry는 `skills/knuaf-doc/references/fact-semantics.json`으로 스킬과 함께 배포되고, `_semantic_context`는 이를 `gg_core.py` 자체 위치 기준(scripts/ → references/)으로 읽는다 — 사용자 프로젝트 루트가 아니므로 어느 프로젝트에서도 실제 registry가 `classify_fact`·`resolve_consumers`에 그대로 흐르고, 보고서에 선언된 registry와 현재 registry의 대조는 지금의 실제 조건이다(Lane 28). 배포 파일이 없거나 손상·스키마 불일치일 때만 `registry=None`으로 되돌아간다. 경제 범위 판정 자체는 계속 선언된 메타데이터의 존재와 형식만 본다.

`gg.py check`/`status` 출력의 `check_id="fact_semantics"`는 경제 사실 각각의 메타데이터 자체가 유효한지(알 수 없는 `measure.kind`·잘못된 currency·허용 외 `finance_role` 등 `gg_fact_semantics.classify_fact`의 판정)를 보고한다. `check_id="finance_review"`는 `review_kind`가 `content`·`logic`·`calculation` 셋 중 하나이면서 경제 사실을 대상으로 하는 검토 기록이 현재 유효한 의미 보고서와 묶여 있는지를 보고한다(Lane 27 — 세 종류가 같은 freshness 계약을 공유). `_finance_review_state`(DESIGN.md §7)가 돌려주는 상태는 다섯 가지다. `not_applicable`은 세 종류 검토가 아니거나 경제 사실을 대상으로 하지 않는 경우로 항목이 만들어지지 않는다. `unresolved`는 경제 검토인데 묶인 보고서(`path`·`report_hash`)가 없는 상태이며 `gg-finance-semantics-report/1` 보고서를 바인딩하면 해소된다. `invalid`는 묶인 파일이 읽히지 않거나 바이트 해시가 다르거나 JSON·스키마가 보고서 형식이 아니거나 연결된 관측 기록이 무효인 상태로, 파일과 기록을 다시 맞춰야 한다. `stale`은 보고서는 유효하지만 검사 범위·바인딩·결과·입력 개정·지문 등이 현재 정본과 어긋나는 상태이며 의미 검토를 다시 실행해 새 보고서로 바인딩해야 한다. `fresh`는 모든 조건이 맞는 상태로 역시 항목이 만들어지지 않는다.

쓰기 경로와 읽기 경로의 역할은 다르다. 새 경제 검토(content·logic·calculation)의 `apply` 등록, 그 검토의 관측 기록 `ingest_review_observation`, 경제 사실을 대상으로 하는 새 출력의 `adopt_output` 등록은 각각 쓰기 시점에 바인딩된 보고서를 검사한다 — 파일이 읽히고, 바이트 해시가 기록과 같고, `gg-finance-semantics-report/1` 스키마와 `semantics` 객체를 갖는 고정 무결성과 함께 현재성도 다시 계산한다. 출력은 `checks[]` 항목의 `evidence_path`·`evidence_hash`가 그 보고서를 가리켜야 한다([section-ledger.md](section-ledger.md)의 출력 checks 계약과 같은 필드). 세 쓰기 경로는 `_validate_new_economic_review_binding` 하나를 공유하며, 그 안의 `_fresh_semantics_issues`가 현재 프로젝트에서 registry·검사 범위·소비 바인딩·입력 지문을 새로 도출해 보고서 선언과 대조한다 — 어긋나면 등록·발행·채택이 그 자리에서 거부된다(Lane 25). 읽기 시점의 `_finance_review_state`는 같은 재계산을 매번 다시 수행하고 저장된 `status="pass"` 문자열을 신뢰하지 않는다 — 쓰기·읽기 두 층이 각각 현재성을 검증한다. 따라서 과거에 유효했던 보고서·검토 조합도 입력이 바뀌면 `stale`로 되돌아오고, `submission_candidate` 출력은 이 항목이 차단 상태인 한 게이트를 통과하지 못한다. `draft`·`review` 출력은 차단 없이 차단 내용을 보존해보낸다([cross-review.md](cross-review.md)의 검토 계약과 같은 원칙).
