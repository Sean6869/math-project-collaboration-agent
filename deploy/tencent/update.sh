#!/usr/bin/env bash
set -Eeuo pipefail

APP_DIR="${MAPLE_APP_DIR:-/opt/maple/app}"
REPO_SLUG="${MAPLE_REPO_SLUG:-Sean6869/math-project-collaboration-agent}"
BRANCH="${MAPLE_BRANCH:-main}"
CONTAINER_NAME="${MAPLE_CONTAINER_NAME:-tencent-app-1}"
IMAGE="${MAPLE_IMAGE:-maple-app:update}"
DATA_DIR="${MAPLE_DATA_DIR:-/opt/maple/data}"
ENV_FILE="${MAPLE_ENV_FILE:-${APP_DIR}/deploy/tencent/.env}"
APP_PORT="${MAPLE_APP_PORT:-8000}"

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
  echo "请使用 sudo 运行此脚本：sudo bash ${APP_DIR}/deploy/tencent/update.sh" >&2
  exit 1
fi

for command in curl docker tar grep git python3 timeout; do
  command -v "${command}" >/dev/null || {
    echo "缺少命令：${command}" >&2
    exit 1
  }
done

test -d "${APP_DIR}"
test -f "${ENV_FILE}"
test -d "${DATA_DIR}"
docker inspect "${CONTAINER_NAME}" >/dev/null
BASE_IMAGE="${MAPLE_BASE_IMAGE:-$(docker inspect --format '{{.Config.Image}}' "${CONTAINER_NAME}")}"
test -n "${BASE_IMAGE}"

STAMP="$(date +%Y%m%d-%H%M%S)"
OLD_CONTAINER="${CONTAINER_NAME}-backup-${STAMP}"
BACKUP_DIR="${MAPLE_BACKUP_DIR:-/opt/maple/backups}"
DATA_BACKUP="${BACKUP_DIR}/data-${STAMP}.tar.gz"
TMP_DIR="$(mktemp -d)"
ARCHIVE="${TMP_DIR}/source.tar.gz"
STAGE_DIR="${TMP_DIR}/source"
HEALTH_FILE="${TMP_DIR}/health.json"
ENV_COPY="${TMP_DIR}/deploy.env"
CANDIDATE_IMAGE="${IMAGE}-${STAMP}"

cleanup() {
  rm -rf "${TMP_DIR}"
}
trap cleanup EXIT

mkdir -p "${BACKUP_DIR}" "${STAGE_DIR}"
cp "${ENV_FILE}" "${ENV_COPY}"

API_URL="https://api.github.com/repos/${REPO_SLUG}/commits/${BRANCH}?t=$(date +%s)"
COMMIT_SHA="$(curl -fsSL --connect-timeout 15 --max-time 30 -H 'Cache-Control: no-cache' \
  -A 'Maple-Update/1' "${API_URL}" | python3 -c 'import json, sys; print(json.load(sys.stdin)["sha"])' 2>/dev/null || true)"
if [[ ! "${COMMIT_SHA}" =~ ^[0-9a-f]{40}$ ]]; then
  echo "GitHub API 不可用，尝试读取 Git 分支引用。" >&2
  COMMIT_SHA="$(timeout 45 git ls-remote "https://github.com/${REPO_SLUG}.git" "refs/heads/${BRANCH}" | awk 'NR == 1 { print $1 }' || true)"
fi
if [[ ! "${COMMIT_SHA}" =~ ^[0-9a-f]{40}$ ]]; then
  echo "无法从 GitHub 读取 ${REPO_SLUG}:${BRANCH} 的 commit。" >&2
  exit 1
fi

ARCHIVE_URL="https://codeload.github.com/${REPO_SLUG}/tar.gz/${COMMIT_SHA}"
retry curl -fL --retry 0 --connect-timeout 20 --max-time 600 \
  -A "Maple-Update/1" "${ARCHIVE_URL}" -o "${ARCHIVE}"
tar -xzf "${ARCHIVE}" -C "${STAGE_DIR}" --strip-components=1
printf '%s\n' "${COMMIT_SHA}" > "${STAGE_DIR}/.github-commit"

reuse_environment=0
if docker image inspect "${BASE_IMAGE}" >/dev/null 2>&1; then
  old_requirements="$(docker run --rm "${BASE_IMAGE}" cat /app/requirements.txt 2>/dev/null || true)"
  new_requirements="$(cat "${STAGE_DIR}/requirements.txt")"
  if [[ -n "${old_requirements}" && "${old_requirements}" == "${new_requirements}" ]]; then
    reuse_environment=1
  fi
fi

if [[ "${reuse_environment}" -eq 1 ]]; then
  FAST_DOCKERFILE="${TMP_DIR}/Dockerfile.update"
  cat > "${FAST_DOCKERFILE}" <<EOF
  FROM ${BASE_IMAGE}
COPY . /app
USER appuser
EOF
  echo "依赖文件未变化，复用现有 Python 环境。"
  docker build -f "${FAST_DOCKERFILE}" \
    --label "org.opencontainers.image.revision=${COMMIT_SHA}" \
    -t "${CANDIDATE_IMAGE}" "${STAGE_DIR}"
else
  echo "依赖文件已变化，重新构建 Python 环境。"
  docker build --cache-from "${BASE_IMAGE}" \
    --label "org.opencontainers.image.revision=${COMMIT_SHA}" \
    -t "${CANDIDATE_IMAGE}" "${STAGE_DIR}"
fi

docker image inspect "${CANDIDATE_IMAGE}" >/dev/null
docker tag "${CANDIDATE_IMAGE}" "${IMAGE}"

# 更新工作目录，但保留服务器上的生产环境配置。
tar -xzf "${ARCHIVE}" -C "${APP_DIR}" --strip-components=1
install -m 600 "${ENV_COPY}" "${ENV_FILE}"

docker stop "${CONTAINER_NAME}"
if ! tar -czf "${DATA_BACKUP}" -C "$(dirname "${DATA_DIR}")" "$(basename "${DATA_DIR}")"; then
  docker start "${CONTAINER_NAME}"
  echo "数据备份失败，已启动原容器。" >&2
  exit 1
fi

docker rename "${CONTAINER_NAME}" "${OLD_CONTAINER}"

rollback() {
  docker rm -f "${CONTAINER_NAME}" >/dev/null 2>&1 || true
  docker rename "${OLD_CONTAINER}" "${CONTAINER_NAME}"
  docker start "${CONTAINER_NAME}" >/dev/null
  echo "新容器未通过检查，已恢复原容器：${CONTAINER_NAME}" >&2
}

if ! docker run -d \
  --name "${CONTAINER_NAME}" \
  --restart unless-stopped \
  --env-file "${ENV_FILE}" \
  -p "127.0.0.1:${APP_PORT}:${APP_PORT}" \
  -v "${DATA_DIR}:/app/data" \
  "${CANDIDATE_IMAGE}"; then
  rollback
  exit 1
fi

ready=0
for _ in $(seq 1 30); do
  if curl -fsS --max-time 5 "http://127.0.0.1:${APP_PORT}/api/health" > "${HEALTH_FILE}" \
    && grep -Eq '"status"[[:space:]]*:[[:space:]]*"ok"' "${HEALTH_FILE}"; then
    ready=1
    break
  fi
  sleep 2
done

running_commit="$(docker exec "${CONTAINER_NAME}" cat /app/.github-commit 2>/dev/null || true)"
if [[ "${ready}" -ne 1 || "${running_commit}" != "${COMMIT_SHA}" ]]; then
  rollback
  exit 1
fi

cat "${HEALTH_FILE}"
echo
echo "更新完成"
echo "GitHub 仓库：https://github.com/${REPO_SLUG}"
echo "GitHub commit：${COMMIT_SHA}"
echo "容器内 commit：${running_commit}"
echo "数据备份：${DATA_BACKUP}"
echo "旧容器：${OLD_CONTAINER}"
docker ps --filter "name=${CONTAINER_NAME}"
