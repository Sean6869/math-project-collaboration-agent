#!/usr/bin/env bash
set -Eeuo pipefail

# 首次部署（腾讯云公网 IP / HTTP）。
# 用法：sudo bash deploy/tencent/first-deploy-ip.sh

APP_DIR="${MAPLE_APP_DIR:-/opt/maple/app}"
REPO_URL="${MAPLE_REPO_URL:-https://github.com/Sean6869/math-project-collaboration-agent.git}"
BRANCH="${MAPLE_BRANCH:-main}"
PUBLIC_IP="${MAPLE_PUBLIC_IP:-175.178.183.31}"

if [[ "${EUID}" -ne 0 ]]; then
  echo "请使用 sudo 运行：sudo bash deploy/tencent/first-deploy-ip.sh" >&2
  exit 1
fi

export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y ca-certificates curl git docker.io docker-compose-v2
systemctl enable --now docker

# 腾讯云服务器直连 Docker Hub（尤其解析到 IPv6 时）可能超时。
# 首次部署先启用腾讯云镜像加速，避免拉取 Caddy/Python 镜像失败。
mkdir -p /etc/docker
if [[ ! -s /etc/docker/daemon.json ]]; then
  cat > /etc/docker/daemon.json <<'EOF'
{
  "registry-mirrors": ["https://mirror.ccs.tencentyun.com"]
}
EOF
elif ! grep -q 'mirror.ccs.tencentyun.com' /etc/docker/daemon.json; then
  cp /etc/docker/daemon.json "/etc/docker/daemon.json.bak.$(date +%Y%m%d%H%M%S)"
  echo "检测到已有 /etc/docker/daemon.json，未自动覆盖。" >&2
  echo "请确保其中配置 registry-mirrors：https://mirror.ccs.tencentyun.com" >&2
fi
systemctl daemon-reload
systemctl restart docker

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
mkdir -p "${DEPLOY_DIR}"

if [[ -f "${ENV_FILE}" ]]; then
  echo "已存在 ${ENV_FILE}，保留现有配置。"
else
  read -r -p "管理员教师账号 [maple_admin]：" username
  username="${username:-maple_admin}"
  read -r -s -p "管理员教师密码（至少 8 位）：" password
  echo
  if [[ -z "${password}" || ${#password} -lt 8 ]]; then
    echo "密码至少需要 8 位。" >&2
    exit 1
  fi
  read -r -s -p "火山方舟 API Key（可留空）：" ark_key
  echo
  umask 077
  cat > "${ENV_FILE}" <<EOF
MAPLE_DOMAIN=http://${PUBLIC_IP}
MPT_ENV=production
MPT_HOST=0.0.0.0
MPT_PORT=8000
MPT_COOKIE_SECURE=0
MPT_TEACHER_USERNAME=${username}
MPT_TEACHER_PASSWORD=${password}
MPT_CLASS_NAME=默认班级
MPT_GROUP_COUNT=3
MPT_ACCOUNT_ENCRYPTION_KEY=
MPT_RESET_DATA_ONCE=
ARK_API_KEY=${ark_key}
ARK_TEXT_MODEL=doubao-seed-2-1-turbo-260628
ARK_VISION_MODEL=doubao-seed-2-1-turbo-260628
ARK_PLAN_MODEL=doubao-seed-2-1-turbo-260628
ARK_IMAGE_MODEL=doubao-seedream-5-0-260128
EOF
  chmod 600 "${ENV_FILE}"
fi

cd "${DEPLOY_DIR}"
docker compose config --quiet
docker compose up -d --build --remove-orphans
docker compose ps

echo
echo "部署完成： http://${PUBLIC_IP}/"
echo "健康检查： curl -fsS http://${PUBLIC_IP}/api/health"
echo "注意：Caddy 对外使用 80 端口，8000 仅用于容器内部通信。"
