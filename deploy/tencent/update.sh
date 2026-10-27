#!/usr/bin/env bash
set -Eeuo pipefail

APP_DIR="${MAPLE_APP_DIR:-/opt/maple/app}"
BRANCH="${MAPLE_BRANCH:-main}"

retry() {
  local attempt=1 max_attempts=5
  until "$@"; do
    if (( attempt >= max_attempts )); then
      echo "命令连续失败 ${max_attempts} 次：$*" >&2
      return 1
    fi
    echo "网络请求失败，5 秒后重试（${attempt}/${max_attempts}）：$*" >&2
    attempt=$((attempt + 1))
    sleep 5
  done
}
if [[ "${EUID}" -ne 0 ]]; then
  echo "请使用 sudo 运行此脚本。" >&2
  exit 1
fi
retry git -C "${APP_DIR}" fetch origin "${BRANCH}"
git -C "${APP_DIR}" checkout "${BRANCH}"
retry git -C "${APP_DIR}" pull --ff-only origin "${BRANCH}"

# 更新与首次部署共用同一套环境迁移、Caddy 版本、HTTPS 验证和启动逻辑，
# 避免更新后重新出现 http://IP、非安全 Cookie 或本地证书配置。
export MAPLE_APP_DIR="${APP_DIR}"
export MAPLE_BRANCH="${BRANCH}"
export MAPLE_SKIP_SYSTEM_SETUP=1
export MAPLE_SKIP_GIT_SYNC=1
bash "${APP_DIR}/deploy/tencent/deploy.sh"

docker image prune -f
