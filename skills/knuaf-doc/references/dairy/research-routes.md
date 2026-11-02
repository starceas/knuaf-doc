# 낙농전공 리서치 경로 (research-routes)

이 문서는 낙농전공(젖소 및 한우)의 **공식 리서치 경로**와 **오용 금지 규칙**을 정의한다.

> **단일 경로 원천 원칙**: 본 문서의 모든 경로 식별자, URL, 판정 상태 및 적용 경계는 책임자·검토자가 직접 대조하고 확정한 **`research/ROUTE-LIST-FINAL.md` (R2) 단 하나만을 정본 원천으로 삼는다.** A1/A1b 정제본의 미검증 식별자나 구버전 URL은 일체 수록하지 않는다. (근거: `DESIGN-v2.md` §2.2, `REVIEW-V1-FIX2.md`).

> **수치 배제 원칙**: 가격·생산비·급여량·수취단가 등 변동 관측값과 기준 수치는 본 문서에 싣지 않는다. 수치는 학생 작업 시 해당 경로를 직접 조회하여 출처 계약(`source-contract.md`)에 따라 등록한다.

---

## 1. 판정 상태 및 계보 규칙

- **`READY`**: URL 및 식별자가 수집 증거(`evidence`)와 일치하고 실제 파일 해시 대조를 통과한 검증된 경로.
- **`CONDITIONAL`**: URL 계보는 확인되었으나 수집 내용이 개요·활용 안내·부분 표에 한정된 경로. 명시된 범위에 한해 안내할 수 있으며, 본문 표나 상세 요구량표로 승격하여 사용할 수 없음.
- **`FOLLOW_UP`**: 상세 자료 또는 고유 식별자가 미확인된 탐색 후보. 확정 식별자가 아니므로 실제 조회 조건과 원문을 추가 확보한 뒤 사용해야 함.
- **`X / HX`**: 오용 금지 규칙. 이 행의 READY는 금지 규칙이 유효하게 확립되었음을 의미함.

---

## 2. 공통 및 젖소 경로 (A1 번호 체계 정본)

| ID | 판정 | 기관·자료명 / 식별자 / URL | 주기·단위 | 적용 경계 및 주의사항 |
|---|---|---|---|---|
| **D01** | FOLLOW_UP | 법제처 국가법령정보센터 [법령 검색](https://www.law.go.kr): 축산법·낙농진흥법·가축분뇨법·가축전염병예방법 | 개정 시; 조문별 단위 | 공통. 법령명·시행일·조문·별표 확보 후 적용. 현행 의무 자동 확정 아님 |
| **D02** | FOLLOW_UP | 정부24 [민원](https://www.gov.go.kr), 자치법규정보시스템 [자치법규](https://www.elis.go.kr): 축산업 허가·가축사육제한 조례 | 개정/신청 시; m²·거리 등 | 공통. 관할 지자체·신축/증축·시설규모 확인 필요 |
| **D03** | FOLLOW_UP | 농식품부 [정책자료](https://www.mafra.go.kr), 낙농진흥회 [용도별 차등가격제](https://dairy.or.kr/kor/sub03/menu_01_1_1.html) | 정책/사업 개정 시; 사업별 단위 | 차등가격제는 젖소, 조사료 지원은 해당 재배자. 집유주체·사업연도 필요 |
| **D04** | FOLLOW_UP | KREI [농업관측](https://aglook.kr): 젖소 관측 자료 후보 | 발간 주기별; 두·톤·원 등 | 젖소. 분기보 존재 및 현행 발간 주기 미확정. D06 연간 전망과 구분 |
| **D05** | FOLLOW_UP | KREI [관측 데이터](https://aglook.kr): OASIS 젖소·원유 시계열 후보 | 표별; 두·톤 등 | 젖소. 개별 표 ID 및 조회 기간 조건 필요 |
| **D06** | **READY** | KREI 『농업전망 2026』 제4장 젖소 절 [PDF](https://www.aglook.kr/upload/eventdata/f9f1aec0d59447a88343854978bdf2bb.pdf) | 연간; 두·톤·% 등 원표 | 젖소. 실적·전망·잠정치 분리. 제도가격은 D08과 구분 (한우 절은 H12) |
| **D07** | **READY** | 낙농진흥회 [원유생산통계](https://dairy.or.kr/kor/sub05/menu_01_3_1.php); menu_01_3_1.php | 월 잠정/연 확정; 톤·kℓ | 젖소. 환산 기준기간 보존. 개별 농장 납유량과 전국 통계 구분 |
| **D08** | **READY** | 낙농진흥회 [산정체계 게시판](https://dairy.or.kr/kor/sub03/menu_01_2_2.html), table_idx=21862, [2025 시행 안내 PDF](https://dairy.or.kr/ezs_board/ezsboard_filedownload.php?nfile=1_1751870981.pdf&b_code=101) | 개정 시; 원/ℓ·성분 %·위생단위 | 젖소. 해당 시행판 제도 안내. 실제 집유계약·정산은 D12. 과거판 사용 금지 |
| **D09** | **READY** | 낙농진흥회 [원유수취가격](https://dairy.or.kr/kor/sub05/menu_01_3_3.php) | 월; 원/ℓ | 젖소. 진흥회 소속 농가 정상단가 모집단 주석 보존. 농가 실수령액과 구분 |
| **D10** | **READY** | 낙농진흥회 [원유검사현황](https://dairy.or.kr/kor/sub05/menu_01_3_2.php) | 월; %·천/㎖ 등 원표 | 젖소. 평균 및 등급비율 모집단 주석 확인. 농장 자체 검사성적으로 대체 금지 |
| **D11** | **READY** | 낙농진흥회 [원유수급개황](https://dairy.or.kr/kor/sub05/menu_01_4_1.php) | 월/연; 원유환산 톤 | 젖소. 생산·수입·소비·재고 및 원유환산 기준 구분 |
| **D12** | FOLLOW_UP | 소속 집유주체 계약·유대명세·쿼터 규정 (공개 공통 URL 없음; D08은 제도 참고용) | 계약/정산월; kg 또는 ℓ·원 | 젖소. 집유주체·쿼터량·정상/초과 조건 필요. 통계단가로 실제 계약 대체 금지 |
| **D13** | **READY** | 농협 젖소개량사업소 『2025년도 전국 유우군능력검정사업 보고서』 [PDF](https://www.dcic.co.kr/statics/_uploadFiles/_annual_report/2025_main.pdf) | 연간; kg·%·일·개월 등 | 젖소 검정참여 집단. 검정기간과 연간 납유량 구분. 개인·농장 식별 복제 금지 |
| **D14** | FOLLOW_UP | 한국종축개량협회 [유우개량](https://aiak.or.kr/renewal/ka_new/ka_dims/main.jsp), 농협 [젖소개량사업소](https://www.dcic.co.kr/Web/Main): 등록·심사·젖소 씨수소 | 개정/평가판별; 원/두·정액단위 | 젖소. 한우 KPN 경로(H09)와 분리. 수수료·공급표 확인 필요 |
| **D15** | **CONDITIONAL** | 국립축산과학원 『한국가축사양표준 젖소』 4차 개정판 **활용자료** [본문](https://www.nias.go.kr/front/atis_FarmingApply_view.do?cmCode=M091023194037455&otptCode=CIEA011123); cmCode=M091023194037455, otptCode=CIEA011123 | 개정 시; 영양소별 원표 단위 | 젖소. 확보 내용은 활용 안내 HTML임. 사육단계별 요구량표 전체 원문 확보 전 승격 금지 |
| **D16** | **CONDITIONAL** | 농진청 농사로 [농산물소득자료](https://www.nongsaro.go.kr/portal/ps/psb/psbf/frmprdIncomeInfoNew.ps?menuId=PS03617); PS03617 | 작물별 연간; ha·10a·원 등 | 공통 중 **자가 조사료 재배에 한함**. 젖소/한우 본체 원가 자료가 아님. 개별 사일리지 기술 원문 미확보 |
| **D17** | FOLLOW_UP | 사료업체·TMR공장 거래명세/견적·성분분석서 (공개 공통 확정 URL 없음) | 거래/제품별; 원/kg·성분 % | 공통. 구매단위·수분·운임 확인. 표준사료성분과 실제 구매제품 구분 |
| **D18** | FOLLOW_UP | 축평원 [다봄](https://www.ekapepia.com/v3/web/main.do): 젖소 송아지·도태우 판매 후보 | 거래별; 원/두·원/kg 생체/지육 | 젖소. 성별·월령·체중 확인. 한우 가격(H01/H02) 전용 금지 |
| **D19** | FOLLOW_UP | 농협 [축산정보센터](https://livestock.nonghyup.com): 젖소·육우 시장시세 후보 | 일/월; 원/두 등 원표 | 젖소·육우 품종·월령·거래조건 구분 |
| **D20** | FOLLOW_UP | 농림축산검역본부 [KAHIS 발생통계](https://home.kahis.go.kr/home/lkntscrinfo/selectLkntsOccrrnc.do) 및 방역공고 | 공고/발생별; 두·농장·회 | 공통. 발생두수와 발생률 구분. 접종 의무는 지역·일자별 별도 확인 |
| **D21** | FOLLOW_UP | 국립축산과학원 [축산기술](https://www.nias.go.kr): 젖소 질병·번식장애 자료 후보 | 기술자료 개정 시; 질병별 | 젖소. 질병명·시험조건 확인 필요. 치료계획은 실제 진료 근거 바탕 |
| **D22** | FOLLOW_UP | 농업정책보험금융원 [보험제도](https://www.apfs.kr), 실제 보험사 약관·증권 | 약관 개정 시; 원·%·보장단위 | 공통. 축종·가입연도·보장범위·지자체 지원조건 확인 |
| **D23** | **READY** | 환경부 공고 제2022-444호 가축분뇨 배출원단위 [PDF](https://mcee.go.kr/home/file/readDownloadFile.do?fileId=248270&fileSeq=1); fileId=248270, fileSeq=1 | 공고 개정 시; kg/두·일 | 공통. **한우·젖소 행 분리 적용**. 세정수 ‘-’를 0으로 해석 금지 |
| **D24** | FOLLOW_UP | 축산환경관리원 [환경기술](https://www.lemi.or.kr): 퇴비·악취·처리시설 지침 | 개정/시설별; 용량·기간 등 | 공통. 처리방식(퇴비화/액비화/정화) 및 시설 조건 확인 |
| **D25** | FOLLOW_UP | 축평원 [이력 통계](https://www.mtrace.go.kr/stats/) + 관할 지자체 통계연보 | 월/연; 두·농장 | 공통. 품종·지역·기준일 확인. 한육우 합계를 한우로 임의 축소 금지 |
| **D26** | **READY** | 국가데이터처 『2025년 축산물생산비조사 결과』 [PDF](https://www.mods.go.kr/boardDownload.es?bid=227&list_no=445127&seq=3); bid=227, list_no=445127, seq=3; 승인통계 101043 | 연간; 우유 원/100ℓ, 젖소 천원/마리 | 젖소 표: 물리 21~22쪽, 용어 31쪽. 통계비용과 현금비용 분리. KOSIS tblId 미확정 |
| **D27** | FOLLOW_UP | 국가데이터처 [가축동향 발표 목록](https://mods.go.kr/board.es?bid=225&mid=a10301080400), [KOSIS](https://kosis.kr) | 분기; 두·농장 등 원표 | 공통. 품종·연령·조사기준일 구분 |
| **D28** | FOLLOW_UP | 국가데이터처 [KOSIS 지수](https://kosis.kr): CPI·농가판매/구입가격지수 | 월/연; 지수 | 공통. 지수는 단가가 아님. 원유가격에 자동 외삽 적용 금지 |
| **D29** | FOLLOW_UP | 기상청 [ASOS](https://data.kma.go.kr/data/grnd/selectAsosRltmList.do?pgmNo=36), 축과원 [가축사육기상](https://chuksaro.nias.go.kr/lwis/gis/tpIdex/tpIdex.do) | 관측 주기별; ℃·%·mm·지수 | 공통. 관측소 선정 근거 필요. 젖소 THI(가축더위지수)를 한우에 전용 금지 |
| **D30** | FOLLOW_UP | 농진청 [흙토람](https://soil.rda.go.kr): 조사료포 토양자료 후보 | 갱신 시; 원표 단위 | 공통 중 조사료 재배지. 우사 자체에 작물 시비처방 강제 금지 |
| **D31** | FOLLOW_UP | 농식품부 [사업지침](https://www.mafra.go.kr): 후계농·청년농·축사시설현대화 | 사업연도별; 원·%·년 | 공통. 공고·대출약정 조건 확인. 지원금리 임의 기본값 생성 금지 |

---

## 3. 한우 경로 (A1b 번호 체계 정본)

적용 구분: 번=번식우, 비=비육우, 일=일관사육.

| ID | 판정 | 기관·자료명 / 식별자 / URL | 주기·단위 | 적용 경계 및 주의사항 |
|---|---|---|---|---|
| **H01** | **READY** | 축평원 다봄 [소 산지가격](https://www.ekapepia.com/v3/price/livestock/cow/producer.do), 원천 농협 조사 | 일/월; 천원/마리 등 화면 단위 | 번·비·일. 송아지/큰소·성별·월령·체중 기준 보존. 지육가격과 구분 |
| **H02** | **READY** | 축평원 [지육 경락가격](https://www.ekapepia.com/v3/price/livestock/cow/wholesale/carcassPrice.do), [경락가격·출하 시계열](https://www.ekapepia.com/v3/supplyTrend/statistics/auctionPrice/cow.do) | 조회별; 원/kg 지육·두 | 비·일(번 도태우 조건부). 한우·성별·등급 필터 필수. 농가 실수령액은 정산서 확인 |
| **H03** | FOLLOW_UP | KREI [한육우 월보](https://aglook.krei.re.kr/main/uObserveMonth/OVR0000000015), [원문 후보](https://aglook.krei.re.kr/main/uObserveMonth/OVR0000000015/download/8752/ORGTEXT_FILE) | 발간호별; 두·원/kg 등 | 번·비·일. 전망·잠정·확정치 분리 |
| **H04** | **READY** | 국가데이터처 송아지 생산비·한우번식우 사육비: [생산비 PDF](https://www.mods.go.kr/boardDownload.es?bid=227&list_no=445127&seq=3), 승인통계 101043 | 연간; 천원/송아지 마리·번식우 마리 | 번·일. 물리 15~16쪽. 송아지 생산비와 모축 사육비의 분모 구분 필수 |
| **H05** | **READY** | 국가데이터처 한우비육우 생산비·사육비: [동일 PDF](https://www.mods.go.kr/boardDownload.es?bid=227&list_no=445127&seq=3), 승인통계 101043 | 연간; 천원/생체100kg·마리 | 비·일. 물리 17~18쪽. 전체 비육비용을 1년 유지비로 취급 금지. 육우와 분리 |
| **H06** | **CONDITIONAL** | 국립축산과학원 한국가축사양표준 4차 개정 **보도자료** [본문](https://nias.go.kr/front/prboardView.do?boardSeqNum=3923&cmCode=M090814150850297&currPage=1); boardSeqNum=3923 | 개정 시; 원표 단위 | 번·비·일. 보도자료 본문 확인. 한우 단계별 요구량표 책자 확보 전 승격 금지. **‘D01 사료성분표’ 참조 제거됨** |
| **H07** | FOLLOW_UP | 농사로 사료빈 잔량 확인장치 [후보](http://www.nongsaro.go.kr/portal/ps/psz/psza/contentSub.ps?menuId=PS00077&cntntsNo=246348) | 기술자료별; 센서·설비 | 번·비·일 중 설비 필요 시. **‘D03 한우 사료섭취 행동’ 참조 제거됨**. cntntsNo 미확인 후보 |
| **H08** | FOLLOW_UP | 농사로 한우 암소 개량 DB·유전능력평가 [후보](http://www.nongsaro.go.kr/portal/ps/psz/psza/contentSub.ps?menuId=PS00077&cntntsNo=209295) | 기술자료별; 형질 단위 | 번·일. 암소 기록·혈통·평가방법 확인 필요 |
| **H09** | **CONDITIONAL** | 농협 한우개량사업소 [KPN 분할육 비율 EPD 일람표](https://www.limc.co.kr/KpnInfo/KpnList_iframe_div.asp) | 평가/게시판별; KPN·비율 EPD | 번·일. 확보 표는 **분할육 비율**임. 현재 정액 재고·가격·도체형질 전체표로 확대 금지 |
| **H10** | **CONDITIONAL** | 국립축산과학원 [한우 교배계획 길라잡이 40호 안내](https://nias.go.kr/front/prboardView.do?boardSeqNum=4525&cmCode=M090814150850297); boardSeqNum=4525 | 발간호별; 형질별 단위 | 번·일. 발간 안내 본문 확인. 길라잡이 책자 원본 미확보. 혈연계수와 근교계수 구분 |
| **H11** | FOLLOW_UP | 농사로 번식·경산우 비육 및 축과원 임신우 연구 묶음 ([번식일정 후보](https://nongsaro.go.kr/portal/ps/psb/psbl/workScheduleDtl.ps?cntntsNo=30729&menuId=PS00087&sKidofcomdtySeCode=210007&totalSearchYn=Y), [경산우비육 후보](https://www.nongsaro.go.kr/portal/ps/psb/psbb/farmUseTechDtl.ps?farmPrcuseSeqNo=6418&menuId=PS00072)) | 시험/자료별; 체중·단위 | 번·비·일. 과거 특정 시험급여 수익을 현행 표준으로 전용 금지 |
| **H12** | **READY** | KREI 『농업전망 2026』 제4장 한육우 절·부록 [PDF](https://www.aglook.kr/upload/eventdata/f9f1aec0d59447a88343854978bdf2bb.pdf) | 연간; 두·톤·원/kg 등 원표 | 번·비·일. 한우/육우, 성별, 등급별 실적 및 전망 분리 (D06과 동일 파일) |

---

## 4. 오용 금지 규칙 (X01~X09, HX01~HX10)

| 규칙 ID | 판정 | 금지 내용 및 대체 준수 경로 |
|---|---|---|
| **X01** | READY | 농사로 작물소득자료를 젖소 본체 원가로 사용 금지. 생산비는 D26, 자가 조사료 재배만 D16 적용 |
| **X02** | READY | 소매 우유 가격이나 KAMIS 소매가를 농가 원유 수취 단가로 사용 금지. D08(제도 안내), D09(수취가격 통계), D12(실제 집유계약) 분리 |
| **X03** | READY | 작물 가격 평균이나 일반 CPI 외삽 산식을 원유 제도가격(용도별 차등가격)에 임의 적용 금지 |
| **X04** | READY | 유가공업체용 원유구입요령을 낙농가 쿼터 매입 규정으로 오용 금지. 실제 집유주체 규정(D12) 확인 필요 |
| **X05** | READY | 오래되거나 대응이 미확인된 KOSIS 표를 현행표로 사용 금지. D26 확보 PDF 원문 우선 |
| **X06** | READY | 한우·육우 가격 및 사양 기준을 젖소 자료로 전용 금지 (D15·D26 사용) |
| **X07** | READY | 선배 논문의 농장 고유 수치, 납유량, 쿼터, 단가를 새 계획에 복제 금지 |
| **X08** | READY | 축종 평균 분뇨 배출원단위를 임의 분할하거나, 세정수 '-' 표기를 0으로 자의적 해석 금지 (D23 기준) |
| **X09** | READY | 조사료 재배지가 없는 일반 축사에 작물 토양 검정 및 시비 처방 경로 강제 금지 |
| **HX01** | READY | 농사로 작물소득자료를 한우 사육 원가로 사용 금지. H04(송아지·번식우), H05(비육우) 적용 |
| **HX02** | READY | 쇠고기 소매 정육가격을 농가 출하가격으로 전용 금지. H01(산지가격), H02(지육 경락가격) 분리 |
| **HX03** | READY | 생체중과 도체중, 생체 100kg당 생산비와 지육 kg당 경락단가를 혼합 계산 금지 |
| **HX04** | READY | 비육기간 전체(입식~출하) 사육비를 1마리의 연간 유지비용으로 오인 금지 (H05 조사기간 확인) |
| **HX05** | READY | 송아지당 생산비와 번식우당 사육비의 분모를 혼동하거나 중복 합산 금지 (H04 적용) |
| **HX06** | READY | 거세비육우 단가를 경산암소나 노폐도태우에 그대로 적용 금지 (H02 성별·등급 필터) |
| **HX07** | READY | 일관사육 농장의 내부 송아지 이동을 농장 전체 외부 매출/매입으로 중복 계상 금지 |
| **HX08** | READY | 통계청 '한육우' 합계 통계를 한우 단독 통계로 자의적 해석 금지 (품종 분리 확인) |
| **HX09** | READY | 한우 계획에 젖소 전용 자료(D06~D15, D18~D19, D21 등)를 전용 금지 (단 D26 생산비 파일 자체는 H04/H05 표를 포함하므로 차단 대상 아님) |
| **HX10** | READY | 구판 분뇨 배출계수, 과거 특정 시험의 일시적 수익액을 현행 일반 기준으로 전용 금지 (D23·H06 준수) |
