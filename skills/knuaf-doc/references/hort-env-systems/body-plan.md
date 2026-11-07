# 원예환경시스템전공 본문 집필 순서표 (HT2 목차 계약)

소유 전공: `hort_env_systems`
기준 module_version: 0.2.0
형식 기준(교수 지정 틀기준): HT2 — `profile.json` `precedents[1]`(sha256 `e5ca9ff3…8e4c`, 물리 1–178쪽)
내용 참고: HT1 — `profile.json` `precedents[0]`

이 문서는 **장 순서**가 아니라 **집필 작업 순서**와 절마다 필요한 입력·근거·부록 시트를 묶은 표다. 절 제목·쪽번호의 정본은 `profile.json` `precedents[1].sections`·`appendices`이고, 이 표는 그 노드를 빠짐없이 한 번씩 소비한다. 제목·순서가 바뀌면 `profile.json`을 고치고 이 표를 다시 대조한다(시험 `tests/test_hort_body_plan.py`).

원문에서 추출한 목차·본문 쪽은 저장소에 넣지 않는다. 추출 결과는 작업 폴더 산출물(`wb-out/ht2-toc.tsv`, `wb-out/ht2-front.txt`, `wb-out/ht2-back.txt`)에만 두고, 이 문서에는 노드·쪽번호·형식 관찰만 남긴다.

## 재무 수치 자리 표시

재무 숫자의 정본은 학교 엑셀(18시트)이 실제 Excel에서 재계산한 값이며, 그 값이 정본 금액 사실로 등록되기 전에는 본문에 숫자를 쓰지 않고 `[확인 필요 — 엑셀 작성 후]`로 둔다. 대상: 요약의 사업규모·중장기 목표 수치, Ⅲ-1-나 재무능력, Ⅳ-2 영농 목표의 연도별 수치, Ⅴ-1 투자·원리금, Ⅴ-3 생산계획의 수량·금액, Ⅴ-4 생산원가, 부록 1–18. 그 밖의 절은 엑셀 없이 먼저 쓸 수 있다(설계 §3-4).

## 집필 단계

| 단계 | 범위 | 끝나는 판단 |
|---|---|---|
| 1. 기초 사실 | Ⅰ, Ⅱ-1·2·3 | 학생 본인 사실·작목·시설·재배 방식이 정본 답변으로 확정됨 |
| 2. 분석 | Ⅲ-1(재무 수치 제외)·2·3·4, Ⅳ-1·2(수치 제외)·3 | 분석이 1단계 사실과 공개 근거에서 나오고, SWOT가 Ⅲ-1·2·3의 결과를 회수함 |
| 3. 계획 | Ⅴ-1·2·3·4·5·6 | 시설 규모→생산능력→판매→원가가 같은 기간·단위로 이어짐. 재무 숫자는 엑셀 사실 등록 뒤 채움 |
| 4. 마무리 | 요약, Ⅵ, 참고문헌, 감사의 글, 부모님 동의서, 부록 1–18 | 본문·재무가 바뀐 뒤 요약·목차·표/그림 목차를 다시 대조함 |

## 절별 표

`질문`은 `gg_major_contract.HORT_ENV_SYSTEMS_MODULE.question_schema`의 field_id(접두 `hort_env_systems.` 생략), `근거`는 `profile.json` `evidence_sources`의 use_scope, `시트`는 `workbook_reference.appendix_map`의 부록 번호다. `—`는 그 절에 직접 대응하는 선언 입력이 없다는 뜻이며, 없는 근거를 만들어 채우지 않는다.

| 단계 | HT2 노드 | HT2 인쇄쪽 | 절 | 질문 | 근거 | 시트 |
|---|---|---|---|---|---|---|
| 1 | ch1 | 1 | Ⅰ. 머리말 | business_type, startup_type, crop_item | industry_context | — |
| 1 | ch2 | 3 | Ⅱ. 농장현황 | — | — | — |
| 1 | ch2_sec1 | 3 | 1. 농장개요 | region, site_area, facility_type, facility_area, cultivation_area, startup_type | — | — |
| 1 | ch2_sec2 | 6 | 2. 생산품목 | crop_item, cultivar, product_unit, marketable_rate | industry_context | — |
| 1 | ch2_sec3 | 9 | 3. 생산기술 및 재배방법 | cultivation_system, crop_cycle, environment_control, water_energy_source | technical_reference, facility_spec_registry | — |
| 2 | ch3 | 55 | Ⅲ. 영농환경 분석 | — | — | — |
| 2 | ch3_sec1 | 55 | 1. 내부역량 분석 | labor_plan, funding_plan | — | 1, 17 |
| 2 | ch3_sec2 | 56 | 2. 외부환경 분석 | region, disaster_design_basis, channel, price_basis, growth_assumption | disaster_design_standard, income_comparison, industry_context | 7 |
| 2 | ch3_sec3 | 91 | 3. 모델농장 벤치마킹 분석 | — | industry_context | 3 |
| 2 | ch3_sec4 | 94 | 4. SWOT 분석 | — | — | — |
| 2 | ch4 | 98 | Ⅳ. 농장비전 및 영농목표 | — | — | — |
| 2 | ch4_sec1 | 98 | 1. 농장비전 | crop_item, business_type | — | — |
| 2 | ch4_sec2 | 99 | 2. 영농 목표 | growth_assumption, fiscal_year_mapping | income_comparison | 2 |
| 2 | ch4_sec3 | 100 | 3. 농장 경영전략 | labor_plan, funding_plan, environment_control, channel | — | — |
| 3 | ch5 | 102 | Ⅴ. 영농계획수립 | — | — | — |
| 3 | ch5_sec1 | 102 | 1. 투자계획 및 원리금 상환계획 | facility_quote, funding_plan, workbook_selection | — | 4, 5 |
| 3 | ch5_sec2 | 104 | 2. 시설계획 | facility_type, facility_area, cultivation_area, disaster_design_basis, environment_control, water_energy_source, facility_quote | disaster_design_standard, facility_spec_registry, technical_reference | 6 |
| 3 | ch5_sec3 | 119 | 3. 생산계획 | yield_basis, marketable_rate, crop_cycle, fiscal_year_mapping | income_comparison | 8, 10 |
| 3 | ch5_sec4 | 120 | 4. 생산원가계획 | workbook_selection | — | 9, 11, 12, 13, 14 |
| 3 | ch5_sec5 | 123 | 5. 마케팅계획 | channel, price_basis, product_unit | industry_context | 7 |
| 3 | ch5_sec6 | 127 | 6. 교육 및 봉사계획 | — | — | — |
| 4 | ch6 | 128 | Ⅵ. 맺음말 | — | — | — |
| 4 | back_references | 130 | 참고문헌 | — | — | — |
| 4 | back_ack | 131 | 감사의 글 | — | — | — |
| 4 | back_consent | 133 | 부모님 동의서 | — | — | — |

## 부록 (18시트 ↔ 부록 번호)

부록은 학교 엑셀 18시트를 그대로 붙인다. 부록 제목은 HT2 `appendices` 제목이며 시트명과 표기(띄어쓰기)만 다르다. HT2 부록 목록에는 쪽번호가 없고, 부록 본문 쪽번호는 인쇄쪽 135–164로 이어진다. 재무 사실 등록 전에는 각 부록을 `[확인 필요 — 엑셀 작성 후]`로 둔다.

| 부록 | HT2 제목 | 시트 |
|---|---|---|
| 1 | 부록 1. 자산조사 | 1. 자산조사 |
| 2 | 부록 2. 중장기 영농목표 | 2. 중장기영농목표 |
| 3 | 부록 3. 모델농장분석 | 3.모델농장분석 |
| 4 | 부록 4. 투자계획 | 4.투자계획 |
| 5 | 부록 5. 원리금 상환계획 | 5. 원리금상환계획 |
| 6 | 부록 6. 시설계획 | 6.시설계획 |
| 7 | 부록 7. 판매계획 | 7. 판매계획 |
| 8 | 부록 8. 생산계획 | 8. 생산계획 |
| 9 | 부록 9. 영농자재 구매계획 | 9. 영농자재구매계획 |
| 10 | 부록 10. 작부체계 | 10. 작부체계 |
| 11 | 부록 11. 인건비 계획 | 11. 인건비계획 |
| 12 | 부록 12. 경비계획 | 12 .경비계획 |
| 13 | 부록 13. 감가상각비 계획 | 13. 감가상각비계획 |
| 14 | 부록 14. 생산원가계획 | 14. 생산원가계획 |
| 15 | 부록 15. 손익계획 | 15. 손익계획 |
| 16 | 부록 16. 수익성 분석 | 16. 수익성분석 |
| 17 | 부록 17. 추정대차대조표 | 17. 추정대차대조표 |
| 18 | 부록 18. 현금흐름계획 | 18. 현금흐름계획 |

## HT2에서 관찰한 형식 (A — 지킬 항목)

실측은 HT2 물리 3–14쪽(앞머리·목차)과 15–178쪽(본문·후미)의 텍스트 추출이다. **선배 논문의 지면 배치를 학교 의무로 승격하지 않는다.** 아래는 형식 기준으로 채택한 관찰이며, 확인되지 않은 값은 정하지 않는다.

| 항목 | HT2 관찰 | 적용 |
|---|---|---|
| 앞머리 순서 | (별도 학교 서식: 논문지도교수 신청서·논문심사위원추천서) → 겉표지 → 표제면 → 제출면 → 인준면 → 목차 | `SCHOOL_FRONTMATTER["hort_env_systems"]`의 두 표지·제출·인준 순서와 같다. 신청서·추천서는 본문에 넣지 않고 `신청서·추천서는 학교 서식으로 따로 제출`로 알린다 |
| 지도교수 표기 | 표제면이 아니라 제출면 상단 `○○○ 교수 지도` | 빌더가 이미 제출서에 넣는다 |
| 목차 머리 | `목차` 자신을 첫 항목으로 싣고 `요약`(ⅲ)·`표목차`(ⅳ)·`그림목차`(ⅵ)를 Ⅰ장 앞에 둔다 | 빌더의 자동 목차는 `요약`·`표 목차`·`그림 목차`를 낸다. `목차` 자기 항목은 넣지 않는다(자기 참조) |
| 장 번호 | Ⅰ–Ⅵ, Ⅵ은 `맺음말` | 같다 |
| 절 번호 | `1.` → `가.` → `가)` 3단계 | 같다 |
| 표 제목 | 표 **위**에 `표 N. 제목`, 표 목차에 등재 | A. 캡션은 표 앞 |
| 그림 제목 | 그림 **아래**에 `그림 N. 제목`, 그 위에 `출처 : …` | A. 캡션은 그림 뒤, 출처는 캡션 앞 |
| 부록 | `부록 N. <시트 제목>` 18개, 부록 목록에 쪽번호 없음, 본문 인쇄쪽 연속 | A. 부록 목록 쪽번호를 억지로 만들지 않는다 |
| 후미 순서 | 참고문헌 → 감사의 글 → 부모님 동의서 → 부록 | A. `부모님 동의서`가 후미에 온다 |

## HT2 목차와 현행 계약의 차이 (책임자 보고용)

`profile.json`의 HT2 `sections`는 장·절(1단계)과 일부 `가.`만 담는다. HT2 목차 전문(66행)과 대조한 차이는 다음과 같다. **계약을 넓힐지는 책임자 판단**이며, 이 문서는 차이만 남긴다.

| # | HT2 목차 | 현행 `profile.json`/`document_plan` | 성격 |
|---|---|---|---|
| 1 | Ⅱ-2 아래 `가. 프릴아이스 / 나. 버터헤드 / 다. 로메인 / 라. 바타비아`(품종 4개) | 없음 | 작목별 내용 — 계약에 넣지 않는다 |
| 2 | Ⅲ-2 아래 `가. 영농환경 분석 / 나. 시장환경 / 다. 정책환경 / 라. 민원발생 가능성 및 해결방안` | `sections`는 절까지만 | 2단계 확장 후보. `라. 민원`은 다른 선례에 없는 항목 |
| 3 | Ⅳ-3 아래 `가. 의사결정 / 나. 생산관리 / 다. 품질관리 / 라. 자재구매 및 관리 / 마. 인사노무관리 / 바. 자금관리 / 사. 판매 및 고객관리` | `sections`는 절까지만 | 2단계 확장 후보 |
| 4 | Ⅴ-1 아래 `가. 투자계획 / 나. 원리금 상환계획` | `document_plan`은 `investment_repayment` 한 노드 | 계획 노드는 하나, 본문은 두 소절 — 노드 분리 불필요 |
| 5 | Ⅴ-2 아래 `가. 시설모델 / 나. 시공비 / 다. 시설 배치도 / 라. 환경설계 / 마. 내부 주요설비계획` | `facility_plan` 한 노드 | 같음 |
| 6 | Ⅴ-4 아래 `가. 생산원가 / 나. 영농자재 구매 계획` | `cost_plan` 한 노드 | 영농자재 계획이 Ⅴ-4-나에 온다(별도 절 아님) |
| 7 | Ⅴ-6 `교육 및 봉사계획` | `education_service` | 같음 |
| 8 | 후미 `부모님 동의서` | `sections`에 `back_consent`, 프런트매터 명세에는 없음 | 후미 생성은 빌더 미지원 영역 — 동의서는 학교 서식/별도 제출로 안내 |
| 9 | 표 목차에 `표 50.`이 두 번(난방기 용량 비교 다음, 시설재배 작물 계수) | — | **선배 논문 오기** — 재현하지 않는다. 번호는 1부터 연속으로 매긴다 |
| 10 | Ⅳ장 제목 `농장비전 및 영농목표`(HT1은 Ⅵ장을 `Ⅴ. 맺음말`로 오기) | HT2 표기와 같음 | HT1 오기는 따르지 않는다 |
