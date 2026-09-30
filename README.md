# knuaf-doc

한국농수산대학교 창업논문·영농계획서와 동반 재무자료를 학교 공식 작성지침에 맞춰 작성·검토하는 로컬 도구입니다. Codex/Claude 계열 에이전트의 스킬(플러그인)로 동작합니다.

## 이 도구가 하는 일

- 학교 공식 PDF 지침과 원답변을 근거로 창업논문 Ⅰ~Ⅵ장을 작성·검토합니다.
- 사실·목표·가정·계산을 구분해서 관리하고, 원문 근거 없는 주장을 자동으로 통과시키지 않습니다.
- 재무계획 엑셀(XLSX)과 논문 워드(DOCX) 산출물을 만들고, 학교 지침 항목(131개 세부 항목 등)과 대조합니다.
- 생성된 결과물은 지도교수 승인과 학교 제출 절차를 대신하지 않습니다. 최종 책임은 항상 작성자 본인에게 있습니다.

## 설치

`skills/knuaf-doc`가 설치 대상 스킬입니다. 설치 방법은 사용 중인 에이전트(Codex CLI/앱)의 스킬 설치 안내를 따르세요. Codex의 GitHub 스킬 설치기는 이 폴더만 복사하므로 저장소의 `.codex-plugin/plugin.json`은 복사본에 들어가지 않습니다.

사용 중 오류·불편·요청은 AI에게 "신고할래"라고 말하세요. 신고는 개발자가 먼저 비공개로 받아 검토합니다(개인 정보는 공개 이슈에 올리지 않습니다).

### 실행 의존성

`skills/knuaf-doc/scripts/requirements-runtime.txt`에 선언되어 있습니다.

```
openpyxl>=3.1,<4
python-docx>=1.1,<2
pypdf>=6,<7
```

`pywin32`는 필수 의존성에서 뺐습니다(아래 플랫폼 지원 참고). 설치 방법은 `skills/knuaf-doc/references/package-install.md`를 참고하세요.

## 업데이트

0.1.2 이상 설치본은 스킬을 처음 쓰는 새 대화에서 공개 릴리스를 한 번 확인하고, 새 버전이 있으면 별도 동의 없이 자동으로 교체합니다(`auto`). 업데이트는 기존 스킬 폴더를 `<CODEX_HOME>/knuaf-doc-update/backups/`로 옮기고 새 복사본을 놓습니다. 학생의 작업 폴더·원고·엑셀·Kordoc 캐시는 건드리지 않습니다. 교체되면 AI가 "새 버전으로 자동 업데이트했어요"라고 한 줄 알리고, 새 SKILL.md 지침은 같은 답변에서 다시 읽어 적용합니다(스킬 목록·설명의 갱신은 다음 대화부터).

0.1.1에는 첫 진입의 버전 확인(check)과 새 버전 알림이 있지만 자동 교체(auto)는 없습니다. 0.1.1과 0.1.0은 아래 "0.1.0/0.1.1에서 옮기기"의 adopt 절차로 한 번만 옮깁니다. 0.1.1의 `apply`는 스킬 폴더 안에서 바뀐 파일을 검사하지 않으므로 쓰지 않습니다. adopt는 새 버전의 검사기로 설치본을 원래 공개 배포본과 한 파일씩 대조하고, 다른 파일이 하나라도 있으면 아무것도 옮기지 않고 멈춥니다.

직접 **“업데이트 확인”**이라고 물으면 현재 확인 상태를 설명합니다. 설치본의 버전 원천은 [`skills/knuaf-doc/version.json`](skills/knuaf-doc/version.json)입니다. 릴리스 확인이 안 되면 원래 논문 작업을 계속할 수 있습니다. Git 체크아웃과 플러그인 캐시 설치본은 자동 교체 대상이 아니며, 안내된 설치 방식에 따라 수동으로 갱신합니다.

업데이트가 끊기면 OS 잠금은 자동으로 풀립니다. 끊긴 교체 저널이 남아 있으면 0.1.2 이상에서는 다음 `auto` 실행이 먼저 자동으로 복구를 시도하고, 명시 명령 `gg_update.py recover --home <CODEX_HOME> --confirm`은 학생이 요청할 때만 실행합니다. 이 업데이트 잠금은 같은 로컬 잠금 파일을 쓰는 도구 호출끼리만 보장합니다. 사람·다른 프로그램의 상태 폴더 또는 잠금 파일 동시 교체와 NFS·SMB·클라우드 동기 폴더는 지원하지 않습니다.

## 0.1.0/0.1.1에서 옮기기

0.1.0에는 업데이트 확인 장치가 없고, 0.1.1에는 확인·알림만 있고 자동 교체(auto)가 없습니다. 두 버전 모두 아래 adopt 절차로 한 번 옮기며 0.1.1의 `apply`는 쓰지 않습니다. adopt 절차는 두 버전 모두에서 새 복사본을 `skills` 밖에 설치한 뒤, 그 복사본의 `gg_update.py adopt`로 기존 설치본을 옮깁니다. AI에게 **“https://github.com/starceas/knuaf-doc README대로 knuaf-doc 업데이트해줘”**라고 말해 아래 절차를 진행할 수 있습니다.

1. `$CODEX_HOME/skills/knuaf-doc`(기본 `~/.codex/skills/knuaf-doc`)의 `SKILL.md`에 `name: knuaf-doc`가 있는 복사 설치본을 찾습니다. 없거나 여러 개면 멈춥니다.
2. 스킬 설치기의 `install-skill-from-github.py --repo starceas/knuaf-doc --path skills/knuaf-doc --ref vX.Y.Z --dest <CODEX_HOME>/knuaf-doc-update/incoming-<UTC>`로 새 버전을 `skills` 밖에 설치합니다. `vX.Y.Z`에는 공개된 최신 릴리스 태그를 넣습니다.
3. 변경 범위와 백업 위치를 듣고 명시적으로 동의한 뒤 `python3 <incoming>/knuaf-doc/scripts/gg_update.py adopt --source <incoming>/knuaf-doc --target <CODEX_HOME>/skills/knuaf-doc --confirm`을 실행합니다. Windows에서는 `python3` 대신 `py -3`을 사용합니다.
4. 기존 폴더 백업과 incoming 복사본을 보존하고 새 채팅을 엽니다. 상세 복구 절차는 [`update.md`](skills/knuaf-doc/references/update.md)를 따릅니다.

## 배포(관리자)

매 패치에서 `skills/knuaf-doc/version.json`과 `.codex-plugin/plugin.json`의 버전을 같은 값으로 올립니다. 검토자가 PASS를 준 트리와 머지 대상을 대조한 뒤 머지하고, **그 머지 커밋**을 대상으로 `gh release create vX.Y.Z --target <merge sha>`를 실행합니다. 공개 릴리스를 만들어야 설치본의 첫 진입 자동 확인(0.1.2 이상은 `auto`, 0.1.1은 `check`)이 새 버전을 발견합니다. 릴리스 태그와 업로드 권한은 관리자에게 있습니다.

## 전공별 지원 범위

| 전공 | 지원 범위 |
|---|---|
| 특용작물 | 질문 → 본문 Ⅰ~Ⅵ 작성·재무 XLSX·DOCX 검토본 |
| 산업곤충 | 질문 목록·선택형 문서 계획·공식 통계 근거 검토 (본문·XLSX·재무 계산 미지원) |
| 과수 | 학교 과수 양식(S01) 작성계획·물량 검산 (본문·XLSX·재무 계산 미지원) |
| 원예환경시스템 | 질문·문서 계획·근거 규칙 (전용 18시트 조건이 충족될 때만 전용 재무 출력) |

전공 상세는 `skills/knuaf-doc/references/majors.md`와 각 전공 README를 참고하세요. 미등록·미확정 전공은 전공 의존 작성을 보류합니다.

## 플랫폼 지원

핵심 로직(사실 관리·지침 검사·DOCX/XLSX 생성)은 순수 Python이라 macOS/Windows/Linux 어디서든 동일하게 동작합니다. **Windows는 작성·검토본(DOCX·XLSX 생성, 검토)까지 지원합니다. Office 자동 재계산·PDF 변환은 macOS만 지원합니다.**

다만 "네이티브 Office로 실제 재계산·페이지 렌더까지 검증"하는 마무리 단계(`scripts/gg_office.py`)는 실제 Word/Excel 앱을 직접 구동합니다.

| 플랫폼 | 구현 | 실사용 검증 |
|---|---|---|
| macOS | AppleScript(osascript) | 완료 — 실제 Word/Excel로 반복 검증됨 |
| Windows | COM 자동화(pywin32, 필수 의존성 아님) | 차단됨 — 실제 Windows + Office 실사용 검증 전까지 COM 호출 전에 멈춘다 |

Windows 네이티브 경로는 아직 실사용 승인 대상이 아닙니다. Office 자동화 호출은 COM 연결 전에 차단되도록 설계되어 사용자의 기존 Word/Excel 인스턴스에 붙거나 종료하지 않습니다. Windows에서 실제로 써보고 문제를 발견하시면 이슈나 PR로 알려주세요. 실사용 피드백을 기다리고 있습니다.

로컬·오프라인 작업과 클라우드 공유 폴더(OneDrive/iCloud 등)에서의 동시 작성은 지원하지 않습니다. 한 작업 폴더를 여러 환경이 동시에 쓰는 구성은 정본 잠금·리비전 계약 밖입니다.

## 학교 공식 지침 원문

저작권이 불확실한 학교 공식 PDF/발췌본은 이 저장소에 포함되어 있지 않습니다. 사용자가 자신의 학교 공식 원문을 직접 준비해서 등록해야 합니다.

## 검증

이 저장소는 회귀 시험과 묶음 무결성 검사를 소스에 포함합니다. Python 3.10 이상이 필요합니다.

```
python3 -m pip install -r requirements-validation.txt   # 런타임 의존성과 동일, 추가 검증 의존성 없음
python3 tools/check_bundle.py --root .                  # baseline·검증 묶음 무결성
python3 tools/run_validation.py                         # 회귀 시험 한 명령
```

- 시험은 `skills/knuaf-doc/scripts`의 실제 배포 파일을 import합니다. 개발 트리 경로에 의존하지 않습니다.
- 알려진 baseline 결함은 개별 expected-failure로 표시됩니다. runner가 녹색이어도 제품 PASS를 의미하지 않습니다. 결함 목록은 개발 계보 문서 `docs/validation/REGRESSIONS.md`(학생·사용자 필독 문서가 아니라 개발 이력)를 참고하세요.
- 배포 단계별 수정 묶음(P1, P2 …)은 함께 운영될 때만 완결됩니다. 개발 계보 문서 `REGRESSIONS.md`에 `open`으로 남은 항목(예: 재무 대조 항목)은 각 단계의 후속 수정이 붙기 전까지 해당 기능을 승인된 동작으로 간주하지 않습니다. P2에서 잠금 프로토콜 v2(stale 잠금 복구 포함)와 발행 수령증 계층이 착륙했습니다. 잠금 구조·`unlock`/`lock-upgrade` 복구 명령은 `skills/knuaf-doc/references/locking.md`를 참고하세요.
- CI(`.github/workflows/validation.yml`)는 PR마다 같은 명령을 실행합니다. Windows 잡은 구조 검사만 수행하며, 네이티브 Windows/Office 검증은 아직 수행되지 않았습니다.
- 묶음·manifest 계약의 상세는 `docs/validation/BUNDLE.md`를 참고하세요.

## 라이선스

MIT. 자유롭게 가져다 쓰고, 수정하고, PR을 보내주세요. 자세한 내용은 [LICENSE](LICENSE)를 참고하세요.

## 기여

이슈와 PR을 환영합니다. 특히 아래 영역의 실사용 검증/피드백이 필요합니다.

- Windows 환경에서 네이티브 Office 자동화 실제 동작 확인
- 다른 학교/다른 판 지침 PDF에 대한 호환성
