#!/usr/bin/env bash
# edge スタックをデプロイする（設計 13.3）。
#
# 使い方:
#   scripts/deploy-edge.sh dev
#   scripts/deploy-edge.sh prod
#
# SSM から origin-verify 秘密を復号取得し、パーミッション 600 の一時 samconfig 経由で
# sam deploy へ渡す（コマンドライン引数に載せないため ps から見えない。SEC-15）。
# sam の出力は秘密を伏せてから表示する。
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

# --parameter-overrides で渡すと秘密が sam プロセスのコマンドライン（/proc/*/cmdline は
# 同一ホストの他ユーザーからも読める）に載る。パーミッション 600 の一時 samconfig に
# 書いて --config-file で渡し、終了時に必ず消す（SEC-15）。
umask 077
TMP_CONFIG="$(mktemp -t example-backoffice-edge-samconfig.XXXXXXXX.toml)"
trap 'rm -f "${TMP_CONFIG}"' EXIT

OVERRIDES="${OVERRIDES}" python3 -c '
import os
import sys
import tomllib


def dump(value):
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return repr(value)
    if isinstance(value, list):
        return "[" + ", ".join(dump(v) for v in value) + "]"
    return "\"" + str(value).replace("\\", "\\\\").replace("\"", "\\\"") + "\""


source, config_env, destination = sys.argv[1:4]
with open(source, "rb") as f:
    section = tomllib.load(f)[config_env]

lines = ["version = 0.1", ""]
for command, body in section.items():
    parameters = dict(body.get("parameters") or {})
    if command == "deploy":
        parameters["parameter_overrides"] = os.environ["OVERRIDES"]
    lines.append(f"[{config_env}.{command}.parameters]")
    lines.extend(f"{key} = {dump(value)}" for key, value in parameters.items())
    lines.append("")

with open(destination, "w", encoding="utf-8") as f:
    f.write("\n".join(lines))
' "${EDGE_DIR}/samconfig.toml" "${CFG_ENV}" "${TMP_CONFIG}"
OVERRIDES=""

# edge テンプレートは Lambda コードを持たない（CloudFront Function はインライン）ため
# sam build は不要。
#
# sam deploy は "Deploying with following values" の banner に parameter_overrides を
# そのまま印字し（samcli/commands/deploy/utils.py の print_deploy_args）、非 guided デプロイでは
# NoEcho パラメータもマスクしない。端末とログに残さないよう、秘密を伏せてから表示する。
(
  cd "${EDGE_DIR}"
  sam deploy --config-file "${TMP_CONFIG}" --config-env "${CFG_ENV}" --no-fail-on-empty-changeset 2>&1
) | SECRET_MASK="${SECRET}" python3 -c '
import os
import sys

# 先に置換してから、末尾が秘密の先頭と一致する分だけ次の塊へ繰り越す。順序が逆だと
# 読み取り境界が秘密の終端に落ちたとき、秘密自身の末尾が「次に来るかもしれない秘密の先頭」と
# 誤認されて置換の対象から外れ、秘密が丸ごと素通りする。完全一致を先に潰しておけば
# 残る部分一致は必ずバッファの末尾にあり、境界の位置に依存しない。
# 一致しなければ 1 バイトも溜めないので、改行を伴わない変更セットの確認プロンプトも
# そのまま表示される。
secret = (os.environ.get("SECRET_MASK") or "").encode()
out = sys.stdout.buffer


def carry_over(buffered):
    for size in range(min(len(secret) - 1, len(buffered)), 0, -1):
        if secret.startswith(buffered[-size:]):
            return size
    return 0


tail = b""
while chunk := os.read(0, 65536):
    buffered = (tail + chunk).replace(secret, b"***") if secret else tail + chunk
    held = carry_over(buffered) if secret else 0
    boundary = len(buffered) - held
    tail = buffered[boundary:]
    out.write(buffered[:boundary])
    out.flush()
if tail:
    out.write(tail)
    out.flush()
'
SECRET=""

SITE_URL="$(aws cloudformation describe-stacks --stack-name "${PROJECT}-edge-${ENV_NAME}" --region "${REGION}" --query "Stacks[0].Outputs[?OutputKey=='SiteUrl'].OutputValue" --output text || true)"
echo ""
echo "デプロイ完了: ${SITE_URL}"
echo "次の手順: scripts/deploy-frontend.sh ${ENV_NAME}"
