# 작업 폴더 잠금(v2)과 복구

한 작업 폴더의 정본(`project.json`)과 발행 산출물을 쓰는 명령은 **잠금 capability**를 실제로 점유한 동안에만 실행된다. 이 문서는 운영자·기여자가 잠금 구조와 복구 명령을 이해하기 위한 참조다. 사용자에게 JSON이나 명령어를 요구하지 않는다.

## 디렉터리 구조

`init`이 만드는 `.gg-lock/`은 상주 프로토콜 디렉터리다. 잠금이 풀려도 남는다.

| 경로 | 역할 |
| --- | --- |
| `.gg-lock/protocol.json` | 잠금 프로토콜 정본 — `workspace_id`, 루트·디렉터리·가드의 파일 식별자, 프로토콜 해시. no-replace로 한 번 발행된다 |
| `.gg-lock/guard` | 1바이트 가드 파일 — OS 파일 잠금(fd)의 대상. **쓰기 권한의 실체**다 |
| `.gg-lock/owner.json` | 마지막 점유자의 증거 기록(소유자·시각). 권한이 아니라 **증거**다 |

## 권한과 증거의 구분

- 쓰기 권한은 **가드 파일의 OS 잠금을 점유한 살아있는 capability**다. capability는 토큰·PID·루트/디렉터리/가드 식별자·프로토콜 해시를 묶은 객체이며, 복제·위조·해제된 capability는 모든 쓰기에서 거부된다.
- `owner.json`은 마지막으로 누가 잡았는지의 **증거**일 뿐이다. 남아 있는 stale owner는 새 점유를 막지 않는다 — 가드를 획득하는 쪽이 owner를 대체한다. 비정상 종료로 owner가 남아도 다음 작업은 계속된다.
- 반대로 owner가 지워져도 가드·프로토콜·디렉터리는 그대로다. owner 삭제는 잠금 해제가 아니다.

## 명령

| 명령 | 동작 | 쓰는 API |
| --- | --- | --- |
| `gg.py doctor <폴더>` | 읽기 전용 진단. 가드를 한 번 잡았다 놓아 상태(`free`/`busy`/`unavailable`)를 관측한다. owner를 쓰거나 지우지 않는다 | `probe_lock` |
| `gg.py unlock <폴더>` | stale owner 증거를 정리한다. 가드를 한 번 점유한 뒤 `owner.json`만 제거한다. 결과의 `owner_cleaned`가 실제로 제거됐는지 나타낸다. 살아있는 점유(busy)·미사용 가능·손상 상태는 변경 없이 거부한다 | `cleanup_owner` |
| `gg.py lock-upgrade <폴더>` | 오프라인 전환·복구. **사용자의 오프라인 확인이 선행 조건**이며, 확인 없이 호출하면 `blocked`/`offline_confirmation_required`를 반환한다. 잠금 상태 보고가 아니라 복구 결과(`recovery_id`·`commit_state`·수령증)를 돌려준다 | `upgrade_offline` |

`unlock`은 owner 기록만 정리한다. 가드·프로토콜·잠금 디렉터리를 삭제하거나 다른 프로세스의 살아있는 점유를 끊지 않는다.

## 복구가 필요한 상태

- **복구 진행 중 표식**(`.gg-lock-recovery-*` 계열의 active 표식)이 있으면 모든 제품 쓰기와 점유가 `busy`/`recovery_active`로 거부된다. 진행 중인 복구를 건너뛰거나 대신 완료하지 않는다 — `lock-upgrade`의 resume 경로로만 계속한다.
- 가드·프로토콜·디렉터리가 서로 맞지 않는 손상 상태는 `unavailable`로 보고되며 자동 수리를 시도하지 않는다.

## 발행 수령증과의 관계

export·paper·adopt-output·observe·import는 완료 후 불변 수령증(`.gg-artifacts/<id>/.publication.json`, `.gg-observations/<id>.json`, `.gg-import-publication.json` 등)을 남긴다. 수령증은 잠금 해제 뒤에도 남으며, 같은 요청의 재실행은 수령증을 대조해 기존 결과를 돌려준다(정확 재현)거나 충돌로 거부한다. 수령증 파일을 직접 고치거나 지우지 않는다.
