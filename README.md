# oci-a1-sniper

OCI 오사카(ap-osaka-1) Always Free ARM 인스턴스(VM.Standard.A1.Flex, 2 OCPU / 12GB)를
GitHub Actions로 10분마다 자동 재시도해 확보합니다. 성공하면 이슈로 알림을 남기고 워크플로를 스스로 끕니다.

## 필요한 Secrets (Settings → Secrets and variables → Actions)

| 이름 | 값 |
|---|---|
| `OCI_USER` | 사용자 OCID (`ocid1.user...`) |
| `OCI_TENANCY` | 테넌시 OCID (`ocid1.tenancy...`) |
| `OCI_FINGERPRINT` | API 키 지문 (`aa:bb:...`) |
| `OCI_KEY` | API 개인키(.pem) 파일 내용 전체 |
| `SSH_PUBLIC_KEY` | 새 서버 접속용 SSH 공개키 (`ssh-ed25519 ...`) |

모든 값은 Secrets로만 전달되며 코드에는 들어 있지 않습니다.

## 실행
Actions 탭 → **OCI A1 Sniper (Osaka)** → **Run workflow** 로 바로 시작하거나, 10분마다 자동 실행을 기다립니다.
