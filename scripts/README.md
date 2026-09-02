# scripts

example-backoffice の秘密登録・デプロイ・運用スクリプト。すべて `bash` で書かれており、
リポジトリのルートから実行する。AWS の接続先は環境変数（`AWS_PROFILE` / `AWS_REGION`）で
指定する（`samconfig.toml` にプロファイルは書かない）。

- `{env}` は `dev` または `prod`。それ以外を渡すと usage を表示して終了コード 1。
- `sam deploy` の `--config-env` は `dev` → `default`、`prod` → `production` に対応する。
- 秘密（origin-verify・Slack bot token・Webhook URL・パスワード・ID トークン）は
  引数・標準出力・ログに出さない。`jq` は使わない（AWS CLI の `--query` と `python3`）。

## 実行順（初回セットアップ）

```bash
export AWS_PROFILE=default

# 1. origin-verify の共有秘密を登録する（設計 13.4 の手順 1）
scripts/init-secrets.sh dev

# 2. Slack の秘密を登録する（設計 13.4 の手順 2。Bot token が無ければ webhook で先に進める）
scripts/register-slack.sh dev webhook

# 3. 全スタックを依存順にデプロイする
scripts/deploy-all.sh dev
```

## 一覧

| スクリプト | 引数 | 用途 |
| --- | --- | --- |
| `init-secrets.sh` | `{env}` | `/example-backoffice/{env}/edge/origin-verify-secret` を SecureString で登録する |
| `register-slack.sh` | `{env} {bot\|webhook}` | Slack の Bot token / Webhook URL を登録する（`bot` のときのみ `/shared/slack/mode` も更新） |
| `deploy-edge.sh` | `{env}` | SSM から秘密を取り出して edge スタックをデプロイする |
| `deploy-frontend.sh` | `{env}` | frontend をビルドして S3 へ同期し CloudFront を無効化する |
| `deploy-all.sh` | `{env}` | shared → core → apps/\* → edge → frontend の順に一括デプロイする |
| `list-mfa-status.sh` | `{env}` | Cognito ユーザーの MFA 設定状況を一覧する |
| `get_id_token.py` | `{env} {email} [--totp-code CODE]` | Cognito の ID トークンを標準出力へ出す |
| `validate-templates.sh` | なし | 全テンプレートに `sam validate` と `cfn-lint` をかける |

## init-secrets.sh

```bash
scripts/init-secrets.sh dev            # 未登録なら生成して登録、登録済みなら何もしない
ROTATE=1 scripts/init-secrets.sh dev   # 既存値をローテーションする（確認プロンプトあり）
DRY_RUN=1 scripts/init-secrets.sh dev  # AWS を呼ばずに実行内容だけ表示する
```

`openssl rand -base64 48` で秘密を生成し、`--cli-input-json` 経由で SSM に登録する
（値がコマンドライン引数に載らないため `ps` から見えない）。既存値があるときは
`ROTATE=1` を付けない限り上書きしない。ローテーション後は必ず `deploy-edge.sh` を
実行して CloudFront 側のカスタムヘッダを更新する（更新するまで API が 403 になる）。

## register-slack.sh

```bash
scripts/register-slack.sh dev bot      # Bot token を登録し mode を bot にする
scripts/register-slack.sh dev webhook  # Webhook URL を登録する（mode は変更しない）
```

値は**引数では渡せない**（シェル履歴に秘密を残さないため）。実行後のプロンプトから
非エコーで入力する。引数が 2 個でないときは usage を出して終了コード 1。
`init-secrets.sh` と同様に `DRY_RUN=1` で AWS を呼ばずに実行内容だけ表示できる。

登録するパラメータ:

| 種別 | パラメータ | 型 |
| --- | --- | --- |
| `bot` | `/example-backoffice/{env}/shared/slack/bot-token` | SecureString |
| `webhook` | `/example-backoffice/{env}/shared/slack/webhook-url` | SecureString |
| `bot` のみ | `/example-backoffice/{env}/shared/slack/mode` | String（`bot`） |

`webhook` は URL の登録・ローテートだけを行い、投稿方式（`mode`）は変えない
（bot 運用中にフォールバック用 URL を更新しても方式が切り替わらないように）。

**`bot` を登録したら `shared/samconfig.toml` の `SlackMode` も `bot` に直して shared を再デプロイする。**
直さないと次の shared デプロイで `mode` が samconfig の値（既定 `webhook`）に戻る。
samconfig はスクリプトからは編集しない。

## deploy-edge.sh

```bash
scripts/deploy-edge.sh dev
```

1. `/example-backoffice/{env}/edge/origin-verify-secret` を `--with-decryption` で取得する
   （未登録なら「先に init-secrets.sh を実行」と出して終了コード 1）。
2. `/example-backoffice/{env}/apps/saas-a-billing/api-domain` があれば
   `SaasABillingApiDomain` に足す。無ければ足さない（API 経路なしで edge が立つ）。
3. `infra/edge/samconfig.toml` の `parameter_overrides` を `tomllib` で読み、上の値を
   連結して `sam deploy --parameter-overrides` に渡す。

`sam deploy --parameter-overrides` は **samconfig の同名キーを丸ごと置き換える**
（追記ではない）ため、`SiteDomain` / `HostedZoneId` / `AcmCertificateArn` /
`AllowedIpAddresses` を消さないよう samconfig 側の値を前置している。
edge テンプレートは Lambda コードを持たない（CloudFront Function はインライン）ので
`sam build` は行わない。

> **注意（秘密の露出範囲）**: `sam deploy` は `Deploying with following values` の banner に
> `parameter_overrides` をそのまま印字するため、`OriginVerifySecret` の値がデプロイ実行時の
> 端末に表示される（`sam deploy` 側の挙動で抑止オプションが無い。CloudFormation コンソール上は
> `NoEcho` でマスクされる）。同じ理由で値は `sam` プロセスのコマンドライン引数にも載る。
> **デプロイログをそのまま保存・共有しないこと。** 漏れた疑いがあるときは
> `ROTATE=1 scripts/init-secrets.sh {env}` でローテーションしてから `deploy-edge.sh` を流し直す。

## deploy-frontend.sh

```bash
scripts/deploy-frontend.sh dev
```

SSM から `/cognito/user-pool-id` と `/cognito/client-id`、edge スタックの Outputs から
`SpaBucketName` / `DistributionId` / `SiteUrl` を取得し、次を実行する。

```
npm ci
VITE_COGNITO_USER_POOL_ID=... VITE_COGNITO_CLIENT_ID=... npm run build
aws s3 sync dist/ s3://{bucket}/ --delete
aws s3 cp dist/index.html s3://{bucket}/index.html --cache-control no-cache --content-type text/html
aws cloudfront create-invalidation --paths "/*"
```

`prod` は `--delete` 付き同期の前に確認プロンプトを出す。

## deploy-all.sh

```bash
scripts/deploy-all.sh dev
```

`shared` → `core` → `apps/*`（`template.yaml` があるもの）→ `deploy-edge.sh` →
`deploy-frontend.sh` の順に流す。`core` が SSM `/shared/layer-arn` を読むため `shared` が先。
Lambda コードを持つスタック（shared / core / apps）は `sam build --use-container` を先に実行する。

## get_id_token.py

```bash
ID_TOKEN=$(uv run python scripts/get_id_token.py dev devA@example.co.jp)
uv run python scripts/get_id_token.py dev devA@example.co.jp --totp-code 123456
```

SPA 用 App Client（`ALLOW_USER_SRP_AUTH`・シークレット無し）を `pycognito` の SRP 認証で
そのまま使うため、E2E 専用の App Client は作らない。パスワードは引数では渡せない
（環境変数 `COGNITO_PASSWORD` か対話入力）。標準出力へ出るのは ID トークンだけで、
アクセストークン・リフレッシュトークンは出さない。

## list-mfa-status.sh

```bash
scripts/list-mfa-status.sh dev
```

`list-users` の各ユーザーに `admin-get-user` して `UserMFASettingList` を確認し、
未設定のユーザーを `(未設定)` として表示、最後に未設定件数を出す。

## 前提ツール

`bash` / `aws` (CLI v1 以上) / `python3` (3.11 以上。`tomllib` を使う) / `openssl` /
`sam` / `npm`。`jq` は不要（使っていない）。`shellcheck` は任意。

```bash
command -v shellcheck >/dev/null && shellcheck scripts/*.sh || echo 'shellcheck 未インストールのためスキップ'
```
