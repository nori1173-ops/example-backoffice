# example-backoffice

社内保守管理サイト。CS 部・開発部が使う保守アプリを 1 つのサイトに集約する。

- 入口は CloudFront 1 つ（prod `backoffice.example-cloud.com` / dev `backoffice-dev.example-cloud.com`）。`/` は SPA、`/api/{app}/*` はアプリ別の HTTP API。
- 認証は Cognito User Pool（`@example.co.jp` 限定セルフサインアップ、TOTP MFA は Cognito 側で任意。サイトに登録画面は無い）。社内 IP 制限は CloudFront Function（viewer-request）。
- メール（SES）と Slack（Bot token の `chat.postMessage`。未登録時は Incoming Webhook）は core の通知基盤（SQS → notifier）に集約し、アプリは `notify.send()` を呼ぶだけ。
- スタック間の契約は SSM Parameter Store `/example-backoffice/{env}/...`。ID や ARN を人がコピーしない。
- アプリは `apps/{app}`（独立 SAM スタック）と `frontend/src/apps/{app}`（マニフェスト）を足すだけで追加できる。

## ディレクトリ構成

```text
example-backoffice/
├── README.md
├── Documents/
│   ├── DESIGN.md                  プラットフォーム設計書（SSM キー一覧の正本を含む）
│   ├── ADD-APP-GUIDE.md           アプリ追加手順・レビューチェックリスト
│   ├── images/                    SVG 図
│   └── apps/{app}/DESIGN.md       アプリ設計書
├── infra/
│   ├── core/                      Cognito / SES / SQS+DLQ / notifier / 添付 S3 / SSM 契約
│   └── edge/                      S3(SPA)+OAC / CloudFront / CloudFront Function（IP 制限 + SPA フォールバック）/ Route53
├── shared/                        共通 Lambda Layer（auth / origin_verify / notify / response / logging）
├── apps/
│   ├── saas-a-billing/           第一弾アプリ（独立 SAM スタック）
│   ├── daily-security-report/     AWS日次セキュリティレポート（独立 SAM スタック）
│   └── admin-users/               ユーザー管理（admin 限定。独立 SAM スタック）
├── frontend/                      単一 Vue 3 SPA（src/apps/{app}/ にアプリを追加）
├── templates/app/                 sam init --location 用のアプリ雛形
└── scripts/                       init-secrets / deploy-edge / deploy-frontend / deploy-all / add-admin
```

## ドキュメント

| 文書 | 内容 |
| --- | --- |
| [Documents/DESIGN.md](Documents/DESIGN.md) | プラットフォーム設計書（構成、スタック分割、SSM 契約、入口、認証、通知、フロント、セキュリティ、デプロイ、命名、コスト） |
| [Documents/ADD-APP-GUIDE.md](Documents/ADD-APP-GUIDE.md) | アプリ追加手順（10 ステップ）、雛形の説明、レビューチェックリスト、よくある失敗 |
| [Documents/apps/saas-a-billing/DESIGN.md](Documents/apps/saas-a-billing/DESIGN.md) | SaaS_A 月額課金レポート アプリ設計書 |
| [Documents/apps/daily-security-report/DESIGN.md](Documents/apps/daily-security-report/DESIGN.md) | AWS日次セキュリティレポート アプリ設計書（料金見積は同じフォルダの COST-ESTIMATE.md） |
| [Documents/apps/admin-users/DESIGN.md](Documents/apps/admin-users/DESIGN.md) | ユーザー管理 アプリ設計書（admin 限定） |

## デプロイ順

`env` は `dev` または `prod`。`AWS_PROFILE` は環境変数で指定する。core は notifier が共通 Layer を使い
`/example-backoffice/{env}/shared/layer-arn` を読むため、shared を先にデプロイする。

| 順番 | 対象 | コマンド |
| --- | --- | --- |
| 1 | shared | `cd shared && sam build --use-container && sam deploy [--config-env production]` |
| 2 | core | `cd infra/core && sam build --use-container && sam deploy [--config-env production]` |
| 3 | app（各アプリ） | `cd apps/{app} && sam build --use-container && sam deploy [--config-env production]` |
| 4 | edge | `scripts/deploy-edge.sh {env}` |
| 5 | frontend | `scripts/deploy-frontend.sh {env}` |

初回は次の順で流す（詳細と注意は [scripts/README.md](scripts/README.md)）。削除は逆順。

1. `scripts/init-secrets.sh {env}` — SecureString（origin-verify）を登録
2. `scripts/register-slack.sh {env} bot` — Slack App の Bot token を登録（shared のデプロイの前後どちらでもよい。投稿方式 `/shared/slack/mode` は shared の samconfig の `SlackMode` が決める）
3. アプリ固有の秘密を SecureString で登録（saas-a-billing なら `targets/{target_env}/{base-url,user-id,password}` の 3 本。prod は `targets/production` のみ）
4. `scripts/deploy-all.sh {env}` — 全スタックを依存順にデプロイ
5. Ops トピックのメール購読を手で作り、確認メールのリンクを 3 日以内に踏む（core デプロイ直後。CloudFormation のインライン購読は期限切れで消えるため）
6. `scripts/add-admin.sh {env} {email}` — 最初の管理者を `admin` グループへ（本人のサインアップ後。admin が居ないとユーザー管理画面も設定画面も開けない）。2 人目以降はその管理者が `/admin-users` の画面から付ける

```bash
# 5: Ops トピックの購読（アラームが鳴っても届かない事故を防ぐ）
TOPIC=$(aws ssm get-parameter --name /example-backoffice/prod/ops/topic-arn --query Parameter.Value --output text)
aws sns subscribe --topic-arn "$TOPIC" --protocol email --notification-endpoint devA@example.co.jp
aws sns list-subscriptions-by-topic --topic-arn "$TOPIC" --query 'Subscriptions[].Endpoint' --output text
```

Bot token が用意できるまでは Incoming Webhook（`.../shared/slack/webhook-url`）でも動く。その場合は `shared/samconfig.toml` の `SlackMode` を `webhook` に直して shared をデプロイする（`/shared/slack/mode` は CloudFormation が唯一の管理者で、スクリプトからは書かない）。
**prod の初回だけ core を 2 回流す**: samconfig は dev / prod とも `CognitoEmailSendingAccount=DEVELOPER` だが、SES Identity（`backoffice.example-cloud.com`）が未検証のうちは `DEVELOPER` で作成できない。初回だけ `--parameter-overrides CognitoEmailSendingAccount=COGNITO_DEFAULT` で上書きして立ち上げ、Identity が `Success` になったら上書きなしで（= samconfig どおり `DEVELOPER` で）再デプロイする。

## アプリ一覧

| id | 表示名 | 内容 | SPA | API | スタック |
| --- | --- | --- | --- | --- | --- |
| `saas-a-billing` | SaaS_A 課金レポート | SaaS_A の月額ライセンス料金を月次集計し Excel をメール添付 + Slack 通知。設定画面あり。**集計対象環境はサイト固定**（dev=検証環境 / prod=本番。画面からは変更できない） | `/saas-a-billing` | `/api/saas-a-billing/*` | `example-backoffice-app-saas-a-billing-{env}` |
| `daily-security-report` | AWS日次セキュリティレポート | 本番 AWS アカウント全体を読み取り専用で点検し、レポートと東京の GuardDuty 重大検出を Slack・メールへ通知。設定とレポート一覧 | `/daily-security-report` | `/api/daily-security-report/*` | `example-backoffice-app-daily-security-report-{env}` |
| `admin-users` | ユーザー管理 | 登録ユーザーの一覧・admin 付与/解除・無効化/有効化・パスワードリセット・削除。**`admin` 限定**（メニューも出ない）。自分自身への解除・無効化・削除と最後の admin の解除はサーバが拒否する | `/admin-users` | `/api/admin-users/*` | `example-backoffice-app-admin-users-{env}` |

公開版について: 第二弾アプリ（別の IoT 観測 SaaS の月次棚卸し）は、取引先の画面仕様を含むため公開版から除外した。
