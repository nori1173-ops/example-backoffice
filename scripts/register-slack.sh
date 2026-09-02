#!/usr/bin/env bash
# Slack 通知の秘密（Bot token / Incoming Webhook URL）を SSM に登録する（設計 13.4 の手順 2）。
#
# 使い方:
#   scripts/register-slack.sh dev bot        Bot token を登録する
#   scripts/register-slack.sh dev webhook    Webhook URL を登録する
#   DRY_RUN=1 scripts/register-slack.sh dev bot
#
# 値は引数では受け取らない（シェル履歴に秘密を残さないため）。必ず標準入力から
# 非エコーで読み取り、標準出力にもログにも出さない。
#
# 投稿方式（/shared/slack/mode）はこのスクリプトでは書かない。CloudFormation が
# shared/template.yaml の SlackModeParameter として管理しており、先に put-parameter で
# 作ってしまうと shared スタックの作成が AlreadyExists で失敗する。方式を変えるときは
# shared/samconfig.toml の SlackMode を直して shared を再デプロイする。
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
投稿方式（/shared/slack/mode）は shared の samconfig（SlackMode）で決まる。

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
    ;;
  webhook)
    PARAM_NAME="/${PROJECT}/${ENV_NAME}/shared/slack/webhook-url"
    PARAM_DESC="Slack Incoming Webhook URL"
    PROMPT="Slack incoming webhook URL: "
    ;;
  *)
    usage
    exit 1
    ;;
esac

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
echo "  Mode      : 変更しない（shared の samconfig の SlackMode が決める）"
echo "  Region    : ${REGION}"
echo "  Profile   : ${AWS_PROFILE:-(未設定。既定プロファイルを使う)}"

if [ "${DRY_RUN}" = "1" ]; then
  echo "[DRY RUN] 上記パラメータを SecureString で登録します"
  echo "[DRY RUN] /shared/slack/mode は書きません（CloudFormation の管理下）"
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

echo ""
echo "投稿方式（/shared/slack/mode）は変更していません。これは shared スタックが"
echo "shared/samconfig.toml の SlackMode から作る CloudFormation 管理のパラメータで、"
echo "このスクリプトからは書かない（先に作ると shared の作成が AlreadyExists で失敗する）。"
echo "方式を切り替えるときは samconfig の SlackMode を直して shared を再デプロイする:"
echo "  cd shared && sam build --use-container && sam deploy --config-env $([ "${ENV_NAME}" = "prod" ] && echo production || echo default)"
echo "秘密の登録は shared のデプロイの前後どちらでもよい（notifier は実行時に読む）。"
