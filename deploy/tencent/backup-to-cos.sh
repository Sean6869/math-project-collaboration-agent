#!/usr/bin/env bash
set -Eeuo pipefail

APP_DIR="${MAPLE_APP_DIR:-/opt/maple/app}"
COS_DESTINATION="${MAPLE_COS_DESTINATION:-}"
if [[ -z "${COS_DESTINATION}" ]]; then
  echo "请设置 MAPLE_COS_DESTINATION，例如 cos://maple-backup/data/" >&2
  exit 1
fi
if ! command -v coscli >/dev/null 2>&1; then
  echo "未找到 coscli，请先按腾讯云 COSCLI 文档安装并配置最小权限密钥。" >&2
  exit 1
fi

cd "${APP_DIR}/deploy/tencent"
BACKUP_DIR="$(mktemp -d /tmp/maple-backup.XXXXXX)"
trap 'rm -rf -- "${BACKUP_DIR}"' EXIT
docker compose exec -T app python - <<'PY' > "${BACKUP_DIR}/platform.db"
import os, sqlite3, sys, tempfile
fd, backup_path = tempfile.mkstemp(suffix='.db')
os.close(fd)
source = sqlite3.connect('/app/data/platform.db')
try:
    target = sqlite3.connect(backup_path)
    source.backup(target)
    target.close()
    source.close()
    with open(backup_path, 'rb') as stream:
        sys.stdout.buffer.write(stream.read())
finally:
    source.close()
    if os.path.exists(backup_path):
        os.unlink(backup_path)
PY
docker compose cp app:/app/data/uploads "${BACKUP_DIR}/uploads"
coscli sync "${BACKUP_DIR}/" "${COS_DESTINATION}" -r --snapshot-path=/var/lib/maple-cos-snapshot
echo "COS 备份完成：${COS_DESTINATION}"
