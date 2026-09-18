# RDA·MAFRA 기준자료 벤치마크 조회 (rda-benchmark)

설계 정본: 리포지토리 docs 폴더의 아키텍처 설계 문서(RDA 기준자료 벤치마크 팩 아키텍처, 동결됨)
구현: `scripts/gg_rda_lookup.py`, `scripts/gg_rda_research.py`
CLI: `python3 scripts/gg.py rda-lookup`

## 0. 고지 — 승격 절차 이탈 사항

`references/benchmark-packs/`의 4개 실데이터 팩(`rda.income.national.2024`,
`rda.income.regional.2024`, `rda.econ.2025`,
`mafra.specialty.production.2024`)은 SWE 구현 지시서(docs/briefs 폴더) §2-4가
요구하는 **Opus 표본 대조 PASS 이전에** 승격되었다. 이전 세션에서 사용자
(운영자)가 명시적으로 승인한 예외 처리이며, 정식 게이트인 Opus 검증 지시서
(마찬가지로 docs/briefs 폴더)는 아직 이 문서 작성 시점에 완료되지 않았다.
승격된 4종 팩과 그 안의 6,055개
레코드는 Opus PASS를 받기 전까지 **잠정(provisional)** 으로 취급하고,
`gg_finance.calculate()` 최종 재무 확정에는 사용하지 않는다. `status=research`
후보 생성에는 쓸 수 있으나 학생 `user_answer`를 절대 덮어쓰지 않는다(기존 계약
유지).

## 1. 언제 쓰나

- 인터뷰·1차검산 단계에서 학생이 단가·소득·생산량을 "모름"이라고 답했을 때,
  웹 조사(`research-sources.md`)에 앞서 내장 팩을 1차 조회한다.
- MVP 범위: 재무 자동연동 훅은 **`major_id == "specialty_crops"`(특용작물전공)
  일 때만** 동작한다. 다른 7개 전공은 팩이 비어 있어(`empty_slot`) 조회해도
  `status=miss, reason=pack_not_promoted`가 나오고, 기존 웹 조사 흐름으로
  넘어간다.
- 학생이 이미 값을 답했으면(`status != unresolved`) 이 조회를 거치지 않아도
  되고, 거치더라도 `gg_rda_research.propose()`가 `keep_existing`을 반환해
  덮어쓰지 않는다.

## 2. 승격된 팩

| pack_id | kind | major_id | 레코드 수 | 내용 |
|---|---|---|---|---|
| `rda.income.national.2024` | common | null | 102 | 전국 작목별 소득자료(2024) |
| `rda.income.regional.2024` | common | null | 365 | 지역(시도)별 소득자료(2024) |
| `rda.econ.2025` | common | null | 2,600 | 경제성분석 기준자료집(2025): 투입재 단가, 단수, 도매가격 계열 |
| `mafra.specialty.production.2024` | specialty_crops | specialty_crops | 2,988 | 농식품부 특용작물 생산실적(2024): 생산통계 + KAMIS 가격동향 |

나머지 7개 전공(식량작물, 산림, 조경, 채소, 원예환경, 과수, 곤충)은
`references/benchmark-packs/majors/<major>/manifest.json`만 존재하고
`status=empty_slot`이다. `catalog.json`의 `packs[]`에 없으므로 조회는 항상
공통 팩까지만 병합되고 전공 팩 병합은 생략된다.

`catalog.json.gaps[]`에는 원본 자료집에 표 자체가 없어 비워 둔 4개 항목이
기록되어 있다(인삼 특용 생산실적 조사 제외, 시설 신축단가·유지보수율·농기계
가격표 표 부재). miss를 0원이나 추정치로 채우지 않는다.

## 3. 조회 순서 (rank)

1. **rank 1 (공통)**: 작목(+form)+kind로 3개 공통 팩(전국 소득/지역 소득/경제성분석)을
   먼저 찾는다. region이 광역시도면 지역 레코드도 함께 반환하되
   `caveat: 통계적 유의성 없음`을 붙인다.
2. **rank 2 (전공)**: `major=특용작물전공|특용작물|specialty_crops`일 때만
   특용 생산실적 팩을 추가 병합한다. 생산량(ha, M/T)과 소득(10a, 원)은 같은
   레코드로 합치지 않고 `kind`가 다른 레코드로 나란히 반환한다.
3. **rank 3 (웹, 스텁)**: `allow_web=True`이고 `kind == "input_price"`일 때만
   시도한다. 이 슬라이스에서는 실크롤러가 아니라
   `status=miss, reason=web_not_implemented_in_mvp`를 반환하는 스텁이다.
   다른 kind는 `reason=web_not_applicable`로 즉시 스킵한다.

0건이면 `status=miss`(사유는 `pack_not_promoted` /
`excluded_from_mafra_specialty_survey` / `major_pack_empty` / `no_match` 중
하나), 2건 이상 동점이면 `status=ambiguous`이며 **첫 값을 임의로 고르지
않는다**. 단, `official_market_price`·`wholesale_price_series`처럼 연도별로
한 행씩 쌓인 시계열 kind는 form 미지정 상태에서 연도만 여럿인 것을 동점으로
보지 않는다(아래 5절 참고).

## 4. 사용 예 — Python

```python
import sys
sys.path.insert(0, "scripts")
import gg_rda_lookup

result = gg_rda_lookup.lookup_rda_data(
    "참깨", "경남", major="특용작물전공"
)
# result["status"] == "hit"
# result["rank_used"] == 1  (공통 팩에서 이미 히트)
# result["records"]에 crop_income, crop_income_regional,
#   crop_income_cost_breakdown, production_stat(2건),
#   official_market_price(21건, 연도별) 등 kind가 섞여 반환된다.
```

인삼 예시(특용 생산실적 조사 제외):

```python
result = gg_rda_lookup.lookup_rda_data(
    "인삼", "전국", major="특용작물전공", kind="production_stat"
)
# result["status"] == "miss"
# result["reason"] == "excluded_from_mafra_specialty_survey"
```

## 5. 사용 예 — research 후보 생성 + 1차검산 기록

```python
import gg_rda_research

lookup = gg_rda_lookup.lookup_rda_data("참깨", "전국", major="특용작물전공")
candidate = gg_rda_research.propose(lookup, "gross_receipts")
# candidate == {
#   "status": "research", "value": "...",
#   "source_ref": "rda.income.national.2024#<record_id>", "reason": None
# }
# 학생이 이미 답했다면(current_status="user_answer") status="keep_existing"

check = gg_rda_research.build_rda_check(
    "specialty_crops", "참깨",
    [{"field": "gross_receipts", "plan": None, "benchmark": candidate["value"],
      "pack_id": lookup["pack_id"], "verdict": "plan_missing_benchmark_available"}],
)
gg_rda_research.write_rda_check(folder, check)
# -> <folder>/reviews/rda-check.json (gg_core.atomic으로 원자적 저장)
```

`gg_rda_research.propose()`는 `gg_finance.py`의 `calculate()` /
`investment()`를 호출하거나 변경하지 않는다. 산출된 `research` 레코드는
`gg_finance.require_classified_workbook_inputs()`가 기대하는 형식
(`status/value/unit/period/source_ref/meaning_id`)에 맞춰 호출자가 조립해서
넣어야 하며, `unit`/`period`/`meaning_id`는 이 모듈이 채우지 않는다.

## 6. CLI

```
python3 scripts/gg.py rda-lookup --crop 참깨 --region 경남 --major 특용작물전공
python3 scripts/gg.py rda-lookup --crop 참깨 --region 경남 --major 특용작물전공 --rda-kind official_market_price --year 2020
```

`folder` 위치 인자는 `rda-lookup`에서는 필요 없다(다른 서브커맨드와 달리
로컬 폴더 상태를 읽거나 쓰지 않는다. 단, `gg_rda_research.write_rda_check()`를
별도로 호출할 때는 폴더가 필요하다).

## 7. 실측 데이터 품질 메모 (Opus 검증 참고용)

파싱된 실데이터를 조회 모듈에 연결하며 확인한 원자료 특이사항. 버그가 아니라
원본 자료집의 실제 구조이므로 조회 로직이 이를 전제한다.

- **연도별 1행 시계열**: `official_market_price`(예: 참깨 2004~2024, 21행)와
  `wholesale_price_series`는 같은 작목·form·지역에 대해 연도마다 별도 레코드로
  쌓여 있다. `year`를 지정하지 않으면 여러 연도가 함께 반환되며, 이는 `form`
  동점과 달리 `ambiguous`로 취급하지 않는다(3절).
- **`crop.name`이 빈 문자열인 레코드**: 경제성분석 도매가격 계열 일부는
  `crop.name=""`이고 대신 `crop.form`(예: `"쌀"`)에 식별자가 들어 있다.
  작목 매칭 시 `name`만 보면 누락되므로 `form`도 함께 본다.
- **`production_stat`의 국가/시도 혼재**: 특용 생산실적 팩 한 곳에 전국 단위
  94행과 시도 단위 1,598행이 함께 들어 있다(`region.level` 필드로 구분).
- **인삼 표기가 자료집마다 다름**: 전국 소득자료는 `crop.name="인삼"` +
  `form="4년근"`, 지역별 소득자료는 `crop.name="인삼(4년근)"` +
  `form="4년근"`과 `crop.name="인삼(6년근)"` + `form="6년근"` 두 종류로
  나뉘고, 경제성분석 단수 계열은 `crop.name="인삼(4년1기작)"` + `form=null`이다.
  네 표기 모두 같은 작목이지만 재배 형태(근수·기작)가 다르므로 자동으로
  합치지 않는다.

## 8. 금지 사항 (재확인)

- miss를 0원·평균값으로 채우지 않는다.
- 도매가(`wholesale_price_series`, `official_market_price`)를 농가수취가격으로
  바꿔 쓰지 않는다.
- 10a ↔ ha ↔ 평 자동 환산 후 원값을 버리지 않는다. 환산은 호출자가 `basis`를
  보고 수행하고 근거를 남긴다.
- `allow_web=False`(기본)인데 웹을 호출하지 않는다.
- 빈 전공 슬롯 miss를 웹으로 자동 메우지 않는다.
- `explicit_assumption`을 사용자 승인 없이 만들지 않는다(`gg_finance.py` 계약,
  이 모듈은 손대지 않음).

## 9. 관련 파일

- `scripts/gg_rda_lookup.py` — 팩 로드·조회·동점 판정
- `scripts/gg_rda_research.py` — research 후보 생성, `reviews/rda-check.json` 저장
- `references/benchmark-packs/catalog.json` — 팩 목록·gaps
- `references/benchmark-packs/aliases/major-aliases.json` — 전공 별칭
- `references/research-sources.md` — 웹 조사 경로(내장 조회 우선 안내 포함)
- `references/interview-guide.md` — Q2.1 전공 식별자, 자료 미대체 원칙
- 개발 트리 `tests/` 폴더의 회귀 테스트(픽스처: `tests/fixtures/rda/`) — 배포판에는 포함하지 않는다. 합성 데이터로 조회·miss·ambiguous 형식을 고정한다.
