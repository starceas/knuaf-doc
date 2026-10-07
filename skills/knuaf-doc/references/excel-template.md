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
a separately documented correction is required. Do not type desired totals into
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

1. **세 상승률의 분리 및 관측 CAGR 강제**:
   - 일반물가 상승률은 영농자재·경비, 임금 상승률은 노무비, 판매가 상승률은 매출에 각각 적용한다.
   - 세 입력을 하나의 소비자물가 상승률로 묶지 않는다.
   - **상승률 r은 공식 관측 수열의 끝점 연평균 변화율(CAGR)로만 산출**된다. 명시적 `rate` 입력은 검산용(`max(5e-4, 0.05 × |cagr|)` 허용오차 내 일치)이며, 관측값 없는 임의 계획 가정 r은 입력할 수 없다. 내부 계산 배율은 `1 + r`이다.
   - 계획연도 배율은 `(1 + r)^(계획연도 - application_base_year)`로 적용되며, 적용 횟수는 연도 차이와 같다. 모든 관측값·r·배율은 유한수여야 한다(NaN/Infinity 거부).
2. **노무비 반영 및 이중 적용 금지**:
   - 노무비(고용노동임금) 계획은 **연도별 임금 상승률**을 반영한다.
   - 동일 가격에 상승률을 두 번 곱하지 않는다. 노무 단가에 임금 상승률을 곱한 뒤, 노무비 총액에 일반물가를 다시 곱하지 않는다. 감가상각비와 고정 차입금 원리금에는 물가를 적용하지 않는다.
3. **공식 통계 근거 및 판매가 미반영 명시 선택**:
   - 일반물가는 KOSIS 소비자물가 총지수(`DT_1J22003`), 자재·경비 세목은 농가구입가격지수 재료비·경비(`DT_1J62`), 노무비는 농가구입가격지수 노무비(`DT_1J62`), 판매가는 농진청 소득자료집 동일 작목 주산물 단가를 우선한다.
   - 판매가 수열은 계획의 작목·재배형태·지역·상품·단위·거래단계 차원과 일치해야 한다(`crop` 필수). **총합 지수인 농가판매가격지수 총지수(`DT_1J60`)와 번들 KAMIS 쌀 수열(`allowed_roles: []`)은 판매가로 적용할 수 없다.**
   - 학생 작목과 동일 조건의 공식 수열이 없으면, 전국 총지수를 임의 대입하지 않고 **"판매가 상승 미반영(확인 불가)"을 명시 선택**한다.
   - 통계에서 확인되지 않은 수치는 임의 추정 없이 `확인 불가`로 남긴다.
4. **수식 숫자 표기 및 Excel 정밀도 보존 (`_excel_number`)**:
   - 패치(`materialize-d8`)가 워크북 수식에 쓰는 상승률 상수는 **유효숫자 15자리**(Excel이 보존하는 정밀도)로 직렬화하여 기록한다.
   - Excel은 수식에 적힌 숫자를 최대 15자리까지만 보존하고 끝자리를 잘라내므로(절단), 15자리를 초과하는 숫자를 그대로 쓰면 Excel에서 열고 다시 저장할 때 수식 글자(문자열 토큰)가 달라져 보존 검사에 걸리게 된다. 처음부터 15자리 정밀도로 기록해 두면 재저장해도 수식 문자열이 바뀌지 않아 안전하게 통과할 수 있다.
   - 부호 없는 숫자가 21자리 이하이면 일반 소수점 형태(고정소수점)로 표기하고, 초과하면 지수 형태(`d.dddE±dd`)로 표기한다. 끝에 붙는 불필요한 `.0`이나 부호 있는 0(`-0`)은 제거한다.
   - 영수증 `d8.rates[*]`에는 원래 계산된 상승률 `r`(원 CAGR 계산값)을 그대로 보존하고, 수식에 기록된 값은 `r_literal` 필드에 별도로 남긴다.
   - 원래 `r`과 수식 표기값 `r_literal`의 차이는 최대 `1.1e-14 * |r|` 수준으로 아주 미세하여, 하류 허용오차(`1e-12`, 1조 분의 1) 안에서 안전하게 일치한다.

#### 두 엑셀 경로별 사용 절차 및 명령 계약

두 경로(템플릿 우선 경로 및 레거시 17시트 생성기 경로) 모두 동일한 공통 가격 가정 모듈(`gg_price_assumptions`)의 공식 출처 계약과 기준연도/역할 검증을 공유한다.

##### 경로 1. 학과 원본 템플릿 우선 경로 (W-B 패치·채우기 계약)

학과에서 배포한 원본 통합문서(XLSX)를 사본으로 복구하고 채우는 주 경로다.

1. **구조 복구 (`materialize-d8`)**:
   - 명령:
     ```bash
     python3 skills/knuaf-doc/scripts/gg_excel_formula_patch.py materialize-d8 --source <원본.xlsx> --out <패치맵.json>
     python3 skills/knuaf-doc/scripts/gg_excel_formula_patch.py --source <원본.xlsx> --map <패치맵.json> --out <복구사본.xlsx> --receipt <영수증.json> --project <프로젝트폴더> --major specialty_crops
     ```
   - 정상 X01은 0개 패치로 변경 없음 영수증을 발급한다(무변경 영수증은 기존 영수증 유지). 수식 패치 시 `r_literal`이 수식 문자열 상수로 기록된다.
2. **관측 준비 (`--prepare-observations` 및 `wage-map`)**:
   - 명령:
     ```bash
     python3 skills/knuaf-doc/scripts/gg_excel_formula_patch.py materialize-d8 --source <사본.xlsx> --out <준비맵.json> --prepare-observations
     python3 skills/knuaf-doc/scripts/gg_excel_formula_patch.py --source <사본.xlsx> --map <준비맵.json> --out <준비사본.xlsx> --receipt <영수증.json> --project <프로젝트폴더> --major specialty_crops
     python3 skills/knuaf-doc/scripts/gg_excel_template.py wage-map --source <준비사본.xlsx> --out-map <값지도.json>
     ```
   - **X01 원본 관측 준비**: 등록 X01 원본의 실제 관측 수식 변형(`Z27=Z28`)은 원본 SHA(`5d19b6a...`) 및 감사 자료(`formula-audit/x01.json`, SHA `785c2a1...`)에 묶어 검토된 수식으로 허용된다. 따라서 `materialize-d8 --prepare-observations`가 X01 원본 사본과 FX 양쪽 모두에서 정상 동작한다.
   - 관측 연도 사슬과 복사 수식(X01은 Z27, FX는 Z28)을 명시적으로 비우며, 기존 fill CLI를 통해 관측값을 입력한다(수식 쓰기 금지 유지).
3. **관측값 입력 형식 (`wage_observations`) 및 시간급 환산 계약**:
   - 값 파일에 `statistic_id`, `base_year`, 6개 `observation_years`, `application_base_year`를 지정한다.
   - `values`는 8번 시트 X26:Z31 18개 셀 모두 정수 연도 및 양수 원/일이어야 하며, `evidence_ref(source_id, revision, locator, origin="factual")`가 필수다.
   - **등록 수열 원단위 값·연도 대조**: 등록 id를 사용할 경우 환산 전 원단위 값과 연도가 등록 수열의 관측값과 **정확히 일치**해야 한다(`check_observations`).
   - **임금 관측 및 패치 성별 계약**: 등록 수열에 성별 차원이 없으면 남자(Y열)·여자(Z열) 모두 같은 등록 관측값이어야 한다. 등록 수열에 성별 차원(`gender`·`sex`·`성별`)이 있는 수열은 현재 단일 출처·남녀 전체 블록(`wage_observations`) 입력 형식으로는 **거부**된다(남성 수치를 여성 관측으로 복제 금지). 남녀 상승률(`wage_male`·`wage_female`) 입력은 각각 맞는 성별 출처만(`wage_male`은 남자, `wage_female`은 여자) 사용할 수 있으며 반대 성별이나 복수 성별 선언은 거부된다. 패치 시에도 생산자(`gg_excel_template.check_wage_gender`)를 통해 대상 열(남자=AC38, 여자=AD38)과 출처 성별을 엄격히 대조한다. 미관측 연도나 임의 변조값은 거부되며, 다른 값·연도를 쓰려면 다른 id의 공식 스키마 `price_sources`로 명시 등록해야 한다(같은 번들 id 덮어쓰기 불가).
   - **단위 일치 및 시간급 환산**: 관측표(X26:Z31)는 원/일 단위이므로 등록된 원/일 수열만 직접 입력할 수 있다. 농진청 소득자료집 고용노동 단가나 최저임금 등 시간급(원/시간) 수열을 일급으로 환산할 때는 반드시 `unit_conversion: {"from": "원/시간", "to": "원/일", "hours_per_day": float, "evidence_ref": {...}}` 명시 계약이 있어야 한다. **기본 환산 시간은 존재하지 않으며**(Q-W-B2 사용자/조사 후 결정 대기), 대조가 끝난 원값에 명시 시간을 곱한 결과로만 일급이 결정된다(임의 보상 환산 금지).
4. **가격 계약 (`--assumptions <JSON>`)**:
   - materialize-d8에 `--assumptions <가정.json>`을 전달한다.
   - 필드는 `wage_male`, `wage_female`, `sales`, 선택적 `general`로 분리한다. 각 계산 항목은 `base_year`, `observation_years`, `observed_values`, `application_base_year`, `evidence_ref`를 요구한다. 학생 선언 수열은 `assumptions.price_sources`에 포함한다.
   - r은 공식 관측값의 끝점 CAGR로만 정해지며(음수 허용), 명시 r은 검산 오차 내 일치해야 한다.
   - **가격 가정 어댑터**: 공통 차원 정규화(`canonical_dimensions`)를 적용하여 판매가 작목(`target_dimensions`)은 문자열(`crop: "봄감자"`), 목록(`crop: ["봄감자"]`), 또는 한글 별칭(`작목: "봄감자"`)을 모두 지원한다. 임금(`wage_male`, `wage_female`)에는 작목이 필수가 아니며 지역(`region` 또는 `지역` 별칭)만 지정할 수 있다.
   - **계획 차원의 권위**: 사업계획의 최상위 차원이 기준이며 보조 차원이 충돌하면 거부된다. 판매가는 수열이 선언한 모든 차원(단위 포함)이 계획과 일치해야 하며(`crop` 필수), 전국은 고정 지역값이지 와일드카드가 아니다. 미일치 시 `{"mode": "not_applied", "reason": "확인 불가"}`를 명시한다.
   - **역할별 허용 계약 (`allowed_roles`)**: 수열의 `allowed_roles`에 해당 역할(`wage`, `sales`, `general`)이 포함되어 있어야 한다.
     - 번들 일반물가(`general`): `official.kosis.cpi.total`, `official.kosis.farm_purchase.materials`, `official.kosis.farm_purchase.expenses`.
     - 번들 노무비(`wage`): `official.kosis.farm_purchase.labor`, `official.moel.establishment_wage.total`, `official.minimumwage.hourly`.
     - 번들 판매가(`sales`): `official.rda.crop_income.spring_potato.output_price` (단, 작목 등 계획 차원 일치 필수; 번들에 없는 학생 작목은 공식 스키마 `price_sources`로 직접 등록).
     - 시장 참고용(판매가 적용 불가, `allowed_roles: []`): `official.kosis.farm_sales.total`(총지수), `official.kamis.retail.rice_20kg.top`(쌀 소매가).
5. **적용 기초연도 대조 및 경계**:
   - 임금과 판매의 적용 기초연도는 원본에 보존된 F6 및 C36과 대조한다. 불일치를 이유로 원본 연도나 연결을 임의 변경하지 않는다. 공식 수열 준비와 구조 복구만으로 제출/통계 승인이라고 간주하지 않는다.
6. **Ⅳ장 재무표 각주 참고값 계약**:
   - Ⅳ장 상승률 각주는 전부 "참고값"으로 출력되며, 표 계산에 쓰였는지는 통합문서에서 따로 확인해야 한다 (전개기는 워크북 수식·캐시 역산 검증이나 실제 사용 보증을 하지 않음). 상세 계약과 구조화 각주 입력 형식은 [finance-flow](specialty-crops/finance-flow.md)를 따른다.

##### 경로 2. 레거시 17시트 생성기 경로 (W-A 스펙 계약)

스펙 JSON에서 17시트 전체를 새로 생성하는 보조 경로다.

- **스펙 계약**:
  - `spec["price_assumptions"]` 객체에 `general`, `wage`, `sales` 세 역할을 필수로 정의한다.
  - 이전 `inflation` 단일 키 입력 시 명시적 오류로 거부된다.
  - 각 역할은 공식 관측 수열의 끝점 CAGR로 계산되며, `rate`를 명시할 경우 수열 CAGR과 일치해야 한다.
  - 각 역할 정의 형식:
    ```json
    {
      "general": {
        "source_id": "official.kosis.cpi.total",
        "base_year": 2020,
        "application_base_year": 2025
      },
      "wage": {
        "source_id": "official.kosis.farm_purchase.labor",
        "base_year": 2020,
        "application_base_year": 2025
      },
      "sales": {
        "status": "not_applied",
        "reason": "확인 불가"
      }
    }
    ```
  - 학생이 직접 조회한 작목별 수열은 `spec["price_sources"]`에 선언한다. 학생 수열도 공인 기관 종류(`agency_kind`), 엄격 https 공식 도메인(백슬래시·@·공백·제어문자·NBSP·443 외 포트·유니코드/IDNA 변형 등 모호한 URL 거부), locator, 기준연도(지수 정수, 명목 null), unit, dimensions, allowed_roles를 준수해야 하며, 관측/미확인 연도 교집합은 거부된다.
  - 생성 명령:
    ```bash
    python3 skills/knuaf-doc/scripts/build_excel_finance_template.py project --input spec.json --out build/output.xlsx --major specialty_crops
    ```

#### 보류 사항 (사용자 결정 대기)

1. **W-023 노무비 첫해 F6=2027년 및 생산원가 첫해 대응**:
   - 실제 FX 및 X01 양식의 `8. 노무비계획!F6` 셀은 `2027년`으로 기재되어 있으며, 생산원가계획 첫해와의 연결 대응은 지도교수 해석 대상이다. 코드는 이를 임의 수정하지 않고 보존하며, **사용자 결정 대기**로 둔다.
2. **첫해 자재·경비의 `*1.025*1.025*1.025` 식**:
   - X01 `7!C16:C19`, `9!C18`의 1.025 3회 곱셈 식은 기준 단가의 연도 근거 확인 전까지 임의 변경하지 않고 보존하며, **사용자 결정 대기**로 둔다.
3. **Q-W-B2 시간급→일급 1일 환산 노동시간**:
   - 템플릿 임금 관측표는 원/일 단위이고, 번들 공식 고용노동 단가는 원/시간이다. 1일 노동시간의 공적 근거 확인 전까지 기본 환산 시간을 두지 않으며, 학생이 공식 근거와 함께 `unit_conversion`을 명시해야만 환산을 허용한다 (**사용자/조사 후 결정 대기**).

#### 특용작물 17시트의 좌표 (해당 원본 전용)

확인한 특용작물 양식에서는 매출이 Sheet 5, 영농자재가 Sheet 7, 노무비가 Sheet 8, 경비가 Sheet 9다. 이 좌표를 다른 전공에 적용하지 않는다. Ⅳ장 표 전개는 [specialty-crops/finance-flow.md](specialty-crops/finance-flow.md)를 따른다.

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
--template-receipt filled-receipt.json --out-dir native-v1`. This checks the fill
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
