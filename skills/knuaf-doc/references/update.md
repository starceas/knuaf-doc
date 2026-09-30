# 업데이트 (AI 행동 규칙)

학생이 "업데이트해줘", "업데이트 확인", "새 버전 나왔어?"라고 말할 때와 SKILL.md 첫 진입 ③의 자동 확인 절차. 기본 경로는 `auto` 한 번이다.

설치본 버전에 따라 경로가 다르다. **0.1.2 이상**은 `auto`(자동·무동의 교체), **0.1.0·0.1.1**은 새 복사본의 `adopt`로 1회 전환한다.

## 자동 업데이트 (0.1.2 이상, 기본)

`python3 <이 스킬 루트>/scripts/gg_update.py auto`를 실행한다(Windows는 `py -3`). stdout의 JSON 한 개를 읽는다. auto는 끊긴 교체의 복구 → 버전 확인 → 복사 설치일 때만 교체까지 한 번에 한다. 학생 확인은 받지 않는다 — 바뀌는 것은 `skills/knuaf-doc` 폴더 하나뿐이고, 기존 폴더는 지우지 않고 `<홈>/knuaf-doc-update/backups/<버전>-<시각>-<난수>`로 옮긴다. 어느 결과든 exit 0이다(명령 사용 오류만 exit 2). 실패는 학생의 원래 작업을 막지 않는다.

auto의 저널 복구는 자동이다 — 이전에 끊긴 교체가 있으면 학생에게 묻지 않고 `recover --confirm`과 같은 정리를 먼저 하고 그 결과를 `recover` 필드에 담는다. 반대로 `recover` 명령 자체를 따로 실행하는 것은 학생이 요청했을 때만이다(아래 금지 절).

첫 진입 ③에서 실행할 때와 학생이 "업데이트해줘 / 업데이트 확인"이라고 말했을 때 같은 표를 쓴다. 첫 진입에서는 권한 상승·승인 요청을 하지 않는다. 학생이 직접 업데이트를 요청한 차례에는 네트워크·쓰기 권한 요청을 해도 된다.

| status | 의미 | 학생에게 하는 말 |
|---|---|---|
| updated | to 버전으로 교체됐다. backup에 이전 폴더가 있다 | "{to}로 자동 업데이트했어요." 그런 다음 **같은 차례에** 새 SKILL.md를 다시 읽고 그 지침으로 작업을 이어간다. 스킬 목록·설명의 갱신은 다음 대화부터 적용된다 |
| up_to_date | 이미 최신이다 | 첫 진입이면 말하지 않는다. 학생이 물었으면 "지금 버전이 최신이에요." |
| skipped·install_kind | 복사 설치가 아니다(git·플러그인 캐시·기타) | 첫 진입이면 말하지 않는다. 물었으면 설치 방식을 설명한다(git이면 `git pull --ff-only`) |
| skipped·local_newer | 설치본이 공개 릴리스보다 새롭다 | 첫 진입이면 말하지 않는다. 물었으면 "설치된 버전이 공개 릴리스보다 새로워요. 개발 중 버전일 수 있어요." |
| skipped·no_release | 공개 릴리스가 아직 없다 | 첫 진입이면 말하지 않는다. 물었으면 "아직 공개된 업데이트가 없어요." |
| skipped·locked | 다른 업데이트·복구가 진행 중이다 | 첫 진입이면 말하지 않는다. 물었으면: OS 잠금은 끊기면 자동으로 풀리므로 잠시 뒤 auto를 다시 실행해 본다 |
| skipped·user_files_in_skill | 스킬 폴더 안에서 원래 배포본과 다른 파일이 있다 | "스킬 폴더 안에서 원래 배포본과 다른 파일이 있어 자동 업데이트를 건너뛰었어요" 한 줄 안내 + 개발자 신고 권유. files 목록의 파일을 학생 작업 폴더로 옮기라고 안내한다 |
| skipped·install_manifest_invalid | 0.1.2 이상 설치본의 설치 정보가 없거나 읽을 수 없거나 손상됐다(state=missing·unreadable·corrupt) | "스킬 설치 정보가 손상돼 자동 업데이트를 멈췄어요" 한 줄 안내 + 개발자 신고 권유. 설치 파일을 그대로 보존한다 |
| skipped·legacy_reference_unavailable | 구 배포본의 기준 해시가 없거나 읽을 수 없거나 손상돼 원본 여부를 확인할 수 없다 | "구버전 설치 파일을 확인할 수 없어 업데이트를 멈췄어요" 한 줄 안내 + 개발자 신고 권유. 설치 파일을 그대로 보존한다 |
| skipped·기타 reason | 안전 검사에서 멈췄다 | 첫 진입이면 말하지 않는다. 물었으면 reason을 설명하고 상태 그대로 개발자에게 신고한다 |
| check_failed | 확인 자체가 안 됐다(offline·unknown) | "업데이트를 확인하지 못했어요(인터넷 연결이 막혀 있을 수 있어요). 지금 작업은 그대로 할 수 있어요." |
| failed | 교체·복구 도중 실패 | "업데이트가 중간에 멈췄어요" 한 줄 안내 + 신고 권유. state가 old_restored·target_intact면 학생 파일은 그대로다. backup_preserved_target_missing이면 JSON의 `recover` 문자열 또는 `recover_argv` 배열 명령을 실행한다(셸 없이 실행할 때는 `recover_argv`). state가 unknown이면 실제 상태를 확인하지 못한 것이므로 `version.json`·`journal.json`을 보고 JSON의 recover 명령을 실행한다 |
| recovered | 이전에 끊긴 교체를 복구했다 | "지난번 끊긴 업데이트를 정리했어요" 한 줄 안내 + 신고 권유. recover.status가 recovered_old면 이전 버전이 복원됐고, completed면 새 버전이 이미 놓여 있었고, not_started면 교체가 시작되지 않았던 것이다 |

응답이 끊겼으면 미적용으로 단정하지 않는다. `version.json`과 `<홈>/knuaf-doc-update/journal.json`으로 실제 상태를 먼저 확인한다.

## 0.1.1 → 0.1.2 (한 번만 수동 전환)

0.1.1 설치본에는 auto가 없다. 학생이 "업데이트해줘"라고 하면 아래 adopt 절차로 한 번만 전환한다. **0.1.1의 옛 apply는 쓰지 않는다.** 옛 코드에는 원고 보존 검사가 없어 스킬 폴더 안의 변경 파일을 옮길 수 있다.

먼저 동의를 받는다: `skills/knuaf-doc` 폴더 하나를 교체하며 기존 폴더는 백업으로 옮긴다. 새 복사본의 adopt가 구 배포본 기준 해시를 확인하고, 원래 배포본과 다른 파일이 있거나 기준을 읽을 수 없으면 교체 전에 거절한다. 0.1.2부터는 auto가 있어 이 전환 절차가 더 이상 필요 없다.

## adopt(0.1.0·0.1.1에서 옮기기, 1회 전환)

0.1.0 설치본에는 업데이트 확인 장치가 없다. 0.1.0·0.1.1 모두 다음 순서로 한 번만 전환한다:

1. `$CODEX_HOME/skills/knuaf-doc`(기본 `~/.codex`)에서 SKILL.md의 `name:`이 `knuaf-doc`인 설치본을 찾는다. 찾지 못했거나 여러 개면 멈추고 학생에게 안내한다.
2. skill-installer로 0.1.2 이상 새 버전을 **skills 밖 임시 위치**에 설치한다:
   `install-skill-from-github.py --repo starceas/knuaf-doc --path skills/knuaf-doc --ref v<새버전> --dest <CODEX_HOME>/knuaf-doc-update/incoming-<UTC시각>`
3. 그 새 복사본의 스크립트로 교체한다:
   `python3 <incoming>/knuaf-doc/scripts/gg_update.py adopt --source <incoming>/knuaf-doc --target <설치본> --confirm`
4. applied면 새 채팅을 안내한다. incoming 폴더는 지우지 않는다(복구 재료다).

## 명시 명령 결과 코드 (apply·adopt·recover)

`auto` 외 명시 명령을 쓸 때의 결과별 대응이다.

| 결과 | 의미·대응 |
|---|---|
| applied | 교체 완료. backup 경로를 알리고 새 채팅을 안내한다. |
| refused confirm_required | 동의 없이 --confirm을 붙이려 했다. 학생 동의를 먼저 받는다. |
| refused user_files_in_skill | 스킬 폴더 안에서 원래 배포본과 다른 파일이 있다. files 목록의 파일을 작업 폴더로 옮기라고 안내하고, 필요하면 개발자에게 신고한다. |
| refused install_manifest_invalid | 설치 정보가 없거나 읽을 수 없거나 손상됐다(state=missing·unreadable·corrupt). 설치 파일을 보존하고 개발자에게 신고한다. 구버전 판정으로 우회하지 않는다. |
| refused legacy_reference_unavailable | 구 배포본의 기준 해시를 읽을 수 없어 원본 여부를 확인하지 못했다. 설치 파일을 보존하고 개발자에게 신고한다. |
| refused zip_invalid | 새 복사본의 배포 목록이 없거나(detail=manifest_missing), 잘못됐거나(manifest_invalid), 실제 파일과 다르다(manifest_mismatch). 새 복사본을 다시 확보하고 설치본은 보존한다. |
| refused locked | 다른 업데이트·복구가 진행 중이다. 잠시 뒤 다시 확인한다. 업데이트가 끊기면 OS 잠금은 자동으로 풀린다. 저널이 남았으면 학생 요청 시 `recover --home <홈> --confirm`을 실행한다. |
| refused lock_replaced / lock_unsupported / journal_requires_recovery | 잠금 파일의 외부 교체가 감지됐거나 OS 잠금을 지원하지 않거나 이전 저널의 복구가 필요하다. 상태를 보존하고 개발자에게 신고한다. 이전 저널은 먼저 recover로 확인한다. |
| refused not_update_available / not_latest | 다시 확인한 결과 대상이 최신이 아니거나 요청 버전이 최신이 아니다. check 결과를 그대로 설명한다. |
| refused install_kind | 복사 설치가 아니다. hint를 따른다(git이면 `git pull --ff-only`). |
| refused not_newer / target_version_invalid / source_target_alias | 대상이 지금 버전보다 낮지 않거나 knuaf-doc 0.1.0 설치본으로 확인되지 않는다. 다른 폴더를 가리켰는지 확인한다. |
| refused symlinked_path / cwd_inside_skill / cross_device / unsafe_state_path / helper_exists | 경로·장치 안전 검사에서 멈췄다. 파일은 바뀌지 않았다. 상태 그대로 개발자에게 신고한다. |
| failed old_restored / target_intact | 새 버전을 놓지 못해 원래대로 되돌렸다. 학생 파일은 그대로다. 신고를 권한다. |
| failed backup_preserved_target_missing | 예상 밖 상태. backup과 recover 명령을 보존하고 그 명령을 실행한다: `python3 <state_dir>/bin/gg_update-<txid>.py recover --home <홈> --confirm`. JSON의 `recover` 문자열은 POSIX 인용이 적용되고 `recover_argv`는 인용 없는 인자 배열이므로 셸 없이 실행할 때는 `recover_argv`를 쓴다. |
| failed unknown | 파일시스템 오류로 실제 상태를 확인하지 못했다. 아무것도 움직였다고 단정하지 않는다. `version.json`·`knuaf-doc-update/journal.json`을 확인하고 JSON의 `recover`·`recover_argv` 명령을 실행한다. |
| recover: recovered_old | 이전 버전이 복원됐다. check로 현재 버전을 확인한다. |
| recover: completed | 새 버전이 이미 놓여 있었다. 새 채팅을 안내한다. |
| recover: not_started | 교체가 시작되지 않았다. 기존 설치 그대로다. |
| recover: manual_required | 예상 밖 상태라 아무것도 바꾸지 않았다. journal과 경로를 보존하고 개발자에게 신고한다. |

## 금지

- 스킬 폴더를 직접 삭제·이름 변경하지 않는다.
- skill-installer로 활성 `skills/knuaf-doc`를 덮어쓰지 않는다(설치 경로는 항상 skills 밖 incoming).
- `auto` 외 명시 명령(apply·adopt·recover)은 학생이 요청했을 때만 실행한다.
- 논문·엑셀 본문이나 학생 저자란에 업데이트 표시를 넣지 않는다.

## 한계

같은 로컬 잠금 파일을 쓰는 이 도구의 호출끼리만 배타성을 보장한다. 사람이나 다른 프로그램이 `knuaf-doc-update` 상태 폴더 또는 잠금 파일을 동시에 바꾸는 경우는 지원하지 않는다. NFS·SMB·클라우드 동기 폴더 위의 상태 폴더도 지원하지 않는다.
