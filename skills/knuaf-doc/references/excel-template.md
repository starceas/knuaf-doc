# Reusable XLSX template workflow

Source identity, author/authority separation, physical sheet ranges, and task
records follow [source-contract.md](source-contract.md). This page documents
only the reusable CLI contract.

`scripts/gg_excel_template.py` creates a local, source-bound blank copy of an
existing school workbook. It does not generate a replacement workbook and does
not correct legacy formulas.

```text
python3 scripts/gg_excel_template.py inspect \
  --source "/path/to/source.xlsx" \
  --out-map build/template-review/source-map.json \
  --report build/template-review/inspect-report.json

python3 scripts/gg_excel_template.py clear \
  --source "/path/to/source.xlsx" \
  --map build/template-review/source-map.json \
  --out build/template-review/blank-v1.xlsx \
  --receipt build/template-review/receipt-v1.json
```

`inspect` records the source SHA-256, all observed cells/formulas, sheet order,
and an explicit per-sheet map. The map records `semanticField`, `reason`, and
`action` (`clear` or `preserve`). `clear` refuses a source whose SHA-256 no
longer matches the map and refuses to overwrite an existing output or receipt.

The copy operation removes values only from mapped non-formula cells. Formula
expressions, cell styles, merged ranges, sheet names/order, print settings,
drawings, images and links remain in the copied archive. Formula cached values
are invalidated and the workbook is marked for recalculation when opened, so a
blank template does not show stale financial results. Existing formulas are
never repaired or rewritten.

The receipt reports cleared cells, mapped formulas protected, merged-cell
protection, formula caches invalidated, and hard-coded numeric cells left
outside the map as `ambiguousCount`. A `partial` status is expected when such
legacy constants remain; it is not a clean financial model or a submission
approval. The reference model-farm sheet is explicitly preserved as an
example-only reference. Shared-string entries and other private sample data
are not anonymized or publication-ready by this utility.

Choose a new output directory or versioned filenames for each run. Counts and
status must come from that run's source-bound map and receipt. A previous
developer's template, receipt, school workbook or private sample is not a
dependency of the installed plugin. Register the workbook supplied for the
current project and retain its hash and source role. The source workbook is
never written. An NFD filename is resolved when an NFC path is supplied.

## Specialty-crop input-sheet roles in the supplied 17-sheet template

This mapping is limited to the supplied specialty-crop template. Other majors
use their own reviewed form and role map; the horticultural-environment H01
workbook has 18 sheets and does not use these coordinates.

User-confirmed on 2026-09-15: in the current numbered 17-sheet school workbook,
excluding the contents sheet, red input sheets are 1, 3, 4, 5, 6, 7, 8, 9 and 10.
They cover initial finances, investment, loan repayment, sales, production,
materials, labor, expenses and depreciation. Yellow and green sheets contain
linked calculations. Confirm the actual sheet names, tab colors and source hash
when applying this mapping; a different template needs its own role map.

Write plan values only to reviewed input cells on those red sheets. A red tab
does not make every cell editable: preserve formulas, merges and labels unless
a separately documented correction is required (the crop-category label layer
below is one such correction and changes label text only). Do not type desired totals into
yellow/green calculation cells. Recalculate them and reconcile them to the inputs
and manuscript. An input quantity may legitimately be a numeric constant whose
calculation is explained in the manuscript or evidence; do not demand that every
student input become an Excel formula.

An inherited formula defect is separate from missing student input. Compare it
with the supplied original, record whether it was inherited or introduced, and
use the reviewed formula-correction workflow below on a copy. Do not treat tab
color as approval for arbitrary formula changes. Investment analysis absent from
the prescribed workbook can be verified in a separate calculation artifact;
do not add sheets to the submission form merely to satisfy a preferred model.

### Inflation and labor cost input rules (물가 및 노무비 반영 규칙)

> ℹ️ **적용 범위**: 상승률 분리 규칙은 **모든 전공 공통 기본값**이다([사용자 결정 2026-10-06 22:39]). 입력 위치와 시트 수는 해당 전공 양식을 따른다.

**구현 상태**: 물가·노무비 공식 통계 분리(D8) 및 Ⅳ장 표 전개기(D9) 구현 완료. 과거 단일 `inflation` 배율 입력은 완전히 **폐기**되었으며(입력 시 오류 발생), 일반물가·임금·판매가 상승률을 각각 분리하여 공식 통계 기반으로 입력해야 한다.

1. **세 상승률의 분리와 산출 방식**:
   - 일반물가 상승률은 영농자재·경비, 임금 상승률은 노무비, 판매가 상승률은 매출에 각각 적용한다.
   - 판매가 r이 CPI를 사용해도 일반물가·임금·판매 역할을 하나로 합치지 않는다. 비용·임금은 판매 자료와 독립해서 검증하며 `sales: not_applied` 자리표시를 요구하지 않는다. 판매가 5년 완전성을 임금용 최소 2점 관측 계약에 일괄 적용하지 않는다.
   - 일반물가 r은 해당 역할의 공식 관측 수열 CAGR, 판매가 r은 **문서 작성연도 직전 5년의 전국 연간 CPI 총지수 CAGR(4간격)**으로 산출한다. 판매 시작가 B는 같은 5년 동일 작목·조건 가격 산술평균이다. [판매가 계약](price-research-routes.md#판매가-시작가상승률적용-계약)을 따른다. 명시적 `rate` 입력은 검산용(`max(5e-4, 0.05 × |cagr|)` 허용오차 내 일치)이며, 관측값 없는 임의 계획 가정 r은 입력할 수 없다. 내부 계산 배율은 `1 + r`이다.
   - **특용작물 X01 임금 상승률은 교수님 양식 그대로다**([사용자 결정 2026-10-28]): `8. 노무비계획` 관측표(X26:Z31)의 연도별 상승률 AC26:AC30·AD26:AD30을 산술평균한 AC38 `=AVERAGE(AC26:AC37)`(남)·AD38 `=AVERAGE(AD26:AD37)`(여)를 쓴다. `materialize-d8`은 이 두 수식을 바꾸지 않고, 이전 판이 쓴 끝점 CAGR 수식이나 상승률 상수가 남은 사본은 원래 수식으로 되돌린다. 임금 입력(`wage_male`·`wage_female`)은 공식 출처·성별·연도만 검증하며 `r` 직접 입력은 거부한다. 영수증 `d8.rates.wage_*`에는 `rate_method: x01_arithmetic_mean_of_yearly_rates`와 실제 적용값 `applied_rate`가 남는다.
   - 비용·임금은 각 역할의 기존 적용 기초연도를 사용한다. 판매가는 `application_base_year=S`로 영농 첫해 B, 다음해 B(1+r), 이후 B(1+r)^(t−S)다. 작성연도 W와 S는 따로 확인하며 관측 창을 S나 현재 날짜로 바꾸지 않는다. 모든 관측값·r·배율은 유한수여야 한다(NaN/Infinity 거부).
2. **노무비 반영 및 이중 적용 금지**:
   - 노무비(고용노동임금) 계획은 **연도별 임금 상승률**을 반영한다.
   - 동일 가격에 상승률을 두 번 곱하지 않는다. 노무 단가에 임금 상승률을 곱한 뒤, 노무비 총액에 일반물가를 다시 곱하지 않는다. 감가상각비와 고정 차입금 원리금에는 물가를 적용하지 않는다.
3. **공식 통계 근거 및 판매 자료 부족 처리**:
   - 일반물가는 KOSIS 소비자물가 총지수(`DT_1J22003`), 자재·경비 세목과 노무비는 농가구입가격지수의 해당 항목(`DT_1J62`)을 사용한다. 판매 시작가는 농진청 소득자료집 등 동일 작목·조건의 공식 연간 단가, 판매가 r은 별도 `DT_1J22003` 전국 연간 총지수다. 가격과 CPI 각각 정확한 5개 연도 전체·원값·출처를 확인하며 CPI에 작목 차원 일치를 요구하지 않는다.
   - 판매가 수열은 계획의 작목·재배형태·지역·상품·단위·거래단계 차원과 일치해야 한다(`crop` 필수). **총합 지수인 농가판매가격지수 총지수(`DT_1J60`)와 번들 KAMIS 쌀 수열(`allowed_roles: []`)은 판매가로 적용할 수 없다.**
   - 가격 또는 CPI가 부족하면 해당 계산·의존 출력만 보류한다. 역할·앵커·필요 창·누락 연도·부족 조건을 남기고, 실제 공식 후보의 연도·단위·가격 성격·계획 차이·조회 근거를 제시한다. 조사 미실행·후보 없음/접속 실패·후보 미선택·사람 선택 기록을 구분한다. 빈 후보나 `sales.status/mode=not_applied`는 계산 우회로가 아니다. 사람 선택 후에도 자료·창·차원을 재검증하며 다른 창/조건은 명시적 계획·규칙 변경으로 기록한다.
   - 통계에서 확인되지 않은 수치는 임의 추정 없이 `확인 불가`로 남긴다.
4. **수식 숫자 표기 및 Excel 정밀도 보존 (`_excel_number`)**:
   - 패치(`materialize-d8`)가 워크북 수식에 쓰는 판매가·일반물가 상승률 상수는 **유효숫자 15자리**(Excel이 보존하는 정밀도)로 직렬화하여 기록한다. 임금 상승률은 X01 수식이 계산하므로 상수를 쓰지 않는다.
   - Excel은 수식에 적힌 숫자를 최대 15자리까지만 보존하고 끝자리를 잘라내므로(절단), 15자리를 초과하는 숫자를 그대로 쓰면 Excel에서 열고 다시 저장할 때 수식 글자(문자열 토큰)가 달라져 보존 검사에 걸리게 된다. 처음부터 15자리 정밀도로 기록해 두면 재저장해도 수식 문자열이 바뀌지 않아 안전하게 통과할 수 있다.
   - 부호 없는 숫자가 21자리 이하이면 일반 소수점 형태(고정소수점)로 표기하고, 초과하면 지수 형태(`d.dddE±dd`)로 표기한다. 끝에 붙는 불필요한 `.0`이나 부호 있는 0(`-0`)은 제거한다.
   - 영수증 `d8.rates.sales`·`d8.rates.general`에는 원래 계산된 상승률 `r`(원 CAGR 계산값)을 그대로 보존하고, 수식에 기록된 값은 `r_literal` 필드에 별도로 남긴다.
   - 원래 `r`과 수식 표기값 `r_literal`의 차이는 최대 `1.1e-14 * |r|` 수준으로 아주 미세하여, 하류 허용오차(`1e-12`, 1조 분의 1) 안에서 안전하게 일치한다.

#### 특용작물 엑셀 경로: 교수님 양식(X01·X02) 기준

특용작물전공 제출 엑셀은 교수님 양식(X01, 대식물 계정이 필요하면 X02)을 틀로 복제해 값만 채운다([사용자 결정 2026-10-28]). 선배가 채운 통합문서는 규칙 원본이 아니라 비교 참고다. 스펙에서 17시트를 새로 조립하는 레거시 생성기(`build_excel_finance_template.py`, `gg_school_excel.school_workbook`, `gg_finance.workbook`의 `school_17_sheet_v1`)는 특용작물에서 입구에서 `held/specialty_template_route_required`로 멈추고 아래 경로 1로 안내한다. 다른 전공의 경로는 바뀌지 않는다.

##### 경로 1. 학과 원본 템플릿 우선 경로 (W-B 패치·채우기 계약)

학과에서 배포한 원본 통합문서(XLSX)를 사본으로 복구하고 채우는 주 경로다.

1. **구조 복구 (`materialize-d8`)**:
   - 명령:
     ```bash
     python3 skills/knuaf-doc/scripts/gg_excel_formula_patch.py materialize-d8 --source <원본.xlsx> --out <패치맵.json>
     python3 skills/knuaf-doc/scripts/gg_excel_formula_patch.py --source <원본.xlsx> --map <패치맵.json> --out <복구사본.xlsx> --receipt <영수증.json> --project <프로젝트폴더> --major specialty_crops
     ```
   - 정상 X01은 0개 패치로 변경 없음 영수증을 발급한다(무변경 영수증은 기존 영수증 유지). 판매가·일반물가 수식 패치 시 `r_literal`이 수식 문자열 상수로 기록된다. 임금 상승률 AC38·AD38은 X01 산술평균 수식 그대로다.
2. **관측 준비 (`--prepare-observations` 및 `wage-map`)**:
   - 명령:
     ```bash
     python3 skills/knuaf-doc/scripts/gg_excel_formula_patch.py materialize-d8 --source <사본.xlsx> --out <준비맵.json> --prepare-observations
     python3 skills/knuaf-doc/scripts/gg_excel_formula_patch.py --source <사본.xlsx> --map <준비맵.json> --out <준비사본.xlsx> --receipt <영수증.json> --project <프로젝트폴더> --major specialty_crops
     python3 skills/knuaf-doc/scripts/gg_excel_template.py wage-map --source <준비사본.xlsx> --out-map <값지도.json>
     ```
   - **X01 원본 관측 준비**: 등록 X01 원본의 실제 관측 수식 변형(`Z27=Z28`)은 등록 원본(SHA `5d19b6a...`)과 감사 자료(`formula-audit/x01.json`, SHA `785c2a1...`)에 묶어 검토된 수식으로 허용된다. 등록 원본 파일 자체이거나, 모든 수식이 감사 목록과 칸마다 같은 사본(칸 이름 변경·값 채우기만 거친 사본)이면 허용한다. 수식이 하나라도 바뀌거나 늘거나 줄었거나 시트가 다르면 거부한다. 따라서 `label-map`과 `--prepare-observations`는 어느 순서로 해도 같은 사본이 된다. FX 양식도 그대로 동작한다.
   - 관측 연도 사슬과 복사 수식(X01은 Z27, FX는 Z28)을 명시적으로 비우며, 기존 fill CLI를 통해 관측값을 입력한다(수식 쓰기 금지 유지).
3. **관측값 입력 형식 (`wage_observations`) 및 시간급 환산 계약**:
   - 값 파일에 `statistic_id`, `base_year`, 6개 `observation_years`, `application_base_year`를 지정한다.
   - `values`는 8번 시트 X26:Z31 18개 셀 모두 정수 연도 및 양수 원/일이어야 하며, `evidence_ref(source_id, revision, locator, origin="factual")`가 필수다.
   - **등록 수열 원단위 값·연도 대조**: 등록 id를 사용할 경우 환산 전 원단위 값과 연도가 등록 수열의 관측값과 **정확히 일치**해야 한다(`check_observations`).
   - **임금 관측 및 패치 성별 계약**: 등록 수열에 성별 차원이 없으면 남자(Y열)·여자(Z열) 모두 같은 등록 관측값이어야 한다. 등록 수열에 성별 차원(`gender`·`sex`·`성별`)이 있는 수열은 현재 단일 출처·남녀 전체 블록(`wage_observations`) 입력 형식으로는 **거부**된다(남성 수치를 여성 관측으로 복제 금지). 남녀 상승률(`wage_male`·`wage_female`) 입력은 각각 맞는 성별 출처만(`wage_male`은 남자, `wage_female`은 여자) 사용할 수 있으며 반대 성별이나 복수 성별 선언은 거부된다. 패치 시에도 생산자(`gg_excel_template.check_wage_gender`)를 통해 대상 열(남자=AC38, 여자=AD38)과 출처 성별을 엄격히 대조한다. 미관측 연도나 임의 변조값은 거부되며, 다른 값·연도를 쓰려면 다른 id의 공식 스키마 `price_sources`로 명시 등록해야 한다(같은 번들 id 덮어쓰기 불가).
   - **단위 일치 및 시간급 환산**: 관측표(X26:Z31)는 원/일 단위이므로 등록된 원/일 수열만 직접 입력할 수 있다. 농진청 소득자료집 고용노동 단가나 최저임금 등 시간급(원/시간) 수열을 일급으로 환산할 때는 반드시 `unit_conversion: {"from": "원/시간", "to": "원/일", "hours_per_day": float, "evidence_ref": {...}}` 명시 계약이 있어야 한다. **기본 환산 시간은 존재하지 않으며**(Q-W-B2 사용자/조사 후 결정 대기), 대조가 끝난 원값에 명시 시간을 곱한 결과로만 일급이 결정된다(임의 보상 환산 금지).
4. **가격 계약 (`--assumptions <JSON>`)**:
   - materialize-d8에 `--assumptions <가정.json>`을 전달한다.
   - 역할은 `wage_male`, `wage_female`, `sales`, 선택적 `general`로 분리한다. 비용·임금 관측 항목은 `base_year`, `observation_years`, `observed_values`, `application_base_year`, `evidence_ref` 계약을 유지하고 학생 선언 수열은 `assumptions.price_sources`에 포함한다. 판매가에는 명시적 작성연도 W·워크북 첫 판매연도 S·활성 앵커별 가격 근거와 별도 CPI 근거가 필요하다. 판매가의 두 근거를 단일 source_id·작목 CAGR로 합치지 않는다.
   - 일반물가 r은 해당 공식 관측 CAGR, 판매가 r은 동일 5년 전국 연간 CPI CAGR이다. 임금 r은 X01 AC38·AD38 산술평균이 계산하며 입력으로 받지 않는다. 완전한 관측에서 나온 0%·음수 r은 허용되며 명시 r은 검산 오차 내 일치해야 한다. 가격 부족을 0%로 바꾸지 않는다.
   - **가격 가정 어댑터**: 공통 차원 정규화(`canonical_dimensions`)를 적용하여 판매가 작목(`target_dimensions`)은 문자열(`crop: "봄감자"`), 목록(`crop: ["봄감자"]`), 또는 한글 별칭(`작목: "봄감자"`)을 모두 지원한다. 임금(`wage_male`, `wage_female`)에는 작목이 필수가 아니며 지역(`region` 또는 `지역` 별칭)만 지정할 수 있다.
   - **계획 차원의 권위**: 사업계획의 최상위 차원이 기준이며 보조 차원이 충돌하면 거부된다. 판매가는 수열이 선언한 모든 차원(단위 포함)이 계획과 일치해야 하며(`crop` 필수), 전국은 고정 지역값이지 와일드카드가 아니다. 미일치는 해당 판매 계산 보류와 공식 후보 조사·사람 선택·재검증으로 처리한다. CPI의 전국 차원은 해당 상승률 수열의 조건이며 가격의 지역 조건을 덮지 않는다.
   - **역할별 허용 계약 (`allowed_roles`)**: 수열의 `allowed_roles`에 해당 역할(`wage`, `sales`, `general`)이 포함되어 있어야 한다.
     - 번들 일반물가(`general`): `official.kosis.cpi.total`, `official.kosis.farm_purchase.materials`, `official.kosis.farm_purchase.expenses`.
     - 번들 노무비(`wage`): `official.kosis.farm_purchase.labor`, `official.moel.establishment_wage.total`, `official.minimumwage.hourly`.
     - 번들 판매 시작가 후보: `official.rda.crop_income.spring_potato.output_price`. 작목 등 계획 차원 일치와 필요한 5년 전체가 필수다. 로컬 번들은 2025가 미확인이므로 W=2026 계산은 보류한다. 판매 상승률에 CPI를 허용해도 명목 단가의 `allowed_roles`·단위·차원 검사를 생략하지 않는다.
     - 시장 참고용(판매가 적용 불가, `allowed_roles: []`): `official.kosis.farm_sales.total`(총지수), `official.kamis.retail.rice_20kg.top`(쌀 소매가).
5. **적용 기초연도 대조 및 경계**:
   - 임금은 원본 F6, 판매가는 첫 판매연도 C36=S와 적용 기초연도를 대조한다. 판매가의 W를 S−1로 추론하지 않는다. 불일치를 이유로 원본 연도나 연결을 임의 변경하지 않는다. 공식 수열 준비와 구조 복구만으로 제출/통계 승인이라고 간주하지 않는다.
   - D8 입력은 `assumptions.writing_year=W`, `sales.application_base_year=S`와 `sales.anchors`를 사용한다. 앵커 키는 `O7`, `O8`, `O9`, `O11`, `O12`, `O13`이며 활성 조건마다 `source_id`·`base_year`·`target_dimensions`를 지정한다. 가격 수열은 최상위 `price_sources`의 공식 스키마로 선언한다. 예를 들어 아래 O7 부분은 위 합성 수매 수열을 `price_sources`로 함께 제공할 때의 구조이며 전체 패치 입력은 아니다.
     ```json
     {
       "writing_year": 2026,
       "sales": {
         "application_base_year": 2027,
         "anchors": {
           "O7": {
             "source_id": "student.purchase",
             "base_year": null,
             "target_dimensions": {"crop": "합성작목", "region": "전국", "channel": "수매", "unit": "원/kg"}
           }
         }
       }
     }
     ```
   - D8는 활성 앵커마다 출처·가격 5개 원값·원단위·B·대상 셀을 연결해야 한다. 등록 D8의 매출 38/39/40/41/42/43행은 O7/O8/O9/O11/O12/O13을 참조한다. 학교 생성의 O7/O10 좌표를 D8에 전용하지 않는다. 기존 앵커의 일부 평균이나 직접값은 검증된 B를 대신하지 못한다. 첫해 매출은 수량×B/1000, 후속 수식은 동일 r 누적으로 대조한다.
6. **Ⅳ장 재무표 각주 참고값 계약**:
   - Ⅳ장 상승률 각주는 전부 "참고값"으로 출력되며, 표 계산에 쓰였는지는 통합문서에서 따로 확인해야 한다 (전개기는 워크북 수식·캐시 역산 검증이나 실제 사용 보증을 하지 않음). 상세 계약과 구조화 각주 입력 형식은 [finance-flow](specialty-crops/finance-flow.md)를 따른다.

##### 경로 2. 레거시 17시트 생성기 경로 (W-A 스펙 계약) — 특용작물에서는 닫힘

스펙 JSON에서 17시트 전체를 새로 생성하는 옛 보조 경로다. 특용작물전공(`specialty_crops`)에서는 입구에서 `held/specialty_template_route_required`로 멈추며 경로 1을 쓴다. 생성기 내부 코드와 시험은 아직 남아 있으나 학생 작업 경로가 아니다. 아래 계약은 생성기 본체의 검증 규칙 기록이다.

- **스펙 계약**:
  - `spec["price_assumptions"]` 객체에 `general`, `wage`, `sales` 세 역할을 필수로 정의한다.
  - 이전 `inflation` 단일 키 입력 시 명시적 오류로 거부된다.
  - 비용·임금은 해당 역할의 공식 관측 CAGR, 판매가는 별도 가격 5년 평균 B와 같은 창 CPI CAGR r로 계산한다. 수매·직거래 등 각 조건의 근거를 분리하고 명시 `rate`는 계산된 r의 검산값으로만 사용한다.
  - 작성연도 W와 영농 시작연도 S를 명시하고 같은 5년 가격·CPI 근거를 연결한다. `sales.status=not_applied`를 넣어 계속 계산하는 구 예제는 사용하지 않는다.
  - 학교 생성의 수매 O7·직거래 O10에는 각 채널 B가 들어가고 첫해 매출은 수량×B/1000으로 대조한다. 이전 `purchase_price`·`direct_price`나 선택적 이력 평균이 검증된 B를 덮지 못한다. 생성 수식과 `gg_school_verify` 기대값을 함께 확인한다.
  - 비용·임금 역할 입력 부분 예시(판매가·전공·생산 근거 등 전체 생성 spec은 아님):
    ```json
    {
      "general": {
        "source_id": "official.kosis.cpi.total",
        "base_year": 2020,
        "application_base_year": 2027
      },
      "wage": {
        "source_id": "official.kosis.farm_purchase.labor",
        "base_year": 2020,
        "application_base_year": 2027
      }
    }
    ```
  - 판매가 입력은 최상위 `writing_year=W`, `start_year=S`와 `price_assumptions.sales.application_base_year=S`를 분리한다. 학교 앵커명은 `purchase`·`direct`, 각 앵커는 가격 `source_id`·명목 수열 `base_year:null`·`target_dimensions`를 지정한다. 계획의 공통 차원과 앵커 차원을 합쳐 모든 가격 차원을 검증하며 상충 값으로 계획을 덮지 않는다.
  - `cpi_source_id`는 생략 가능하고 지정해도 `official.kosis.cpi.total`만 허용한다. CPI 관측은 번들에서 읽으며 학생 `price_sources`로 그 값을 덮어쓰지 않는다. 각 앵커 `base_price`는 출처의 원 단위 값이고 학교 원/천원·판매 수량 단위와 환산을 대조한다.
  - 합성 가격을 선언하는 판매가 입력 부분 예시(전공·비용·생산 등 전체 생성에 필요한 필드는 별도로 제공):
```json
{
  "writing_year": 2026,
  "start_year": 2027,
  "crops": ["합성작목"],
  "region": "전국",
  "price_dimensions": {"unit": "원/kg"},
  "price_assumptions": {
    "sales": {
      "application_base_year": 2027,
      "cpi_source_id": "official.kosis.cpi.total",
      "anchors": {
        "purchase": {"source_id": "student.purchase", "base_year": null, "target_dimensions": {"channel": "수매"}},
        "direct": {"source_id": "student.direct", "base_year": null, "target_dimensions": {"channel": "직거래"}}
      }
    }
  },
  "price_sources": [
    {
      "id": "student.purchase", "agency": "합성 공공기관(시험용)", "agency_kind": "public_corporation",
      "statistic_name": "합성 수매가격", "item_name": "합성작목", "base_year": null, "unit": "원/kg",
      "url": "https://synthetic.example.go.kr/prices", "evidence_locator": "시험용 선언; 실제 공표값 아님",
      "dimensions": {"crop": "합성작목", "region": "전국", "channel": "수매"}, "allowed_roles": ["sales"],
      "observations": [{"year":2021,"value":100},{"year":2022,"value":200},{"year":2023,"value":300},{"year":2024,"value":400},{"year":2025,"value":500}],
      "unconfirmed_years": []
    },
    {
      "id": "student.direct", "agency": "합성 공공기관(시험용)", "agency_kind": "public_corporation",
      "statistic_name": "합성 직거래가격", "item_name": "합성작목", "base_year": null, "unit": "원/kg",
      "url": "https://synthetic.example.go.kr/prices", "evidence_locator": "시험용 선언; 실제 공표값 아님",
      "dimensions": {"crop": "합성작목", "region": "전국", "channel": "직거래"}, "allowed_roles": ["sales"],
      "observations": [{"year":2021,"value":300},{"year":2022,"value":400},{"year":2023,"value":500},{"year":2024,"value":600},{"year":2025,"value":700}],
      "unconfirmed_years": []
    }
  ]
}
```
  - 위 합성 가격 평균은 수매 300원/kg·직거래 500원/kg이며 CPI는 합성 5%가 아니라 로컬 2021~2025 관측에서 약 **3.276849820915354%**다. 2027 단가는 각각 300·500, 2028은 각각 그 값에 `(1+r)`를 한 번 곱한다. 기존 `purchase_price`·`direct_price`나 이력이 다른 평균을 주장하면 거부하며, 두 채널 평균을 복제하지 않는다. 합성 URL·기관·가격은 실제 조사 근거가 아니다.
  - 판매가의 수학적 검산 예: W=2026/S=2027, 동일 조건 가격 100·200·300·400·500, CPI 100·105·110.25·115.7625·121.550625이면 B=300·r=5%, 2027=300·2028=315·2029=330.75. 합성 설명값이며 실제 출처나 생성 승인 예제가 아니다.
  - 학생이 직접 조회한 작목별 수열은 `spec["price_sources"]`에 선언한다. 학생 수열도 공인 기관 종류(`agency_kind`), 엄격 https 공식 도메인(백슬래시·@·공백·제어문자·NBSP·443 외 포트·유니코드/IDNA 변형 등 모호한 URL 거부), locator, 기준연도(지수 정수, 명목 null), unit, dimensions, allowed_roles를 준수해야 하며, 관측/미확인 연도 교집합은 거부된다.
  - 생성 명령(특용작물에서는 위 사유로 막힘):
    ```bash
    python3 skills/knuaf-doc/scripts/build_excel_finance_template.py project --input spec.json --out build/output.xlsx --major specialty_crops
    ```

#### 원예 판매단가 및 공통 역할 검증

원예 가격 사실은 `price_assumption.writing_year=W`, `application_base_year=S`, 가격 `source_id`·`base_year`·`target_dimensions`로 같은 월·조건의 5년 공식 수열에 연결하고 확인된 `plan.start_year`와 대조한다. 관측표 B8:B12·C8:N12의 완전성만 확인하고 실제 계획단가를 독립 견적 입력으로 남겨두면 안 된다. 활성 월의 C:N 72/82/92/102/112행 단가와 24/33/42/51/60행 표시, 25/34/43/52/61행 매출까지 B·CPI 누적이 연결돼야 한다. 월별에서 연간으로 집계할 근거가 없으면 해당 가격을 보류한다.

각 월의 첫해 사실 `sales.y1.m<n>.price_krw_per_unit`에 위 `price_assumption`을 둔다. 공식 수열은 기존 `project.sources[source_id].price_series` 또는 `fact.price_assumption.series`에 등록하고 `price_series.dimensions.month`를 `"1"`~`"12"` 문자열로 선언한다. month 조건은 `param_key`의 월에서 확정해 출처와 대조하며 다른 월 수열로 바꿀 수 없다. 명목 단가의 `base_year`는 null이다. 첫해 사실값은 검증된 월별 B와 일치하는지 확인하는 검산값이다. 이후 4년 단가 사실은 생략할 수 있고, 제공했으면 계산값과 다를 때 거부한다. 공개 파라미터 계약은 `price_contract: sales:five_year_price`이며 실제 60셀은 첫해 COUNT=5 조건의 관측 AVERAGE·후속 동일 CPI 누적 수식으로 작성한다. 월별 단가의 새 연간 평균을 만들지 않는다.

공통 판매 검증은 `normalize_sales_assumption`, 역할 단위 검증은 `normalize_price_role`로 분리한다. 일반물가·임금 호출은 판매 자료나 판매 미적용 자리표시를 요구하지 않는다. 임금의 성별·원/시간→원/일 근거·기존 관측 계약과 원예 O84/O94/O104/O114 생산량 증가율은 유지한다.

#### 보류 사항 (사용자 결정 대기)

1. **W-023 노무비 첫해 F6=2027년 및 생산원가 첫해 대응**:
   - 실제 FX 및 X01 양식의 `8. 노무비계획!F6` 셀은 `2027년`으로 기재되어 있으며, 생산원가계획 첫해와의 연결 대응은 지도교수 해석 대상이다. 코드는 이를 임의 수정하지 않고 보존하며, **사용자 결정 대기**로 둔다.
2. **첫해 자재·경비의 `*1.025*1.025*1.025` 식**:
   - X01 `7!C16:C19`, `9!C18`의 1.025 3회 곱셈 식은 기준 단가의 연도 근거 확인 전까지 임의 변경하지 않고 보존하며, **사용자 결정 대기**로 둔다.
3. **Q-W-B2 시간급→일급 1일 환산 노동시간**:
   - 템플릿 임금 관측표는 원/일 단위이고, 번들 공식 고용노동 단가는 원/시간이다. 1일 노동시간의 공적 근거 확인 전까지 기본 환산 시간을 두지 않으며, 학생이 공식 근거와 함께 `unit_conversion`을 명시해야만 환산을 허용한다 (**사용자/조사 후 결정 대기**).

#### 특용작물 17시트의 좌표 (해당 원본 전용)

확인한 특용작물 양식에서는 매출이 Sheet 5, 영농자재가 Sheet 7, 노무비가 Sheet 8, 경비가 Sheet 9다. 이 좌표를 다른 전공에 적용하지 않는다. Ⅳ장 표 전개는 [specialty-crops/finance-flow.md](specialty-crops/finance-flow.md)를 따른다.

#### 작목 분류별 칸 이름 (교수님 양식 예외 층)

교수님 양식의 예시 작목은 묘목을 심는 나무 작목이라 몇몇 칸 이름이 그 전제로 고정돼 있다. 양식은 그대로 보존하되, 아래 칸 **이름만** 학생 작목의 분류에 맞춰 바꾼다. 칸 목록·원래 글자·분류별 허용 이름은 `references/common-workbooks/x01-crop-labels.json`이 정한다. 이 칸들은 양식 안 어떤 수식도 참조하지 않는다.

| 시트·칸 | 원래 글자 | 나무 작목(`woody_perennial`) | 초본 작목(`herbaceous`) | 버섯(`mushroom`) |
|---|---|---|---|---|
| 5. 판매계획 B30 | 미상품과 | 미상품과 | 미상품 | 미상품 |
| 6. 생산계획 B17 | 묘목수 | 묘목수 | 정식 주수·파종량·재배면적·재배시설 중 계획에 맞는 것 | 배지 수 |
| 6. 생산계획 E25·E27 | 묘목수(1년차)·(2년차 이후) | 그대로 | 정식 주수·파종량·재배면적 + (1년차)·(2년차 이후) | 배지 수 + 같은 꼬리 |
| 7. 영농자재소요계획 C4 | 묘목비(원/10a) | 그대로 | 종자·육묘비 또는 종근비(원/10a) | 종균비(원/10a) |
| 8. 노무비계획 O24·O38 | 고용(남자, 단가 동일) | 고용(남자) | 고용(남자) | 고용(남자) |

- 작목 분류는 전공·작목명으로 정하고, 모르면 학생에게 묻는다. 생산량을 포기 수가 아니라 면적으로 계산하는 계획이면 `재배면적`을 고른다.
- 명령: `gg_excel_template.py label-map --source <사본> --category <분류> [--choice "6. 생산계획!B17=재배시설"] --out-map <맵> --out-values <값>` → `gg_excel_fill.py`로 새 사본에 적용 → `gg_excel_template.py label-check --before <사본> --after <새 사본>`. `label-check`는 **보조 경고, 판정 근거 아님**이다. 표에 있는 칸이 원래 글자에서 허용 이름으로 바뀐 것 말고 다른 차이가 있으면 `message=확인 권장`, `status=warning`과 차이 목록을 낸다. 수식은 글자·`<f>` 속성(공유수식 `t`·`si`·`ref`)·풀어 쓴 수식까지, 값은 자료형까지(숫자 6611과 글자 "6611"은 다르다) 비교한다. 차이가 없으면 `차이 없음`/`match`, 두 파일 중 하나라도 읽지 못하면 `읽기 실패 — 사람 확인`/`read_failed`이다. 셋 다 종료 0이며 작성을 막지 않는다.
- `label-map`의 원본은 등록 X01·X02 원본, 또는 모든 수식이 등록 감사와 칸마다 같은 사본(관측 준비·값 채우기를 거친 사본)이다. 원래 글자가 표와 다르거나 수식이 등록 감사와 다른 양식(새 연도 정본 등)은 거부한다. 표를 새 좌표·글자·감사로 다시 맞춘 뒤 쓴다.
- 읽기 공통 확인(**보조 확인, 판정 근거 아님**): `label-map`·`label-check`·`labor-review`·`materialize-d8`·`wage-map`·`inspect`와 인쇄 사본(`gg_excel_print.py`)은 먼저 `gg_excel_template.read_template`을 지난다. 첫 확인은 두 가지를 함께 본다. (1) 뒤 검사가 쓰는 openpyxl로 파일 열기. (2) 패키지 구조 확인: `[Content_Types].xml`, `_rels/.rels`(통합문서를 가리킴), `xl/workbook.xml`, `xl/_rels/workbook.xml.rels`가 있고 XML로 읽히며 뿌리가 맞아야 한다. 공유 문자열 표가 목록에 있거나 파일로 있으면 `xl/sharedStrings.xml`이 있고 읽혀야 한다. 시트는 1개 이상이고, 시트 목록이 가리키는 모든 시트 부품이 있으며 worksheet(+`sheetData`)·chartsheet 뿌리여야 한다. 시트 목록은 openpyxl이 연 것과 같아야 한다. 그다음 모든 칸·공유수식을 읽고, 양식 식별 설정(`kang-finance-workbook.json` `layout_anchors`)으로 식별한다. 어느 단계든 실패하면 `TemplateUnreadable`이다.
  - 검사(`label-check`·`labor-review`)는 `읽기 실패 — 사람 확인`을 출력 맨 앞에 쓰고 종료 0으로 끝난다. 작성을 막지 않는다.
  - 맵·사본·인쇄본을 만드는 도구(`label-map`·`wage-map`·`materialize-d8`·`inspect`·`clear`·인쇄 사본, D8 맵을 쓰는 패치 적용)는 못 읽은 입력에서 아무것도 만들지 않는다. 출력 맨 앞에 `읽기 실패 — 사람 확인`과 이유를 쓰고 종료 2로 끝난다. 이것은 작성 판정이 아니라 "만들 결과물이 없음"이다.
  - "다른 양식(`unknown`, 해당 없음)"은 열기·읽기·식별이 모두 성공하고 X01이 아님을 확인했을 때만 나온다. `detect_layout`도 읽기 실패를 `unknown`으로 바꾸지 않고 예외를 낸다.
  - 이 확인은 엄격한 OOXML 검증이 아니다. 알려진 빈틈: 공유 문자열 번호가 음수(`-1` 등)이면 표 끝에서 세어 읽으므로 통과한다. 이 빈틈과 엄격 읽기는 다음 판에서 다룬다.
- `1. 기초재무상태조사` (3) 대식물 표(주수·경제수령·식재년도)는 다년생 나무가 없으면 칸 이름을 바꾸지 않고 표 전체를 "해당 없음"으로 둔다. `7. 영농자재소요계획` 각주의 예시 묘목 단가 문장은 학생 자재 근거로 교체한다(값 채우기 경로).
- 이름을 바꾼 칸이 칸 폭을 넘으면 칸 폭을 바꾸지 말고 렌더 검사에서 확인한다.

#### 일용 노무 성별·작업별 확인

`8. 노무비계획` 11행(남)·12행(여)의 연도별 작업 일수와 작업별 산출근거(1년차 26~33행, 2년차 이후 40~47행)의 남자 O열·여자 R열 일수를 학생 계획대로 나눈다. 작업별(수확·선별·포장 등)로 누구를 고용하는지 묻고([interview-guide](interview-guide.md#q2-4-투자자금-조달-iv장-전체)), 근거 자료에 성별 구분이 없으면 작업 관행으로 정한 이유를 각주에 적는다. `gg_excel_template.py labor-review --workbook <재계산본>`과 `gg.py check`의 `finance_plan_review`는 고용 일수가 남자 칸에만 있으면 `LABOR_HIRED_MALE_ONLY`(`확인 권장`) 경고와 작업별 일수 목록을 낸다. 이 확인은 **보조 경고, 판정 근거 아님**이며 막지 않는다 — 실제로 남자만 고용하는 계획이면 그 근거를 본문·각주에 남긴다.

이 검사는 X01 배치로 식별된 통합문서에만 적용한다. 읽기 공통 확인을 통과하고 식별 결과가 X01이 아닌 통합문서(다른 전공 양식·X02 등)는 검사 대상이 아니라 이슈를 내지 않는다. 확인하지 못한 경우는 모두 `읽기 실패 — 사람 확인` 경고(`status: pending`, `severity: warning`)이고 종료 0이며, CLI 출력 맨 앞이 `message: 읽기 실패 — 사람 확인`이다.
- 파일이 없거나, openpyxl로 열리지 않거나, 패키지·시트·공유 문자열 부품이 없거나 깨졌거나, 양식 식별 설정을 못 읽음: `LABOR_WORKBOOK_UNREADABLE`
- X01인데 `8. 노무비계획` 시트가 없음: `LABOR_SHEET_MISSING`
- 칸 이름 배치가 다름: `LABOR_LAYOUT_UNSUPPORTED`
- 일수 칸이 글자이거나 수식 캐시가 없음: `LABOR_DAYS_UNREADABLE`

엑셀에서 재계산·저장한 뒤 다시 확인한다. 빈 칸(수식 없음)만 0일로 본다.

#### 7번 영농자재소요계획 인쇄 설정

X01 `7. 영농자재소요계획`은 교수님 양식 인쇄 설정(인쇄 영역 B2:G61, 세로, 배율 85%, 행·열 나눔 없음)을 그대로 쓴다([사용자 결정 2026-10-28]). 특용작물 사본에서 `gg_excel_print.py` 맵에 이 시트를 넣으면 거부한다. 읽고 식별했지만 X01·X02 어느 쪽도 아닌 사본(`unknown`)도 X01이 아니라고 증명할 수 없으므로 같이 거부한다. 읽기 공통 확인을 통과하지 못한 파일은 인쇄 사본을 만들지 않는다(종료 2). 다른 시트와 X02로 식별된 사본은 기존대로 인쇄 사본을 만들 수 있다.

#### 양식 연도 등록과 갱신

교수님 양식은 해마다 조금씩 바뀔 수 있다. 정본이 새로 나오면 아래 순서로 등록하고, 진행 중인 논문 작업의 양식은 바꾸지 않는다.

1. **연도 등록**: 새 파일을 `references/workbook-reference-set.json` `members`에 새 `ref_id`로 추가한다(SHA-256·시트 이름·받은 날·출처). 옛 항목은 지우지 않는다. 논문 작업은 시작할 때 쓰는 양식 `ref_id`·해시를 정본에 기록하고 작업 중에는 바꾸지 않는다.
2. **차이표**: 옛·새 정본 각각에 `gg_excel_template.py inspect`(시트 순서·모든 칸 글자·값·수식)와 `gg_workbook_audit.py inventory --ref <ref_id>`(수식 목록)를 돌려 비교표를 만들고, 인쇄 설정(인쇄 영역·방향·배율·나눔)은 두 파일의 시트 XML `pageSetup`·`rowBreaks`·`colBreaks`와 인쇄 영역 정의를 직접 대조한다. 관리자가 확인한 뒤 기본 양식을 넘긴다.
3. **예외 층 다시 맞추기**: 칸 이름 표(`x01-crop-labels.json`의 등록 해시·감사 파일·원래 글자, 다르면 거부), 레이아웃 기준점(`kang-finance-workbook.json` `layout_anchors`, 맞지 않으면 `unknown`으로 거부), 수식 정정 명세(`corrections/`, 원본 해시·기대 수식 전제조건으로 거부)는 새 좌표·글자로 다시 맞춘다. 새 정본의 수식 감사(`formula-audit/`)를 만들어 칸 이름 표와 `d8-spec.json`에 감사 SHA를 함께 적는다. 7번 인쇄 설정 규칙은 X01 배치로 감지되는 모든 사본에 걸리므로 새 정본의 인쇄 설정이 달라졌으면 규칙 문구와 `gg_excel_print.py`의 설명을 함께 고친다.
4. **알려진 오류 목록**: 정본 안의 명백한 오류는 고치지 않고 그대로 쓰되, `references/common-workbooks/formula-audit/AUDIT.md`에 연도별로 적고 다음 정본에서 고쳐졌는지 확인한다.

## Saved-file compatibility verification

The generator preserves original XML namespace prefixes and declarations with
DOM edits. Markup Compatibility attribute values (including unqualified
`mc:Choice/@Requires`) must resolve in their original namespace scope. Invalid
references fail before publication. ZIP integrity and generic XML parsing alone
do not establish that Excel can open a workbook.

Before recommending a generated copy as usable, open an isolated read-only copy
in the target spreadsheet application with repair alerts enabled, confirm the
expected worksheets, and inspect a native preview/render. Bind that evidence to
the delivered file hash. Record application/OS scope; unavailable native checks
remain unverified. A successful open is separate from financial recalculation
and submission approval.

## Fill the reviewed copy with current inputs

Blanking and filling are separate operations. A cell preserved during blanking
may contain an exemplar year, price or ratio. It becomes a student input only
after its role, period and source are reviewed. Never silently reuse it.

Run `python3 scripts/gg_excel_fill.py --template blank.xlsx --map write-map.json
--values current-values.json --out filled-v1.xlsx --receipt filled-v1-receipt.json`
with real paths. Always retain the receipt for the project workflow.

The write-map schema is `gg-xlsx-fill-map/v1`. Its `template.sha256` must match
the actual input copy. Each `entries` item names an exact `sheet`, `cell` or
`range`, semantic `role`, applicable `period`, `source_note`, and explicit
`editable: true`. This is not the `inspect|clear` map schema.

The values schema is `gg-xlsx-fill-values/v1`. Each item in `values` names an
exact `sheet` and `cell`, `value`, `value_type` (`string`, `integer`, `number`,
`boolean`, or `blank` with null), and `evidence_ref` containing `source_id`,
`revision`, `locator`, and `origin` (`factual`, `assumption`, or `synthetic`).
Register those source IDs/revisions in the project and read the actual source;
the utility checks the supplied structure, not whether a citation is true.
Synthetic inputs belong only to clearly labeled development fixtures.

Unknown values stay unresolved in the project. An explicit blank removes the
cell value; the spreadsheet may calculate a zero, partial sum or error. None
of these proves that the unknown equals zero. Keep affected conclusions
blocked and explain the limit in the manuscript. Omitted mapped cells remain
unchanged and are counted in the receipt.

The utility refuses stale hashes, duplicate/overlapping targets, unknown
sheets/cells, formula writes and merged non-anchor writes. It preserves
formulas/formatting and invalidates formula caches. The receipt binds template,
map, values and output hashes and records each old/new value with its evidence.
It may contain private inputs and is not a public artifact.

After filling, recalculate in the spreadsheet application and compare the
saved values in the applicable major's form with the manuscript. Check inherited formulas, including
channel/grade ratios and period offsets. `filled` means mapped values were
written; it is not calculation or content approval. Formula corrections need
a separate reviewed change and dependent checks; this utility never rewrites
formulas. Corrections create a new input/output revision and invalidate old
review claims through the project ledger.

## Declare display rounding where the manuscript shows rounded numbers

When the recalculated workbook is compared with the manuscript, every figure
is checked digit for digit: the printed number must equal the stored workbook
value exactly. If a passage deliberately prints a rounded figure, the rounding
has to be declared right next to that figure; without a declaration the
comparison stays exact and reports a mismatch.

Write the declaration in the same clause as the claim, or in the owning
table's own caption or note, naming the unit and the number of decimal places
kept:

- `… 당기순이익은 4,289.7 천원(소수 1자리까지 반올림하여 표시)이다.`
- `표 1. 농장A 손익 (단위: 천원, 소수 1자리까지 반올림하여 표시)`
- `주) 천원은 소수 3자리까지 반올림하여 표시한다.` — a table note works too
- `소수 0자리까지` or `정수로 반올림하여 표시` for whole-number display

반올림 here is the everyday rule: at the cut position a discarded digit of 5
or more raises the last kept digit, so `14532.549` becomes `14532.5` and
`14532.550` becomes `14532.6`. Negative amounts round away from zero in the
same manner (`-14532.55 → -14532.6`).

The declaration is local and is applied strictly:

- It governs only its own claim clause or its own table. A neighbouring
  sentence, clause or table cannot borrow it.
- The unit tied to the declaration must be the unit of the figure being
  compared; a declaration written about `원` does not apply to a `천원`
  figure.
- The number of places must be explicit. A bare `반올림하여 표시` with no
  place count, two different place counts for the same unit, or an
  unreasonable count is not a usable declaration: the figure is reported as
  a mismatch instead of being quietly compared exactly.
- With a valid declaration, the workbook value is rounded to the declared
  places for comparison only — the printed figure itself is never
  re-rounded. Printing the full-precision value (`14,532.549`) or a
  differently rounded one (`14,532.4`) under a one-place declaration still
  fails.

The stored workbook values and every formula keep full precision at all
times. A rounded display figure is presentation only; never reuse it as an
input to another calculation.

Accounting-style negatives are read only inside table cells: a cell whose
whole value is `(N)` — one matched ASCII parenthesis pair around an
unsigned integer or decimal, e.g. `(121,246.7)` — is compared as `-N`,
and `(N) 천원`-style trailing units follow the same unit rules as plain
cells. Signed forms inside or before the parentheses (`(-N)`, `(+N)`,
`-(N)`), nested or unbalanced parentheses, malformed commas, letters or
units inside the parentheses, and trailing junk are rejected rather than
interpreted. Sentence claims and workbook prose comparisons do not read
parenthesized negatives — there they still need an explicit `-` sign.
Fullwidth parentheses and other notations are unsupported.


## Correct a verified formula defect in a copy

Preserving formulas during blanking/filling does not certify the example's
arithmetic. If a formula uses the wrong area, period or loan condition, record
the evidence and exact old/new expression before modifying a new copy. Never
silently alter the student's plan to fit an example formula.

`gg_excel_formula_patch.py --source copy.xlsx --map formula-map.json --out
patched.xlsx --receipt patch-receipt.json` accepts `gg-xlsx-formula-patch-map/v1`
with `source.sha256` and `patches`: exact `sheet`, `cell`, `expected_formula`,
`new_formula`, `reason`, and `evidence_ref` (`source_id`, `revision`, `locator`).
Ordinary existing formulas use `expected_formula`. An explicitly reviewed numeric constant can instead use `expected_value` (finite number, exact decimal match) to restore a missing calculation; this must never be an implicit conversion. The receipt records the original numeric text and `value_to_formula` operation. For a shared-formula target, the utility materializes only its complete rectangular shared group using the unique anchor and relative/absolute reference translation, then checks the exact effective `expected_formula`. The receipt lists every materialized member in `expanded_shared_formulas`; unrequested members must retain their effective formulas. Missing or ambiguous anchors/ranges, incomplete groups, array formulas and unsafe translations are refused. Materialization is not permission to alter unrequested formulas. The source is preserved and mismatched formulas,
external formulas, duplicate targets and overwrite attempts are refused.
Both old/new formulas are recorded and caches are invalidated. A patch receipt
is an execution record; independent arithmetic and native checks still apply.
Update the fill map hash to the patched copy before filling it.

For native output from a supplied template, use `gg_office.py excel filled.xlsx
--template-receipt filled-receipt.json --out-dir .gg-work/<산출물>/<회차>`. This checks the fill
receipt's input binding and compares saved sheet order, visibility, merges,
print ranges, formulas and values against this copy. It does not apply the
legacy generator's fixed cell contract. Native number-format changes are listed
for visual review; fonts, row/column layout and full pages still require render
inspection. `financial_content_validation: not_run` remains explicit until the
separate current-input financial/body comparison is recorded. Do not combine
this option with a legacy generated-workbook `--spec`.

### Print-layout copy

Native PDF inspection may reveal orphaned columns or split table blocks even when all cells and formulas are correct. Prepare a `gg-xlsx-print-map/v1` map bound to `source.sha256`, with explicit sheet names, `fit_width` (positive integer), `fit_height` (0 for unlimited or positive), optional orientation and existing print-area rectangle, optional `row_breaks` (unique positive row numbers; `fit_height: 0`) to keep complete table blocks together, and a reason for each change. Run `python3 scripts/gg_excel_print.py --source copy.xlsx --map print-map.json --out print-copy.xlsx --receipt print-receipt.json` before typed input filling. It changes only print settings in a new copy and records the change; render every final PDF page to judge readability. Do not shrink a long table to an unreadable page merely to reduce page count. An original exemplar footer, year, crop, source attribution or percentage is editable case data when it describes the previous author's case: explicitly replace it from current evidence and record provenance, instead of retaining a misleading example claim.

A plan may also provide explicit `row_heights` and `wrap_cells` entries when a native preview exposes a local readability defect. `row_heights` is a list of `{ "row": <existing row number>, "height": <finite number> }`; heights must be greater than 0 and at most Excel's 409.5-point limit. `wrap_cells` is a list of exact existing cell references (for example `B6`). The utility rejects missing rows/cells, duplicate targets, out-of-range values, and merged non-anchor cells. Wrapping clones the selected cell's `xf`, appends a unique style record, and sets `alignment wrapText="1"`; it never mutates a shared style or cell value/formula. Receipts list row-height and style changes separately while `cell_changes` remains zero. No global row or column formatting is inferred. If `cellXfs` uses an `AlternateContent` with more than one effective `xf` branch (for example both `Choice` and `Fallback`), wrapping is refused because branch selection is application-dependent; no style is guessed.

### Meaning and yearly references

For a verified rate displayed as zero by an integer format, an explicit
`number_formats` entry can use `0.00%`. The stored fraction and its dependent
formulas remain unchanged; verify the displayed rate in the native PDF.
Fractional labor days can use an explicit `#,##0.00` override without rounding
the stored days or changing the labor-cost formula.

A zero error-cell count is not a financial review. Check first-year ratios and subtotals against the remaining years; trace each yearly rent, tools, other expense, subsidy and investment row to that same year, including subtotal-to-detail links and cumulative balances. Compare written rate/period notes with formulas. Use a separate nonzero, different-per-year probe or inspect every corresponding reference: all-zero sample costs can hide copied first-year references. Keep probe values out of the student plan. A hidden reference/helper cell must never influence a printed result without a labeled input and source. Record the tested assumptions and applicability of each correction; do not force a student's loan, depreciation, rent or grant-accounting conditions to match the exemplar. Explicit no-expense answers in a synthetic fixture may be zero; missing real answers remain unknown.


### Registering a verified supplied-template output

After the native Excel conversion, copy its manifest into the project workspace.
Register the current XLSX output with `layout_kind: "provided_template"` and
`native_manifest: {"path": "<workspace-relative manifest>", "sha256": "<actual hash>"}`.
The manifest must bind the current workbook hash, a successful Microsoft Excel
run, `validation.excel.valid: true`, and `school_validation:
"source_template_preservation_only"`, with no structure/school issues. The
specialty-crop supplied-template runtime also scans that workbook for formula and cached error cells.
This route preserves the supplied form's coordinates; it does not certify
financial interpretation or replace independent content and print review.
Do not label an unverified workbook or an old output with this tag to bypass
checks. Native Excel is the verified adapter currently implemented for this
route; another application requires its own tested adapter and evidence.

### Verify the complete operation lineage

After all filling, formula/layout changes and native saves, check the ordered
receipt chain rather than refreshing an old receipt's output hash:

```text
python3 scripts/gg_office.py verify-template-lineage final.xlsx \
  --receipts clear-receipt.json fill-receipt.json layout-receipt.json \
  native/final.office.manifest.json --json
```

List every intervening operation in execution order, including intermediate
native manifests. The chain must begin with a clear or fill receipt. Every
declared input, map, values file, output and native PDF must still match its
recorded hash; each operation's source must match the preceding output.
Native manifests must bind the preceding operation receipt. Relative artifact
paths resolve beside their receipt. Missing paths, omitted transformations,
wrong ordering and a different final workbook are refused.

The result's `authority: operation_lineage_only` proves file linkage, not
independent truth, arithmetic, privacy, readability or professor approval.
Receipts are local records from the selected project. The CLI may resolve an
absolute artifact path when a source and its receipt live in different
folders; that path is checked for existence and exact hash, not certified as
an approved source merely because it was named. Do not adopt receipts found
in an unrelated reference folder as the current project's records. Inspect
the selected source role and operation chain before using the result. This
local trust model does not prevent a user with equal filesystem write access
from forging all records; it is not a signature or professor identity service.
A clear operation's `partial` status remains visible in the result and is
not promoted. Hash-only legacy receipts can support the older single-operation
binding but cannot establish complete lineage. Do not manufacture a missing
native receipt after manual recovery; preserve the gap and re-execute that
stage through the observed adapter before claiming an unbroken chain.
