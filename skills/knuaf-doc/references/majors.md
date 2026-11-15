# 전공별 지원 범위

전공 계약의 공용 규칙(바인딩·출력 가드)은 SKILL.md의 "전공 선택·계약"과 "결과물 출력 전 전공 확인"에 있다. 이 문서는 등록 전공별로 어디까지 지원하는지를 정리한다. 각 전공의 상세 경계는 아래 연결된 전공 README를 따른다.

## 특용작물 (specialty_crops, 모듈 1.0.0)

지원 범위는 **특용작물 작목 자체의 재배법**과 **특용작물을 활용한 재배법·가공·유통 계획**이다. 약용·향신·특용 작목의 재배 기술, 생산·가공·판매 계획, 재무표를 다루며, 가공·유통은 재배 계획과 재무에 연결된 범위에서 서술한다. 다음은 이 전공 범위가 아니다: 다른 전공 작목(채소·과수·화훼·식량작물)의 단독 재배, 재배 없이 가공품만 다루는 계획, 버섯류(별도 전공이 있는 학교에서는 특용작물전공 주제로 먼저 제안하지 않는다 — [lessons-learned.md](lessons-learned.md) L4).

질문·본문 Ⅰ~Ⅵ 작성·재무 엑셀·DOCX 검토본까지 지원한다. `gg.py rda-candidates <폴더> --crop <품목> --region <지역> --year <연도> --unit <단위> --use-scope <용도>`는 정본의 전공을 사용하고, 기존 `rda-lookup`은 물리 행 감사용 조회로 유지한다. `rda-lookup`은 원자료 감사용이다. `verification.verified`는 관측이 원문과 대조됐다는 뜻일 뿐 사용 승인이 아니며, 본문·표에 넣는 값은 `rda-candidates`의 `approved`만 쓴다. `unverified`·`ambiguous`·`series` 결과나 라벨이 '옮기지 말 것'인 값은 어떤 경우에도 옮기지 않는다.

`gg.py paper <폴더> --input <spec> [--out <경로>] --major specialty_crops`(저수준 `scripts/gg_school_paper.py`)는 겉표지~감사의 글·Ⅰ–Ⅵ 골격과 기상·재해표를 만들며, 미제공 값은 `[확인 필요]`로 남긴다. 이 본문 생성기는 특용작물 작목 본문 전용이다. 관리 발행 경로는 spec의 `table_sources`로 표 1~6의 외부 기관명만 제목 끝 `(출처 : 기관명)`으로 표시한다. 자체 작성·직접 조사 표는 출처 없음, 미제공은 no-op이며 자체 작성 여부는 사람이 검토한다([outputs](outputs.md)). `--body <병합 본문.md>`로 학생 본문을 실을 수 있다(앞머리는 생성기가 붙인다). 저수준 생성기 코드는 불변(frozen)으로 보존된다.

## 산업곤충 (industrial_insects, 모듈 0.2.0)

질문 목록·선택형 문서 계획·공식 통계 근거 검토까지만 지원한다: `gg.py insect-plan <폴더> --input <폴더 안 선택.json>`이 동급 선례(F0 교수 배포 예시, E1–E4 통과 논문) 위치를 연결한 계획을(`--input` 없이 실행하면 제품 라인·계획 연도별 질문 상태만 — 공용 인터뷰의 인스턴스 출처), `gg.py insect-evidence <폴더> --pdf <R0> --xlsx <R1>`이 고정 해시 원본을 매번 다시 읽은 공용 근거 축 검토를 읽기 전용으로 보여 준다. 선례는 강제 목차·표 수가 아니고 학생 수치는 기본값이 아니다. 곤충 본문·XLSX·재무 계산은 `unsupported_output`으로 멈추며 벤치마크 팩은 `empty_slot`이다. 같은 선례 PDF를 다시 받으면 `source-scan`/`source-resolve`의 등록부 판정(`insect.example.*`)이 `reuse_ready`일 때 원본을 다시 파싱하지 않고 내장 위치 지도를 쓴다(판정만 하며 코드가 막지는 않는다). 제품 라인 선언(`line_inventory`, id `l01`…)·라인 질문·모듈 0.2.0 재바인딩(같은 사실 id의 새 revision, 교수 재승인)·선택 형식·근거 상태·재사용 키·한계는 [industrial-insects](industrial-insects/README.md)를 따른다.

## 과수 (fruit_trees, 모듈 0.2.0)

학교 과수 양식(S01) 구조의 작성계획·물량 검산·**본문 DOCX·PDF·HWPX 검토본**을 지원한다:
- `scripts/gg_fruit_plan.py <폴더>`가 등록 원본·연구 전달물·구역/식재집단/수확 배치/물량 이동 답변·검토 상태를 읽기 전용으로 보여 준다.
- `scripts/gg_orchard_production.py <폴더>`가 학생 입력만으로 생산량·배치 재고(kg)를 읽기 전용으로 검산한다(금액 계산은 하지 않음).
- `build_docx.py <폴더> --major fruit_trees`가 S01 앞머리 순서(겉표지 → 표제면·제출서·인준서 → 목차)를 적용해 검토 전 DOCX를 만든다. 앞머리 역할은 표제면·제출서·인준서가 한 묶음이고, 물리 배치는 공용 기본값이라 학교 최종 서식 적합 판정은 별도다.
- `gg_office.py word`(macOS Word 설치 시)로 PDF를, `build_hwpx.py <폴더> --major fruit_trees`로 HWPX를 만든다. HWPX는 쪽 나눔·가운데 정렬·목차 실제 쪽번호·표 폭·장 시작 쪽을 한글에서 사람이 확인해야 한다.
- 재무 XLSX는 `fruit_16_sheet_v1` 프로필에 한해 열린다: 원본 sha256과 시트 순서·시트별 수식 지문이 실바이트에서 일치해야 사본 생성(`gg_excel_template.py clear`, 지도 `gg-xlsx-template-map/v1`)이 된다. 값 채우기(`gg_excel_fill.py`)는 **다른 스키마**(`gg-xlsx-fill-map/v1` + `template.sha256`)와 항목별 `role`·`period`·`source_note`·`editable is true`를 요구한다. 재계산값 금액 사실 등록(`gg_excel_facts.py`)은 native 재계산 영수증과 `gg-xlsx-fact-map/v1` 지도를 요구한다. 배포된 [등록용 사실 지도](fruit-trees/fruit-finance-fact-map.json)는 `gg-xlsx-fact-map/v1` 70행 중 매출액 계열 10행만 등록 경로(`cells`)에 두고, 2020 고정 물가 지수 구간에 수식으로 종속된 60행은 `held_cells`로 보류했다. `workbook`·`receipt` 해시는 저장소에서 비어 있어(`status: rows_reviewed_binding_pending_native_run`) native 재계산 실행 때 결속해야 한다. 본문 숫자를 등록된 엑셀값과 자동 대조하는 기능은 아직 과수 본문에서 쓸 수 없다.
- **아직 안 되는 것**: 배포된 셀 지도는 사본 생성용이다 — 1,961건(clear 339 / preserve 1,622)은 clear/preserve 분류까지만 검토됐고 `period`·`editable`·`source_note`가 없어 값 채우기 지도로 쓸 수 없다. 등록용 사실 지도는 매출액 계열 10행만 열려 있고 나머지 60행은 경비 연도 정정을 기다린다. 본문 숫자 자동 대조는 아직 불가하다. 파이썬 재무 계산은 지원하지 않는다. 등록 순서·집단 field ID·참조 XLSX 규칙은 [fruit-trees](fruit-trees/README.md)를 따른다.

유효한 전공 바인딩이 있으면 [전공별 본문 초안(글)](major-body-draft.md)을 작성할 수 있다.

## 원예환경시스템 (hort_env_systems, 모듈 0.2.0)

질문·선택형 문서 계획·근거 규칙과 **본문 DOCX·PDF·HWPX 검토본**을 지원한다:
- `scripts/gg_hort_plan.py --project <폴더>`가 원예 질문별 답변 상태(값 없음), 선배 전체논문의 장·부록 위치, 전공 교재 18시트 엑셀의 주의 목록(정본에 등록된 파일을 다시 해시해 일치할 때만)을 읽기 전용으로 보여 준다. 교재 엑셀은 공용 17시트 좌표와 호환되지 않으며 셀 값은 수업 예시다.
- `build_docx.py <폴더> --major hort_env_systems`가 HT2 앞머리를 적용해 검토 전 DOCX를 만든다: 겉표지·표제면 두 장 뒤에 제출서·인준서·목차가 각각 별도 쪽으로 오고, 논문지도교수 신청서·심사위원 추천서는 본문에 넣지 않고 "신청서·추천서는 학교 서식으로 따로 제출" 안내를 넣는다. 지도교수는 제출서에 들어간다. 표지에는 계획명 줄이 없고(공백), 물리 배치는 공용 기본값이라 학교 최종 서식 적합 판정은 별도다.
- `gg_office.py word`(macOS Word 설치 시)로 PDF를, `build_hwpx.py <폴더> --major hort_env_systems`로 HWPX를 만든다. HWPX는 쪽 나눔·가운데 정렬·목차 실제 쪽번호·표 폭·장 시작 쪽을 한글에서 사람이 확인해야 한다.
- 재무 엑셀은 전용 18시트 경로만 열린다: `hort_18_sheet_reconstructed_v1`은 finance capability가 supported이고, 변환 영수증·native 영수증 사슬과 등록된 단일 운반본·시트 정합성·금액 원천을 확인해야 채택·내보내기가 된다. 공용 프로필 목록(`workbook_profiles`)이 비어 있어 일반 사본·채우기 경로는 없다.
- 엑셀 재계산값은 사실로 등록되고 학교 서식 본문 DOCX·PDF까지 만들어진다. 다만 본문 숫자를 등록된 엑셀값과 자동 대조하는 기능은 아직 원예 본문에서 쓸 수 없다(등록 이름이 기계용이라 대조 대상이 막힘 — 알려진 한계, 다음 판에서 표 전개 방식으로 연다).
- **아직 안 되는 것**: 일반(17시트) XLSX 사본·채우기, 파이썬 재무 계산. 경계와 근거 규칙은 [hort-env-systems](hort-env-systems/README.md)를 따른다.

유효한 전공 바인딩이 있으면 [전공별 본문 초안(글)](major-body-draft.md)을 작성할 수 있다.

## 낙농 (dairy, 모듈 0.1.0)

질문 목록·문서 계획·근거 검토와 **본문 DOCX·PDF·HWPX 검토본**을 지원한다:
- `gg.py major-plan <폴더>`가 축종 스위치(`dairy_switch`: 젖소 착유 / 한우 번식·비육·일관), 학교 작성요령 기본 목차(앞부분, 본문 1~12장, 부록 1~10), 축종별 내용 치환을 읽기 전용으로 보여 준다.
- `build_docx.py <폴더> --major dairy`가 학교 작성요령 앞부분 순서(겉표지 → 제출서 → 인준서 → 학부모 동의서 → 요약 → 감사의 글 → 목차, 이어서 표 목차·그림 목차)를 적용해 검토 전 DOCX를 만든다. 공식 물리 배치가 확인되지 않아 공용 기본값을 쓰고 가운데 정렬을 생략하며 "학교 양식 확인 필요" 안내를 넣는다.
- `gg_office.py word`(macOS Word 설치 시)로 PDF를, `build_hwpx.py <폴더> --major dairy`로 HWPX를 만든다. HWPX는 쪽 나눔·정렬·목차 실제 쪽번호를 한글에서 사람이 확인해야 하며, 매니페스트의 앞머리 감지(`school_frontmatter`)는 낙농에서 `null`로 기록된다.
- **학교 28시트 재무 엑셀 빈 사본**: 2판에서 열었다. `gg_excel_template.py clear --project <폴더> --major dairy --source <학교 원본> --map references/dairy/corrections/dairy-clear-map.json --out <사본>`은 원본을 복제해 입력 칸 1,070개만 비우고 표 구조·참조 기준값 5,463칸은 남긴다(`blank_template`, 원본 해시 불변). 원본 경로는 매번 지정해야 한다(번들 양식은 17시트, 식량작물 양식은 16시트). 지도 규칙·수식 안 상수·예상 `#DIV/0!`·시세 칸 안내는 [corrections/DAIRY-CLEAR.md](dairy/corrections/DAIRY-CLEAR.md).
- **아직 안 되는 것**: 엑셀 값 채우기(`gg_excel_fill`)는 `fill_unsupported_for_major`로, 축산 재무 계산(`dairy_cattle_milk_cash`·`hanwoo_herd_cash`)은 `unsupported`로 계속 닫힌다. 벤치마크 팩은 `empty_slot`이다. 목차 계약·축종 치환·서식·문체(S1)·리서치 경로는 [dairy](dairy/README.md)를 따른다.

유효한 전공 바인딩이 있으면 [전공별 본문 초안(글)](major-body-draft.md)을 장별로 작성할 수 있다.

## 식량작물 (food_crops, 모듈 0.1.0)

질문 목록·문서 계획·근거 검토와 **본문 DOCX·PDF·HWPX 검토본**을 지원한다:
- `gg.py major-plan <폴더>`가 갈래 스위치(영농 시작 형태 `food_crops.entry_mode`: 승계/창업, 작부 형태 `food_crops.cropping_system`: 단작/이모작), 학교 양식 본문 골격 기준 목차(앞부분, 본문 1~4장, 맺음말, 참고문헌, 학부모동의서), 승계 전제 자리 창업농 대체 안내를 읽기 전용으로 보여 준다.
- `build_docx.py <폴더> --major food_crops` 및 `build_hwpx.py <폴더> --major food_crops`가 양식 골격을 적용해 검토 전 DOCX·HWPX를 만든다: 요약 대항목은 아라비아 번호(1~5), 본문은 `1. 머리말`로 시작하며 쪽번호가 1부터 다시 시작하고, 맺음말은 장 번호가 없으며, 학부모동의서는 문서 맨 끝에 위치한다.
- **학교 16시트 재무 엑셀 빈 사본**: 2판에서 열었다. `gg_excel_template.py clear --project <폴더> --major food_crops --source <학교 원본> --map references/food-crops/corrections/fc-clear-map.json --out <사본>`은 원본을 복제해 입력 칸 396개만 비우고 연도 40칸과 양식 주석 39칸은 남긴다(`blank_template`, 원본 해시 불변). 수식 고치기(`gg_excel_formula_patch.py patch`)는 이 전공의 기본 프로필을 스스로 쓰고, 원본과 같은 경로·이미 있는 파일·등록 지문과 다른 구조는 쓰기 전에 거절한다. 원본 경로는 매번 지정해야 한다(번들 양식은 17시트). 지도·`#DIV/0!` 안내는 [corrections/FC-CLEAR.md](food-crops/corrections/FC-CLEAR.md).
- **아직 안 되는 것**: 사본을 대신 채우거나 파이썬으로 재무를 계산하는 일은 여전히 미지원이며 입구에서 `fill_unsupported_for_major`로 거절한다(출력·영수증 없음). 벤치마크 팩은 `empty_slot`이다. 목차 계약·창업농 경로·서식·리서치 경로는 [food-crops](food-crops/README.md)를 따른다.

유효한 전공 바인딩이 있으면 [전공별 본문 초안(글)](major-body-draft.md)을 장별로 작성할 수 있다.

## 미등록·미확정 전공

전공이 없거나 검증되지 않으면 전공 의존 작성을 보류한다. 사용자가 확인한 전공이 등록 전공에 없으면(`gg.py major-plan`이 `reason: unknown_major`로 보류하면. 전공을 아직 기록하지 않은 `major_required`는 해당하지 않는다) "그 전공은 아직 준비되지 않았다"고 안내하고 전공 의존 인터뷰·작성을 진행하지 않는다(자료 정리·원답변 기록 같은 공용 작업은 기존 규칙을 유지한다).

## 학교 구조 검사와 전공 목차 계약

학교 구조 검사(`gg_document.py`의 `school_structure`: SWOT 절 위치, 장절 골격 등)는 **선택한 전공의 목차 계약**(profile/registry)에 따라 적용된다:
- **특용작물**: 학교 6장 구조, 앞부분(표지·인준 등), Ⅲ-2-마 SWOT 분석 등 특용작물 전용 목차/내용 슬롯 검사를 유지한다.
- **과수**: 레지스트리에 선언된 S01 목차 계약에 따라 Ⅱ-4 위치의 SWOT 분석을 검사하며, 특용작물의 6장 목차나 Ⅲ-2-마 위치를 강제하지 않는다.
- **원예환경시스템**: 환경분석에 연결된 선배 논문 프로필(HT1·HT2) 계약에 따라 Ⅲ-4 위치의 SWOT 분석을 검사한다.
- **낙농**: 학교 작성요령 목차 계약에 따라 본문 1~12장(10 경영수지 포함) 및 앞부분·부록을 검사하며, 2-다 '개선해야 될 사항' 위치의 SWOT 표를 허용한다. 특용작물의 6장 목차, Ⅲ-2-마 SWOT 위치, 또는 기상·재해·농장개요 표 내용 검사를 강제하지 않는다.
- **식량작물**: 학교 양식 본문 골격(1. 머리말, 2. 영농환경 분석, 3. 영농계획 수립, 4. 재무계획, 맺음말 및 발전방향, 참고문헌, 학부모동의서) 계약에 따라 검사하며, 요약 아라비아 번호(1~5), 2장 끝 SWOT, 맺음말 장 번호 없음, 학부모동의서 문서 맨 끝 배치를 허용한다.
- **계약이 없는 전공 (산업곤충 등)**: 해당 구조 규칙을 건너뛰어 다른 전공의 양식을 강제하는 오탐(잘못된 오류 판정)을 방지한다. 계약이 등록된 전공만 그 전공 기준으로 검사한다.
