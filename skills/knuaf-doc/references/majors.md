# 전공별 지원 범위

전공 계약의 공용 규칙(바인딩·출력 가드)은 SKILL.md의 "전공 선택·계약"과 "결과물 출력 전 전공 확인"에 있다. 이 문서는 등록 전공별로 어디까지 지원하는지를 정리한다. 각 전공의 상세 경계는 아래 연결된 전공 README를 따른다.

## 특용작물 (specialty_crops, 모듈 1.0.0)

질문·본문 Ⅰ~Ⅵ 작성·재무 엑셀·DOCX 검토본까지 지원한다. `gg.py rda-candidates <폴더> --crop <품목> --region <지역> --year <연도> --unit <단위> --use-scope <용도>`는 정본의 전공을 사용하고, 기존 `rda-lookup`은 물리 행 감사용 조회로 유지한다. `rda-lookup`은 원자료 감사용이다. `verification.verified`는 관측이 원문과 대조됐다는 뜻일 뿐 사용 승인이 아니며, 본문·표에 넣는 값은 `rda-candidates`의 `approved`만 쓴다. `unverified`·`ambiguous`·`series` 결과나 라벨이 '옮기지 말 것'인 값은 어떤 경우에도 옮기지 않는다.

`scripts/gg_school_paper.py <폴더> --input <spec> [--major <전공ID>]`는 겉표지~감사의 글·Ⅰ–Ⅵ 골격과 기상·재해표를 만들며, 미제공 값은 `[확인 필요]`로 남긴다. 이 본문 생성기는 특용작물 작목 본문 전용이다.

## 산업곤충 (industrial_insects, 모듈 0.2.0)

질문 목록·선택형 문서 계획·공식 통계 근거 검토까지만 지원한다: `gg.py insect-plan <폴더> --input <폴더 안 선택.json>`이 동급 선례(F0 교수 배포 예시, E1–E4 통과 논문) 위치를 연결한 계획을(`--input` 없이 실행하면 제품 라인·계획 연도별 질문 상태만 — 공용 인터뷰의 인스턴스 출처), `gg.py insect-evidence <폴더> --pdf <R0> --xlsx <R1>`이 고정 해시 원본을 매번 다시 읽은 공용 근거 축 검토를 읽기 전용으로 보여 준다. 선례는 강제 목차·표 수가 아니고 학생 수치는 기본값이 아니다. 곤충 본문·XLSX·재무 계산은 `unsupported_output`으로 멈추며 벤치마크 팩은 `empty_slot`이다. 같은 선례 PDF를 다시 받으면 `source-scan`/`source-resolve`의 등록부 판정(`insect.example.*`)이 `reuse_ready`일 때 원본을 다시 파싱하지 않고 내장 위치 지도를 쓴다(판정만 하며 코드가 막지는 않는다). 제품 라인 선언(`line_inventory`, id `l01`…)·라인 질문·모듈 0.2.0 재바인딩(같은 사실 id의 새 revision, 교수 재승인)·선택 형식·근거 상태·재사용 키·한계는 [industrial-insects](industrial-insects/README.md)를 따른다.

## 과수 (fruit_trees, 모듈 0.2.0)

학교 과수 양식(S01) 구조의 작성계획과 물량 검산까지 지원한다: `scripts/gg_fruit_plan.py <폴더>`가 등록 원본·연구 전달물·구역/식재집단/수확 배치/물량 이동 답변·검토 상태를 읽기 전용으로 보여 주고, `scripts/gg_orchard_production.py <폴더>`가 학생 입력만으로 생산량·배치 재고(kg)를 읽기 전용으로 검산하며(금액 없음), 과수 본문·XLSX·재무 계산은 `unsupported_output`으로 멈춘다. 등록 순서·집단 field ID·참조 XLSX 규칙은 [fruit-trees](fruit-trees/README.md)를 따른다.

## 원예환경시스템 (hort_env_systems, 모듈 0.2.0)

질문·선택형 문서 계획·근거 규칙까지 지원한다: `scripts/gg_hort_plan.py --project <폴더>`가 원예 질문별 답변 상태(값 없음), 선배 전체논문의 장·부록 위치, 전공 교재 18시트 엑셀의 주의 목록(정본에 등록된 파일을 다시 해시해 일치할 때만)을 읽기 전용으로 보여 준다. 교재 엑셀은 공용 17시트 좌표와 호환되지 않으며 셀 값은 수업 예시다. 전용 18시트 재무 출력은 모듈 0.2.0 바인딩과 등록된 교재 원본 등 별도 조건에서만 열리며 경계와 근거 규칙은 [hort-env-systems](hort-env-systems/README.md)를 따른다. 조건이 안 맞으면 원예 본문·XLSX·재무 계산은 `unsupported_output`으로 멈춘다.

## 미등록·미확정 전공

전공이 없거나 검증되지 않으면 전공 의존 작성을 보류한다. 사용자가 확인한 전공이 등록 전공에 없으면(`gg.py major-plan`이 `reason: unknown_major`로 보류하면. 전공을 아직 기록하지 않은 `major_required`는 해당하지 않는다) "그 전공은 아직 준비되지 않았다"고 안내하고 전공 의존 인터뷰·작성을 진행하지 않는다(자료 정리·원답변 기록 같은 공용 작업은 기존 규칙을 유지한다).
