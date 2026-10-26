#!/usr/bin/env bash
set -Eeuo pipefail

APP_DIR="${MAPLE_APP_DIR:-/opt/maple/app}"
BRANCH="${MAPLE_BRANCH:-main}"
if [[ "${EUID}" -ne 0 ]]; then
  echo "请使用 sudo 运行此脚本。" >&2
  exit 1
fi
git -C "${APP_DIR}" fetch origin "${BRANCH}"
git -C "${APP_DIR}" checkout "${BRANCH}"
git -C "${APP_DIR}" pull --ff-only origin "${BRANCH}"
cd "${APP_DIR}/deploy/tencent"
docker compose config --quiet
docker compose up -d --build --remove-orphans
docker image prune -f
docker compose ps
