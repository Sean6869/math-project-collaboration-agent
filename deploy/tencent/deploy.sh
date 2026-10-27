#!/usr/bin/env bash
set -Eeuo pipefail

APP_DIR="${MAPLE_APP_DIR:-/opt/maple/app}"
REPO_URL="${MAPLE_REPO_URL:-https://github.com/Sean6869/math-project-collaboration-agent.git}"
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

normalize_site() {
  local value="$1"
  value="${value#http://}"
  value="${value#https://}"
  value="${value%/}"
  if [[ -z "${value}" || "${value}" == */* || "${value}" == *\?* || "${value}" == *\#* || "${value}" == *:* ]]; then
    echo "平台地址必须是纯域名或公网 IPv4，不能包含协议、端口或路径：${value}" >&2
    return 1
  fi
  printf '%s' "${value}"
}

upsert_env() {
  local key="$1" value="$2" temp_file
  temp_file="$(mktemp)"
  awk -v key="${key}" -v value="${value}" '
    BEGIN { found = 0 }
    index($0, key "=") == 1 {
      if (!found) print key "=" value
      found = 1
      next
    }
    { print }
    END { if (!found) print key "=" value }
  ' "${ENV_FILE}" > "${temp_file}"
  install -m 600 "${temp_file}" "${ENV_FILE}"
  rm -f "${temp_file}"
}

if [[ "${EUID}" -ne 0 ]]; then
  echo "请使用 sudo 运行：sudo bash deploy/tencent/deploy.sh" >&2
  exit 1
fi

if [[ "${MAPLE_SKIP_SYSTEM_SETUP:-0}" != "1" ]]; then
  apt-get update
  DEBIAN_FRONTEND=noninteractive apt-get install -y ca-certificates curl git openssl docker.io
  if ! docker compose version >/dev/null 2>&1; then
    DEBIAN_FRONTEND=noninteractive apt-get install -y docker-compose-v2 || \
      DEBIAN_FRONTEND=noninteractive apt-get install -y docker-compose-plugin
  fi
  systemctl enable --now docker
fi

if [[ "${MAPLE_SKIP_GIT_SYNC:-0}" != "1" ]]; then
  mkdir -p "$(dirname "${APP_DIR}")"
  if [[ -d "${APP_DIR}/.git" ]]; then
    retry git -C "${APP_DIR}" fetch origin "${BRANCH}"
    git -C "${APP_DIR}" checkout "${BRANCH}"
    retry git -C "${APP_DIR}" pull --ff-only origin "${BRANCH}"
  else
    retry git clone --branch "${BRANCH}" --single-branch "${REPO_URL}" "${APP_DIR}"
  fi
fi

DEPLOY_DIR="${APP_DIR}/deploy/tencent"
ENV_FILE="${DEPLOY_DIR}/.env"
if [[ ! -f "${ENV_FILE}" ]]; then
  MAPLE_DOMAIN_INPUT="${MAPLE_SITE_ADDRESS:-}"
  if [[ -z "${MAPLE_DOMAIN_INPUT}" ]]; then
    DETECTED_IP="$(curl -4 -fsS --max-time 10 https://api.ipify.org 2>/dev/null || true)"
    read -r -p "平台公网 IP 或域名 [${DETECTED_IP}]：" MAPLE_DOMAIN_INPUT
    MAPLE_DOMAIN_INPUT="${MAPLE_DOMAIN_INPUT:-${DETECTED_IP}}"
  fi
  MAPLE_DOMAIN_INPUT="$(normalize_site "${MAPLE_DOMAIN_INPUT}")"
  read -r -p "管理员教师账号：" MPT_TEACHER_USERNAME_INPUT
  read -r -s -p "管理员教师密码（至少 8 位）：" MPT_TEACHER_PASSWORD_INPUT
  echo
  read -r -s -p "火山方舟 API Key（可暂时留空）：" ARK_API_KEY_INPUT
  echo
  if [[ -z "${MPT_TEACHER_USERNAME_INPUT}" || ${#MPT_TEACHER_PASSWORD_INPUT} -lt 8 ]]; then
    echo "教师账号和至少 8 位密码不能为空。" >&2
    exit 1
  fi
  umask 077
  ACCOUNT_KEY="$(openssl rand -base64 32 | tr '+/' '-_')"
  {
    printf 'MAPLE_DOMAIN=%s\n' "${MAPLE_DOMAIN_INPUT}"
    printf 'MPT_ENV=production\nMPT_HOST=0.0.0.0\nMPT_PORT=8000\n'
    printf 'MPT_TEACHER_USERNAME=%s\n' "${MPT_TEACHER_USERNAME_INPUT}"
    printf 'MPT_TEACHER_PASSWORD=%s\n' "${MPT_TEACHER_PASSWORD_INPUT}"
    printf 'MPT_CLASS_NAME=默认班级\nMPT_GROUP_COUNT=3\n'
    printf 'MPT_COOKIE_SECURE=0\n'
    printf 'MPT_ACCOUNT_ENCRYPTION_KEY=%s\n' "${ACCOUNT_KEY}"
    printf 'ARK_API_KEY=%s\n' "${ARK_API_KEY_INPUT}"
    printf 'ARK_TEXT_MODEL=doubao-seed-2-1-turbo-260628\n'
    printf 'ARK_VISION_MODEL=doubao-seed-2-1-turbo-260628\n'
    printf 'ARK_PLAN_MODEL=doubao-seed-2-1-turbo-260628\n'
    printf 'ARK_IMAGE_MODEL=doubao-seedream-5-0-260128\n'
    printf 'MPT_RESET_DATA_ONCE=\n'
  } > "${ENV_FILE}"
  chmod 600 "${ENV_FILE}"
else
  CURRENT_SITE="$(awk -F= '$1 == "MAPLE_DOMAIN" { sub(/^[^=]*=/, ""); print; exit }' "${ENV_FILE}")"
  CURRENT_SITE="${MAPLE_SITE_ADDRESS:-${CURRENT_SITE}}"
  CURRENT_SITE="$(normalize_site "${CURRENT_SITE}")"
  upsert_env "MAPLE_DOMAIN" "${CURRENT_SITE}"
  upsert_env "MPT_COOKIE_SECURE" "0"
fi

cd "${DEPLOY_DIR}"
docker compose config --quiet
retry docker compose pull caddy
docker compose up -d --build --remove-orphans
docker compose ps

MAPLE_DOMAIN="$(awk -F= '$1 == "MAPLE_DOMAIN" { sub(/^[^=]*=/, ""); print; exit }' "${ENV_FILE}")"
if curl --retry 12 --retry-all-errors --retry-delay 5 \
  -fsS "http://127.0.0.1/api/health" >/dev/null; then
  echo "部署完成：http://${MAPLE_DOMAIN}/"
else
  echo "服务健康检查未通过，请检查容器日志。" >&2
  echo "诊断命令：cd ${DEPLOY_DIR} && docker compose logs --tail=120 caddy" >&2
  exit 1
fi
