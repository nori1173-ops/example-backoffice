#!/usr/bin/env bash
# edge スタックをデプロイする（設計 13.3）。
#
# 使い方:
#   scripts/deploy-edge.sh dev
#   scripts/deploy-edge.sh prod
#
# SSM から origin-verify 秘密を復号取得し、samconfig の parameter_overrides に
# 連結して sam deploy へ渡す。取得した値は標準出力にもログにも出さない。
set -euo pipefail
set +x

PROJECT="example-backoffice"
REGION="${AWS_REGION:-ap-northeast-1}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
EDGE_DIR="${ROOT}/infra/edge"

export AWS_PAGER=""

usage() {
  cat >&2 <<'USAGE'
usage: scripts/deploy-edge.sh {dev|prod}

前提:
  scripts/init-secrets.sh {env} で /example-backoffice/{env}/edge/origin-verify-secret
  が登録済みであること。

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
  dev)
    CFG_ENV="default"
    ;;
  prod)
    CFG_ENV="production"
    ;;
  *)
    usage
    exit 1
    ;;
esac

SECRET_PARAM="/${PROJECT}/${ENV_NAME}/edge/origin-verify-secret"
API_DOMAIN_PARAM="/${PROJECT}/${ENV_NAME}/apps/saas-a-billing/api-domain"

echo "=== edge デプロイ ==="
echo "  Env       : ${ENV_NAME} (--config-env ${CFG_ENV})"
echo "  Directory : ${EDGE_DIR}"
echo "  Region    : ${REGION}"
echo "  Profile   : ${AWS_PROFILE:-(未設定。既定プロファイルを使う)}"

if ! SECRET="$(aws ssm get-parameter --name "${SECRET_PARAM}" --with-decryption --region "${REGION}" --query 'Parameter.Value' --output text 2>/dev/null)"; then
  echo "${SECRET_PARAM} を取得できませんでした。先に scripts/init-secrets.sh ${ENV_NAME} を実行してください。" >&2
  exit 1
fi
if [ -z "${SECRET}" ] || [ "${SECRET}" = "None" ]; then
  echo "${SECRET_PARAM} が空です。先に scripts/init-secrets.sh ${ENV_NAME} を実行してください。" >&2
  exit 1
fi
echo "  OriginVerifySecret : SSM から取得しました（値は表示しない）"

API_DOMAIN="$(aws ssm get-parameter --name "${API_DOMAIN_PARAM}" --region "${REGION}" --query 'Parameter.Value' --output text 2>/dev/null || true)"
if [ "${API_DOMAIN}" = "None" ]; then
  API_DOMAIN=""
fi
if [ -n "${API_DOMAIN}" ]; then
  echo "  SaasABillingApiDomain : ${API_DOMAIN}"
else
  echo "  SaasABillingApiDomain : 未登録（${API_DOMAIN_PARAM}）。API 経路なしでデプロイします"
fi

# sam deploy --parameter-overrides はコマンドラインの指定が samconfig の同名キーを
# 丸ごと置き換える（追記ではない）。samconfig 側の値を読み出して前置する。
BASE_OVERRIDES="$(python3 -c '
import sys
import tomllib

with open(sys.argv[1], "rb") as f:
    config = tomllib.load(f)
print(config[sys.argv[2]]["deploy"]["parameters"].get("parameter_overrides", ""))
' "${EDGE_DIR}/samconfig.toml" "${CFG_ENV}")"

if [ -z "${BASE_OVERRIDES}" ]; then
  echo "${EDGE_DIR}/samconfig.toml の [${CFG_ENV}.deploy.parameters] に parameter_overrides がありません。" >&2
  exit 1
fi

OVERRIDES="${BASE_OVERRIDES} OriginVerifySecret=${SECRET}"
if [ -n "${API_DOMAIN}" ]; then
  OVERRIDES="${OVERRIDES} SaasABillingApiDomain=${API_DOMAIN}"
fi
SECRET=""

# edge テンプレートは Lambda コードを持たない（CloudFront Function はインライン）ため
# sam build は不要。
#
# 注意: sam deploy は "Deploying with following values" の banner に parameter_overrides を
# そのまま印字する（samcli/commands/deploy/utils.py の print_deploy_args）。非 guided デプロイでは
# NoEcho パラメータもマスクされないため、OriginVerifySecret の値が端末に表示される。
# デプロイログをそのまま保存・共有しないこと（CloudFormation のコンソール上は NoEcho で
# マスクされる）。
(
  cd "${EDGE_DIR}"
  sam deploy --config-env "${CFG_ENV}" --no-fail-on-empty-changeset --parameter-overrides "${OVERRIDES}"
)
OVERRIDES=""

SITE_URL="$(aws cloudformation describe-stacks --stack-name "${PROJECT}-edge-${ENV_NAME}" --region "${REGION}" --query "Stacks[0].Outputs[?OutputKey=='SiteUrl'].OutputValue" --output text || true)"
echo ""
echo "デプロイ完了: ${SITE_URL}"
echo "次の手順: scripts/deploy-frontend.sh ${ENV_NAME}"
