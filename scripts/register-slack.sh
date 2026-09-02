#!/usr/bin/env bash
# Slack 通知の秘密（Bot token / Incoming Webhook URL）を SSM に登録する（設計 13.4 の手順 2）。
#
# 使い方:
#   scripts/register-slack.sh dev bot        Bot token を登録し mode を bot にする
#   scripts/register-slack.sh dev webhook    Webhook URL を登録する（mode は変更しない）
#   DRY_RUN=1 scripts/register-slack.sh dev bot
#
# 値は引数では受け取らない（シェル履歴に秘密を残さないため）。必ず標準入力から
# 非エコーで読み取り、標準出力にもログにも出さない。
set -euo pipefail
set +x

PROJECT="example-backoffice"
REGION="${AWS_REGION:-ap-northeast-1}"
DRY_RUN="${DRY_RUN:-0}"

export AWS_PAGER=""

usage() {
  cat >&2 <<'USAGE'
usage: scripts/register-slack.sh {dev|prod} {bot|webhook}

Bot token / Webhook URL は引数では渡せない。実行後にプロンプトから入力する。

環境変数:
  DRY_RUN=1  AWS を呼ばずに実行内容だけ表示する（入力プロンプトも出さない）
  AWS_PROFILE / AWS_REGION  AWS CLI の接続先
USAGE
}

if [ "$#" -ne 2 ]; then
  usage
  exit 1
fi

ENV_NAME="$1"
KIND="$2"

case "${ENV_NAME}" in
  dev | prod) ;;
  *)
    usage
    exit 1
    ;;
esac

case "${KIND}" in
  bot)
    PARAM_NAME="/${PROJECT}/${ENV_NAME}/shared/slack/bot-token"
    PARAM_DESC="Slack Bot token（chat:write / chat:write.public）"
    PROMPT="Slack bot token (xoxb-...): "
    MODE="bot"
    ;;
  webhook)
    PARAM_NAME="/${PROJECT}/${ENV_NAME}/shared/slack/webhook-url"
    PARAM_DESC="Slack Incoming Webhook URL"
    PROMPT="Slack incoming webhook URL: "
    MODE="webhook"
    ;;
  *)
    usage
    exit 1
    ;;
esac

MODE_PARAM_NAME="/${PROJECT}/${ENV_NAME}/shared/slack/mode"

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
# 引数には載せない。AWS CLI v1 の cli_follow_urlparam（--value が URL のとき中身を
# 取得してしまう挙動）も --cli-input-json なら踏まない。
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

echo "=== Slack 通知の秘密を登録 ==="
echo "  Env       : ${ENV_NAME}"
echo "  Kind      : ${KIND}"
echo "  Parameter : ${PARAM_NAME}"
if [ "${KIND}" = "bot" ]; then
  echo "  Mode      : ${MODE_PARAM_NAME} = ${MODE}"
else
  echo "  Mode      : 変更しない"
fi
echo "  Region    : ${REGION}"
echo "  Profile   : ${AWS_PROFILE:-(未設定。既定プロファイルを使う)}"

if [ "${DRY_RUN}" = "1" ]; then
  echo "[DRY RUN] 上記パラメータを SecureString で登録します"
  if [ "${KIND}" = "bot" ]; then
    echo "[DRY RUN] mode を bot に更新します"
  fi
  echo "[DRY RUN] AWS は呼び出しませんでした"
  exit 0
fi

if [ "${ENV_NAME}" = "prod" ]; then
  if ! confirm "prod の Slack 設定を上書きします。続行しますか?"; then
    echo "中止しました。"
    exit 1
  fi
fi

VALUE=""
read -r -s -p "${PROMPT}" VALUE || true
echo ""
if [ -z "${VALUE}" ]; then
  echo "値が空です。中止しました。" >&2
  exit 1
fi

put_parameter "${VALUE}" "${PARAM_NAME}" SecureString true "${PARAM_DESC}"
VALUE=""

if [ "${KIND}" = "bot" ]; then
  put_parameter "${MODE}" "${MODE_PARAM_NAME}" String true "notifier の Slack 投稿方式 (bot / webhook)"

  echo ""
  echo "次の手順:"
  echo "  1. shared/samconfig.toml の parameter_overrides の SlackMode を ${MODE} に直す"
  echo "     （直さないと次の shared デプロイで SSM の mode が samconfig の値に戻る）"
  echo "  2. cd shared && sam build --use-container && sam deploy --config-env $([ "${ENV_NAME}" = "prod" ] && echo production || echo default)"
else
  echo ""
  echo "mode は変更していません（webhook URL のみ更新）。投稿方式を webhook に切り替える場合は"
  echo "shared/samconfig.toml の SlackMode を webhook にして shared を再デプロイする。"
fi
