# RDA·MAFRA 기준자료 벤치마크 조회 (rda-benchmark)

구현: `scripts/gg_rda_lookup.py` (물리 행 감사 조회), `scripts/gg_rda_candidates.py`
(승인 후보 — 작성 경로), `scripts/gg_rda_research.py` (연구 제안 propose·적용 apply),
`scripts/gg_rda_provenance.py` (감사 키·팩 무결성)
CLI: `python3 scripts/gg.py rda-lookup` / `rda-candidates` / `rda-propose` / `rda-apply`

## 0. 고지 — 승격 배경과 현재 계약

`references/benchmark-packs/`의 4개 실데이터 팩은 표본 전수 원문 대조가 끝나기 전에
승격됐다(당시 운영자 승인 예외). 이후 계약은 **"팩 전체 승인"이 아니라
관측(record)별 검증 상태**로 바뀌었다:

- 조회가 돌려주는 레코드에는 `verification` 묶음이 붙는다. `level`은
  `verified`(원문 대조 완료) / `exploratory`(탐색용, 원문 대조 미완) /
  `quarantined`(격리 — 원문 불일치 또는 미확인) / `unknown`(상태 확인 불가)이며,
  `verified` 외에는 라벨이 "본문·표에 옮기지 말 것"이다.
- `unique` 판정은 "검증된 물리 관측 1건이 식별됐다"는 뜻일 뿐 **사용 승인이
  아니다.** 본문·표에 넣는 값은 `rda-candidates`의 `approved` 상태뿐이다
  ([majors.md](majors.md)의 candidate 계약). 현재 팩은 적용 적합성 확장 축
  선언(`evidence`)을 갖지 않으므로 승인되는 레코드가 없다 — 조회 결과는
  답변 보조·되물음 재료로만 쓴다.
- 검증 상태 실측 예: D5(내용연수 셀 정정, 단계 P15) 이후 rda.econ.2025의
  관측 상태 합계는 verified 105 / exploratory 131 / quarantined 2,471이다
  (repo 루트 `docs/validation/d5-rda-useful-life.json`). 검증이 안 된 값을 학생
  `user_answer`나 재무 확정 입력으로 쓰지 않는다.

## 1. 언제 쓰나

- 인터뷰·1차검산 단계에서 학생이 단가·소득·생산량을 "모름"이라고 답했을 때,
  웹 조사([research-sources.md](research-sources.md))에 앞서 내장 팩을 1차 조회한다.
- 학생이 이미 값을 답한 대상은 덮어쓰지 않는다 — `gg_rda_research`는 제안·
  적용 양쪽에서 대상 상태를 확인한다(§6).
- 전공 팩이 채워진 것은 `specialty_crops`(특용작물전공)뿐이다. 나머지 전공(낙농 `dairy`, 식량작물 `food_crops` 포함)은
  `references/benchmark-packs/majors/<major>/manifest.json`만 있고
  `status=empty_slot`이라 조회 시 `not_found`(`reason=major_pack_empty` 또는
  `pack_not_promoted`)가 나오고 기존 웹 조사 흐름으로 넘어간다(낙농은 축산 전용 경로 [dairy/research-routes.md](dairy/research-routes.md), 식량작물은 [food-crops/research-routes.md](food-crops/research-routes.md) 준수).
- 작성 경로는 `rda-candidates <폴더>`다 — 정본의 `common.major_id` 바인딩에서
  전공을 읽는다. `rda-lookup`은 전공을 선택 인자로 받는 원자료 감사용 조회다.

## 2. 승격된 팩 (`references/benchmark-packs/catalog.json` 기준, 2026-11-14 실측)

| 팩 | ID | record 수 | 출처 |
|---|---|---|---|
| 전국 농가소득 2024 | `rda.income.national.2024` | 102 | rda-income.xlsx — 전국 평균 |
| 시도별 농가소득 2024 | `rda.income.regional.2024` | 365 | rda-income.xlsx — 도별 평균 |
| 농업경영조사 2025 | `rda.econ.2025` | 2,707 | farm-management.xlsx — 시트·행·셀 단위 |
| 특용작물 생산실적 2024 | `mafra.specialty.production.2024` | 2,988 | mafra-specialty-production-2024.json — 특용·약용작물 전국·시도·시장 |
| 전국 농가소득 2025 | `rda.income.national.2025` | 104 | 농산물소득자료집(2025년산) — 인용 색인 경유 |
| 시도별 농가소득 2025 | `rda.income.regional.2025` | 49 | 농산물소득자료집(2025년산) — 인용 색인 경유 |

6개 팩 합계 6,315건 (기존 카탈로그의 `record_count`와 무관 — 실제 레코드 수).
공통 소득표(전국·시도)는 소득·가계비·자가노동비용 계열이고,
특용작물 팩은 생산실적·공식시장가격·관측가격을, econ 팩은 농업경영 비용·
가격·시계열을 담는다. 학교 양식 입력칸으로의 매핑은 없다 — 이 조회는 답변 보조다.

**행별 출처(`source`) 상태 (2026-11-14, main 5f132a5 실측).** 소득 팩 4개
(2024·2025 전국·지역, 620행)는 전 행이 `source`(publisher·survey·survey_label·
aggregation_group·data_year…)를 갖는다 — `rda.income` 602행,
`kostat.production_cost` 18행. `rda.econ.2025`(2,707행)·
`mafra.specialty.production.2024`(2,988행)는 아직 `source`가 없어 연도 기본값이
`source_unknown`으로 막힌다(§4.1) — 그 팩들은 기존 조회 결과가 그대로이고(실측:
10,659질의 결과 바이트 동일), 출처 채우기는 다음 데이터 PR에서 한다.
실측 분포: 다년 4축 그룹 422개 중 기본값 적용 144·막힘 278(전부 econ·mafra
`source_unknown`), 한 그룹에 survey가 둘 이상인 경우 0. `rda-candidates` 진입점
기준으로 기본값이 붙는 소득 그룹 146개는 관측 판정이 `ambiguous`→`unique`로
풀리고, 사용 승인 상태는 그대로 `unapproved`다(연도 선택은 승인이 아니다).

**144 vs 146 — 같은 측정의 두 분모 (W3G-02).** 두 수는 모순이 아니라 그룹
묶음 축과 모집단이 다르다. 한 스크립트는 지역을 `level + sido`로, 다른
스크립트는 `region.name`으로 묶고, 후자는 작목명·지역명이 없는 행(도매
시세 시계열 등)을 먼저 버린다. 광역시 표기가 연도별로 갈린 2그룹이
전자에서만 연도별로 쪼개진다.

| 측정 | 그룹 축 | 모집단 행 | 다년 그룹 | 기본값 적용 |
|---|---|---:|---:|---:|
| `measure-g3.py` | `kind + crop.name + region.level/sido + crop.form` | 6,315 | 422 | 144 |
| `entry_trans.py` | `kind + crop.name + region.name + crop.form` | 4,041 | 395 | 146 |
| 차 | — | −2,274 | −27 | **+2** |

갈린 2그룹(2024·2025 소득 지역 팩 실측):

| 작목 | form | 지역 | 2024 `region.level` | 2025 `region.level` | 연도 |
|---|---|---|---|---|---|
| 고구마 | 노지 | 인천 | `metropolitan` | `provincial` | 2024·2025 |
| 밀 | 노지 | 광주 | `metropolitan` | `provincial` | 2024·2025 |

`region.name`으로 묶으면 같은 지역의 두 해가 한 그룹이 되고(다년 → 기본값
적용), `level`까지 묶으면 연도별로 다른 level 때문에 각각 1년짜리 두
그룹이 되어 기본값이 붙지 않는다 — 그래서 적용 수가 144 대 146으로 갈린다.
조회 코드는 두 표기를 같게 다루므로(`gg_rda_lookup._region_matches`가
`provincial`·`metropolitan`을 함께 처리하고, `gg_rda_candidates._record_region`도
sido만 비교한다) 진입점 결과에는 차이가 없다 — 갈리는 것은 **측정 분모뿐**이다.
2024 팩의 `metropolitan` 표기는 PR4 데이터 단계에서 `provincial`로 고친다.

`catalog.json.gaps[]`(현재 4건)은 팩이 덮지 못하는 범위를 누적으로 기록한 목록이다.
확인된 공백 — 시설비·인건비·종묘비·비료·농약비·관수·전기·수선유지·공공요금·재배기술·
인건비 단가(작업별)·재배시설 시공비 등. 누락 시 `not_found`로 돌아가고
공백 보고를 강제하지는 않으나, 웹 조사로 전환해 해소한다.

## 3. 조회 순서 (rank)

레코드의 `rank`는 0~4 정수다 — **낮을수록 우선**한다.

| rank | 종류 (kind) | 의미 |
|---|---|---|
| 0 | `production_stat` | 재배면적·생산량(톤)·호당수입 — 공식 생산 통계 |
| 0 | `official_market_price` | mafra 공식 관측단가(원/kg) — 단위는 필터·단위변환 없이 그대로 |
| 1 | `crop_income` | 호당 조수입·소득(전국 평균) |
| 1 | `crop_income_cost_breakdown` | 경영비·자가노동비용·자본용역비 등 세목 |
| 1 | `crop_income_regional` | 도별 조수입·소득 |
| 2 | `official_market_price`(시계열 다중), `wholesale_price_series`, `yield_series`, `input_price`, `production_cost` 등 | 시계열·보조 관측 |
| 3 | (예비) | |
| 4 | `web_stub` 등 연구용 스텁 — `gg_rda_research`만 쓰고 본문에 옮기지 않는다 |

`rda-candidates`는 정본 `common.crop`의 작목을 기준으로 이 순서로 후보를 만들고
`missing`을 적는다. `rda-lookup`도 같은 순위로 정렬한다.

## 4. 반환 형식과 판정

`rda-lookup`은 항상 `{status, reason, rank, pack_id, records, verification_summary, web}` 구조다.
`status`는 5가지다:

- `not_found` — 후보가 없다. `reason`으로 구분:
  - `pack_not_promoted` — 해당 전공 팩이 비어 있다(`empty_slot`)
  - `major_pack_empty` — 전공 팩 자리만 있고 레코드가 없다
  - `excluded_from_mafra_specialty_survey` — 조사 대상 밖 작목 (예: 인삼·산삼 계열)
  - `no_match` — 팩은 있으나 필터 조합에 맞는 레코드가 없다
  - `audit_key` 관련 사유 — 감사 키가 팩·해시와 불일치
  - `전공 식별 오류` 계열 — crop 인자가 전공 식별자로 오인된 경우
- `ambiguous` — 관측이 2건 이상이거나, 부분 필터로는 고유성을 확정할 수 없다.
  **첫 레코드를 임의로 고르지 않는다.** `audit_key` 또는
  crop/region/major/kind/year/form 완전 조합으로 좁힌다.
- `series` — 시계열 관측(연도·기간이 다른 같은 종류 관측 다수, 예: `official_market_price`
  2004~2024 21행). `audit_key`로 골라도 series다 — `period` 등 명시 선택 없이
  스칼라 자동 추출 금지.
- `unverified` — 관측 1건이나 원문 대조가 안 됐거나 격리 중이다.
  본문·표에 옮기지 않고 연구 제안(§6)으로만 다룬다.
- `unique` — 검증된 물리 관측 1건이 식별됐다. **사용 승인이 아니다** —
  본문·표 반영은 `rda-candidates`의 `approved`만 따른다.

`verification_summary`는 결과 레코드들의 검증 상태 합계다.
`web` 필드는 `kind=input_price` + `allow_web=True`일 때 스텁
(`miss`/`web_not_implemented_in_mvp`)을 보고할 뿐 실제 웹 호출을 하지 않는다.

### 4.1 연도 기본값 (연도를 안 준 `rda-candidates`)

`rda-lookup`(원시 계약)은 **바뀌지 않았다** — 연도를 안 주면 지금도
`ambiguous`/`series`/`not_found`다. 기본 연도는 그 위 `rda-candidates`
진입점에서만, `year is None and audit_key is None`일 때만 해석한다.

결과에 `year_default` 키가 붙는다:

| status | 의미 |
|---|---|
| `applied` | 한 연도를 골랐다. `year`·`form`·`kind`·`survey`·`aggregation_group`·`catalog_status`가 채워지고, 원시 조회가 그 연도로 다시 돈다 |
| `blocked` | 고르지 않았다. 사유는 `year_default.reason`에만 있고, 원시 조회는 연도 없이 그대로 돈다 — 최상위 `status`·`reason`·후보 목록은 연도 기본값이 없던 때와 똑같다 |
| `not_attempted` | 후보가 0건이라 시도하지 않았다 |
| `null` | 이 요청은 대상이 아니다(명시 `year`·`audit_key`) |

`blocked` 사유:

| reason | 조건 |
|---|---|
| `source_unknown` | 후보 중 하나라도 `source` 없음 / `survey`·`data_year`·`aggregation_group` 없음·빈 값 |
| `source_invalid` | 값 형식 위반, `aggregation_group != survey + "." + data_year`, 또는 `source.data_year != basis.year` |
| `survey_mixed` | 후보에 `source.survey`가 둘 이상 — **승인상태·연도 비교보다 먼저** 검사한다 |
| `group_unresolved` | 호출자가 안 준 `kind`·`form` 축에 값이 둘 이상(예: 노지·시설) |
| `year_unknown` | 고를 등급 안에 정수 연도가 없다 |

출처 연도는 관측 연도와 같아야 한다 — 행이 대표하는 기준 연도가 곧
`source.data_year`이고, 여러 해를 한 레코드에 담은 시계열은 표의 마지막
연도가 그 값이다. 어긋난 출처는 다른 해 표에 붙은 영수증이므로
`source_invalid`로 막는다. `basis.year`가 정수가 아니면 이 검사는 걸지
않고 아래 순서의 `year_unknown`에 맡긴다(같은 결함을 두 번 보고하지 않는다).

순서: 출처 검사 → survey 혼합 → 그룹 유일성 → (최고 `catalog_status` 등급
→ 그 등급 안 최대 연도). **어떤 경우에도 출처를 지어내지 않는다.** 명시
`year`는 대체하지 않고(`not_found` 유지), `audit_key` 조회에는 연도를
주입하지 않는다. 고른 행이 `exploratory`·`quarantined`여도 승인 상태를
올리지 않는다 — `rda-candidates`의 `approved` 판정은 그대로다.

### 4.2 여러 레코드를 함께 쓰는 경로의 계약

합산·평균·순위처럼 **여러 레코드를 한 숫자로 합치는** 코드는 `source`가
같은 조사 묶음인지 먼저 확인해야 한다. 계약 함수는
`gg_rda_candidates.require_single_aggregation_group(rows)`다 — 같은
`aggregation_group`이면 그 문자열을, 다르면
`AggregationGroupError(reason="aggregation_group_mixed")`, 행에 출처가
없으면 `source_unknown`, 형식이 틀리면 `source_invalid`로 거부한다.
빈 목록은 호출자 오류(`ValueError`)다.

한 연도의 대표 관측 **한 건**을 고르는 것(위 4.1)은 이 계약의 대상이
아니다 — "한 건 선택"과 "여러 해를 섞어 합치기"는 다르다. 현재 코드에
합산·평균 경로는 없고(실측: `gg_rda_candidates.py`의 `sum`은 자격 후보
개수 세기), 새로 생기는 경로는 이 함수를 거친다.

## 5. 사용 예 — Python (2026-10-10 실측 출력)

```python
import sys
sys.path.insert(0, "scripts")
import gg_rda_lookup as L

# ① 참깨 + 지역만 → ambiguous (30건, 종류 혼합)
r = L.lookup_rda_data("참깨", "경남", major="특용작물전공")
# status=ambiguous, reason="서로 다른 관측 30건 — audit_key 또는
#  완전한 필터 조합으로 좁혀야 한다", n=30

# ② kind로 좁혀도 시계열이면 ambiguous → series
r = L.lookup_rda_data("참깨", "전국", major="특용작물전공",
                      kind="official_market_price")
# ambiguous (21건, 연도별) → 첫 레코드의 audit_key로 다시 조회하면
# status=series (스칼라 자동 추출 금지 — period 명시 필요)

# ③ 부분 필터 → ambiguous
r = L.lookup_rda_data("참깨", "경남", major="특용작물전공",
                      kind="crop_income_regional", year=2024)
# ambiguous, reason="후보 1건이나 부분 필터만으로는 고유성을 확정할 수 없다"
#  (form 미지정 — form이 없는 레코드도 완전 조합에는 form 인자가 필요)

# ④ 조사 대상 밖 작목 → not_found
r = L.lookup_rda_data("인삼", "전국", major="특용작물전공", kind="production_stat")
# not_found, reason=excluded_from_mafra_specialty_survey
```

호출 인자: `lookup_rda_data(crop, region, *, major=None, kind=None, year=None,
form=None, allow_web=False, audit_key=None)`. `region`은 필수 인자다.
작목 표기는 `references/benchmark-packs/aliases/major-aliases.json`의 별칭을 거친다.

## 6. 연구 제안 → 정본 반영 (propose → apply)

조회가 miss·unverified·series로 끝난 값을 정본(`project.json` 계열)에 넣고
싶을 때 쓰는 경로다. 웹 접근은 기본 금지(`allow_web=False`).

- `gg_rda_research.propose(pack_revision, target, selection)` — selection은
  `audit_key`, 조회 후보 레코드, 또는 조회 결과. 대상(`target`)은
  `state == not_provided`일 때만 제안이 만들어진다(`_ELIGIBLE_TARGET_STATES`).
  이미 학생이 답한 대상이면 제안 자체가 나오지 않는다.
  성공 시 `proposal` 객체, 실패 시 `unresolved(reason=...)`를 반환한다.
- `gg_rda_research.apply(proposal, *, expected_revision, target_current=None,
  packs_dir=None)` — `expected_revision` 이후 인자는 키워드로 전달한다.
  팩 무결성(records 해시·audit_key)·라이브 바이트 해시·대상 현재 상태를
  다시 확인하고 `ready | stale | rejected`로 판정한다. 제안 후 대상이
  응답됐거나 제안 시점 상태와 다르면 `stale`로 거부 — 덮어쓰기 없다.
  `ready`로 반영되는 fact의 `verification`은 `"proposed_research"`다.
- `build_rda_check(major_id, crop, comparisons)` + `write_rda_check(root, data)`
  — `<폴더>/reviews/rda-check.json`에 원자적으로 쓴다(검토 기록).
- propose·apply는 `gg_finance.calculate()`를 호출도 변경도 하지 않는다 —
  재무 계산과 분리된 별개 경로다.

## 7. CLI

```bash
cd skills/knuaf-doc

# 원자료 감사 조회 (본문 승인 경로 아님)
python3 scripts/gg.py rda-lookup --crop 참깨 --region 경남 --major 특용작물전공
python3 scripts/gg.py rda-lookup --crop 인삼 --region 전국 --rda-kind production_stat
python3 scripts/gg.py rda-lookup --crop 참깨 --region 전국 --rda-kind official_market_price --audit-key '<json>'

# 작성 경로 — 정본 바인딩 기준 후보 목록 + missing
# (연도를 안 주면 연도 기본값이 붙는다 — §4.1)
python3 scripts/gg.py rda-candidates <프로젝트폴더>

# 연구 제안·적용 (검토 흐름 — reviews/rda-check.json과 함께)
python3 scripts/gg.py rda-propose ...
python3 scripts/gg.py rda-apply ...
```

## 8. 실측 데이터 품질 메모 (2026-09-29~10-10 조회 기준)

- **시계열 레코드**: `official_market_price`(mafra 팩, 참깨 2004~2024 21행)·
  `wholesale_price_series`·`yield_series`는 연도별 별도 레코드다. 같은 kind의
  연도 다중 관측은 `ambiguous`가 아니라 `series`로 돌아온다.
- **`crop.name`이 빈 문자열인 레코드**가 존재한다(예: econ 팩 `wholesale_price_series`
  계열 113건, `crop.form="쌀"` 등 form만 식별자). 매칭 시 form도 본다.
- **mafra 팩 `production_stat`의 지역 층**: national 94 + provincial 1,598 +
  market 1,296행 (`region.level`로 구분). 도매시장 단가는 농가수취가격이 아니다.
- **인삼 표기 다중화**: 소스별로 `인삼(4년근)`·`인삼(6년근)`·`수삼`·`홍삼 원료` 등
  form이 다르게 잡힌다 — 같은 작물로 자동 병합하지 않는다.

## 9. 금지 사항

- `not_found`를 0원·평균·추정으로 채우지 않는다 — 공란으로 두고 웹 조사·인터뷰 재질의로 해소한다.
- 도매시장가·공식시장가격을 농가수취가격으로 바꾸지 않는다. 단위 환산 시 원값과 환산 근거를 함께 보존한다.
- `allow_web` 기본값은 False — 조회는 웹을 타지 않는다.
- `ambiguous`·`series`·`unverified`·"옮기지 말 것" 라벨 값을 본문·표에 옮기지 않는다. `unique`도 승인이 아니다(§4).
- 학생이 답한 대상을 조회값으로 덮어쓰지 않는다 — propose/apply가 대상 상태를 재확인한다(§6).
- 연도 기본값은 **한 연도를 고르는 일**이지 출처·승인 상태를 만드는 일이 아니다 — `survey_mixed`·`source_unknown`·`group_unresolved`로 막히면 그대로 두고 사유를 보고한다(§4.1).
- `explicit_assumption`은 사용자 승인 없이 만들지 않는다.

## 10. 관련 파일

- `scripts/gg_rda_lookup.py` — 감사 조회 (status/reason/verification)
- `scripts/gg_rda_candidates.py` — 작성 경로 후보·missing (approved 계약)
- `scripts/gg_rda_research.py` — propose·apply·rda-check 기록
- `scripts/gg_rda_provenance.py` — audit_key 생성·팩 무결성 검증
- `references/benchmark-packs/catalog.json` — 팩·버전·gaps 정본
- `references/benchmark-packs/aliases/major-aliases.json` — 작목 별칭
- `references/benchmark-packs/schema/{pack,record}.schema.json` — 팩·레코드 스키마
- `references/majors.md` — approved candidate 계약·전공 바인딩
- `references/research-sources.md` — 웹 조사 경로(팩 miss 해소용)
- `../../docs/validation/` — P4 계보(`p4-lineage.json`)·D5 내용연수(`d5-rda-useful-life.json`)·REGRESSIONS.md (repo 루트 기준 `docs/validation/`)
- 시험: `skills/knuaf-doc/tests/test_gg_rda_*.py`, `repo/tests/`의 정본 계약 시험
