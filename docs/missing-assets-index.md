# 대체·제외 자산 색인 초안

출처는 누락 자산 감사 부록 A다. `S0`~`S5`는 감사 원본 위치를 익명화한 라벨이며 개인 로컬 경로를 공개하지 않는다. 39행 전부를 기록했으며, 감사 표의 개발 트리 후보를 일괄 복원하지 않는다. `배포(w5R)`는 w5R 소유 경로로 넘긴 항목이며 이 D3 초안이 복사·수정한다는 뜻이 아니다. `배포`는 0.1.4 초안에서 복구 대상으로 정한 상태이지 현재 설치본에 이미 있다는 뜻이 아니다.

| 감사 경로 | SHA-256 | 처분 | 현행 대체·처분 이유 |
|---|---|---|---|
| `S0/AGENTS.md` | `7adcf529b87afff3eaf2006a095c734d5fd2c3dd6be46a6b0a092b74bc3b8afc` | 대체 | 개발 지침 전용; 학생 배포 제외 |
| `S0/LICENSE` | `5c0dec70fcf17ee5b7996447fcb5cea513accb1c9343e5e064d130906b98d7c0` | 대체 | 저장소 루트 LICENSE |
| `S0/README.md` | `5677feab698e2221239296665dc0d4872c9971be07875c7fd2dc66b7364f26eb` | 대체 | 저장소 README.md 및 SKILL.md |
| `S0/agents/openai.yaml` | `8b83485a8d847de9c2dc1e6653968ae68aab2ad20444f8449495858e02136385` | 개발 전용 | 현재 호스트 메타데이터; 옛 스킬명 후계 미확인 |
| `S0/references/approval-checklist.md` | `c25c026da3946279baaaefc9445ef3e634158c28d042126821a5dba302a6a72a` | 대체 | core·cross-review·guideline-tracking |
| `S0/references/config.md` | `c9f653cec10fe526dafd5005439be81eace89c53d317d834ee38fd361aff5870` | 대체 | intake-selection.md 및 project.json |
| `S0/references/entry-points.md` | `7f0a8806e80ca489bc2a66c13dac9c36b2cbc4e8cee69fcc44e2e05bac6c0602` | 대체 | 현행 SKILL·workflow-order; 원고 분할은 N4 |
| `S0/references/evidence-lint.md` | `481877f8e366b58dd7a3cc25827a038edb3262fec76e90d1817e6232db1e86ef` | 대체 | source-contract.md; DOI 조회는 N3 |
| `S0/references/excel-format.md` | `cf13f0e6659f55e81ed88a7ff658c34509b9c0133d48b67e355e92fd50ab4153` | 대체 | excel-template.md·outputs.md 및 교정 명세 |
| `S0/references/excel-submission.md` | `f5b802c93c832392ac832415f51376993c4d1fb0ad60b070f2311ecd54c0b06c` | 개발 전용 | excel-template.md·outputs.md |
| `S0/references/exemplar-guide.md` | `a3da3cb0e4b5286972eb0a59a0016bc7106778bfb3f1d338679c5d04c8d6be84` | 개발 전용 | exemplar-quality.md·narrative-expansion.md |
| `S0/references/financial-feasibility-gate.md` | `73849f5a46bef2eacfc6d60d936ff4168baefaf8fd224643f83a5d20ec926be4` | 대체 | w5R 현행 재무 흐름; 옛 전역 BLOCK 문서 전체 복원 금지 |
| `S0/references/finance-bible.md` | `d24f1d904463d3d62854fe976e3163698e4fae40ada9a910237885f270a8cd93` | 배포(w5R) | w5R 담당 산출물(배포(w5R)) |
| `S0/references/lessons-learned.md` | `d31d58983d116a0d9e639ca593b6b237cdb2b3d007cb33a51fc5cc88239bcc19` | 배포(w5R) | w5R 담당 산출물(배포(w5R)) |
| `S0/references/official-toc.md` | `9c8285baf5be8887c8e9b3dc6322279e0997afb7ceb9ebd522e6f8a141bddac0` | 배포 | N1 복구 초안: references/official-toc.md |
| `S0/references/open-questions.md` | `58545a533bd4b391af926cc81cd04a58d515ade2db40e13e81bbc1adff5f0b42` | 배포(w5R) | w5R 담당 산출물(배포(w5R)) |
| `S0/references/rda-benchmark.md` | `4f556164ba38ca2d2ded8be4c4ff708c05942fd08e2b7741bb9df4ce5ae7a2a4` | 배포(w5R) | w5R 담당 산출물(배포(w5R)); N5 진입 안내 별도 |
| `S0/references/rule-enforcement.md` | `235f6c0ce268aba2058883b223370b1a819925e2434dc91c008878b55ee9f07f` | 대체 | guideline-tracking.md·section-ledger.md·source-contract.md |
| `S0/references/rules/CH1_intro.md` | `bc8fcdc39f5a35f51d6b13d9631b3fd0d323ae3a37793cc280bce0516411b60e` | 배포 | N1/N3/N4 복구 대상; N1: 머리말 요구·소제목. 허용목록 밖; 원문/현행 대조 후 필요한 기능만 복구 |
| `S0/references/rules/CH2_external.md` | `43a9c7409a822d47cdb6ebab54d8bccb8391970d2bd3bf4258368c23866d2308` | 배포 | N1/N3/N4 복구 대상; N1: 외부환경 세부 요구·조사/표 위치. 허용목록 밖; 원문/현행 대조 후 필요한 기능만 복구 |
| `S0/references/rules/CH3_plan.md` | `2fc4b127bad453a29b50d9d712e0167ca878eb690b52e44130bc64fb9b0a54ee` | 배포 | N1/N3/N4 복구 대상; N1: 영농계획 세부 요구·재배/판매/관련 논문. 허용목록 밖; 원문/현행 대조 후 필요한 기능만 복구 |
| `S0/references/rules/CH4_finance.md` | `71ada58f00d9d23a848aad8ed783bfc4294e36e20ffadc08f2251ab7fa51dee5` | 배포 | N1/N3/N4 복구 대상; N1: 재무계획 요구·산출근거; 옛 제출 지표는 현행 정책 대조 필요. 허용목록 밖; 원문/현행 대조 후 필요한 기능만 복구 |
| `S0/references/rules/CH5_CH6_closing.md` | `d144be69807d63f9e6a1cf22d475f668b08f8c8db24217bcdc1af04779c53158` | 배포 | N1/N3/N4 복구 대상; N1: 맺음말·참고문헌 요구. 허용목록 밖; 원문/현행 대조 후 필요한 기능만 복구 |
| `S0/references/rules/EX_excel_buildspec.md` | `36989513830cff529ef16846afe7286c87e8210533a908545362e142343a401d` | 배포 | N1/N3/N4 복구 대상; N1: 엑셀 생성 세부 명세; 현행 템플릿 계약에 상당 부분 이관. 허용목록 밖; 원문/현행 대조 후 필요한 기능만 복구 |
| `S0/references/rules/EX_excel_structure.md` | `8d9ac226dd5593b34fc70ba3159b422395f2b2573fafc05766db59b30f677734` | 배포 | N1/N3/N4 복구 대상; N1: 예시 엑셀의 구조·계산 관찰; 원자료 배포와 구분. 허용목록 밖; 원문/현행 대조 후 필요한 기능만 복구 |
| `S0/references/rules/EX_exemplar_structure.md` | `c25eadff3d31f0f04796cff0cf5110b93708ad6774ffe27a3c39997931ca2487` | 배포 | N1/N3/N4 복구 대상; N1: 선배논문 구조 관찰; 공식 학교 의무로 승격 금지. 허용목록 밖; 원문/현행 대조 후 필요한 기능만 복구 |
| `S0/references/rules/F_format.md` | `8994a3a2568656d742da21c17fcbeca0ccb44a2411567b75fec6c3a4624bc356` | 배포 | N1/N3/N4 복구 대상; N1: 학명·인용·단위·표그림·번호·병합 규칙; 검사기 일부 유지. 허용목록 밖; 원문/현행 대조 후 필요한 기능만 복구 |
| `S0/references/rules/PRE_frontmatter.md` | `3d27bca302860acceb7e005b1c68d4e97af494ea6a3548242b0887066f61aeca` | 배포 | N1/N3/N4 복구 대상; N1: 머리지면·요약·목차; 생성기 일부 유지. 허용목록 밖; 원문/현행 대조 후 필요한 기능만 복구 |
| `S0/references/rules/W_writing_spec.md` | `1b3a71c5880d420163698f47bda61ee89b59c547b7f58fe1909b2f2be8ade9dd` | 배포 | N1/N3/N4 복구 대상; N1: 절 작성 사양; narrative-expansion 등 일부 대체. 허용목록 밖; 원문/현행 대조 후 필요한 기능만 복구 |
| `S0/scripts/build_plugin.py` | `68cb9cb8a7bbf38f7c413f9190a60fb736a954168f650f1e68b365a6f0acda13` | 개발 전용 | 학생 설치 제외; 공개 재현 경로는 통합 단계에서 별도 소유 |
| `S0/scripts/extract_draft.py` | `52c7e95dc8e1163cec3587fb0227d3f855259351d4ec9b3919f3687eba8275f1` | 배포 | N4에서 현행 파서·절 등록 경로에 맞춰 대체 구현 |
| `S0/scripts/gg_finance.py.bak` | `dde17691dd4c543db066f018226dbcc3f36f407ec4c20accf73d6c399c1f1465` | 개발 전용 | 백업, 실행 대체 경로 없음 |
| `S0/scripts/init_workspace.py` | `8c7a0adf40bef4d227b6a8e3743989a63cbf9a8c6256a99e755c29a0103a33df` | 대체 | gg.py init |
| `S0/scripts/verify_doi.py` | `6e78d0c254fd27a70ee09c2ca6c4c67f78421c1aef8d38cdac04e433f6176617` | 배포 | N3에서 source-contract 기반으로 대체 구현 |
| `S1/manifest.json` | `a8a301a644e36db961635571e4dd2d89350dcd659a7ad9a60f5a8ce1ca52f21d` | 개발 전용 | 권고: source-snapshot 내부 메타데이터. 학생 스킬 공통 파일이 아님 |
| `S2/scripts/inert_probe.py` | `e5bde4f0eec314dd3bf1333bd5737d35d6b1a1507998759072b5e5b65b32387e` | 개발 전용 | 권고: 업데이트 시험용 probe. 배포 제외 유지 |
| `S3/references/student-data.json` | `b5f56421ca8ec1a26aa255980d8f78719086462ee31cfd986fe333da1af6a847` | 개발 전용 | 권고: 사용자 파일 보존 시험 fixture. 실제 학생 자료로 오인하거나 배포 금지 |
| `S4/.knuaf-doc-version` | `5a435b5bc39eb42bbfb42176fd49bef1fd7a274c42f0dbf35c7f6931a3aaf2a1` | 대체 | 권고: QA 설치 표시. 현재 version.json이 버전 계약을 소유 |
| `S5/references/output-tooling.md` | `e382d328662b49f2bddd7b9e56f2b1958946a86903cd80788c4dfbcaa3283e50` | 대체 | 권고: QA 설치본의 출력 설명 분리본. 현재 outputs.md가 출력 명령·한계를 소유; 독립 누락 기능 미확인 |

## 제외 원칙

- 학교 규칙 N1, DOI 조회 N3, 원고 장 분할 N4는 일괄 옛 파일 복사가 아니라 현행 정책·파서 계약에 맞춰 필요한 자산만 배포한다.
- 선배 논문·개인 프로젝트의 원본, 사용자 데이터 보존 fixture, 설치 QA 파일, 개발 probe, 백업은 학생 payload에 넣지 않는다.
- K1 4개는 w5R 담당 경계로 전달한다. 구 `financial-feasibility-gate.md` 전체는 현재 전역 작성 중단 등 충돌 때문에 그대로 복구하지 않고 기능 단위 대체를 따른다.
- `manifest.json` 등 S0 바깥의 QA·스냅샷 후보는 누락 결함으로 계산하지 않는다. 이 표는 공개 저장소 `docs/` 예정 문서의 초안이며 로컬 절대 경로나 학생 원고·개인정보를 포함하지 않는다.
