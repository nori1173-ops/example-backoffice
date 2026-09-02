# 実装計画（example-backoffice）

| 計画 | dev-tracker | 内容 | Phase / タスク数 |
| --- | --- | --- | --- |
| [2026-08-28-platform-plan.md](2026-08-28-platform-plan.md) | #1002 | 基盤（monorepo / shared / core / edge / edge-global / frontend シェル） | 6 Phase / 27 タスク |
| [2026-08-28-saas-a-billing-plan.md](2026-08-28-saas-a-billing-plan.md) | #1003 | 第一弾アプリ（SaaS_A 月額課金レポート） | 7 Phase / 27 タスク |

ブランチ `feature/#1001`。実行は `superpowers:subagent-driven-development`（1 タスク = Opus サブエージェント 1 体）。コミットは `mod refs #1001 <要点>`、push はメインが行う。

## 順序と依存関係

`基盤 P0（リポ基盤）→ 基盤 P1（shared）` の後、`基盤 P2（core）→ P3（edge/edge-global/scripts）→ P4（frontend シェル）→ P5（dev デプロイ）` と `課金 A〜F（コードのみ）` を**並行**で進め、最後に `課金 G（dev デプロイ・実運用確認）`。

- 課金 **Phase A〜D と E-1 / E-2** は基盤 Phase 1（shared レイヤ）完了後から着手できる。
- **E-3（SAM テンプレート / edge-snippet）は基盤 P3-3（edge テンプレート）と P4-6（`templates/app` 雛形）に依存**するため、基盤 Phase 3・4 完了後に行う。
- **F-1〜F-4（フロント）は基盤 P4-4（`createAppApi` / ルーター / ガード）と P4-6（`_template` / E2E 土台）に依存**するため、基盤 Phase 4 完了後に行う。
- **課金 Phase G は基盤 Phase 5 完了が必須。** 基盤 Phase 5 では saas-a-billing 未デプロイのため edge の API origin / behavior を一時コメントアウトし、課金 G-1 で戻す。`execute-api` 直叩きの 403 / 200 実測も基盤 P5-3 から課金 G-1 へ持ち越す。
- デプロイ順は設計 3.3 の `edge-global → core → shared → app → edge → frontend`。**実装順（shared を先に書く）とは逆**。理由は各計画の「前提と判断 1」。

## ユーザー作業チェックポイント（サブエージェントは実施できない）

| # | いつ | 作業 |
| --- | --- | --- |
| CP-1 | 基盤 P3 完了後 | Slack App 作成 + Bot に `chat:write` / `chat:write.public` を付与して Bot token を登録（`scripts/register-slack.sh dev bot`）。用意できなければ Incoming Webhook を作成して `webhook` で登録 |
| CP-2 / CP-B3 | 基盤 P3 完了後 | `#proj-saas-a-billing` のチャンネル ID（`C...`）を Slack から取得して伝える |
| CP-3 | 基盤 P5（core デプロイ後） | dev サイトで管理者ユーザーをサインアップし Cognito コンソールで `admin` グループに追加。E2E 用ユーザー（TOTP 未登録）も作成 |
| CP-4 | 基盤 P5 | 社内 IP 以外からのアクセスが 403 になることの確認（社外回線が必要） |
| CP-B1 | 課金 G の前 | **SaaS_A 検証環境に専用 API ユーザーを作成**（MFA なし・システム管理者権限。`saasa-dev` MCP のユーザーとは別） |
| CP-B2 | 課金 G の前 | **SSM に検証環境の接続情報を登録**（`/example-backoffice/dev/apps/saas-a-billing/targets/verification/{base-url,user-id,password}` を SecureString で） |
| CP-B4 | 課金 G-4 | メール受信と Slack 投稿の目視確認 |

**メール送信テストは `devA@example.co.jp` 宛のみ。Slack への投稿はテスト用チャンネルまたは CP-2 のチャンネルのみ。** dev デプロイは承認済みで自走してよいが、共有リソース（Route53 `Z0EXAMPLEZONE0` / ACM `*.example-cloud.com` / SES 送信枠 / `example-sam-*` バケット / レイヤ `ExamplePowertoolsPython:86`）は参照・新規追加のみで、**他プロジェクトのリソースは変更しない**。prod へのデプロイは両計画の範囲外。
