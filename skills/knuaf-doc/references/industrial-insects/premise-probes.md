# 산업곤충전공 핵심 전제 목록
- 소유 전공: industrial_insects · 문서 버전: 2026-09-26.2 · 기준 module_version: 0.2.0

학생이 흔히 전제로 깔고 있지만 확인하지 않으면 사업 성립·현금·원가가 틀어지는 항목이다. 공용 인터뷰 형식(`premise probes`)을 따르며, 질문 횟수·보류는 연결 `field_id`의 공용 질문 상태를 쓴다. probe 하나는 기존 field 하나에만 연결한다(같은 결정). 라인 템플릿 field(`industrial_insects.line.{id}.…`)의 실제 라인 목록과 상태는 "인스턴스 출처"의 읽기 전용 출력에서 가져온다. 라인 질문은 프로젝트 요약 질문의 답을 상속하지 않는다. 여기 적힌 것은 **묻는 거리**이지 사실·기본값이 아니다. 예시 논문(F0·E1–E4)의 수치나 통계 총액을 답으로 채우지 않는다.

## industrial_insects.probe.product_lines — 제품 라인 선언
- 연결 field_id: industrial_insects.line_inventory
- 무엇을 가정하나: 팔거나 만들 제품이 하나뿐이라고 보거나, 생충·건조품·분변토·서비스를 한 덩어리로 본다.
- 영향: 최대 원가·수량 — 라인별 판매·원가, 부산물 매출
- 트리거 신호: 두 가지 이상 제품을 말하면서 수량·단가는 하나만 제시
- 선행 정보: 대상 종(industrial_insects.species)
- 질문 예: 팔거나 만들 제품을 모두 알려 주세요(생충·건조품·분변토·처리 서비스 등).
- 모를 때 도움: 주 제품 하나를 먼저 정하고 나머지는 부산물·가공·서비스 라인으로 나누기
- 조사로 대체: 아니오 — 사업 구성 결정

## industrial_insects.probe.plan_years — 계획 연도
- 연결 field_id: industrial_insects.plan_years
- 무엇을 가정하나: 계획 기간을 따로 정하지 않아도 된다고 본다.
- 영향: 현금 시점 — 연도별 판매·재고·현금
- 트리거 신호: 연도 없이 '몇 년 뒤' 식으로 말함
- 선행 정보: 창업·가동 시작 시점
- 질문 예: 이 계획은 몇 년부터 몇 년까지 잡을까요?
- 모를 때 도움: 학교 양식 기간(예: 10년)에 맞추기, 가동 시작 연도부터 세기
- 조사로 대체: 아니오 — 계획 기간 결정

## industrial_insects.probe.feed_supply — 먹이·배지 조달
- 연결 field_id: industrial_insects.line.{id}.feed_supply
- 인스턴스 출처: `gg.py insect-plan <폴더>`의 "instances"·"line_questions" (선택 JSON 없이 실행 가능)
- 무엇을 가정하나: 먹이·배지를 원하는 양만큼 늘 싸게 구할 수 있다고 본다.
- 영향: 최대 원가·수량 — 영농자재 구입, 생산원가, 사육 규모 상한
- 트리거 신호: "음식물·부산물을 받아 쓴다", "먹이는 거의 공짜", 먹이 이야기 없이 생산량만 제시
- 선행 정보: 이 라인의 먹이 종류(feed_or_substrate)
- 질문 예: 이 라인의 먹이와 배지는 직접 만드나요, 사오나요, 받아 쓰나요 — 한 달에 얼마나 필요하다고 보나요?
- 모를 때 도움: ① 자가 제조 ② 구입(업체·단가 조사) ③ 부산물 수거(공급처와 조건 확인) 중 고르고, 양은 회차 기준으로 나중에 채우기
- 조사로 대체: 부분 — 단가·공급처는 조사 가능, 조달 방식 선택은 학생 결정

## industrial_insects.probe.survival — 생존율
- 연결 field_id: industrial_insects.line.{id}.survival_rate
- 인스턴스 출처: `gg.py insect-plan <폴더>`의 "instances"·"line_questions" (선택 JSON 없이 실행 가능)
- 무엇을 가정하나: 넣은 알·종충이 거의 다 자라 판매량이 된다고 본다.
- 영향: 최대 원가·수량 — 판매 가능 수량, 종충 투입량
- 트리거 신호: 입식량과 판매량을 같은 숫자로 제시, "병은 거의 없다"
- 선행 정보: 이 라인의 입식량과 입식 단위
- 질문 예: 한 회차에 넣은 양 가운데 판매 단계까지 살아남는 비율을 어느 정도로 보나요?
- 모를 때 도움: 사육 경험·교육 자료·선배 농가 확인 중 하나로 범위를 잡기. 생존율로 판매량을 계산하지 않으니 판매 가능량은 따로 묻는다
- 조사로 대체: 부분 — 종별 일반 범위는 조사 가능하나 자기 시설 값은 아님

## industrial_insects.probe.survival_basis — 생존율의 기준
- 연결 field_id: industrial_insects.line.{id}.survival_basis
- 인스턴스 출처: `gg.py insect-plan <폴더>`의 "instances"·"line_questions" (선택 JSON 없이 실행 가능)
- 무엇을 가정하나: 생존율이 마릿수 기준인지 무게 기준인지 구분하지 않아도 된다고 본다.
- 영향: 서술 — 생존율 해석
- 트리거 신호: 생존율 숫자만 말하고 기준을 말하지 않음
- 선행 정보: 이 라인의 생존율 답
- 질문 예: 그 생존율은 마릿수 기준인가요, 무게 기준인가요?
- 모를 때 도움: 개체 수 / 중량 중 고르기
- 조사로 대체: 아니오 — 학생의 측정 기준

## industrial_insects.probe.capacity_boxes — 사육 상자 수
- 연결 field_id: industrial_insects.line.{id}.rearing_boxes
- 인스턴스 출처: `gg.py insect-plan <폴더>`의 "instances"·"line_questions" (선택 JSON 없이 실행 가능)
- 무엇을 가정하나: 시설 면적만 정하면 생산량이 따라 나온다고 본다.
- 영향: 최대 원가·수량 — 시설 투자, 생산 계획
- 트리거 신호: 면적과 연 생산량만 있고 사이에 상자·선반이 없음
- 선행 정보: 사육 시설 면적(facility_area)
- 질문 예: 이 라인에 사육 상자를 몇 개 둘 계획인가요?
- 모를 때 도움: 선반 배치 스케치로 상자 수 먼저 세기
- 조사로 대체: 아니오 — 자기 시설 배치 결정

## industrial_insects.probe.capacity_tiers — 선반 단 수
- 연결 field_id: industrial_insects.line.{id}.box_tiers
- 인스턴스 출처: `gg.py insect-plan <폴더>`의 "instances"·"line_questions" (선택 JSON 없이 실행 가능)
- 무엇을 가정하나: 상자를 바닥에만 둔다고 보거나 단 수를 생각하지 않는다.
- 영향: 최대 원가·수량 — 시설 투자·작업 동선
- 트리거 신호: 상자 수만 말하고 쌓는 방식이 없음
- 선행 정보: 이 라인의 상자 수
- 질문 예: 상자를 몇 단으로 쌓을 계획인가요?
- 모를 때 도움: 선반 높이·작업 높이 기준으로 단 수 정하기
- 조사로 대체: 아니오 — 자기 시설 배치 결정

## industrial_insects.probe.capacity_density — 상자당 사육 밀도
- 연결 field_id: industrial_insects.line.{id}.density_per_box
- 인스턴스 출처: `gg.py insect-plan <폴더>`의 "instances"·"line_questions" (선택 JSON 없이 실행 가능)
- 무엇을 가정하나: 상자 하나에 원하는 만큼 넣을 수 있다고 본다.
- 영향: 최대 원가·수량 — 회차당 생산량 상한
- 트리거 신호: 상자 수는 있는데 상자당 양이 없음
- 선행 정보: 이 라인의 상자 수와 입식 단위
- 질문 예: 상자 하나에 얼마나(마리·g 등) 넣을 계획인가요?
- 모를 때 도움: 교육 자료·선배 농가 값으로 범위 잡기, 단위를 함께 적기
- 조사로 대체: 부분 — 종별 권장 밀도는 조사 가능

## industrial_insects.probe.cycle_year — 연간 회차 수
- 연결 field_id: industrial_insects.line.{id}.cycles_per_year
- 인스턴스 출처: `gg.py insect-plan <폴더>`의 "instances"·"line_questions" (선택 JSON 없이 실행 가능)
- 무엇을 가정하나: 하루·한 회차 목표를 그대로 곱하면 연 생산량이 된다고 본다(휴지기·청소 없이).
- 영향: 현금 시점 — 연 생산량, 운전자금
- 트리거 신호: 일 생산 목표만 제시, 회차 수 언급 없음
- 선행 정보: 이 라인의 한 회차 일수(cycle_days)
- 질문 예: 정상 가동하는 해에 이 라인을 1년에 몇 회차 돌릴 계획인가요?
- 모를 때 도움: 회차 일수 + 청소·휴지 기간으로 연간 회차 수 세기
- 조사로 대체: 부분 — 종별 생활사는 조사 가능, 운영 일정은 학생 결정

## industrial_insects.probe.first_year — 첫해 판매 회차
- 연결 field_id: industrial_insects.line.{id}.first_year_sold_cycles
- 인스턴스 출처: `gg.py insect-plan <폴더>`의 "instances"·"line_questions" (선택 JSON 없이 실행 가능)
- 무엇을 가정하나: 첫해부터 정상 연도만큼 판다고 본다.
- 영향: 현금 시점 — 첫해 매출·운전자금
- 트리거 신호: 첫해 매출을 정상 연도와 같게 제시
- 선행 정보: 이 라인의 첫 판매 예정 연·월, 연간 회차 수
- 질문 예: 첫해에 판매까지 끝나는 회차는 몇 회인가요?
- 모를 때 도움: 가동 시작 월부터 회차를 달력에 적어 세기. 도구가 대신 추정하지 않는다
- 조사로 대체: 아니오 — 운영 일정 결정

## industrial_insects.probe.first_sale — 첫 판매 시점
- 연결 field_id: industrial_insects.line.{id}.first_sale_month
- 인스턴스 출처: `gg.py insect-plan <폴더>`의 "instances"·"line_questions" (선택 JSON 없이 실행 가능)
- 무엇을 가정하나: 가동을 시작하면 바로 판매가 시작된다고 본다.
- 영향: 현금 시점 — 첫 매출 시점, 운전자금 필요 기간
- 트리거 신호: 시설 준비와 판매 시작을 같은 달로 제시
- 선행 정보: 이 라인의 한 회차 일수(cycle_days), 가동 시작 시점
- 질문 예: 이 라인의 첫 판매는 몇 년 몇 월로 예상하나요?
- 모를 때 도움: 가동 시작 월 + 첫 회차 일수로 달력에 표시해 정하기
- 조사로 대체: 아니오 — 운영 일정 결정

## industrial_insects.probe.year_end_wip — 연말 사육 중 회차
- 연결 field_id: industrial_insects.line.{id}.year.{yyyy}.year_end_in_process
- 인스턴스 출처: `gg.py insect-plan <폴더>`의 "instances"·"line_questions".<id>."years" (연도는 plan_years)
- 무엇을 가정하나: 연말에 사육 중인 물량은 신경 쓰지 않아도 된다고 본다.
- 영향: 현금 시점 — 그해 원가·재고·현금(있으면 그해 재무 계산 보류)
- 트리거 신호: 12월 입식·다음 해 판매 계획
- 선행 정보: 계획 연도(plan_years), 이 라인의 회차 일정
- 질문 예: 그해 12월 말에 아직 사육 중인(판매 전) 회차가 있나요?
- 모를 때 도움: 예 / 아니오 / 모름 중 고르기 — 연도마다 따로 묻는다
- 조사로 대체: 아니오 — 운영 일정 결정

## industrial_insects.probe.stock_source — 종충·알 확보
- 연결 field_id: industrial_insects.line.{id}.stock_source
- 인스턴스 출처: `gg.py insect-plan <폴더>`의 "instances"·"line_questions" (선택 JSON 없이 실행 가능)
- 무엇을 가정하나: 종충·알을 필요할 때 늘 같은 품질로 구할 수 있다고 본다.
- 영향: 사업 성립 — 첫 회차 시작, 연중 생산 유지
- 트리거 신호: 종충 구입처·자가 채란 계획이 없음
- 선행 정보: 이 라인의 대상 종
- 질문 예: 첫 종충이나 알은 어디서 구하고, 그다음부터는 직접 받을 계획인가요?
- 모를 때 도움: ① 구입 ② 자가 채란 ③ 둘 병행 중 고르기
- 조사로 대체: 부분 — 구입처는 조사 가능

## industrial_insects.probe.use_law — 용도별 법 체계
- 연결 field_id: industrial_insects.line.{id}.purpose
- 인스턴스 출처: `gg.py insect-plan <폴더>`의 "instances"·"line_questions" (선택 JSON 없이 실행 가능)
- 무엇을 가정하나: 용도가 달라도 같은 신고 하나로 팔 수 있다고 본다.
- 영향: 사업 성립 — 판매 가능 여부, 인허가 일정·비용
- 트리거 신호: 식용·사료·애완을 한꺼번에 판매 대상으로 제시
- 선행 정보: 이 라인의 대상 종, 제품 형태
- 질문 예: 이 라인은 사람 먹거리, 동물 사료, 애완·학습용 가운데 어디로 팔 계획인가요?
- 모를 때 도움: 용도 하나를 먼저 고르고 나머지는 다른 라인으로 분리
- 조사로 대체: 부분 — 용도별 요건은 현행 법령 조사, 용도 선택은 학생 결정

## industrial_insects.probe.processing_permit — 가공·식품 원료 요건
- 연결 field_id: industrial_insects.regulation_check
- 무엇을 가정하나: 건조·분말 등으로 가공해 팔면 별도 영업 요건이 없다고 본다.
- 영향: 사업 성립 — 판매 가능 여부, 시설 투자
- 트리거 신호: 건조·분말·가공품 판매 계획, "직접 가공해서 판다"
- 선행 정보: 라인별 용도·제품 형태
- 질문 예: 가공품을 직접 만들어 팔 경우 필요한 신고나 인정 절차를 확인해 봤나요?
- 모를 때 도움: 현행 법령·담당 기관 확인 경로 안내, 확인 전에는 해당 판매 계획을 보류 표시
- 조사로 대체: 예 — 국가법령정보센터 현행 조문과 담당 기관 확인(판본·시행일 기록)

## industrial_insects.probe.claims_ad — 효능 표시·광고
- 연결 field_id: industrial_insects.claims_check
- 무엇을 가정하나: 건강·효능을 내세워 홍보해도 된다고 본다.
- 영향: 서술 — 마케팅 계획, 판로 문구
- 트리거 신호: "면역에 좋다", "치료 효과" 등 효능 문구로 판매 계획 설명
- 선행 정보: 라인별 용도·판로
- 질문 예: 홍보에 쓰려는 효능 문구가 있다면 표시·광고 규정에 맞는지 확인했나요?
- 모를 때 도움: 효능 대신 사실 정보(종·사육 방식·위생 관리)로 쓰는 선택지 제시
- 조사로 대체: 예 — 현행 표시·광고 법령 조사

## industrial_insects.probe.price_basis — 단가의 근거
- 연결 field_id: industrial_insects.line.{id}.price_basis
- 인스턴스 출처: `gg.py insect-plan <폴더>`의 "instances"·"line_questions" (선택 JSON 없이 실행 가능)
- 무엇을 가정하나: 통계의 시장 규모나 한 번 본 가격을 자기 판매 단가로 써도 된다고 본다.
- 영향: 최대 원가·수량 — 매출, 수익성
- 트리거 신호: 전국 판매액 총계·기사 속 가격을 단가로 인용
- 선행 정보: 이 라인의 판로와 판매 단위
- 질문 예: 이 라인의 판매 단가는 언제 확인한 어떤 가격(견적·판매가·계약가)인가요?
- 모를 때 도움: 거래처 견적·온라인 판매가·계약 단가 중 하나로 시점을 적어 두기
- 조사로 대체: 부분 — 시장 가격 조사는 가능, 계약 단가는 학생 확인. 공식 통계 총액(R0/R1)은 산업 맥락 값이며 단가가 아니다

## industrial_insects.probe.unit_price — 판매 단가
- 연결 field_id: industrial_insects.line.{id}.unit_price
- 인스턴스 출처: `gg.py insect-plan <폴더>`의 "instances"·"line_questions" (선택 JSON 없이 실행 가능)
- 무엇을 가정하나: 단가를 대략 말해도 계산에 쓸 수 있다고 본다.
- 영향: 최대 원가·수량 — 매출
- 트리거 신호: "적당한 가격", 범위만 말함, 단위 없는 금액
- 선행 정보: 이 라인의 판매 단위, 단가 근거
- 질문 예: 이 라인은 판매 단위 1개(1kg 등)에 얼마(원)로 팔 계획인가요?
- 모를 때 도움: 근거로 확인한 가격 하나를 원 단위로 적고 판매 단위를 함께 적기
- 조사로 대체: 부분 — 시세는 조사 가능, 최종 단가는 학생 결정
