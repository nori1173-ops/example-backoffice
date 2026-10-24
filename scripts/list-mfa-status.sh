#!/usr/bin/env bash
# Cognito ユーザーの MFA 設定状況を一覧する（設計 6.4）。
#
# 使い方:
#   scripts/list-mfa-status.sh dev
#   scripts/list-mfa-status.sh prod
#
# UserMFASettingList が空のユーザー（TOTP 未設定）を洗い出す。
set -euo pipefail

PROJECT="example-backoffice"
REGION="${AWS_REGION:-ap-northeast-1}"

export AWS_PAGER=""

usage() {
  cat >&2 <<'USAGE'
usage: scripts/list-mfa-status.sh {dev|prod}

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

USER_POOL_PARAM="/${PROJECT}/${ENV_NAME}/cognito/user-pool-id"
if ! USER_POOL_ID="$(aws ssm get-parameter --name "${USER_POOL_PARAM}" --region "${REGION}" --query 'Parameter.Value' --output text 2>/dev/null)"; then
  echo "SSM パラメータ ${USER_POOL_PARAM} を取得できませんでした。core スタックのデプロイを確認してください。" >&2
  exit 1
fi

echo "=== Cognito MFA 設定状況 ==="
echo "  Env       : ${ENV_NAME}"
echo "  UserPool  : ${USER_POOL_ID}"
echo "  Region    : ${REGION}"
echo ""
printf '%-40s %-12s %s\n' "USERNAME" "STATUS" "MFA"

total=0
without_mfa=0

while read -r username; do
  [ -n "${username}" ] || continue
  total=$((total + 1))
  info="$(aws cognito-idp admin-get-user --user-pool-id "${USER_POOL_ID}" --username "${username}" --region "${REGION}" --query "[UserStatus, join(',', UserMFASettingList || \`[]\`)]" --output text)"
  status="$(printf '%s' "${info}" | cut -f1)"
  mfa="$(printf '%s' "${info}" | cut -f2)"
  if [ -z "${mfa}" ] || [ "${mfa}" = "None" ]; then
    mfa="(未設定)"
    without_mfa=$((without_mfa + 1))
  fi
  printf '%-40s %-12s %s\n' "${username}" "${status}" "${mfa}"
done < <(aws cognito-idp list-users --user-pool-id "${USER_POOL_ID}" --region "${REGION}" --query 'Users[].Username' --output text | tr '\t' '\n')

echo ""
if [ "${total}" -eq 0 ]; then
  echo "ユーザーが 1 人も取得できませんでした（list-users の失敗も疑ってください）。"
  exit 0
fi
echo "ユーザー数: ${total} / MFA 未設定: ${without_mfa}"
if [ "${without_mfa}" -gt 0 ]; then
  echo "MFA は任意のため未設定でも利用できる（設計 6.4）。必須へ戻す前にはこの件数をゼロにする。"
fi
