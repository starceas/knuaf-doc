# FC-CLEAR — 식량작물 16시트 빈 사본 지도 (2판)

Binding map: `fc-clear-map.json` (sha256
`81bf95912416ae8d1853bbd31f638c6d8c669c43303dbaf96116f506cf3f9fc5`,
schema `gg-xlsx-template-map/v1`, `workbook_profile: food_crops_16_sheet_v1`).

이 파일은 **칸 주소만** 담는다. 지운 값·남긴 값·영수증·사본 파일은 저장소에 넣지
않는다. `gg_excel_template.blank_copy`가 이 지도를 읽어 학교 양식 원본(sha256
`3e92fa51…ea3416`, 105,222 bytes)의 사본에서 `clear` 칸만 비우고 `preserve` 칸은
값을 그대로 둔다. 원본은 절대 수정하지 않는다.

## 지도의 범위

| | 칸 | 뜻 |
|---|---:|---|
| `clear` | **393** | 학생이 자기 근거로 다시 적는 숫자 입력 칸. 양식에 미리 들어 있던 예시값을 지운다 |
| `preserve` — 계획연도 | **40** | `4. 원리금상환계획` `B7:B26`·`B38:B57`. 표의 머리 구조라 지우면 표가 무너진다 |
| `preserve` — 출처 주석 | **42** | `*`로 시작하는 출처·적용 기준 문구. 양식 지침이라 지우면 지침이 사라진다 |
| 합계 | **475** | 지도 밖 칸은 손대지 않고 영수증에 공개한다 |

엔트리는 159개다(연속 구간 단위). 그중 `clear` 122개, `preserve` 37개이며
`range`를 펼친 칸 수가 위 393·82다. role은 세 가지뿐이다 —
`input.student_estimate`, `structure.period_year`, `structure.source_note`.
`explicit: true`는 사람이 그 칸을 직접 검토했다는 표시다.

### 왜 393인가

원본의 숫자 상수는 433칸이다. 그중 **수식 칸의 캐시값과 표 구조 칸 40개를 뺀
나머지 393칸**이 학생이 다시 채워야 하는 입력이다. 433 전부를 지도에 넣지 않으면
`blank_copy`가 지도 밖 숫자를 `unclassifiedNumericCells`로 세고 출력 상태를
`partial`로 낮춘다. 그래서 남기는 칸도 지도에 **적어서** 남긴다 — "지도에 없어서
남았다"와 "검토해서 남겼다"는 다르다.

- 수식 칸 1,256개와 수식 안 상수는 지도에 넣지 않는다. 도구가 수식을 자동으로
  보호하므로 지도에 넣을 이유가 없고, 넣으면 "이 수식을 지워도 된다"는 잘못된
  선언이 된다.
- 텍스트 42개를 `preserve`로 적는 이유도 같다. 지도 밖 텍스트는
  `unclassifiedTextCells`로 잡혀 `partial`이 된다. 이 42개는 선배·학생 수치가
  아니라 양식이 스스로 단 적용 기준 문구다.

## `#DIV/0!` — 그대로 둔다

사본의 수식 캐시는 전부 무효화되고(`formulaCachesInvalidated` 1,256), 통합문서는
`fullCalcOnLoad`로 저장된다. 그래서 학생이 엑셀로 열면 빈 칸을 분모로 쓰는 수식이
`#DIV/0!`로 보인다. **이것은 고장이 아니라 "아직 안 채웠다"는 표시다.**
(설계 결정 2. 실제 엑셀 화면에서 어떻게 보이는지는 R2로 확인하지 않았다 —
이번 단위는 엑셀을 실행하지 않았고, 원본 파일에도 오류값 셀이 0개다.)

- 도구는 `#DIV/0!`를 지우거나 0으로 바꾸지 않는다. 0으로 바꾸면 계산이 조용히
  틀린 값이 되고, 지우면 수식이 사라진다.
- 학생 안내: 자기 값을 채우면 사라진다. 채운 뒤에도 남는 `#DIV/0!`는 분모가
  계획상 0인 칸이므로 그대로 두고 4장 서술에서 이유를 밝힌다.
- 시험은 `#DIV/0!`의 개수를 고정하지 않는다. 엑셀이 계산하는 값이라 파이썬으로
  재현하지 않는다. 대신 **수식 레코드 1,256개의 텍스트·속성이 원본과 같은지**와
  **캐시값이 1,256 → 0이 되었는지**를 고정한다.

## 영수증에서 읽는 것

`blank_copy`는 다음을 낸다. 시험은 이 값들을 원본 실파일에서 직접 확인한다.

| 키 | 기대 | 뜻 |
|---|---|---|
| `cleared` | 393 | 지운 칸 목록(주소·이전 값) |
| `preserved` | 82 | 남긴 칸 목록(주소·이유) |
| `unclassifiedNumericCells` | 0 | 지도에 없는데 숫자가 남은 칸 |
| `unclassifiedTextCells` | 0 | 지도에 없는데 텍스트가 남은 칸 |
| `formulaCellsProtected` | 0 | `clear` 대상과 수식이 겹친 칸 |
| `missingCells` | `[]` | 지도에 있는데 원본에 없는 칸 |
| `formulaCachesInvalidated` | 1,256 | 무효화한 수식 캐시 수 |
| `output.status` | `blank_template` | 위 값이 모두 깨끗할 때만. 하나라도 어긋나면 `partial` |
| `workbook_profile` | `food_crops_16_sheet_v1` | 지도가 선언한 프로필과 요청 프로필이 같아야 한다 |

## 거절되는 경우

지도와 요청이 어긋나면 사본을 만들지 않고 파일도 남기지 않는다.

- 지도의 `workbook_profile`을 다른 프로필(예: `school_17_sheet_v1`)로 바꿔 요청하면
  `unsupported_finance_profile`. 17시트 지도로 16시트 문서를 지우면 좌표가 전부
  어긋나므로, 프로필 이름이 곧 레이아웃 검사다.
- 원본 구조가 등록된 지문과 다르면 `workbook_structure_mismatch`. 메시지에
  `fingerprint`가 들어간다.
- 지도의 `source.sha256`과 실제 원본이 다르면 `RuntimeError("source version changed")`.
  학교가 양식을 개정하면 지도를 다시 검토해야 한다.

## 사본 만드는 명령

```sh
python3 skills/knuaf-doc/scripts/gg_excel_template.py clear \
  --project <프로젝트 폴더> --major food_crops \
  --source <학교 원본 xlsx> \
  --map skills/knuaf-doc/references/food-crops/corrections/fc-clear-map.json \
  --out <사본 xlsx> --receipt <영수증 json>
```

- `--project`는 필수다. 없으면 `output_context_required`(rc 2)로 거절하고 파일을
  만들지 않는다.
- `--major food_crops`도 필수다. 없으면 `major_id_required`(rc 2)다. 프로젝트 정본에
  `common.major_id`가 등록돼 있어도 CLI는 `--major`를 따로 받는다.
- `--source`는 필수다. 번들 양식(`assets/templates/finance-template.xlsx`)은 17시트라
  식량작물 16시트 원본을 대신하지 못한다.
- 성공하면 stdout에 `{"status": "blank_template", "cleared": 393, "formulaProtected": 0,
  "formulaCachesInvalidated": 1256, "missing": 0}`이 나오고 rc 0이다.

## 확인하는 법

```sh
# 지도 자체(엔트리·칸 수·role 화이트리스트)와 합성 문서 거동
python3 -m unittest tests.test_food_crops_excel2_blank_copy -v

# 학교 원본 실파일에서 393/82·수식 레코드·바이트 불변 확인 (파일은 저장소 밖)
KNUAF_REAL_FOOD_CROPS_XLSX=/path/to/창업논문_수도작_원본.xlsx \
  python3 -m unittest tests.local_real_sources -v
```

원본 실측 시험은 환경변수가 없으면 SKIP이며 PASS로 세지 않는다.
