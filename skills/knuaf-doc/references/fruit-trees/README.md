# 과수전공(fruit_trees) 안내 (모듈 0.2.0)

과수 모듈은 **자료를 읽고 학교 과수 양식(S01)에 맞춰 부족한 자료와 작성 계획을 보여 주는 단계**와 **학생이 입력한 물량으로 생산·재고를 검산하는 단계**까지 지원한다. 과수 논문 본문, 학교 재무 XLSX, 재무 계산, 교수 XLSX 사본 채우기는 아직 지원하지 않는다. 이런 출력은 공용 출력 규칙 B에서 `unsupported_output`으로 멈춘다. 과수 통계 팩은 비어 있다(`empty_slot`).

## 지원 경계

| 기능 | 상태 |
|---|---|
| 전공 바인딩, `gg.py major-plan` | 지원 (질문·문서 계획·근거 축·재무 미지원 표시) |
| `scripts/gg_fruit_plan.py <폴더>` 작성계획 | 지원 (읽기 전용, 파일·정본 쓰기 없음, 본문·계산값 없음) |
| `scripts/gg_orchard_production.py <폴더>` 생산·재고 검산 | 지원 (읽기 전용, kg·주·㎡만, 금액 없음) |
| `references/fruit-trees/premise-probes.md` 핵심 전제 질문 | 지원 (공용 인터뷰 형식) |
| 논문 본문·DOCX·PDF | 미지원 → `unsupported_output` |
| 학교 17시트·XLSX·템플릿 복제/채우기/수식 보정/인쇄 조정 | 미지원 → `unsupported_output` |
| 재무 계산(다년생·연간 모두) | 미지원 → `unsupported_output` |

## 문서 계획

S01 구조를 따른다: 앞부분(표지·제출·인준·요약·목차류), Ⅰ 서론, Ⅱ-1~5(입지·기상·토양, 산업현황, 모델농장, SWOT, 지원정책), Ⅲ-1~7(경영주, 품종, 재배작형·재식·재배력, 병해충·방제력, 연도별 생산, 마케팅, 투자·차입), Ⅳ 맺음말·참고문헌, 학부모 동의서, 감사의 글, 재무 부록 15역할. 다른 전공의 6장 구조를 가져오지 않는다. 학생 예시는 선례로 올리지 않고, 학부모 동의서의 합의·서명은 만들지 않는다.

## 자료 등록 순서 (기존 `gg.py apply`만 사용)

1. 전공 바인딩: `common.major_id = fruit_trees` 사실을 원답변 출처와 함께 등록(SKILL.md "전공 선택·계약").
2. 원본 등록: 학교 양식(S01), 공용 참조 XLSX(X01·X02), 연구 전달물(handoff JSON)을 **학생 프로젝트 폴더 안**에 두고 `sources` op로 등록한다. 등록 때 파일 해시가 기록되며, 등록 뒤 바이트가 바뀌면 계획에서 `stale_source`로 보인다. 원본은 제품에 포함하지 않는다.
3. 전달물 지정: 사실 `fruit_trees.research_handoff`(값 = handoff의 source id)를 등록한다.
4. 조사 근거: 전공 폴더 XLSX 부재를 확인할 때는 `source-scan` 수령증·진단 파일을 source로 등록하거나(폴더 전체 경로가 일치해야 함), 조사 범위의 전체 경로가 적힌 사용자 확인서를 source로 등록한다. 근거 없이 "없음"으로 판단하지 않는다.
5. 현재 작업 XLSX: 사실 `fruit_trees.workbook.selection`(값 = inventory `file_id` 또는 `none`). 정본의 선택이 전달물의 연구 당시 선택보다 우선한다.

## 모듈 0.2.0 재바인딩

0.2.0에서 질문 계약이 바뀌었다(수확 배치·물량 이동 집단, 연도별 수량 필드, `fruit_trees.plan_end_year`). `module_version` 0.1.0으로 바인딩한 프로젝트는 계획(`gg_fruit_plan.py`)과 검산(`gg_orchard_production.py`)이 `status: held`, `reason: binding_invalid`로 멈추고, 공용 출력 가드는 `major_binding_invalid`로 거부한다. 전공 바인딩 사실을 `module_version` 0.2.0으로 다시 등록하고(새 정본 개정), 교수 승인을 다시 받는다.

## 반복 집단(구역·식재집단·수확 배치·물량 이동)

집단 ID를 field ID에 넣는다: `fruit_trees.block.<id>.<필드>`, `fruit_trees.cohort.<id>.<필드>`, `fruit_trees.batch.<id>.<필드>`, `fruit_trees.move.<id>.<필드>` (`<id>`는 소문자·숫자·밑줄 32자 이내, 표시 이름과 별개). `...<id>.label`을 등록해야 집단이 선언된다. 질문은 `gg.py question --field fruit_trees.cohort.<id>.tree_count`처럼 집단별로 묻고 횟수를 센다. 과종·품종·대목은 식재집단 → 농장 기본값, 작형·가온은 소속 구역 → 농장 기본값 순으로 이어받되, 명시적 없음·해당 없음·거부·모름은 기본값으로 덮지 않는다. 계획은 합계·수령 곡선·생산량을 계산하지 않는다.

식재집단의 연도별 수량 필드 네 개(`bearing_trees`, `yield_kg_per_tree`, `bearing_area_m2`, `yield_kg_per_10a`)는 같은 field ID에 사실의 `period`("YYYY")만 바꿔 연도마다 등록한다. 계획 출력에서는 `{"by_period": {...}, "invalid_periods": [...]}` 모양이고, 같은 연도 두 값은 `duplicate_answer`다. 질문 횟수는 field ID 단위로 세므로 필요한 연도를 한 질문에 묶어 묻는다.

## 생산·재고 검산 `scripts/gg_orchard_production.py <폴더>`

학생 입력만으로 집단·연도별 생산량(kg), 수확 배치 배분, 배치별 재고 잔고를 정확한 분수로 계산해 읽기 전용으로 보여 준다(`derived: true`, `persisted: false`). 과수 바인딩이 없거나 다른 전공이면 `held`·종료코드 2. 금액·재무·본문·파일 출력은 없으며 공용 출력 규칙 B를 거치지 않는다.

- 생산: `yield_basis`가 `per_tree`면 결실 주수 × 주당 수량, `per_area`면 결실 면적 ÷ 1000 × 10a당 수량(`yield_area_basis`가 `bearing_area`일 때만). 결실 주수가 없으면 전체 주수로 대신하지 않는다. 명시 0만 0이고, 미제공·모름·없음은 `not_computable`.
- 한도: 구역 면적, 집단 면적·주수, 활성기간(`active_from_year`·`active_to_year`)을 대조한다. 한도 값이 없으면 `unverifiable`(`unverified` 숫자만, 소계 제외), 넘으면 `held`.
- 검산 기간: `business_start_year`부터 10년(`plan_end_year`가 있으면 그 해까지). 기간 밖 입력은 계산하되 `outside_plan_window` 경고.
- 배치 `origin`: `harvest`(집단 수확), `opening`(기초재고, `opening_year` 시점 잔량), `regrade`(등급 변경), `mix`(혼합). 이동 `kind`: `sale`·`loss`·`own_use`·`process`·`experience`(출고), `regrade`, `mix_in`. 혼합 배치에서 다시 등급 변경·혼합하는 이동은 `unsupported_move`.
- 결과가 모두 확정이면 `completeness: complete`, 하나라도 미상·위반이면 `partial`. 음수 재고는 해당 연도마다 `negative_inventory`로 남는다. 가공·체험 단위 환산과 재무는 다음 묶음이다.

## 연구 전달물 `knuaf-research-handoff/v1`

공용 검증기 `scripts/gg_research_handoff.py`가 중복 키·NaN·모르는 키·형식·ID 중복·자기해시를 엄격히 검사한다. 자기해시는 파일 내부 일관성만 보이며 출처 진위는 등록 원본 대조로 판단한다. 요구·역할·충돌이 일부 빠지면 계획은 계속 보여 주고 해당 절만 보류한다. 알려진 충돌 C01–C15가 빠지면 기본 영향 절을 보류하고, 결정됨·해당 없음은 등록 원본에 근거가 있을 때만 보류를 푼다. 위치 표시는 XML 문단 위치를 실제로 읽어 제목과 대조하며, 확인할 수 없으면 "미검증"으로 남긴다. 어떤 요구도 "충족"으로 표시하지 않는다.

## 참조 자료 식별값

과수 조사에 쓴 참조 자료 41종(학교 양식, 학생 예시 1건, 과일 전망, 농업기술길잡이 12권, NCS 학습모듈 12개, 공식 웹 원문 14개)은 `references/source-identities.json`에 파일 해시와 서지(제목·저자·발행처·연도)만 등재한다. 발췌·요약·원문은 두지 않으며, 등재는 재사용 권한이나 검증·승인이 아니다(`references/source-intake.md` 식별 전용 우선순위). 학생 예시는 제목과 이름만 적는다.

## 참조 XLSX 결정 (모든 전공 공용)

`scripts/gg_workbook_registry.py`와 `references/workbook-reference-set.json`. 전공 폴더 조사가 근거와 함께 완료되고 전공 XLSX가 없다고 확인되면 X01·X02가 채워 쓸 양식이다(대식물 계정이 필요하면 X02). 전공 XLSX가 있으면 그쪽을 쓰고 X01·X02는 비교 근거로만 둔다. 미조사·손상·미선택은 부재가 아니며, 역할은 사용자가 밝힌 것만 쓴다. 과수 모듈은 계획 출력을 지원하지 않아 양식 적용은 과수 범위 밖이다.

## 계획 검토

계획 검토는 `reviews`에 `review_kind: "plan"`으로 기존 규칙대로 등록한다(작성자와 검토자 분리, 보고서, 대상 참조·입력 지문). 계획은 입력 최신성·보고서·관측 증거·결과(pass·resolved)를 따로 판정한다. 전공 바인딩만 다시 등록한 경우에는 내용 검토를 무효화하지 않는다. 교수 승인은 바인딩으로 새 정본 개정이 생기면 다시 받아야 한다.
