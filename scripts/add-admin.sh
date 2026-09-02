#!/usr/bin/env bash
# Cognito ユーザーを admin グループに追加する（設計 6.3 / 13.4）。
#
# 使い方:
#   scripts/add-admin.sh dev devA@example.co.jp
#   scripts/add-admin.sh prod devA@example.co.jp
#
# admin が 1 人も居ないと設定画面（PUT /settings / POST /prices / GET /audits）が
# 読み取り専用のままで初期設定ができない。リリース当日の最初の 1 人はこれで付ける。
set -euo pipefail

PROJECT="example-backoffice"
REGION="${AWS_REGION:-ap-northeast-1}"
GROUP="admin"

export AWS_PAGER=""

usage() {
  cat >&2 <<'USAGE'
usage: scripts/add-admin.sh {dev|prod} {email}

前提:
  対象ユーザーが自分でサインアップ済みであること（セルフサインアップのみ。設計 6.2）。

環境変数:
  AWS_PROFILE / AWS_REGION  AWS CLI の接続先
USAGE
}

if [ "$#" -ne 2 ]; then
  usage
  exit 1
fi

ENV_NAME="$1"
EMAIL="$2"
case "${ENV_NAME}" in
  dev | prod) ;;
  *)
    usage
    exit 1
    ;;
esac

case "${EMAIL}" in
  *@example.co.jp) ;;
  *)
    echo "サインアップは @example.co.jp のみ許可されています（設計 6.2）: ${EMAIL}" >&2
    exit 1
    ;;
esac

confirm() {
  local answer=""
  read -r -p "$1 [y/N]: " answer || true
  case "${answer}" in
    y | Y | yes | YES) return 0 ;;
    *) return 1 ;;
  esac
}

USER_POOL_PARAM="/${PROJECT}/${ENV_NAME}/cognito/user-pool-id"
if ! USER_POOL_ID="$(aws ssm get-parameter --name "${USER_POOL_PARAM}" --region "${REGION}" --query 'Parameter.Value' --output text 2>/dev/null)" \
  || [ -z "${USER_POOL_ID}" ] || [ "${USER_POOL_ID}" = "None" ]; then
  echo "SSM パラメータ ${USER_POOL_PARAM} を取得できませんでした。core スタックのデプロイを確認してください。" >&2
  exit 1
fi

echo "=== admin グループへの追加 ==="
echo "  Env       : ${ENV_NAME}"
echo "  UserPool  : ${USER_POOL_ID}"
echo "  User      : ${EMAIL}"
echo "  Region    : ${REGION}"

if ! STATUS="$(aws cognito-idp admin-get-user --user-pool-id "${USER_POOL_ID}" --username "${EMAIL}" --region "${REGION}" --query 'UserStatus' --output text 2>/dev/null)"; then
  echo "ユーザー ${EMAIL} が見つかりません。先に本人にサインアップしてもらってください。" >&2
  exit 1
fi
echo "  Status    : ${STATUS}"

if [ "${ENV_NAME}" = "prod" ]; then
  if ! confirm "prod の ${EMAIL} に admin を付与します。続行しますか?"; then
    echo "中止しました。"
    exit 1
  fi
fi

aws cognito-idp admin-add-user-to-group \
  --user-pool-id "${USER_POOL_ID}" \
  --username "${EMAIL}" \
  --group-name "${GROUP}" \
  --region "${REGION}"

echo ""
echo "現在のグループ:"
aws cognito-idp admin-list-groups-for-user --user-pool-id "${USER_POOL_ID}" --username "${EMAIL}" --region "${REGION}" \
  --query 'Groups[].GroupName' --output text

echo ""
echo "反映は次回サインイン（ID トークン再取得）から。既存セッションは最大 60 分そのまま。"
