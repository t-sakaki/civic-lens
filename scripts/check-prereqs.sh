#!/usr/bin/env bash
# GCP ハッカソン前提条件チェッカー
set -u
echo "=== 前提条件チェック ==="

check() {
  if command -v "$1" >/dev/null 2>&1; then
    echo "✅ $1: $(command -v "$1")"
    "$1" --version 2>/dev/null | head -1 || true
  else
    echo "❌ $1: 未インストール"
  fi
}

check gcloud
check docker
check node
check python3
check git
check curl
check jq

echo ""
echo "=== GCP認証状況 ==="
if command -v gcloud >/dev/null 2>&1; then
  gcloud auth list 2>&1 | head -5
  echo "---"
  gcloud config get project 2>&1
fi

echo ""
echo "=== Docker状態 ==="
docker info 2>&1 | head -5 || echo "Docker起動してない可能性"

echo ""
echo "=== 環境変数（要APIキー） ==="
echo "GOOGLE_API_KEY: ${GOOGLE_API_KEY:+設定済}"
echo "GOOGLE_CLOUD_PROJECT: ${GOOGLE_CLOUD_PROJECT:-未設定}"
