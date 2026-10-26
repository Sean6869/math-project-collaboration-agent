#!/usr/bin/env bash
set -Eeuo pipefail

APP_DIR="${MAPLE_APP_DIR:-/opt/maple/app}"
REPO_URL="${MAPLE_REPO_URL:-https://github.com/Sean6869/math-project-collaboration-agent.git}"
BRANCH="${MAPLE_BRANCH:-main}"

if [[ "${EUID}" -ne 0 ]]; then
  echo "请使用 sudo 运行：sudo bash deploy/tencent/deploy.sh" >&2
  exit 1
fi

apt-get update
DEBIAN_FRONTEND=noninteractive apt-get install -y ca-certificates curl git docker.io
if ! docker compose version >/dev/null 2>&1; then
  DEBIAN_FRONTEND=noninteractive apt-get install -y docker-compose-v2 || \
    DEBIAN_FRONTEND=noninteractive apt-get install -y docker-compose-plugin
fi
systemctl enable --now docker

mkdir -p "$(dirname "${APP_DIR}")"
if [[ -d "${APP_DIR}/.git" ]]; then
  git -C "${APP_DIR}" fetch origin "${BRANCH}"
  git -C "${APP_DIR}" checkout "${BRANCH}"
  git -C "${APP_DIR}" pull --ff-only origin "${BRANCH}"
else
  git clone --branch "${BRANCH}" --single-branch "${REPO_URL}" "${APP_DIR}"
fi

DEPLOY_DIR="${APP_DIR}/deploy/tencent"
ENV_FILE="${DEPLOY_DIR}/.env"
if [[ ! -f "${ENV_FILE}" ]]; then
  read -r -p "平台域名（已解析到本机公网 IP，例如 maple.example.com）：" MAPLE_DOMAIN_INPUT
  read -r -p "管理员教师账号：" MPT_TEACHER_USERNAME_INPUT
  read -r -s -p "管理员教师密码（至少 8 位）：" MPT_TEACHER_PASSWORD_INPUT
  echo
  read -r -s -p "火山方舟 API Key（可暂时留空）：" ARK_API_KEY_INPUT
  echo
  if [[ -z "${MAPLE_DOMAIN_INPUT}" || -z "${MPT_TEACHER_USERNAME_INPUT}" || ${#MPT_TEACHER_PASSWORD_INPUT} -lt 8 ]]; then
    echo "域名、教师账号和至少 8 位密码不能为空。" >&2
    exit 1
  fi
  umask 077
  COOKIE_SECURE=1
  if [[ "${MAPLE_DOMAIN_INPUT}" == :* || "${MAPLE_DOMAIN_INPUT}" == http://* ]]; then
    COOKIE_SECURE=0
  fi
  {
    printf 'MAPLE_DOMAIN=%s\n' "${MAPLE_DOMAIN_INPUT}"
    printf 'MPT_TEACHER_USERNAME=%s\n' "${MPT_TEACHER_USERNAME_INPUT}"
    printf 'MPT_TEACHER_PASSWORD=%s\n' "${MPT_TEACHER_PASSWORD_INPUT}"
    printf 'MPT_CLASS_NAME=默认班级\nMPT_GROUP_COUNT=3\n'
    printf 'MPT_COOKIE_SECURE=%s\n' "${COOKIE_SECURE}"
    printf 'ARK_API_KEY=%s\n' "${ARK_API_KEY_INPUT}"
    printf 'ARK_TEXT_MODEL=doubao-seed-2-1-turbo-260628\n'
    printf 'ARK_VISION_MODEL=doubao-seed-2-1-turbo-260628\n'
    printf 'ARK_PLAN_MODEL=doubao-seed-2-1-turbo-260628\n'
    printf 'ARK_IMAGE_MODEL=doubao-seedream-5-0-260128\n'
    printf 'MPT_RESET_DATA_ONCE=\n'
  } > "${ENV_FILE}"
  chmod 600 "${ENV_FILE}"
fi

cd "${DEPLOY_DIR}"
docker compose config --quiet
docker compose up -d --build
docker compose ps
echo "部署已启动。HTTPS 证书签发通常需要域名解析生效并开放 80/443 端口。"
