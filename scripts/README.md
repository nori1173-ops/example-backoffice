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
scripts/init-secrets.sh prod

# 2. Slack の Bot token を登録する（設計 13.4 の手順 2。shared デプロイの前後どちらでも可）
#    投稿方式は shared/samconfig.toml の SlackMode が決める（dev / prod とも bot）。
#    webhook で始めるなら SlackMode を webhook に直して shared をデプロイする
scripts/register-slack.sh prod bot

# 3. アプリ固有の秘密を登録する（saas-a-billing は固定環境の 3 本だけ）
for LEAF in base-url user-id password; do
  aws ssm put-parameter --type SecureString --overwrite \
    --name "/example-backoffice/prod/apps/saas-a-billing/targets/production/$LEAF" --value "<値>"
done

# 4. 全スタックを依存順にデプロイする（shared → core → apps/* → edge → frontend）
scripts/deploy-all.sh prod

# 5. Ops トピックのメール購読を作る（core デプロイ直後。下記「アラーム通知の購読」参照）

# 6. 最初の管理者を付ける（本人のサインアップ後。admin が居ないとユーザー管理画面も設定画面も開けない）
#    2 人目以降はこの管理者が https://backoffice.example-cloud.com/admin-users から付ける
scripts/add-admin.sh prod devA@example.co.jp
```

**アラーム通知の購読（必須・手動）**: core の `OpsTopic` は CloudFormation の
インライン `Subscription` を持つが、**確認メールを 3 日以内に承認しないと購読は破棄され、
以後どれだけ再デプロイしても復活しない**（dev で購読 0 件を実測）。core をデプロイしたら
必ず手で購読を作り、届いた確認メールのリンクを踏んで `Confirmed` を確かめる。購読が無いと
アラーム 5 本（core 1 + saas-a-billing 4）は鳴っても誰にも届かない。

```bash
TOPIC=$(aws ssm get-parameter --name /example-backoffice/prod/ops/topic-arn \
  --query Parameter.Value --output text)
aws sns subscribe --topic-arn "$TOPIC" --protocol email --notification-endpoint devA@example.co.jp
# 確認メールのリンクを踏んでから（SubscriptionArn が PendingConfirmation でなくなる）
aws sns list-subscriptions-by-topic --topic-arn "$TOPIC" \
  --query 'Subscriptions[].[Endpoint,SubscriptionArn]' --output text
```

dev では続けてアラームのスモークテストを行い、実際にメールが届くことを確認する
（状態は次の評価で自動復帰する）。

```bash
aws cloudwatch set-alarm-state --alarm-name example-backoffice-saas-a-billing-lambda-errors-dev \
  --state-value ALARM --state-reason smoke
aws cloudwatch describe-alarms \
  --query 'MetricAlarms[?ends_with(AlarmName,`-dev`)].[AlarmName,length(AlarmActions)]' --output text
```

**投稿方式の管理者は samconfig（SP-03）**: `/shared/slack/mode` は shared スタックが
`SlackMode` パラメータから作る CloudFormation 管理のパラメータで、スクリプトからは書かない
（先に `put-parameter` で作ると shared の作成が `AlreadyExists` で失敗する）。方式を変えるときは
`shared/samconfig.toml` の `SlackMode` を直して shared を再デプロイする（現在は dev / prod とも
`bot`）。Bot token / Webhook URL の登録は shared のデプロイの前後どちらでもよい。

**prod の追加手順（Cognito のメール送信）**: `infra/core/samconfig.toml` の
`CognitoEmailSendingAccount` は dev / prod とも `DEVELOPER`。ただし SES Identity
（`backoffice.example-cloud.com`）が未検証のうちは `DEVELOPER` で作成できないため、
**prod の初回デプロイだけ `--parameter-overrides` で `COGNITO_DEFAULT` に上書きして立てる**。
DKIM の DNS が入って Identity が `Success` になったら、上書きを外して（= samconfig どおり
`DEVELOPER` で）再デプロイする。送信元が `noreply@backoffice.example-cloud.com` になり、
1 日 50 通の上限も外れる。samconfig を編集しないのでテストの期待値も動かない。

```bash
# 初回だけ COGNITO_DEFAULT で立てる（--parameter-overrides は samconfig の同名キーだけを置換する）
cd infra/core && sam build --use-container
sam deploy --config-env production --parameter-overrides CognitoEmailSendingAccount=COGNITO_DEFAULT

# Identity の検証を待つ
aws ses get-identity-verification-attributes --identities backoffice.example-cloud.com \
  --region ap-northeast-1

# VerificationStatus が Success になったら上書きなしで再デプロイ（samconfig の DEVELOPER が効く）
sam deploy --config-env production
```

## 一覧

| スクリプト | 引数 | 用途 |
| --- | --- | --- |
| `init-secrets.sh` | `{env}` | `/example-backoffice/{env}/edge/origin-verify-secret` を SecureString で登録する |
| `register-slack.sh` | `{env} {bot\|webhook}` | Slack の Bot token / Webhook URL を登録する（`/shared/slack/mode` は書かない） |
| `deploy-edge.sh` | `{env}` | SSM から秘密を取り出して edge スタックをデプロイする |
| `deploy-frontend.sh` | `{env}` | frontend をビルドして S3 へ同期し CloudFront を無効化する |
| `deploy-all.sh` | `{env}` | shared → core → apps/\* → edge → frontend の順に一括デプロイする |
| `add-admin.sh` | `{env} {email}` | サインアップ済みユーザーを `admin` グループに追加する（**最初の 1 人**と画面が使えないときの退路。通常はユーザー管理画面から） |
| `list-mfa-status.sh` | `{env}` | Cognito ユーザーの MFA 設定状況を一覧する |
| `get_id_token.py` | `{env} {email} [--totp-code CODE]` | Cognito の ID トークンを標準出力へ出す（`--totp-code` は TOTP 登録済みユーザーのみ） |
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
scripts/register-slack.sh dev bot      # Bot token を登録する
scripts/register-slack.sh dev webhook  # Webhook URL を登録する
```

値は**引数では渡せない**（シェル履歴に秘密を残さないため）。実行後のプロンプトから
非エコーで入力する。引数が 2 個でないときは usage を出して終了コード 1。
`init-secrets.sh` と同様に `DRY_RUN=1` で AWS を呼ばずに実行内容だけ表示できる。

登録するパラメータ:

| 種別 | パラメータ | 型 |
| --- | --- | --- |
| `bot` | `/example-backoffice/{env}/shared/slack/bot-token` | SecureString |
| `webhook` | `/example-backoffice/{env}/shared/slack/webhook-url` | SecureString |

Bot token に要るスコープは `chat:write` + `chat:write.public`（投稿）と
`channels:read` / `groups:read` / `users:read` / `usergroups:read`（設定画面の `#` / `@` 候補検索）。
`bot-token` を読むのは notifier（投稿）と課金アプリの api（候補検索。読み取りのみ）の 2 つである。

どちらも秘密の登録・ローテートだけを行い、投稿方式（`/shared/slack/mode`）は書かない。
このパラメータは shared スタックが `SlackMode` から作る CloudFormation 管理のリソースで、
スクリプトが先に作ると shared の作成が `AlreadyExists` で失敗する（prod 初回で実際に失敗した）。
方式を切り替えるときは `shared/samconfig.toml` の `SlackMode` を直して shared を再デプロイする。
samconfig はスクリプトからは編集しない。

notifier の実挙動は bot-token の有無で決まるため、`mode` は「人が読むための表示値」に過ぎない。
`SlackMode=bot` にしたまま bot-token を消すと、画面は bot と表示しつつ実際は webhook で投稿される。

## deploy-edge.sh

```bash
scripts/deploy-edge.sh dev
```

1. `/example-backoffice/{env}/edge/origin-verify-secret` を `--with-decryption` で取得する
   （未登録なら「先に init-secrets.sh を実行」と出して終了コード 1）。
2. `/example-backoffice/{env}/apps/saas-a-billing/api-domain` があれば
   `SaasABillingApiDomain` に足す。無ければ足さない（API 経路なしで edge が立つ）。
3. `infra/edge/samconfig.toml` の `parameter_overrides` を `tomllib` で読み、上の値を
   連結してパーミッション 600 の一時 samconfig（`/tmp`）へ書き、`sam deploy --config-file`
   で渡す。一時ファイルは `trap` で必ず消す。
4. `sam deploy` の出力は秘密を `***` に置換してから表示する。

`sam deploy --parameter-overrides` は **samconfig の同名キーを丸ごと置き換える**
（追記ではない）ため、`SiteDomain` / `HostedZoneId` / `AcmCertificateArn` /
`AllowedIpAddresses` を消さないよう samconfig 側の値を前置している。
edge テンプレートは Lambda コードを持たない（CloudFront Function はインライン）ので
`sam build` は行わない。

> **注意（秘密の露出範囲）**: `sam deploy` は `Deploying with following values` の banner に
> `parameter_overrides` をそのまま印字する（`sam deploy` 側の挙動で抑止オプションが無い。
> CloudFormation コンソール上は `NoEcho` でマスクされる）。このスクリプトは
> (1) 秘密をコマンドライン引数ではなく 600 の一時 samconfig 経由で渡し、
> (2) `sam` の出力から秘密を `***` に置換することで、`ps` と端末・ログの両方から隠している。
> それでも **prod のデプロイログを不用意に保存・共有しないこと。** 漏れた疑いがあるときは
> `ROTATE=1 scripts/init-secrets.sh {env}` でローテーションしてから `deploy-edge.sh` を流し直す。

## deploy-frontend.sh

```bash
scripts/deploy-frontend.sh dev
```

SSM から `/cognito/user-pool-id` と `/cognito/client-id`、edge スタックの Outputs から
`SpaBucketName` / `DistributionId` / `SiteUrl` を取得し、次を実行する。

```
npm ci
VITE_COGNITO_USER_POOL_ID=... VITE_COGNITO_CLIENT_ID=... VITE_SITE_ENV={env} npm run build
aws s3 sync dist/ s3://{bucket}/ --delete
aws s3 cp dist/index.html s3://{bucket}/index.html --cache-control no-cache --content-type text/html
aws cloudfront create-invalidation --paths "/*"
```

`prod` は `--delete` 付き同期の前に確認プロンプトを出す。

`VITE_SITE_ENV`（`dev` / `prod`）はアプリバーのサイト環境チップと `document.title` の
`[検証]` 前置を決める。渡し忘れたビルドは `dev`（検証環境）として表示される。

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

MFA は任意（`MfaConfiguration: OPTIONAL`）なので、TOTP 未登録のユーザーはコード無しで通る。
`--totp-code` が要るのは認証アプリを登録済みのユーザーだけである。

SPA 用 App Client（`ALLOW_USER_SRP_AUTH`・シークレット無し）を `pycognito` の SRP 認証で
そのまま使うため、E2E 専用の App Client は作らない。パスワードは引数では渡せない
（環境変数 `COGNITO_PASSWORD` か対話入力）。標準出力へ出るのは ID トークンだけで、
アクセストークン・リフレッシュトークンは出さない。

## add-admin.sh

```bash
scripts/add-admin.sh prod devA@example.co.jp
```

`/example-backoffice/{env}/cognito/user-pool-id` から User Pool ID を引き、
`admin-add-user-to-group --group-name admin` を実行する。ユーザーが存在しないときは
`admin-get-user` の失敗を検出して終了コード 1。prod は付与前に確認プロンプトを出す。
反映は次回サインイン（ID トークン再取得）から。

**このスクリプトの出番は 2 つだけ**（#1007 以降）: サイトの**最初の管理者**を作るときと、
ユーザー管理画面（`/admin-users`）が使えない・admin が 1 人も居なくなったときの退路。
普段の付与・解除・無効化・パスワードリセット・削除は admin が画面から行う
（[Documents/apps/admin-users/DESIGN.md](../Documents/apps/admin-users/DESIGN.md)）。

画面を使わずに CLI で行う場合（設計 6.3 の runbook）:

```bash
POOL=$(aws ssm get-parameter --name /example-backoffice/prod/cognito/user-pool-id \
  --query Parameter.Value --output text)
aws cognito-idp admin-remove-user-from-group --user-pool-id "$POOL" --username x@example.co.jp --group-name admin
aws cognito-idp admin-disable-user --user-pool-id "$POOL" --username x@example.co.jp
aws cognito-idp admin-delete-user  --user-pool-id "$POOL" --username x@example.co.jp
```

削除しても実行履歴の `requested_by`（Cognito の `sub`）は残る。

**認証アプリ（TOTP）を失った場合**: このプールは `MfaConfiguration: OPTIONAL`（MFA は任意）
なので、管理者が TOTP だけを解除できる。ユーザーの作り直しは要らない。

```bash
aws cognito-idp admin-set-user-mfa-preference --user-pool-id "$POOL" \
  --username x@example.co.jp \
  --software-token-mfa-settings Enabled=false,PreferredMfa=false
# → 以降はパスワードだけでサインインできる（サイトに TOTP 登録画面は無い）
```

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
