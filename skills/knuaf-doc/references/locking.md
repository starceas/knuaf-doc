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
| `gg.py doctor <폴더>` | 읽기 전용 진단. 잠금 구조·프로토콜 기록을 보고할 뿐 점유를 관측하지 않는다 | — |
| `gg.py doctor <폴더> --probe` | 가드를 한 번 잡았다 놓아 상태(`free`/`busy`/`unavailable`)를 관측한다. owner를 쓰거나 지우지 않는다. 관측 결과는 후속 쓰기 허가가 아니다 | `probe_lock` |
| `gg.py unlock <폴더>` | stale owner 증거를 정리한다. 가드를 한 번 점유한 뒤 `owner.json`만 제거한다. 결과의 `owner_cleaned`가 실제로 제거됐는지 나타낸다. 살아있는 점유(busy)·미사용 가능·손상 상태는 변경 없이 거부한다 | `cleanup_owner` |
| `gg.py lock-upgrade <폴더>` | 오프라인 전환·복구. **사용자의 오프라인 확인이 선행 조건**이며, 확인 없이 호출하면 `blocked`/`offline_confirmation_required`를 반환한다. 잠금 상태 보고가 아니라 복구 결과(`recovery_id`·`commit_state`·수령증)를 돌려준다 | `upgrade_offline` |

`unlock`은 owner 기록만 정리한다. 가드·프로토콜·잠금 디렉터리를 삭제하거나 다른 프로세스의 살아있는 점유를 끊지 않는다.

## 복구가 필요한 상태

- **복구 진행 중 표식**(`.gg-lock-recovery-*` 계열의 active 표식)이 있으면 모든 제품 쓰기와 점유가 `busy`/`recovery_active`로 거부된다. 진행 중인 복구를 건너뛰거나 대신 완료하지 않는다 — `lock-upgrade`의 resume 경로로만 계속한다.
- 가드·프로토콜·디렉터리가 서로 맞지 않는 손상 상태는 `unavailable`로 보고되며 자동 수리를 시도하지 않는다.

## 발행 수령증과의 관계

export·paper·adopt-output·observe·import는 완료 후 불변 수령증(`.gg-artifacts/<id>/.publication.json`, `.gg-observations/<id>.json`, `.gg-import-publication.json` 등)을 남긴다. 수령증은 잠금 해제 뒤에도 남으며, 같은 요청의 재실행은 수령증을 대조해 기존 결과를 돌려준다(정확 재현)거나 충돌로 거부한다. 수령증 파일을 직접 고치거나 지우지 않는다.

## Windows 네이티브 발행 범위

Python 3.10·3.14의 일반 로컬 NTFS 드라이브에서 Windows byte lock을 사용한다. 발행 파일은 binary bytes로 읽고 쓰며, 파일 식별자·수령증·payload hash를 대조한다. 기존 목적지는 덮어쓰지 않는다. 같은 요청은 수령증을 확인해 재사용하고, 요청 ID가 같아도 입력이 다르면 충돌로 거부한다. import 이동 중 가드 점유를 유지하고 이동 후 경로를 다시 연결한다.

디렉터리 구성요소 핸들은 실제 읽기/목록 접근 권한으로 열고 읽기·쓰기 공유를 허용하되 삭제 공유를 거부하여 부모 이름의 rename을 막는다. 일반 파일·산출물 설치는 부모 핀을 유지한다. import에서 자기 소유 workspace를 이동할 때는 열린 guard 파일의 이름을 동일 volume의 이동 tree 밖 임시 경로로 no-replace 대피시킨다. 이 동안 동일 guard fd·byte lock·등록 capability를 유지한다. reader와 이동 tree의 핀을 닫은 뒤 root를 no-replace 이동하고, 목적지의 부모 핀을 다시 걸어 동일 guard 이름을 되돌린 후 root·부모·guard·protocol·수령증을 대조하고 경로를 rebind한다. 타인 목적지·guard는 덮어쓰지 않으며 guard 해제·재획득·복사·삭제 우회는 하지 않는다.

root 미설치를 확인할 수 있으면 guard를 원래 이름으로 복원한 뒤 실패를 보고한다. 위치·identity 또는 guard 복귀가 불확실하면 unknown/indeterminate로 보고하고 staging·대피 guard를 자동 정리하지 않는다. 이 구간의 연속 부모 이름 핀과 전체 작업의 crash-atomicity는 보장하지 않는다. guard가 빠진 protocol은 일반 writer 점유를 거부하며, 프로세스 중단 뒤 남은 객체를 자동 수리·삭제하지 않는다. 비협조적 메타데이터 변경은 보장 범위 밖이다.

경로 구성요소의 reparse point(심볼릭 링크·junction 포함), ADS(`name:stream`), 장치 이름(`CON`·`NUL` 등), 끝 점·공백과 UNC·장치 namespace는 지원하지 않는다. 중첩 경로·한글·공백은 지원한다. 협조하는 쓰기 명령은 같은 가드를 사용해야 한다. 파일 핸들 검사만으로 관리자 권한의 공격자나 모든 비협조적 변경을 차단한다고 보장하지 않는다.

Windows에서는 directory fsync에 따른 전원 상실 내구성을 보장하지 않는다. 설치·재읽기 검증이 성공해도 `durability_notes`의 `dir_fsync_unsupported` 메모가 남을 수 있다. owner 기록 정리 오류와 OS 가드 해제 결과는 별개이며, 실패 기록을 성공으로 바꾸지 않는다. SMB·FAT·클라우드 동기화 폴더·Office 자동화는 이 발행 검증의 범위 밖이다. 기존 Windows protocol 식별자와 새 handle 식별자가 불일치하면 자동 변환하지 않고 정본을 보존한 채 복구 진단으로 거부한다.

검증은 Ubuntu 전체 회귀와 별도의 Windows 3.10·3.14 native publication CI로 나뉜다. Windows 잡은 실제 port의 init/apply·발행·재시도·잠금 경쟁·해제/재획득·import·경로 거부를 실행하며 skip을 허용하지 않는다. 이 잡은 기존 `P2-L09-native` contention placeholder의 별도 미실행 계약을 해제하지 않는다. 특정 커밋의 실제 CI 결과가 실행 증거이며, structural 검사나 macOS 시험만으로 Windows 성공을 판단하지 않는다.

구현 계약 참고: [CreateFileW 공유·reparse 처리](https://learn.microsoft.com/en-us/windows/win32/api/fileapi/nf-fileapi-createfilew), [SetFileInformationByHandle](https://learn.microsoft.com/en-us/windows/win32/api/fileapi/nf-fileapi-setfileinformationbyhandle), [Python os.rename](https://docs.python.org/3.10/library/os.html#os.rename).
