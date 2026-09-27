# 업데이트 (AI 행동 규칙)

학생이 "업데이트해줘", "업데이트 확인", "새 버전 나왔어?"라고 말할 때의 절차. 첫 진입 표시에서의 자동 확인은 SKILL.md 첫 진입 절을 따른다.

## 확인

`python3 <이 스킬 루트>/scripts/gg_update.py check`를 실행한다(Windows는 `py -3`). stdout의 JSON 한 개를 읽는다.

| status | 의미 | 학생에게 하는 말 |
|---|---|---|
| update_available | 새 버전이 있다 | "새 버전 {latest_version}이 나왔어요(지금 {local_version}). '업데이트해줘'라고 말하면 바꿔 드려요." |
| up_to_date | 최신이다 | "지금 버전이 최신이에요." |
| local_newer | 설치본이 릴리스보다 새롭다 | "설치된 버전이 공개 릴리스보다 새로워요. 개발 중 버전일 수 있어요." |
| no_release | 아직 공개 릴리스가 없다 | "아직 공개된 업데이트가 없어요." |
| offline | 네트워크가 안 된다 | "지금은 인터넷 연결을 확인할 수 없어요. 나중에 다시 확인해요." |
| unknown | 답을 알 수 없다 | "업데이트 여부를 확인하지 못했어요. 지금 작업은 그대로 계속할 수 있어요." |

확인은 파일을 바꾸지 않는다. 어느 상태든 학생의 원래 작업을 막지 않는다.

## 적용(업데이트해줘)

동의를 받기 전에 바뀌는 것을 정확히 말한다:

- 바뀌는 것: `skills/knuaf-doc` 폴더 하나뿐이다.
- 학생의 작업 폴더·원고 정본·엑셀·Kordoc 캐시는 그대로다.
- 기존 폴더는 지우지 않고 백업으로 옮긴다. 백업 위치: `<홈>/knuaf-doc-update/backups/<버전>-<시각>-<난수>`.
- 적용이 끝나면 새 채팅을 열어야 새 버전이 적용된다.

학생이 듣고 동의하면 `python3 <스킬 루트>/scripts/gg_update.py apply --version <latest> --confirm`을 실행한다. **명시적 동의 없이 --confirm을 붙이지 않는다.**

## adopt(0.1.0에서 옮기기, 1회 전환)

0.1.0 설치본에는 업데이트 확인 장치가 없다. 다음 순서로 한 번만 전환한다:

1. `$CODEX_HOME/skills/knuaf-doc`(기본 `~/.codex`)에서 SKILL.md의 `name:`이 `knuaf-doc`인 설치본을 찾는다. 찾지 못했거나 여러 개면 멈추고 학생에게 안내한다.
2. skill-installer로 새 버전을 **skills 밖 임시 위치**에 설치한다:
   `install-skill-from-github.py --repo starceas/knuaf-doc --path skills/knuaf-doc --ref v<새버전> --dest <CODEX_HOME>/knuaf-doc-update/incoming-<UTC시각>`
3. 그 새 복사본의 스크립트로 교체한다:
   `python3 <incoming>/knuaf-doc/scripts/gg_update.py adopt --source <incoming>/knuaf-doc --target <설치본> --confirm`
4. applied면 새 채팅을 안내한다. incoming 폴더는 지우지 않는다(복구 재료다).

## 결과 코드별 대응

| 결과 | 의미·대응 |
|---|---|
| applied | 교체 완료. backup 경로를 알리고 새 채팅을 안내한다. |
| refused confirm_required | 동의 없이 --confirm을 붙이려 했다. 학생 동의를 먼저 받는다. |
| refused locked | 다른 업데이트가 진행 중이거나 잠금이 남았다. 잠시 뒤 check로 상태를 본다. 프로세스가 죽어 남은 잠금이면 `unlock --home <홈> --confirm`을 학생 동의 후 실행한다. |
| refused not_update_available / not_latest | 다시 확인한 결과 대상이 최신이 아니거나 요청 버전이 최신이 아니다. check 결과를 그대로 설명한다. |
| refused install_kind | 복사 설치가 아니다. hint를 따른다(git이면 `git pull --ff-only`). |
| refused not_newer / target_version_invalid / source_target_alias | 대상이 지금 버전보다 낮지 않거나 knuaf-doc 0.1.0 설치본으로 확인되지 않는다. 다른 폴더를 가리켰는지 확인한다. |
| refused symlinked_path / cwd_inside_skill / cross_device / unsafe_state_path / helper_exists | 경로·장치 안전 검사에서 멈췄다. 파일은 바뀌지 않았다. 상태 그대로 개발자에게 신고한다. |
| failed old_restored / target_intact | 새 버전을 놓지 못해 원래대로 되돌렸다. 학생 파일은 그대로다. 신고를 권한다. |
| failed backup_preserved_target_missing | 예상 밖 상태. backup과 recover 명령을 보존하고 그 명령을 실행한다: `python3 <state_dir>/bin/gg_update-<txid>.py recover --home <홈> --confirm`. |
| recover: recovered_old | 이전 버전이 복원됐다. check로 현재 버전을 확인한다. |
| recover: completed | 새 버전이 이미 놓여 있었다. 새 채팅을 안내한다. |
| recover: not_started | 교체가 시작되지 않았다. 기존 설치 그대로다. |
| recover: manual_required | 예상 밖 상태라 아무것도 바꾸지 않았다. journal과 경로를 보존하고 개발자에게 신고한다. |
| unlock: unlocked / no_lock / manual_required | 잠금을 지웠다 / 잠금이 없었다 / 잠금 파일을 읽지 못해 그대로 뒀다(경로 확인 후 신고). |

응답이 끊겼으면 미적용으로 단정하지 않는다. `check`의 local_version과 `knuaf-doc-update/journal.json`으로 실제 상태를 먼저 확인한다.

## 금지

- 스킬 폴더를 직접 삭제·이름 변경하지 않는다.
- skill-installer로 활성 `skills/knuaf-doc`를 덮어쓰지 않는다(설치 경로는 항상 skills 밖 incoming).
- 학생 동의 없이 apply·adopt·recover·unlock을 실행하지 않는다.
- 논문·엑셀 본문이나 학생 저자란에 업데이트 표시를 넣지 않는다.
