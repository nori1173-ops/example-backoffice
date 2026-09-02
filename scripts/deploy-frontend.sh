#!/usr/bin/env bash
# frontend をビルドして S3 へ同期し、CloudFront のキャッシュを無効化する（設計 13.3）。
#
# 使い方:
#   scripts/deploy-frontend.sh dev
#   scripts/deploy-frontend.sh prod
#
# Cognito の ID は SSM から、バケット名とディストリビューション ID は edge スタックの
# Outputs から取る（人が値をコピーしない）。
set -euo pipefail

PROJECT="example-backoffice"
REGION="${AWS_REGION:-ap-northeast-1}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
FRONTEND_DIR="${ROOT}/frontend"

export AWS_PAGER=""

usage() {
  cat >&2 <<'USAGE'
usage: scripts/deploy-frontend.sh {dev|prod}

前提:
  core スタック（Cognito の SSM パラメータ）と edge スタック（S3 / CloudFront）が
  デプロイ済みであること。

環境変数:
  AWS_PROFILE / AWS_REGION  AWS CLI の接続先
USAGE
}

if [ "$#" -ne 1 ]; then
  usage
  exit 1
fi

ENV_NAME="$1"
case "${ENV_NAME}" in
  dev | prod) ;;
  *)
    usage
    exit 1
    ;;
esac

STACK_NAME="${PROJECT}-edge-${ENV_NAME}"

confirm() {
  local answer=""
  read -r -p "$1 [y/N]: " answer || true
  case "${answer}" in
    y | Y | yes | YES) return 0 ;;
    *) return 1 ;;
  esac
}

require_ssm() {
  local name="$1" value=""
  if ! value="$(aws ssm get-parameter --name "${name}" --region "${REGION}" --query 'Parameter.Value' --output text 2>/dev/null)" \
    || [ -z "${value}" ] || [ "${value}" = "None" ]; then
    echo "SSM パラメータ ${name} を取得できませんでした。core スタックのデプロイを確認してください。" >&2
    return 1
  fi
  printf '%s\n' "${value}"
}

require_output() {
  local key="$1" value=""
  if ! value="$(aws cloudformation describe-stacks --stack-name "${STACK_NAME}" --region "${REGION}" --query "Stacks[0].Outputs[?OutputKey=='${key}'].OutputValue" --output text 2>/dev/null)" \
    || [ -z "${value}" ] || [ "${value}" = "None" ]; then
    echo "スタック ${STACK_NAME} の Output ${key} を取得できませんでした。scripts/deploy-edge.sh ${ENV_NAME} を先に実行してください。" >&2
    return 1
  fi
  printf '%s\n' "${value}"
}

echo "=== frontend デプロイ ==="
echo "  Env       : ${ENV_NAME}"
echo "  Stack     : ${STACK_NAME}"
echo "  Region    : ${REGION}"
echo "  Profile   : ${AWS_PROFILE:-(未設定。既定プロファイルを使う)}"

USER_POOL_ID="$(require_ssm "/${PROJECT}/${ENV_NAME}/cognito/user-pool-id")"
CLIENT_ID="$(require_ssm "/${PROJECT}/${ENV_NAME}/cognito/client-id")"
SPA_BUCKET="$(require_output SpaBucketName)"
DISTRIBUTION_ID="$(require_output DistributionId)"
SITE_URL="$(require_output SiteUrl)"

echo "  UserPoolId     : ${USER_POOL_ID}"
echo "  ClientId       : ${CLIENT_ID}"
echo "  SpaBucket      : ${SPA_BUCKET}"
echo "  DistributionId : ${DISTRIBUTION_ID}"

echo ""
echo "--- npm ci ---"
(cd "${FRONTEND_DIR}" && npm ci)

echo ""
echo "--- npm run build ---"
(
  cd "${FRONTEND_DIR}"
  VITE_COGNITO_USER_POOL_ID="${USER_POOL_ID}" \
    VITE_COGNITO_CLIENT_ID="${CLIENT_ID}" \
    npm run build
)

if [ ! -f "${FRONTEND_DIR}/dist/index.html" ]; then
  echo "${FRONTEND_DIR}/dist/index.html がありません。ビルドに失敗しています。" >&2
  exit 1
fi

if [ "${ENV_NAME}" = "prod" ]; then
  if ! confirm "prod の s3://${SPA_BUCKET}/ を dist/ の内容で同期します（--delete で不要ファイルを削除）。続行しますか?"; then
    echo "中止しました。"
    exit 1
  fi
fi

echo ""
echo "--- s3 sync ---"
aws s3 sync "${FRONTEND_DIR}/dist/" "s3://${SPA_BUCKET}/" --delete --region "${REGION}"

# index.html はキャッシュさせない（SPA のエントリポイント）。
aws s3 cp "${FRONTEND_DIR}/dist/index.html" "s3://${SPA_BUCKET}/index.html" \
  --cache-control no-cache --content-type text/html --region "${REGION}"

echo ""
echo "--- CloudFront invalidation ---"
INVALIDATION_ID="$(aws cloudfront create-invalidation --distribution-id "${DISTRIBUTION_ID}" --paths '/*' --region "${REGION}" --query 'Invalidation.Id' --output text)"
echo "  invalidation: ${INVALIDATION_ID}"

echo ""
echo "デプロイ完了: ${SITE_URL}"
