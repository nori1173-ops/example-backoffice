#!/usr/bin/env bash
# origin-verify 用の共有秘密を SSM に SecureString で登録する（設計 5.5 / 13.4）。
#
# 使い方:
#   scripts/init-secrets.sh dev
#   ROTATE=1 scripts/init-secrets.sh dev     既存値をローテーションする
#   DRY_RUN=1 scripts/init-secrets.sh dev    AWS を呼ばずに実行内容だけ表示する
#
# 生成した値は標準出力にもログにも出さない。AWS CLI へは --cli-input-json で渡すため
# 値がコマンドライン引数（ps の出力）に載らない。
set -euo pipefail
set +x

PROJECT="example-backoffice"
REGION="${AWS_REGION:-ap-northeast-1}"
DRY_RUN="${DRY_RUN:-0}"
ROTATE="${ROTATE:-0}"

export AWS_PAGER=""

usage() {
  cat >&2 <<'USAGE'
usage: scripts/init-secrets.sh {dev|prod}

環境変数:
  ROTATE=1   既存の秘密を上書きする（未指定なら既存値をそのまま残す）
  DRY_RUN=1  AWS を呼ばずに実行内容だけ表示する
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

PARAM_NAME="/${PROJECT}/${ENV_NAME}/edge/origin-verify-secret"

TMP_JSON=""
cleanup() {
  if [ -n "${TMP_JSON}" ]; then
    rm -f "${TMP_JSON}"
  fi
}
trap cleanup EXIT

confirm() {
  local answer=""
  read -r -p "$1 [y/N]: " answer || true
  case "${answer}" in
    y | Y | yes | YES) return 0 ;;
    *) return 1 ;;
  esac
}

# put_parameter <値> <名前> <型> <true|false: 上書き> <説明>
# 値は関数の位置パラメータ（シェル内部）と環境変数だけで受け渡し、外部コマンドの
# 引数には載せない。
put_parameter() {
  local value="$1" name="$2" ptype="$3" overwrite="$4" description="$5"
  local version=""
  TMP_JSON="$(mktemp)"
  chmod 600 "${TMP_JSON}"
  PARAM_VALUE="${value}" python3 -c '
import json
import os
import sys

doc = {
    "Name": sys.argv[1],
    "Type": sys.argv[2],
    "Value": os.environ["PARAM_VALUE"],
    "Description": sys.argv[4],
}
if sys.argv[3] == "true":
    doc["Overwrite"] = True
json.dump(doc, sys.stdout)
' "${name}" "${ptype}" "${overwrite}" "${description}" >"${TMP_JSON}"
  version="$(aws ssm put-parameter --cli-input-json "file://${TMP_JSON}" --region "${REGION}" --query Version --output text)"
  rm -f "${TMP_JSON}"
  TMP_JSON=""
  echo "  -> 登録しました: ${name} (version ${version})"
}

echo "=== origin-verify 秘密の登録 ==="
echo "  Env       : ${ENV_NAME}"
echo "  Parameter : ${PARAM_NAME}"
echo "  Region    : ${REGION}"
echo "  Profile   : ${AWS_PROFILE:-(未設定。既定プロファイルを使う)}"
echo "  Rotate    : ${ROTATE}"

if [ "${DRY_RUN}" = "1" ]; then
  echo "[DRY RUN] openssl rand -base64 48 で秘密を生成し、上記パラメータへ SecureString で登録します"
  echo "[DRY RUN] AWS は呼び出しませんでした"
  exit 0
fi

if aws ssm get-parameter --name "${PARAM_NAME}" --region "${REGION}" --query 'Parameter.Name' --output text >/dev/null 2>&1; then
  EXISTS=1
else
  EXISTS=0
fi

if [ "${EXISTS}" = "1" ] && [ "${ROTATE}" != "1" ]; then
  echo "既に登録済みです。値は変更しません。"
  echo "ローテーションする場合: ROTATE=1 scripts/init-secrets.sh ${ENV_NAME}"
  exit 0
fi

OVERWRITE=false
if [ "${EXISTS}" = "1" ]; then
  OVERWRITE=true
  echo "警告: 既存の秘密を上書きします。上書き後は必ず scripts/deploy-edge.sh ${ENV_NAME} を実行して"
  echo "      CloudFront 側のカスタムヘッダを新しい値に更新してください（更新するまで API が 403 になります）。"
  if ! confirm "${ENV_NAME} の ${PARAM_NAME} を上書きしますか?"; then
    echo "中止しました。"
    exit 1
  fi
fi

# tr -d は base64 の折り返し改行対策（48 バイトなら 1 行だがバイト数を変えても壊れないようにする）
SECRET="$(openssl rand -base64 48 | tr -d '\n')"
put_parameter "${SECRET}" "${PARAM_NAME}" SecureString "${OVERWRITE}" \
  "CloudFront から HTTP API へ渡す origin-verify 共有秘密（scripts/init-secrets.sh が生成）"
SECRET=""

echo ""
echo "次の手順: scripts/deploy-edge.sh ${ENV_NAME}"
