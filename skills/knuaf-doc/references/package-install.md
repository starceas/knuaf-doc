# Python 패키지 의존성 선언과 준비

문서 읽기 도구 준비는 [parser-setup.md](parser-setup.md)를 따른다. 이 문서는 산출물 생성에 필요한 Python 패키지 의존성의 선언·확인·설치 계약이다.

## 선언된 의존성

스킬이 필요로 하는 패키지는 설치된 것처럼 보이는 환경에서 찾지 않고 `scripts/requirements-runtime.txt`와 `scripts/runtime-deps.json`에 명시한다. `runtime-deps.json`에는 배포판 이름, 버전 범위, 실제 import 이름, 용도, 사용하는 스킬 스크립트를 함께 둔다.

| 배포판 | import 이름 | 용도 |
|---|---|---|
| openpyxl | `openpyxl` | XLSX 읽기·생성 — 재무 workbook·학교 양식 검사·출력 검증 |
| python-docx | `docx` | DOCX 생성 — 앞표지·본문 렌더 |
| pypdf | `pypdf` | PDF 구조 검증 — 보낸 PDF 페이지 확인 |

## 정직한 확인

다른 환경에 설치돼 있다는 표시는 이 프로젝트의 실행 증거가 아니다. 호스트 사용자 site-packages·다른 가상환경에 패키지가 있어도, 이 프로젝트를 실행하는 인터프리터에서 `import`가 되지 않으면 미설치로 본다. 호스트의 site-packages를 프로젝트로 복사하거나 경로를 임의로 추가해 "있는 것처럼" 만들지 않는다.

`scripts/gg_deps.py doctor <작업폴더>`는 선언된 각 패키지를 현재 인터프리터와 프로젝트 전용 환경(`<작업폴더>/.venv`)에서 실제로 `import`하고, 설치된 배포판 버전이 `runtime-deps.json`의 선언 범위 안인지 함께 검사한다. 버전을 읽을 수 없거나 안정된 숫자 점 형식(예: `3.1.5`)이 아니면 비교 불가로 보고(`installed-version-unchecked`) 준비로 세지 않는다. `ready`는 선택된 인터프리터가 선언된 모든 패키지를 불러오고 버전이 범위 안일 때만 `true`다.

## 프로젝트 전용 환경 준비

설치가 필요하면 프로젝트 폴더 안의 `.venv`에만 설치한다. 전역 site-packages, 다른 프로젝트 환경, 사용자 홈의 패키지는 변경하지 않는다.

1. `python3 <스킬>/scripts/gg_deps.py doctor <작업폴더>`로 현재 상태를 확인한다.
2. `python3 <스킬>/scripts/gg_deps.py ensure <작업폴더>`로 `<작업폴더>/.venv`를 만들고 선언된 `requirements-runtime.txt`를 설치한다. 로컬 wheel 폴더가 있으면 `--no-index --find-links <wheel폴더>`로 네트워크 없이 설치한다.
3. `ensure`는 pip 실행 뒤 반드시 새 venv 인터프리터로 각 패키지를 실제 `import`하고 버전 범위까지 검증한다. 설치 실패·네트워크 없음·wheel 부재·버전 비교 불가는 실패로 보고하며 성공처럼 기록하지 않는다.
4. 이후 의존성이 필요한 스크립트는 `python3 <스킬>/scripts/gg_deps.py python <작업폴더>`가 출력하는 인터프리터(`.venv`가 있으면 그 안의 python)로 실행한다.

네트워크도 로컬 wheel도 없으면 `ensure`는 실패를 보고하고, 그 사유를 작업폴더 감사 기록에 남긴다. 설치가 불가능한 의존성이 필요한 산출물(XLSX·DOCX·PDF 검증)은 만들지 않거나 "의존성 미설치로 보류"로 기록한다.

### 끊어진 `.venv`를 만났을 때

기준 Python을 옮기거나 지운 뒤에는 `.venv`의 POSIX 실행 파일 링크(`bin/python3`·`bin/python`)가 존재하지 않는 대상을 가리킬 수 있다. `ensure`는 이 경우에만 기존 `.venv`를 지우지 않고 `.venv-broken-<UTC 시각>`으로 옮긴 뒤 새 `.venv`를 준비한다.

보존 뒤 성공 응답은 `preserved_venv` 객체를, 보존 뒤 실패 응답은 `status`와 `reason` 및 같은 `preserved_venv` 객체를 담는다. 실패 reason에는 보존 이름이 있어야 한다. 보존 객체의 `name`·`path`·`link`·`link_target`은 보존 위치와 끊어진 링크를 가리킨다. 보존하지 않은 정상 ensure 결과는 `preserved_venv: null`을 담고, 보존 전 실패(`main`의 `blocked`)에는 이 키가 없다.

보존 뒤 **성공** 응답에는 학생 안내 목록 `notices`가 원소 하나로 실린다: `code: "venv_preserved"`, 기계용 폴더 이름 `folder`, 그리고 값이 박힌 고정 문장 `message`. `message`는 폴더 이름·경로를 넣지 않는 상수(`gg_deps.VENV_PRESERVED_NOTICE`)라서 앱은 그대로 보여 주면 되고, 어느 보존본인지는 `folder`(또는 같은 응답의 `preserved_venv.name`)로 안다. 보존하지 않았거나 설치가 실패한 응답은 모양을 바꾸지 않는다 — `notices` 키가 없고, 실패는 기존대로 `reason`이 보존 이름을 담는다.

- 이름 규칙: `.venv-broken-YYYYmmdd-HHMMSS`. 같은 초 안에서 기본 이름이 이미 있으면 `-2`, `-3` … `-100` 순으로 시도한다. 같은 초 안에서 100개 이름이 모두 차면 덮어쓰지 않고 `blocked`로 멈춘다. 번호는 초가 바뀌면 다시 시작하므로 보존본 전체 개수에 고정 상한은 없다.
- 보존본은 자동으로 지우지 않는다. 학생이 필요 없다고 판단하면 직접 지운다.
- 이름 바꾸기가 실패하면(폴더를 쓰는 앱·Codex·터미널이 열려 있음) 지우지 않고 `blocked`로 돌려주며, 결과 `reason`에 무엇을 끄고 다시 시도해야 하는지 적는다.
- 다른 이유로 깨진 `.venv`(예: 실행 파일이 없거나 pip 손상)는 보존 분기를 타지 않고 기존 생성 경로를 따른다. 그 경로는 새 환경 생성에 성공할 수도, 실패할 수도 있다.

## 실행 인터프리터 선택 순서

스킬 스크립트를 실행할 Python 인터프리터는 다음 순서로 고른다:

1. 현재 실행 인터프리터 — Python 3.10 이상이면 그대로 사용한다.
2. `py -3` — Windows Python 런처.
3. `python3`.
4. 호스트 번들 인터프리터.

`python3 <스킬>/scripts/gg_deps.py python <작업폴더>`가 `.venv`를 포함해 최종 인터프리터를 출력한다.

## Windows·macOS 지원 범위

**Windows는 작성·검토본(DOCX·XLSX 생성, 검토)까지 지원한다. Office 자동 재계산·PDF 변환은 macOS만 지원한다.**

`pywin32`는 Windows COM 자동화에만 쓰이며 필수 의존성에서 뺐다 — `runtime-deps.json`의 필수 목록에 없다. 설치돼 있어도 Windows 네이티브 경로는 아직 실사용 승인 대상이 아니다 — Office 자동화 호출은 COM 생성·접속 전에 차단돼야 하며, 사용자의 기존 Word/Excel 인스턴스에 붙거나 종료하는 경로는 배포하지 않는다. macOS 핵심 경로와 이 차단 없는 Windows 자동화를 같은 검증 등급으로 기록하지 않는다.

`docs/validation/REGRESSIONS.md`는 개발 계보 문서다(학생·배포 필독 문서가 아니다). 수정 단계별(P1, P2 …)로 그곳에 `open`으로 남은 항목이 있는 동안 그 기능은 승인된 동작이 아니며, 후속 단계와 함께 운영할 때만 배포가 완결된다.

## 제외 대상

학교 원문 추출물·예시본·개발 도구는 이 의존성 선언에 포함하지 않는다. 문서 읽기 파서(Kordoc 등)는 Python 패키지가 아니라 [parser-setup.md](parser-setup.md)의 기존 도구 우선 절차를 따른다. 잠금·발행 계층(`gg_lock.py`·`gg_publication.py`·`gg_fs.py`)은 표준 라이브러리만 사용하며 `requirements-runtime.txt`에 추가하지 않는다.
