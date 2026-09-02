# example-backoffice

社内保守管理サイト。CS 部・開発部が使う保守アプリを 1 つのサイトに集約する。

- 入口は CloudFront 1 つ（prod `backoffice.example-cloud.com` / dev `backoffice-dev.example-cloud.com`）。`/` は SPA、`/api/{app}/*` はアプリ別の HTTP API。
- 認証は Cognito User Pool（`@example.co.jp` 限定セルフサインアップ、TOTP MFA）。社内 IP 制限は CloudFront Function（viewer-request）。
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
│   └── saas-a-billing/           第一弾アプリ（独立 SAM スタック）
├── frontend/                      単一 Vue 3 SPA（src/apps/{app}/ にアプリを追加）
├── templates/app/                 sam init --location 用のアプリ雛形
└── scripts/                       init-secrets / deploy-edge / deploy-frontend / deploy-all
```

## ドキュメント

| 文書 | 内容 |
| --- | --- |
| [Documents/DESIGN.md](Documents/DESIGN.md) | プラットフォーム設計書（構成、スタック分割、SSM 契約、入口、認証、通知、フロント、セキュリティ、デプロイ、命名、コスト） |
| [Documents/ADD-APP-GUIDE.md](Documents/ADD-APP-GUIDE.md) | アプリ追加手順（10 ステップ）、雛形の説明、レビューチェックリスト、よくある失敗 |
| [Documents/apps/saas-a-billing/DESIGN.md](Documents/apps/saas-a-billing/DESIGN.md) | SaaS_A 月額課金レポート アプリ設計書 |

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

初回は `scripts/init-secrets.sh {env}` で SecureString（origin-verify）を登録し、Slack App の Bot token を `/example-backoffice/{env}/shared/slack/bot-token` に登録してから `scripts/deploy-all.sh {env}`。Bot token が用意できるまでは Incoming Webhook（`.../shared/slack/webhook-url`）でも動く。削除は逆順。

## アプリ一覧

| id | 表示名 | 内容 | SPA | API | スタック |
| --- | --- | --- | --- | --- | --- |
| `saas-a-billing` | SaaS_A 課金レポート | SaaS_A の月額ライセンス料金を月次集計し Excel をメール添付 + Slack 通知。設定画面あり | `/saas-a-billing` | `/api/saas-a-billing/*` | `example-backoffice-app-saas-a-billing-{env}` |
