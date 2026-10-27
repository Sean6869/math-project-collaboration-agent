#!/usr/bin/env bash
set -Eeuo pipefail

# 腾讯云 Ubuntu 首次部署，使用 HTTP 提供服务。
# 不传地址时会提示填写服务器公网 IP。

APP_DIR="${MAPLE_APP_DIR:-/opt/maple/app}"
REPO_URL="${MAPLE_REPO_URL:-https://github.com/Sean6869/math-project-collaboration-agent.git}"
BRANCH="${MAPLE_BRANCH:-main}"
PUBLIC_IP="${1:-${MAPLE_PUBLIC_IP:-}}"

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
  echo "请使用 sudo 运行：sudo bash deploy/tencent/first-deploy-ip.sh" >&2
  exit 1
fi
if [[ -z "${PUBLIC_IP}" && -r /dev/tty ]]; then
  read -r -p "请输入服务器公网 IPv4：" PUBLIC_IP </dev/tty
fi
if [[ ! "${PUBLIC_IP}" =~ ^[0-9]+(\.[0-9]+){3}$ ]]; then
  echo "公网 IPv4 格式错误；请重新运行并填写服务器公网 IP。" >&2
  exit 1
fi

export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y ca-certificates curl git docker.io
if ! docker compose version >/dev/null 2>&1; then
  apt-get install -y docker-compose-v2 || apt-get install -y docker-compose-plugin
fi
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
  retry git -C "${APP_DIR}" fetch origin "${BRANCH}"
  git -C "${APP_DIR}" checkout "${BRANCH}"
  retry git -C "${APP_DIR}" pull --ff-only origin "${BRANCH}"
else
  retry git clone --branch "${BRANCH}" --single-branch "${REPO_URL}" "${APP_DIR}"
fi

export MAPLE_SITE_ADDRESS="${PUBLIC_IP}"
export MAPLE_SKIP_SYSTEM_SETUP=1
export MAPLE_SKIP_GIT_SYNC=1

# 本脚本通常通过 curl | sudo bash 执行，此时标准输入属于下载管道且已到 EOF。
# 把后续账号、密码和 API Key 的交互输入重新连接到控制终端。
if [[ -r /dev/tty ]]; then
  exec bash "${APP_DIR}/deploy/tencent/deploy.sh" </dev/tty
fi

echo "当前会话没有可用终端，无法安全读取首次部署参数。" >&2
echo "请改为登录服务器后执行：sudo bash ${APP_DIR}/deploy/tencent/deploy.sh" >&2
exit 1
