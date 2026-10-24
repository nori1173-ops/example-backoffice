# 料金見積（daily-security-report）

| 項目 | 内容 |
| --- | --- |
| ドキュメント名 | COST-ESTIMATE.md（AWS日次セキュリティレポート daily-security-report の料金見積） |
| バージョン | v2.2（#1022） |
| 作成日 | 2026-10-15（v2.2 改訂: 2026-10-19） |
| 作成者 | 弊社 開発部 |
| 対象読者 | 社内プロマネ・開発部・管理者層 |
| dev-tracker | #1022（v2 は #1020、v2.1 は #1021。v1 の #1019 は終了） |
| 上位設計書 | [`DESIGN.md`](DESIGN.md)（daily-security-report） / [`../../DESIGN.md`](../../DESIGN.md)（プラットフォーム） |
| 基盤の料金見積 | [`../../COST-ESTIMATE.md`](../../COST-ESTIMATE.md)（プラットフォーム + saas-a-billing、v0.4） |
| Excel 版 | 料金見積の Excel は公開版では省略（同じ内容は COST-ESTIMATE.md） |

> example-backoffice のアプリ daily-security-report（AWS日次セキュリティレポート）を足したときに増える月額費用の見積。単価は AWS Pricing MCP（Price List API）から取得した（2026-10-15、税別・USD）。MCP で取れない無料枠の量などは AWS 公式サイトで補った（10 章）。v2 の Bedrock はブリーフで提供された AWS 公式料金ページの表データ（2026-10-14 公開分、USD / 100 万トークン）を採用する。新しい Claude は料金 MCP に項目が無く Web 取得した値で、本作業ではネットワーク不可のため再取得していない（5.5）。dev / prod は同一アカウント（210000000001）・同一リージョン（ap-northeast-1）に共存するため環境ごとに試算し、アカウント単位でかかる GuardDuty（3 リージョン）は 1 回だけ計上して合計する。
>
> 月額は中シナリオで $82.48（¥12,372）、低 $38.63 〜 高 $217.75。GuardDuty は中 $57.35（約 69.5%）。S3 Protection は 2026-10-19 に停止した前提で全シナリオ $0 とし、S3 区分の CloudWatch GetMetricData を月 $0.05 追加した。追加候補のバケット の要求メトリクスは任意で、低 $0 / 中・高 $4.80（prod 配布時に判断）。アプリの Logs Insights は prod 中 $8.21、Bedrock は既定 Opus $7.92。「ログを見る」の都度課金（alarmhuba 1 回約 $0.14）は定常合計に含めない。アラームと HTTP API 詳細メトリクスの固定費は dev / prod とも $2.00。
>
> 2026-10-19 の使用量 API（有効化後 3.9 日）では、GuardDuty の S3 を除く月換算は約 $18（低〜中の範囲）。短期間の実測のため、CloudTrail・VPC フロー・DNS・Lambda Protection の既存シナリオ幅は保持した。数量・単価はブリーフ提供値を使用し、小計・合計・構成比は Excel の数式の再計算値から転記する（セント未満を含めて集計し、表示時に丸める）。

---

## 1. 前提条件

| 項目 | 値 |
| --- | --- |
| リージョン（主） | ap-northeast-1（東京）。アプリのスタック、Logs Insights の対象、CloudTrail の S3 バケット |
| リージョン（副） | us-east-1 / us-west-2（GuardDuty の検出器。us-east-1 は CloudFront・WAF の指標も GetMetricData で読む） |
| AWS アカウント | 210000000001（dev / prod 同居。集計対象も同じアカウント） |
| 料金基準 | 基盤は AWS Pricing MCP 2026-10-15 取得、Bedrock はブリーフ提供の AWS 公式表データ（2026-10-14 公開分）。税別・USD |
| 為替レート | 1 USD = 150 JPY（参考換算。基盤の見積は 155 JPY を使っている） |
| 1 か月 | 30 日で計算（日次バッチ 30 回） |
| 無料利用枠 | 常時無料枠のみ適用。12 か月無料枠は既存アカウントのため適用しない。アカウント共有の枠は、基盤と既存アプリが先に使っている前提で判定する（6 章） |
| シナリオ | 低 / 中 / 高。GuardDuty の VPC フロー・DNS、Lambda Protection と Logs Insights の走査量で分ける。要求メトリクスの任意追加は低 0 / 中・高 1 バケット（追加候補のバケット のみ）。S3 Protection は全シナリオ $0 |
| GuardDuty | 2026-10-15 に 3 リージョンで有効化（東京の検出器の作成は 03:05 UTC）。30 日無料は 2026-11-14 まで。本見積は S3 Protection 停止後・無料期間後の定常月を示す |
| GuardDuty の機能 | 停止後も有効: CloudTrail 管理イベント、VPC フローログ、DNS ログ（この 3 つは基盤で、無効化できない）、Lambda Protection（Lambda ネットワーク活動）。S3 Protection は 2026-10-19 停止済み（#1022） |
| 日次バッチ | EventBridge Scheduler（毎日 07:00 JST、月 30 回）→ `report` Lambda（1024 MB）。prod は集計 1〜3 分 + AI 最大 150 秒（計 330 秒で計上）。dev は設定が無効のため、起動して数百 ms で終わる |
| 即時通知 | GuardDuty の重大検出（severity 7 以上）→ EventBridge ルール → `guardduty-alert` Lambda（256 MB）。月 0〜10 回（10 回で計上） |
| 画面・API | HTTP API `ANY /api/daily-security-report/{proxy+}`（JWT authorizer、詳細メトリクス有効）→ `api` Lambda（512 MB）。設定画面・レポート一覧 / 詳細・AI 試行。prod 月 500、dev 月 200 リクエスト |
| 通知 | core の notify キュー（SQS）→ notifier → Slack（Bot、無料）と SES メール。メールは prod 月 40 通（レポート 30 + 即時通知 10、1 通 1 宛先で計上） |
| Logs Insights | alarmhuba-backend Resolver（1 日数 GB）を Q1〜Q4 の 4 本、GPS モック Receiver（数 MB）を Q5 の 1 本、1 日 5 クエリ |
| CloudTrail の読み取り | S3 `cloudtrail-example`（ap-northeast-1）から 1 日約 1,300 ファイル（UTC 2 日分）の GET と数十回の LIST |
| GetMetricData | 既存 1 日 100〜300（月 9,000）+ S3 区分 実測 3 リージョンで数十バケット（ap-northeast-1 以外は 0）、1 日約 175 メトリクス（月 5,250）= 月 14,250。S3 増分 $0.0525（表示 $0.05） |
| S3 監視 | CloudWatch `AWS/S3` の日次ストレージ指標は全バケット無料。要求メトリクスは有効化したバケットだけ。既存の要求メトリクスの費用は他システム側で本見積の対象外。任意追加は 1 バケット 16 指標 × $0.30 = $4.80/月。prod 配布時に判断 |
| AI 判定 | Claude（Amazon Bedrock）。既定 Opus 5.5 jp、1 日 1 回、入力 35,000（v2.1 の本番実測 34,853。v2 は 15,556）・出力 5,000 トークン（思考分を含む。実測は約 1,300）の費用予算。月 30 日。試行は月 20 回を追加する感度分析（API 上限は別途 20 回 / 日） |
| IAM | 認証情報レポート、ListRoles / GetRole は無料。ACTOR（100〜300 主体）・PREVIEW（1 日 TTL）の DynamoDB 増分は誤差 |
| データ | DynamoDB 単一テーブル（オンデマンド、SSE、prod は PITR）。日次集計 1 項目/日（v2.1 で約 90 KB、400 日保持）で定常 0.04 GB |
| ログ | CloudWatch Logs ロググループ 3（保持 365 日）。取込は 1 日数十 KB（prod 月 0.01 GB、dev 月 0.003 GB で計上） |
| 監視 | CloudWatch アラーム 5 個 / 環境 |
| 計上しないもの | KMS（本アプリの読み取りでは KMS の呼び出しが生じないことを 2026-10-15 に `get-bucket-encryption` で確認）、Secrets Manager（使わない）、S3 への書き込み（しない）、データ転送（S3・Logs はすべて同一リージョン内）、基盤の CloudFront・Cognito（画面の増分は月数百リクエストで、基盤の見積の範囲内） |

---

## 2. リソース構成

| ID | リソース | 仕様 | 根拠 |
| --- | --- | --- | --- |
| R1 | API Gateway HTTP API | `ANY /api/daily-security-report/{proxy+}`、Cognito JWT authorizer、`DetailedMetricsEnabled: true`（カスタムメトリクス 5 個） | DESIGN 5 章・8 章（改訂で設定画面とレポート一覧の API を足す） |
| R2 | Lambda `api` | Python 3.13、512 MB、timeout 29 秒、x86_64 | DESIGN 5 章 |
| R3 | Lambda `report` | 1024 MB、timeout 900 秒。毎日 1 回、prod 集計 180 秒 + AI 150 秒（計 330 秒） | DESIGN 5 章・6 章 |
| R4 | Lambda `guardduty-alert` | 256 MB、timeout 60 秒。重大検出のときだけ | DESIGN 5 章・6 章 |
| R5 | DynamoDB | `example-backoffice-daily-security-report-data-{env}`、PAY_PER_REQUEST、SSE、prod は PITR | DESIGN 7 章 |
| R6 | EventBridge Scheduler | `cron(0 7 * * ? *)` Asia/Tokyo、月 30 回 | DESIGN 5 章・6 章 |
| R7 | EventBridge ルール | GuardDuty Finding（severity >= 7）→ `guardduty-alert` | DESIGN 6 章 |
| R8 | SQS | Scheduler DLQ 1、Lambda OnFailure 用の失敗キュー 1〜2（ほぼ空。ポーリングしない） | DESIGN 6 章・11 章 |
| R9 | CloudWatch Logs | ロググループ 3、保持 365 日 | DESIGN 14 章 |
| R10 | CloudWatch アラーム | 5 個 / 環境（Lambda Errors、Scheduler DLQ、失敗キュー、EventBridge FailedInvocations） | DESIGN 14 章 |
| R11 | CloudWatch GetMetricData | 既存 月 9,000 + S3 区分 月 5,250 メトリクス。S3 は ap-northeast-1 / us-east-1 / us-west-2 | DESIGN 4.8・6 章 |
| R12 | CloudWatch Logs Insights | 1 日 5 クエリ（Q1〜Q4 は alarmhuba-backend Resolver を各 1 日分、Q5 は GPS モック） | DESIGN 4 章・6 章 |
| R13 | S3 の読み取り（`cloudtrail-example`） | 1 日約 1,300 ファイルの GET + 数十回の LIST。書き込みはしない | DESIGN 4 章 |
| R14 | SES / Slack | core の notifier 経由。SES メール prod 月 40 通、Slack は Bot（無料） | DESIGN 3 章（改訂でメールを足す） |
| R15 | GuardDuty | ap-northeast-1 / us-east-1 / us-west-2、アカウント単位。CloudTrail 管理イベント・VPC フロー・DNS・Lambda Protection。S3 Protection は 2026-10-19 停止済み | DESIGN 1 章・付録 B |
| R16 | Amazon Bedrock | ap-northeast-1 から Converse と ListInferenceProfiles。系統別単価と経路は 5.5 | DESIGN 2.11・4.7・12.4 |

- 根拠の章番号は DESIGN.md v2.2（2026-10-19、章番号を維持）の構成による。
- core の notify キュー・notifier Lambda・Ops SNS トピック・Cognito・CloudFront は基盤の既存リソースで、本アプリが足すのは送信件数とリクエスト数の増分だけ（5 章で計上。ほぼ $0）。

---

## 3. 単価一覧（AWS Pricing MCP 取得値）

AWS Pricing MCP（Price List API）から取得した（2026-10-15）。出所の sku は 10 章にも一覧する。

| サービス | 単価 | 単位 | リージョン | 出所 |
| --- | ---: | --- | --- | --- |
| GuardDuty CloudTrail 管理イベント | $4.72 | /100 万イベント（$0.00000472/件） | ap-northeast-1 | MCP sku TFHCE6Z4YPX3UBZM |
| GuardDuty CloudTrail 管理イベント | $4.00 | /100 万イベント（$0.000004/件） | us-east-1 / us-west-2 | MCP sku KM3AH2Q87UZFX9MJ / MW5UU98V8Z3AKYJQ |
| GuardDuty VPC フローログ・DNS ログ | $1.18 | /GB（最初の 500 GB。次の 2,000 GB は $0.59） | ap-northeast-1 | MCP sku B8T9U7C33PWDWSWX |
| GuardDuty VPC フローログ・DNS ログ | $1.00 | /GB（最初の 500 GB） | us-east-1 / us-west-2 | MCP sku PXTAN6UDSEZEEUUP / 5BRYZ3RVF78ZSHRW |
| GuardDuty S3 Protection（S3 データイベント） | $1.04 | /100 万イベント（$0.00000104/件、最初の 5 億件） | ap-northeast-1 | MCP sku AKNZG2PH483CPE6D |
| GuardDuty Lambda Protection（ネットワーク活動） | $1.18 | /GB（最初の 500 GB） | ap-northeast-1 | MCP sku QNCGZXZ3RYVQB4VH |
| GuardDuty EC2 Runtime Monitoring（参考・未計上） | $2.00 | /vCPU・月（最初の 500 vCPU） | ap-northeast-1 | MCP sku N8N7G5B7GAXMPUNJ |
| CloudWatch Logs Insights 走査 | $0.0076 | /GB 走査 | ap-northeast-1 | MCP sku QE3CDKDURP2A2WR5 |
| CloudWatch GetMetricData | $0.01 | /1,000 メトリクス（$0.00001/メトリクス） | ap-northeast-1 / us-east-1 | MCP sku 6YJR7DASEN49YFY6 / ST83NCNZMVRKWEVJ |
| CloudWatch カスタムメトリクス | $0.30 | /メトリクス・月（最初の 10,000） | ap-northeast-1 | MCP sku F96ZRPX8AM4T93NV |
| CloudWatch アラーム（標準分解能） | $0.10 | /アラームメトリクス・月 | ap-northeast-1 | MCP sku FBMB6TVC844YGZ3J |
| CloudWatch Logs 取込（Standard） | $0.76 | /GB | ap-northeast-1 | MCP sku CWB2GTZJXNX3TA6H |
| CloudWatch Logs ストレージ | $0.033 | /GB-月 | ap-northeast-1 | MCP sku 42CVQU4BPES8AWHC |
| API Gateway HTTP API | $1.29 | /100 万リクエスト（最初の 3 億） | ap-northeast-1 | MCP sku 5TU3JKA59EGYZ8XY |
| Lambda リクエスト | $0.20 | /100 万リクエスト | ap-northeast-1 | MCP sku 3BE8DYKG4FYSZGDW |
| Lambda 実行時間（x86） | $0.0000166667 | /GB-秒（Tier-1、60 億 GB-秒まで） | ap-northeast-1 | MCP sku FSYUV9NMNDEXRJ5H |
| DynamoDB 書込（オンデマンド） | $0.715 | /100 万 WRU | ap-northeast-1 | MCP sku HBK75W4NW4DYPFAC |
| DynamoDB 読込（オンデマンド） | $0.1425 | /100 万 RRU | ap-northeast-1 | MCP sku TX6BW4TWG2FZQMPQ |
| DynamoDB ストレージ | $0.285 | /GB-月（最初の 25 GB は $0） | ap-northeast-1 | MCP sku 2HPSAWPXJ2JJJ6XY |
| DynamoDB PITR | $0.228 | /GB-月 | ap-northeast-1 | MCP sku ZZYBMDK8NF9E89RN |
| EventBridge Scheduler | $1.25 | /100 万起動（最初の 1,400 万起動は $0） | ap-northeast-1 | MCP sku 29C3758CBCYNXH8B |
| EventBridge ルール（AWS サービスのイベント） | $0.00 | 同一アカウントの AWS サービスのイベントは課金なし | ap-northeast-1 | MCP（該当する課金 sku なし）+ Web 取得 |
| SQS（標準キュー） | $0.40 | /100 万リクエスト（Tier1） | ap-northeast-1 | MCP sku 5Q2SBURHPCGERHBD |
| S3 リクエスト（GET） | $0.0037 | /10,000 リクエスト | ap-northeast-1 | MCP sku 9C7873KU3ZQBD6BJ |
| S3 リクエスト（LIST） | $0.0047 | /1,000 リクエスト | ap-northeast-1 | MCP sku XAKHUSC46NWC9PPM |
| SES 送信 | $0.10 | /1,000 宛先（$0.0001/宛先） | ap-northeast-1 | MCP sku 824QF4XUVN48GVC9 |

> 単位の換算に注意: GuardDuty の CloudTrail 管理イベントと S3 データイベントは「/件」、VPC フロー・DNS と Lambda ネットワーク活動は「/GB」、Logs Insights は「/GB 走査」、GetMetricData は「/1,000 メトリクス」、S3 の GET は「/10,000 リクエスト」、LIST は「/1,000 リクエスト」、SES は「/1,000 宛先」である。本書の試算はすべて 1 単位あたりの単価に換算してから計算している。
>
> Logs Insights の東京の単価は $0.0076/GB（DESIGN 4.4 と同じ）。本書の中シナリオは 36 GB/日で $0.27/日（月 $8.21）になる。
>
> EventBridge ルール: Price List API には、同一アカウントの既定のバスで AWS サービスのイベントを受けてターゲットを呼ぶ課金 sku が無い（有料なのはカスタムイベントの PutEvents $1.00/100 万件 sku SG5BXRDK8SBA9Y57 と、別アカウントへの配信 sku FYGDQTAUXE9MYWM7）。料金ページにも「AWS management events are ingested by the event bus for free」とある。月 10 件以下なので、仮にカスタムイベントの単価で見ても $0.00 である。

---

## 4. 固定費

リクエスト数に依存しないコスト。dev / prod ともに同額。

| リソース | 単価 | 数量 | 月額（USD） | 月額（JPY） |
| --- | ---: | ---: | ---: | ---: |
| CloudWatch アラーム | $0.10/月 | 5 | $0.50 | ¥75 |
| HTTP API 詳細メトリクス（カスタムメトリクス） | $0.30/月 | 5 | $1.50 | ¥225 |
| 固定費 合計（1 環境あたり） | | | $2.00 | ¥300 |

dev + prod では $4.00（¥600）。

- CloudWatch アラームの常時無料枠は 10 アラームメトリクス/月（アカウント共有）。基盤の見積（v0.4）は dev 5 + prod 5 でちょうど使い切るとしていたが、その後に第二弾の課金アプリ（公開版では除外）が 1 環境あたり 5 個を足した（core 1 + saas-a-billing 4 + 第二弾の課金アプリ 5 = 10 個/環境、dev + prod で 20 個。各 template.yaml の `AWS::CloudWatch::Alarm` の数）。枠はすでに超えているので、本アプリの 10 個（dev 5 + prod 5）は全部 $0.10/個・月で課金される見込みである。
- HTTP API の詳細メトリクスは、ルート単位のメトリクスを CloudWatch のカスタムメトリクスとして発行する。ルートは `ANY /api/daily-security-report/{proxy+}` の 1 本で、5 メトリクス（Count / 4xx / 5xx / Latency / IntegrationLatency）。基盤の見積 4 章の注記では表に入れていなかったが、本書では計上する。常時無料枠（カスタムメトリクスと詳細モニタリングメトリクスで 10 個）は、saas-a-billing と第二弾の課金アプリ（公開版では除外）の HTTP API（各 5 個/環境、dev + prod で 20 個）が先に使っているため適用しない。
- 固定費はアプリ本体の費用のほぼ全部を占める（prod の Logs Insights を除く）。詳細メトリクスを無効にすれば 1 環境あたり $1.50 減る（8.2）。

---

## 5. 従量費

### 5.1 前提数量

アプリ本体（任意の S3 要求メトリクスを除きシナリオで変わらない）:

| 項目 | dev | prod | 根拠 |
| --- | ---: | ---: | --- |
| HTTP API リクエスト / 月 | 200 | 500 | 設定画面とレポート一覧（月 数百） |
| Lambda 呼出 / 月 | 230 | 540 | api（200 / 500）+ report 30 + guardduty-alert（0 / 10） |
| Lambda GB-秒 / 月 | 45 | 9,980 | 5.4 の計算根拠 |
| DynamoDB WRU / RRU | 200 / 3,000 | 2,000 / 55,000 | 日次集計の書込（v2.1 で約 90 KB、1 回 90 WRU 前後。月 $0.01 未満の差）、7 日分の Query、レポート一覧の読込 |
| DynamoDB ストレージ / PITR | 0.001 GB / なし | 0.02 GB / 0.02 GB | 日次集計 400 日 × 約 90 KB（0.04 GB。無料枠 25 GB 内） |
| Scheduler 起動 / 月 | 30 | 30 | 毎日 07:00 |
| SQS リクエスト / 月 | 0 | 100 | notify キューへの投函 40 と notifier の受信・削除。自スタックの DLQ・失敗キューはほぼ空で、ポーリングもしない |
| SES 送信 / 月 | 0 | 40 通 | レポート 30 + 即時通知 10 |
| CloudWatch Logs 取込 / ストレージ | 0.003 GB / 0.036 GB | 0.01 GB / 0.12 GB | 保持 365 日の定常状態は取込 12 か月分 |
| CloudWatch アラーム / カスタムメトリクス | 5 / 5 | 5 / 5 | 4 章 |
| GetMetricData / 月 | 0 | 14,250 メトリクス | 既存 300/日 × 30 日 = 9,000 + S3 区分 約 175/日 × 30 日 = 5,250 |
| S3 GET / LIST / 月 | 0 / 0 | 40,000 / 1,500 | 1,310 ファイル/日 × 30 日、LIST 50 回/日 × 30 日 |

シナリオで変わる項目:

| 項目 | 低 | 中 | 高 | 根拠 |
| --- | ---: | ---: | ---: | --- |
| Logs Insights 走査（prod）/ 日 | 20 GB | 36 GB | 60 GB | 中 = Q1〜Q4 × 1 日分（実測の取込量から）。低 = クエリをまとめて 2 本強に減らした場合、高 = ログ量の増加や手動の再実行を含む |
| Logs Insights 走査（prod）/ 月 | 600 GB | 1,080 GB | 1,800 GB | × 30 日 |
| GuardDuty CloudTrail 管理イベント（東京）/ 月 | 2,150,000 件 | 2,150,000 件 | 2,150,000 件 | 1 日数万件の実測を月に換算した見積の入力値 |
| GuardDuty CloudTrail 管理イベント（us-east-1 / us-west-2）/ 月 | 1,170 / 90 件 | 1,170 / 90 件 | 1,170 / 90 件 | どちらも 1 日数十件以下 |
| GuardDuty VPC フローログ・DNS ログ（東京）/ 月 | 5 GB | 20 GB | 100 GB | 既存のシナリオ幅を保持（EC2 数台、VPC 内 Lambda 数本）。短期実測は下記。高は悲観値 |
| GuardDuty S3 データイベント / 月 | 0 件 | 0 件 | 0 件 | 2026-10-19 に停止（#1022）。残す場合は実測の量（1 日数百万件規模）× $0.00000104 で月 約 $62 |
| GuardDuty Lambda ネットワーク活動 / 月 | 5 GB | 20 GB | 50 GB | 短期実測は下記。既存のシナリオ幅を保持 |
| CloudWatch 要求メトリクス（任意: 追加候補のバケット） | 0 バケット | 1 バケット・16 指標 | 1 バケット・16 指標 | 追加候補の 1 バケット 16 指標 × $0.30 = $4.80。prod 配布時に判断 |

> dev は `auto_run_enabled` が偽のため、Logs Insights・GetMetricData・S3 を読まない（0 で計上）。dev で動作確認のためにレポートを 1 回流すと約 $0.54（AI $0.264 + 走査等 約 $0.28。中: Insights 36 GB、GetMetricData 既存 300 + S3 約 175 メトリクス、S3 1,310 GET）かかり、dev でも毎日流すと月 約 $16.29（Insights $8.208 + GetMetricData $0.1425 + S3 GET / LIST $0.02185 + AI $7.92）増える。アカウント側で有効化した要求メトリクスの固定費は重複計上しない（DESIGN 3 章で dev を既定で止める理由）。
>
> alarmhuba-backend Resolver のログ量は、2026-10-08〜10-14（JST の 7 日）の CloudWatch 指標 `AWS/Logs` `IncomingBytes` で 1 日数 GB だった（読み取りで確認）。4 本分の走査量は中シナリオの 36 GB/日とほぼ一致する。
>
> GuardDuty の us-east-1 / us-west-2 には EC2 も VPC 内 Lambda も無いため、両リージョンの VPC フロー・DNS と Lambda ネットワーク活動は既存のシナリオでは 0。S3 データイベントは停止後 0 とする。米国 2 リージョンの短期実測は下記に分けて記録する。

GuardDuty 実測注記（2026-10-19、ブリーフ提供の使用量 API）: 有効化から約 4 日の実測で、費用の大半を S3 の費目が占めた。S3 を除いて月に換算すると約 $18（低〜中の範囲）。S3 の費目が大半を占め、特定バケットだけを対象から外す設定も無いため、費用対効果から S3 Protection を 2026-10-19 に停止した（#1022）。短期実測から残る費目のシナリオを一点へ絞らず、既存の数量幅を保持する。

### 5.2 サービス別コスト（アプリ本体、無料枠適用前）

| サービス | 算出根拠（prod） | dev | prod |
| --- | --- | ---: | ---: |
| API Gateway（HTTP API） | 500 × $0.00000129 | $0.00 | $0.00 |
| Lambda リクエスト | 540 × $0.0000002 | $0.00 | $0.00 |
| Lambda 実行時間 | 9,980 GB-秒 × $0.0000166667 | $0.00 | $0.17 |
| DynamoDB 書込 | 2,000 WRU × $0.000000715 | $0.00 | $0.00 |
| DynamoDB 読込 | 55,000 RRU × $0.0000001425 | $0.00 | $0.01 |
| DynamoDB ストレージ | 0.02 GB（25 GB まで $0） | $0.00 | $0.00 |
| DynamoDB PITR | 0.02 GB × $0.228（prod のみ） | $0.00 | $0.00 |
| EventBridge Scheduler | 30 起動（1,400 万まで $0） | $0.00 | $0.00 |
| EventBridge ルール | 月 0〜10 件（AWS サービスのイベントは課金なし） | $0.00 | $0.00 |
| SQS | 100 × $0.0000004 | $0.00 | $0.00 |
| SES 送信 | 40 通 × $0.0001 | $0.00 | $0.00 |
| CloudWatch Logs 取込 | 0.01 GB × $0.76 | $0.00 | $0.01 |
| CloudWatch Logs ストレージ | 0.12 GB × $0.033（保持 365 日） | $0.00 | $0.00 |
| CloudWatch アラーム | 5 × $0.10 | $0.50 | $0.50 |
| HTTP API 詳細メトリクス | 5 × $0.30 | $1.50 | $1.50 |
| CloudWatch GetMetricData（S3 区分を含む） | (9,000 + 5,250) × $0.00001 | $0.00 | $0.14 |
| S3 GET（cloudtrail-example） | 40,000 × $0.00000037 | $0.00 | $0.01 |
| S3 LIST（cloudtrail-example） | 1,500 × $0.0000047 | $0.00 | $0.01 |
| 合計（無料枠適用前、Logs Insights・Bedrock・任意の S3 要求メトリクスを除く） | | $2.01 | $2.36 |

> 各行はセント単位に丸めて表示しているため、表示値を足し上げた額と合計欄がずれることがある。合計欄は丸め前の値で計算している。

### 5.3 サービス別コスト（シナリオで変わる項目、無料枠適用前）

| サービス | 算出根拠（中） | 低 | 中 | 高 |
| --- | --- | ---: | ---: | ---: |
| Logs Insights 走査（prod） | 1,080 GB × $0.0076 | $4.56 | $8.21 | $13.68 |
| GuardDuty CloudTrail 管理イベント（東京） | 2,150,000 件 × $0.00000472 | $10.15 | $10.15 | $10.15 |
| GuardDuty CloudTrail 管理イベント（us-east-1 + us-west-2） | 1,260 件 × $0.000004 | $0.01 | $0.01 | $0.01 |
| GuardDuty VPC フローログ・DNS ログ | 20 GB × $1.18 | $5.90 | $23.60 | $118.00 |
| GuardDuty S3 Protection | 0 件（2026-10-19 停止） | $0.00 | $0.00 | $0.00 |
| GuardDuty Lambda Protection | 20 GB × $1.18 | $5.90 | $23.60 | $59.00 |
| CloudWatch 要求メトリクス（任意: 追加候補のバケット） | 1 バケット × 16 指標 × $0.30 | $0.00 | $4.80 | $4.80 |
| GuardDuty 小計（アカウント単位・1 回だけ計上） |  | $21.95 | $57.35 | $187.15 |
| 合計（シナリオで変わる項目） |  | $26.51 | $70.36 | $205.63 |

GuardDuty S3 Protection の備考: 2026-10-19 に停止（#1022）。残す場合は実測の量（1 日数百万件規模）× $0.00000104 で月 約 $62。

CloudWatch 要求メトリクスの備考: prod 配布時に判断。既存の要求メトリクスの費用は他システム側で、本見積の対象外。任意追加は 1 バケット $4.80/月。

### 5.4 計算根拠

```
[Logs Insights — prod]
  alarmhuba-backend Resolver の取込量 1 日数 GB（2026-10-08〜14 の IncomingBytes）
  Q1〜Q4 が各 1 日分を走査し、Q5（GPS モック）は数 MB
  低: 20 GB/日 × 30 日 =   600 GB × $0.0076 = $4.560
  中: 36 GB/日 × 30 日 = 1,080 GB × $0.0076 = $8.208
  高: 60 GB/日 × 30 日 = 1,800 GB × $0.0076 = $13.680
  常時無料枠 5 GB（取込 + アーカイブ保存 + Insights 走査、アカウント共有）は
  基盤だけで使い切っている（基盤の見積 6 章で 5.2 GB）→ 全量を課金で計上
  クエリ 1 本あたり 1 日分 × 30 日 × $0.0076（中シナリオの 36 GB/日を 4 本で割ると約 $2.05/月）

[GuardDuty — アカウント単位（3 リージョン）]
  CloudTrail 管理イベント 東京      2,150,000 件 × $0.00000472 = $10.148
                          us-east-1     1,170 件 × $0.000004   =  $0.005
                          us-west-2        90 件 × $0.000004   =  $0.000
  VPC フロー・DNS（東京） 低 5 / 中 20 / 高 100 GB × $1.18
                          = $5.90 / $23.60 / $118.00（500 GB までは第 1 段の単価）
  S3 Protection           低 / 中 / 高とも 0 件 = $0.00（2026-10-19 に停止、#1022）
                          残す場合: 実測の量（1 日数百万件規模）× $0.00000104 ≈ 月 $62
  Lambda Protection       低 5 / 中 20 / 高 50 GB × $1.18 = $5.90 / $23.60 / $59.00
  ------------------------------------------------------------
  合計                     低 $21.95 / 中 $57.35 / 高 $187.15
  うち基盤（CloudTrail + VPC フロー・DNS。無効化できない）
                           低 $16.05 / 中 $33.75 / 高 $128.15
  うち任意（Lambda Protection。S3 Protection は停止）
                           低 $5.90 / 中 $23.60 / 高 $59.00

[Lambda 実行時間]
  prod: api              500 回 × 0.3 秒 × 0.5 GB  =    75 GB-秒
        report            30 回 × 330 秒 × 1 GB    = 9,900 GB-秒（集計 180 秒 + AI 150 秒）
        guardduty-alert   10 回 × 2 秒 × 0.25 GB   =     5 GB-秒
        合計 9,980 GB-秒 × $0.0000166667 = $0.166
  dev : api 200 回 × 0.3 秒 × 0.5 GB = 30、report 30 回 × 0.5 秒 × 1 GB = 15 → 45 GB-秒
  常時無料枠 400,000 GB-秒（アカウント共有）の内のため実費 $0.00

[CloudWatch の固定費 — 1 環境あたり]
  アラーム 5 × $0.10 = $0.50
  HTTP API 詳細メトリクス 5 × $0.30 = $1.50
  無料枠（アラーム 10 個・メトリクス 10 個、アカウント共有）は既存アプリが使い切り → 全額を計上

[GetMetricData — prod]
  既存: 300 メトリクス/日 × 30 日 = 9,000 × $0.00001 = $0.090
  S3: 実測 3 リージョンで数十バケット（ap-northeast-1 以外は 0）、約 175 メトリクス/日
      × 30 日 = 5,250 × $0.00001 = $0.0525（表示 $0.05、低 / 中 / 高とも）
  合計: 月 14,250 メトリクス = $0.1425（表示 $0.14、無料枠の対象外）

[CloudWatch 要求メトリクス — 任意、prod 配布時に判断]
  追加候補のバケット: 1 バケット 16 指標 × $0.30 = $4.80/月
  任意追加はこの 1 バケットのみ。低 $0.00 / 中 $4.80 / 高 $4.80
  日次ストレージ指標は全バケット無料。要求メトリクスは有効化済みのバケットのみ

[S3 cloudtrail-example の読み取り — prod]
  GET  1,310 ファイル/日 × 30 日 ≒ 40,000 × $0.00000037 = $0.015
  LIST 50 回/日 × 30 日 = 1,500 × $0.0000047 = $0.007
  KMS の呼び出しは生じず、同一リージョンのためデータ転送の費用も無い

[DynamoDB — prod]
  WRU  2,000 × $0.000000715  = $0.001
  RRU 55,000 × $0.0000001425 = $0.008
  PITR 0.02 GB × $0.228      = $0.005（テーブル本体は 25 GB の $0 レンジ内）
```

---

### 5.5 Bedrock（AI 判定）

Claude の系統だけを設定で選び、版は自動追従する（DESIGN 2.11）。以下は 2026-10-14 公開分の AWS 公式料金ページの表データをブリーフから採用した値で、AWS Pricing MCP に新しい Claude が未収載のため MCP 取得値ではない。単価は税別 USD / 100 万トークン。全モデルを ap-northeast-1 から呼ぶ。

| モデル | 経路 | 入力 | 出力 | キャッシュ読取 |
|---|---|---:|---:|---:|
| Fable 5.1 | global | $10.00 | $50.00 | $0.275 |
| Opus 5.5（既定） | jp（国内） | $4.40 | $22.00 | $0.22 |
| Sonnet 5.5 | global | $2.20 | $11.00 | $0.11 |
| Haiku 5.5 | jp（国内） | $0.11 | $0.55 | $0.011 |

数量は 1 日 1 回、入力 35,000（v2.1 で所見の時刻・場所、用語表、GuardDuty の検出明細、Lambda 上位 20 関数を要約に足した結果。2026-10-16 の本番実測 34,853。v2 は 15,556）、出力 5,000（思考分を含む。実測は約 1,300 で、費用の保守的な予算）、月 30 日。入力 1,050,000・出力 150,000 トークン / 月とする。キャッシュは実装で使わず、削減を見込まない。出力 5,000 は費用の保守的な予算で、推論の maxTokens=8,000（DESIGN 2.11）を変更するものではない。

| モデル | 1 回（入力 + 出力） | 日次 30 回 / 月 | 試行 20 回を追加 | 日次 + 試行 50 回 / 月 |
|---|---:|---:|---:|---:|
| Opus 5.5 | $0.154 + $0.110 = $0.264 | $7.92（約 $7.9） | +$5.28 | $13.20 |
| Fable 5.1 | $0.350 + $0.250 = $0.600 | $18.00 | +$12.00 | $30.00 |
| Sonnet 5.5 | $0.077 + $0.055 = $0.132 | $3.96 | +$2.64 | $6.60 |
| Haiku 5.5 | $0.00385 + $0.00275 = $0.00660 | $0.198（約 $0.20） | +$0.132 | $0.330 |

Opus は `(35,000 × 4.40 + 5,000 × 22.00) / 1,000,000 × 30 = $7.92`。設定画面の系統ごとの月額目安（v2 の数量で Opus 約 $5.3・Sonnet $2.64 等）は v2.1 の数量に合わせて更新が要る（次の改修で対応）。

- 7 章の合計には prod の既定 Opus の日次分 $7.92 だけを加える。dev の自動実行は停止、プレビューは都度課金のため定常合計には含めない。試行を月 20 回追加した場合は +$5.28（環境をまたいで合計 20 回の想定）。API は 20 回 / 日までであり、月間の実装上限を意味しない
- プレビューは保存済み集計で AI だけを呼ぶため、S3・Logs Insights の再走査費は発生しない。Lambda は最大 150 秒 × 20 回 = 3,000 GB-秒を追加（無料枠内）。DynamoDB の ACTOR / PREVIEW の増分は 100〜300 主体の読み書きと 1 日 TTL で誤差。認証情報レポート・ListRoles / GetRole は無料
- 版の自動追従で単価が変わり、フォールバック時は最初の要求の課金有無や再呼び出し分も変動しうる。日次に残した usage / model_id を月次で確認する
- Fable は global 経路と aws_review のデータ保持オプトインが必要で、セキュリティ用途の refusal は規則判定へ戻る（DESIGN 12.4）。学習不使用とは分けて利用条件を確認する

出典: [Amazon Bedrock 料金](https://aws.amazon.com/bedrock/pricing/)（AWS 公式の表データ、2026-10-14 公開分。ブリーフ提供の Web 取得値）。モデルカード・利用条件の公式入口は [Bedrock 対応モデル](https://docs.aws.amazon.com/bedrock/latest/userguide/models-supported.html)、[モデル利用の前提](https://docs.aws.amazon.com/bedrock/latest/userguide/model-access.html)。

---

### 5.6 「ログを見る」の都度課金（v2.1 第 2 段階）

詳細ページの「ログを見る」（admin のみ、DESIGN 6.2d・8 章）は、押したときだけ AWS に問い合わせる。定常の月額には含めず、使った分だけ増える。同じ問い合わせは 24 時間キャッシュされ、同時実行は 3 件まで。

| 対象 | 取得方法 | 1 回の費用（中） | 根拠 |
| --- | --- | ---: | --- |
| alarmhuba（社外からの管理 API・拒否） | Logs Insights 2 本（行の取得と状態コードの突き合わせ） | $0.14（約 21 円） | Resolver 1 日分 × 2 本の走査量 × $0.0076（2026-10-16 の本番実測で約 $0.14） |
| GPS 受信口 | Logs Insights 1 本 | $0.00 | Receiver のログは小さい |
| Lambda エラー | Logs Insights 1 本（その関数の当日分） | $0.00〜 | 関数のログ量次第（example-small-api は 0.44 MB ≒ $0。大きい関数でも数 GB） |
| CloudTrail | LookupEvents（イベント履歴 API） | $0 | 無料（1 秒 2 回の制限のみ） |
| GuardDuty | GetFindings | $0 | 無料 |
| 指標（1 分粒度） | GetMetricData 1〜2 メトリクス | $0.00 | $0.01 / 1,000 メトリクス |

月の目安: alarmhuba の照会を 10 回で +$1.4、30 回で +$4.2。8.1 の変動要因 10 に載せる。

## 6. 無料利用枠の適用

適用するのは常時無料枠のみ。無料枠はアカウント全体で共有されるため、基盤（プラットフォーム + saas-a-billing）と第二弾の課金アプリ（公開版では除外）が先に使っている分を差し引いて判定した。

| サービス | 常時無料枠 | 使用量の見込み | 判定 | 出典 |
| --- | --- | --- | --- | --- |
| Lambda | 100 万リクエスト + 400,000 GB-秒/月 | 本アプリ 770 リクエスト・10,025 GB-秒（基盤は 25,207 リクエスト・6,738 GB-秒） | 枠内 → $0 | Web 取得（aws.amazon.com/lambda/pricing/） |
| DynamoDB ストレージ | 25 GB-月 | 本アプリ 0.02 GB | 枠内 → $0 | MCP（sku 2HPSAWPXJ2JJJ6XY の $0 レンジ） |
| EventBridge Scheduler | 1,400 万起動/月 | 本アプリ 60 | 枠内 → $0 | MCP（sku 29C3758CBCYNXH8B の $0 レンジ） |
| CloudWatch Logs | 5 GB/月（取込 + アーカイブ保存 + Logs Insights の走査） | 基盤だけで 5.2 GB（基盤の見積 6 章） | 使い切り → 取込・保存・Insights 走査は全量課金 | Web 取得（aws.amazon.com/cloudwatch/pricing/） |
| CloudWatch アラーム | 10 アラームメトリクス/月（標準分解能） | 既存 20 個（dev 10 + prod 10）+ 本アプリ 10 個 | 使い切り → 全量課金 | Web 取得（同上） |
| CloudWatch メトリクス | 10 メトリクス（カスタム + 詳細モニタリング） | 既存の HTTP API 詳細メトリクス 20 個 + 本アプリ 10 個。S3 要求メトリクス任意追加は低 0 / 中・高 16 指標 | 使い切り → 全量課金 | Web 取得（同上） |
| CloudWatch GetMetricData | 無料枠の対象外（API リクエスト 100 万件の枠に含まれない） | 14,250 メトリクス（うち S3 5,250） | 全量課金 | Web 取得（同上） |
| SQS | 100 万リクエスト/月 | 基盤だけで 130 万（基盤の見積 6 章） | 使い切り → 全量課金（本アプリの増分は 100 リクエスト） | 基盤の見積の Web 取得値（2026-08-28） |
| GuardDuty | 30 日無料試用（リージョンごと） | 2026-10-15〜11-14 | 期間中は $0、11-15 以降は全額 | Web 取得（aws.amazon.com/guardduty/pricing/） |
| API Gateway / SES | 常時無料枠なし（12 か月無料枠は適用しない） | — | 全量課金 | 基盤の見積と同じ扱い |

### 無料枠適用後のサービス別内訳（アプリ本体）

| サービス | dev | prod（低） | prod（中） | prod（高） |
| --- | ---: | ---: | ---: | ---: |
| CloudWatch アラーム + HTTP API 詳細メトリクス | $2.00 | $2.00 | $2.00 | $2.00 |
| Logs Insights 走査 | $0.00 | $4.56 | $8.21 | $13.68 |
| CloudWatch GetMetricData（S3 区分を含む） | $0.00 | $0.14 | $0.14 | $0.14 |
| CloudWatch 要求メトリクス（任意: 追加候補のバケット） | $0.00 | $0.00 | $4.80 | $4.80 |
| S3（GET + LIST） | $0.00 | $0.02 | $0.02 | $0.02 |
| DynamoDB（WRU + RRU + PITR） | $0.00 | $0.01 | $0.01 | $0.01 |
| CloudWatch Logs（取込 + ストレージ） | $0.00 | $0.01 | $0.01 | $0.01 |
| API Gateway / SQS / SES | $0.00 | $0.00 | $0.00 | $0.00 |
| Lambda / Scheduler / EventBridge ルール / DynamoDB ストレージ | $0.00 | $0.00 | $0.00 | $0.00 |
| Bedrock（既定 Opus、日次 30 回） | $0.00 | $7.92 | $7.92 | $7.92 |
| 合計（アプリ、無料枠適用後） | $2.00 | $14.67 | $23.12 | $28.59 |

> GuardDuty には無料期間（30 日）の後の常時無料枠が無いため、5.3 の額がそのまま月額になる。各行はセント単位に丸めて表示している。

---

## 7. 月額合計

無料枠適用後、GuardDuty の無料期間（2026-11-14 まで）の後の定常月。「ログを見る」の都度課金（5.6）は含めない。

| 区分 | 低 | 中 | 高 |
| --- | ---: | ---: | ---: |
| dev（アプリ） | $2.00 | $2.00 | $2.00 |
| prod（アプリ。Logs Insights・Bedrock・S3 区分を含む） | $14.67 | $23.12 | $28.59 |
| GuardDuty（アカウント単位・3 リージョン） | $21.95 | $57.35 | $187.15 |
| 合計（USD） | $38.63 | $82.48 | $217.75 |
| 合計（JPY、1 USD = 150 円） | ¥5,795 | ¥12,372 | ¥32,663 |
| 年額換算（JPY） | ¥69,537 | ¥148,464 | ¥391,953 |
| 参考: 無料枠適用前の合計（USD） | $38.80 | $82.65 | $217.92 |

### GuardDuty の構成別（S3 Protection 停止後、Lambda Protection の判断材料）

dev + prod + GuardDuty の合計（USD）。中・高には S3 要求メトリクスの任意追加 $4.80 を含める。残す場合の参考行でも CloudWatch の監視費は同じ前提とする。

| 構成 | 低 | 中 | 高 |
| --- | ---: | ---: | ---: |
| v2.2（S3 Protection 停止、Lambda Protection は有効） | $38.63 | $82.48 | $217.75 |
| S3 Protection を残す（参考、実測の量で +$62.40） | $101.03 | $144.88 | $280.15 |
| Lambda Protection も外す（GuardDuty は基盤のみ） | $32.73 | $58.88 | $158.75 |
| 無料期間中（〜2026-11-14。GuardDuty は $0） | $16.68 | $25.13 | $30.60 |

- 2026-11 の請求は、GuardDuty が 11-15〜11-30 の 16 日分（月額の約 53%）になる見込み。
- dev + prod のアプリ本体（Bedrock・S3 区分を含む）は低 / 中 / 高で $16.68 / $25.13 / $30.60。うち $4.00 はアラームと HTTP API 詳細メトリクスの固定費。中・高の要求メトリクス $4.80 は prod 配布時の任意判断。無料期間中も CloudWatch の費用は発生する。

### コスト構成比（中シナリオ）

| 順位 | 項目 | 月額（USD） | 構成比 |
| --- | --- | ---: | ---: |
| 1 | GuardDuty VPC フローログ・DNS ログ | $23.60 | 28.6% |
| 2 | GuardDuty Lambda Protection | $23.60 | 28.6% |
| 3 | GuardDuty CloudTrail 管理イベント（3 リージョン） | $10.15 | 12.3% |
| 4 | Logs Insights 走査（prod） | $8.21 | 10.0% |
| 5 | Bedrock（Opus、日次 30 回） | $7.92 | 9.6% |
| 6 | CloudWatch 要求メトリクス（任意追加 追加候補のバケット、1 バケット） | $4.80 | 5.8% |
| 7 | CloudWatch アラーム + HTTP API 詳細メトリクス（dev + prod） | $4.00 | 4.8% |
| 8 | その他（GetMetricData・S3・DynamoDB・Logs・SES など） | $0.20 | 0.2% |
| 9 | GuardDuty S3 Protection（停止） | $0.00 | 0.0% |

> 中シナリオでは GuardDuty が 69.5%。S3 Protection は $0 とし、主な費目は VPC フロー・DNS と Lambda Protection。要求メトリクスの任意追加 $4.80 は全体の 5.8%。アプリの走査費は Logs Insights のクエリ本数・走査範囲で変わる。数量幅は短期実測だけで確定せず保持した（5.1・8.1）。

---

## 8. 注意点・コスト変動要因

### 8.1 従量のため変動しうる項目

| # | 項目 | 変動要因 | 影響 |
| --- | --- | --- | --- |
| 1 | GuardDuty VPC フローログ・DNS ログ | 3.9 日の短期実測あり（5.1）。EC2 の通信と DNS 問い合わせの量で決まる。基盤データソースのため無効化できない。低・中は DESIGN 付録 B の $1〜24/月に合わせ、高 100 GB は悲観値 | 1 GB 増えるごとに +$1.18（中 20 GB で $23.60、高 100 GB で $118.00）。500 GB を超えた分は $0.59/GB |
| 2 | GuardDuty S3 データイベント | 2026-10-19 に停止済み（#1022）。特定バケットの除外は不可 | 定常 $0。残す場合は実測の量で月 約 $62 |
| 3 | GuardDuty Lambda ネットワーク活動 | アカウント内の全 Lambda 関数のネットワーク通信量 | 1 GB ごとに +$1.18 |
| 4 | GuardDuty CloudTrail 管理イベント | アカウント内の API 呼び出し数（1 日数万件の大半は Lambda などの自動処理） | 100 万件ごとに +$4.72 |
| 5 | Logs Insights の走査量 | クエリ本数 × 走査範囲（alarmhuba-backend Resolver 1 日分。数 GB）。Resolver のログ量の増減、手動の再実行（1 回 約 $0.28 の走査等 + Opus $0.264）、dev での確認 | クエリ 1 本あたり月 約 $2.10。Resolver のログが 2 倍になると中で +$8.21 |
| 6 | dev のレポート | dev は既定で止める（DESIGN 3 章）。止め忘れると prod と同じ走査がもう 1 回分かかる | 中で月 +$16.29（走査等 $8.37 + AI $7.92。要求メトリクスの固定費は重複計上しない） |
| 7 | CloudTrail のログファイル数（S3 GET） | 証跡のマルチリージョン化（DESIGN 14 章）でリージョンのフォルダが増える | 1,000 ファイル/日増えても +$0.01/月 |
| 8 | 為替レート | 1 USD = 150 JPY の参考換算 | 10 円動くと中で月 ±¥825 |
| 9 | Bedrock の版・トークン数・プレビュー | 自動追従、長い見解や文脈、手動試行 | Opus は 1 回 $0.264、月 20 回の試行で +$5.28。Sonnet $3.96 / 月、Fable $18 / 月、Haiku 約 $0.20 / 月（5.5） |
| 10 | 「ログを見る」の走査量（v2.1） | admin の操作回数 × 対象ログの 1 日分（alarmhuba は 1 日分を 2 本、Lambda はその関数の当日分、GPS は小さい）。同じ問い合わせは 24 時間キャッシュ | alarmhuba 1 回 +$0.14（約 21 円）。月 30 回で +$4.2 |
| 11 | S3 区分の GetMetricData | バケット数・StorageType 数・有効な要求メトリクス数。API 回数ではなく取得メトリクス数で課金 | 実測 約 175/日 × 30 日 × $0.00001 = $0.0525（表示 $0.05）/月。日次ストレージ指標の発行は全バケット無料 |
| 12 | CloudWatch 要求メトリクスの任意追加 | 追加候補のバケット。prod 配布時に判断 | 追加候補の 1 バケット 16 指標 × $0.30 = $4.80/月。低 $0 / 中・高 $4.80 |

### 8.2 構成変更によるコスト影響

- S3 Protection は 2026-10-19 に停止済み（#1022）で、本見積は停止後 $0。残す場合は実測ベースで約 +$62/月。代替の S3 区分は容量・件数の前日比と有効バケットの要求基準比で翌朝に検知する。日次ストレージ指標は 1 日 1 点で反映が遅れる。
- Lambda Protection を外す: 低 -$5.90 / 中 -$23.60 / 高 -$59.00。Lambda 関数の通信（不審な宛先への接続など）に基づく検出が無くなる。
- EC2 Runtime Monitoring（参考・未計上）: 対象 EC2 の vCPU あたり $2.00/月。料金ページには、GuardDuty のエージェントが動いている EC2 の VPC フローログの分析は課金されないとある（"no charge for analysis of VPC Flow Logs from instances where the GuardDuty agent is deployed and active"）。VPC フロー分の費用が Runtime Monitoring の費用（vCPU 数 × $2.00）を超え、その大半が EC2 由来なら、Runtime Monitoring を有効にした方が安くなる場合がある（料金ページは VPC フローログについてだけ書いており、DNS ログの扱いは書いていない）。
- Logs Insights のクエリをまとめる: Q1 と Q2 はどちらも alarmhuba-backend のリクエスト行を読むので、1 本にまとめられれば月 約 -$2.10。低シナリオ（20 GB/日）はおおむね 2 本強に減らした場合に当たる。
- Logs Insights の区分を設定画面で OFF にする: prod 低 -$4.56 / 中 -$8.21 / 高 -$13.68（5.3 の走査量がゼロになる）。S3 区分を OFF にすると取得費 $0.05/月が減るが、要求メトリクスの発行費は AWS 側で無効化するまで減らない。要求メトリクスの任意追加を見送ると中・高はそれぞれ -$4.80。他の従来区分の OFF は月 $0.1 未満。GuardDuty の区分 OFF は読み取りを止めるだけで GuardDuty の料金は変わらない。
- HTTP API の詳細メトリクスを無効にする: 1 環境あたり -$1.50（dev + prod で -$3.00）。ルート別のメトリクスは見えなくなるが、API 全体のメトリクスは残る。
- us-east-1 / us-west-2 の重大検出を即時通知するため、転送用の EventBridge ルールを足す場合（DESIGN 3.2）: 転送する件数は月数件で、仮にカスタムイベントの単価（$1.00/100 万件）で見ても $0.00 に丸まる（本書では未計上）。

### 8.3 その他

- 本書はアプリ単体の増分である。基盤（プラットフォーム + saas-a-billing）の費用は [`../../COST-ESTIMATE.md`](../../COST-ESTIMATE.md)（v0.4、無料枠適用後 dev + prod $0.49/月）に含まれ、本書の合計には入れていない。アラーム・メトリクス・Logs の常時無料枠は、基盤と既存アプリが先に使っている前提で判定した（6 章）。
- CloudWatch アラームの常時無料枠（10 アラームメトリクス）は第二弾の課金アプリ（公開版では除外）の追加で既に超えているため、本アプリのアラーム（dev 5 + prod 5）は全数を課金で計上した。基盤の見積 v0.4 の「dev 5 + prod 5 でちょうど 10 個」は古い（基盤の見積側の更新は本書の範囲外）。
- VPC フローログは、顧客側で有効にしていなくても GuardDuty が独立した複製ストリームから解析する。VPC フロー・DNS の費用は使用量 API の実測を使って確認する。2026-10-19 の値は 3.9 日の短期値のため、引き続き月次で確認する。
- GuardDuty はアカウント単位で、alarmhuba-backend や GPS モックなど他のシステムの API 呼び出し・通信も分析の対象になる。費用は daily-security-report のスタックではなくアカウントに付き、dev / prod のスタックを消しても止まらない（止めるには検出器を無効にする）。
- GuardDuty の無料期間は 2026-11-14 まで。S3 Protection は 2026-10-19 に停止（#1022。無料期間を待たず即日）した。追加候補のバケット の要求メトリクス追加（任意）は見送り。停止・有効化の AWS 操作は本書の改訂作業では行わない。
- v1 の AWS 単価は 2026-10-15、v2 の Bedrock 単価はブリーフ提供の 2026-10-14 公開分を使った。v2.2 の S3 数量・要求メトリクス単価・GuardDuty 実測は 2026-10-19 のブリーフ提供値で、新たな Pricing MCP / Web 取得値ではない。Excel（公開版では省略）は既存の 6 シートと SUM・円換算式を保持して更新し、再計算値を本書へ転記する。HTML は両 Markdown から再生成し、PDF は呼び出し元が別環境で再生成する。
- 無料枠はアカウント全体で共有される。本書は Lambda・DynamoDB ストレージ・Scheduler だけを枠内とした。これらも枠を外れた場合の差は月 $0.17 未満（プレビュー分を除く）（7 章の「無料枠適用前の合計」）。
- EC2 と Lightsail のインスタンス指標（2026-10-15 の設計追加。DESIGN 2 章・6 章）は、EC2 の台数分の `GetMetricData` が月 約 500 メトリクス増える（約 $0.005）だけで、Lightsail の `GetInstanceMetricData` は無料である。この約 $0.005 は既存分の誤差として保持し、v2.2 の S3 取得増分 $0.05 は明示的に合計へ追加した。
- AWS の料金は変更される可能性がある。基盤単価は 2026-10-15 取得、Bedrock は 2026-10-14 公開分の提供値による。

---

## 9. 関連ドキュメント

| 種別 | パス | 用途 |
| --- | --- | --- |
| アプリ設計書 | [`DESIGN.md`](DESIGN.md) | リソース構成、集計 6 種（S3 を含む）、Logs Insights のクエリ、AI 判定、dev の運用（レポートを既定で止める）、GuardDuty の費用試算（付録 B） |
| 基盤の料金見積 | [`../../COST-ESTIMATE.md`](../../COST-ESTIMATE.md) | プラットフォーム + saas-a-billing の月額。無料枠の使用状況（6 章）、HTTP API 詳細メトリクスの注記（4 章） |
| 基盤の設計書 | [`../../DESIGN.md`](../../DESIGN.md) | 通知基盤（notify キュー・notifier）、ログ保持 365 日 |
| Excel 版 | 料金見積の Excel は公開版では省略（同じ内容は COST-ESTIMATE.md） | 同内容を Excel の表形式で |

---

## 10. 付録: AWS Pricing MCP 取得 sku 一覧

| サービス | productFamily / group | usagetype | sku | リージョン |
| --- | --- | --- | --- | --- |
| AmazonGuardDuty | Paid CloudTrail Events Processed | APN1-PaidEventsAnalyzed | TFHCE6Z4YPX3UBZM | ap-northeast-1 |
| AmazonGuardDuty | Paid CloudTrail Events Processed | USE1-PaidEventsAnalyzed | KM3AH2Q87UZFX9MJ | us-east-1 |
| AmazonGuardDuty | Paid CloudTrail Events Processed | USW2-PaidEventsAnalyzed | MW5UU98V8Z3AKYJQ | us-west-2 |
| AmazonGuardDuty | Paid Data Events Processed（VPC フロー・DNS） | APN1-PaidEventsAnalyzed-Bytes | B8T9U7C33PWDWSWX | ap-northeast-1 |
| AmazonGuardDuty | Paid Data Events Processed（VPC フロー・DNS） | USE1-PaidEventsAnalyzed-Bytes | PXTAN6UDSEZEEUUP | us-east-1 |
| AmazonGuardDuty | Paid Data Events Processed（VPC フロー・DNS） | USW2-PaidEventsAnalyzed-Bytes | 5BRYZ3RVF78ZSHRW | us-west-2 |
| AmazonGuardDuty | Paid S3 Data Events Processed | APN1-PaidS3DataEventsAnalyzed | AKNZG2PH483CPE6D | ap-northeast-1 |
| AmazonGuardDuty | Paid Lambda Data Events Processed | APN1-PaidLambdaNetworkLogsAnalyzed-Bytes | QNCGZXZ3RYVQB4VH | ap-northeast-1 |
| AmazonGuardDuty | Paid EC2 vCPU Monitored（参考） | APN1-PaidEC2vCPUMonitored | N8N7G5B7GAXMPUNJ | ap-northeast-1 |
| AmazonCloudWatch | Data Payload / Queried Logs | APN1-DataScanned-Bytes | QE3CDKDURP2A2WR5 | ap-northeast-1 |
| AmazonCloudWatch | API Request / CloudWatch Bulk API Requests | APN1-CW:GMD-Metrics | 6YJR7DASEN49YFY6 | ap-northeast-1 |
| AmazonCloudWatch | API Request / CloudWatch Bulk API Requests | CW:GMD-Metrics | ST83NCNZMVRKWEVJ | us-east-1 |
| AmazonCloudWatch | Metric / CloudWatch Custom Metrics | APN1-CW:MetricMonitorUsage | F96ZRPX8AM4T93NV | ap-northeast-1 |
| AmazonCloudWatch | Alarm | APN1-CW:AlarmMonitorUsage | FBMB6TVC844YGZ3J | ap-northeast-1 |
| AmazonCloudWatch | Data Payload / Ingested Logs | APN1-DataProcessing-Bytes | CWB2GTZJXNX3TA6H | ap-northeast-1 |
| AmazonCloudWatch | Storage Snapshot / Standard Storage | APN1-TimedStorage-ByteHrs | 42CVQU4BPES8AWHC | ap-northeast-1 |
| AmazonApiGateway | API Calls / HTTP API Requests | APN1-ApiGatewayHttpRequest | 5TU3JKA59EGYZ8XY | ap-northeast-1 |
| AWSLambda | Serverless / AWS-Lambda-Requests | APN1-Request | 3BE8DYKG4FYSZGDW | ap-northeast-1 |
| AWSLambda | Serverless / AWS-Lambda-Duration | APN1-Lambda-GB-Second | FSYUV9NMNDEXRJ5H | ap-northeast-1 |
| AmazonDynamoDB | PayPerRequest Throughput / DDB-WriteUnits | APN1-WriteRequestUnits | HBK75W4NW4DYPFAC | ap-northeast-1 |
| AmazonDynamoDB | PayPerRequest Throughput / DDB-ReadUnits | APN1-ReadRequestUnits | TX6BW4TWG2FZQMPQ | ap-northeast-1 |
| AmazonDynamoDB | Database Storage | APN1-TimedStorage-ByteHrs | 2HPSAWPXJ2JJJ6XY | ap-northeast-1 |
| AmazonDynamoDB | Database Storage（PITR） | APN1-TimedPITRStorage-ByteHrs | ZZYBMDK8NF9E89RN | ap-northeast-1 |
| AWSEvents | EventBridge / Scheduler | APN1-ScheduledInvocation | 29C3758CBCYNXH8B | ap-northeast-1 |
| AWSEvents | EventBridge / Custom Event（参考） | APN1-Event-64K-Chunks（PutEvents） | SG5BXRDK8SBA9Y57 | ap-northeast-1 |
| AWSEvents | EventBridge / Cross-Account AWS Event（参考） | APN1-Invocation-64K-Chunks | FYGDQTAUXE9MYWM7 | ap-northeast-1 |
| AWSQueueService | API Request / SQS-APIRequest-Tier1 | APN1-Requests-Tier1 | 5Q2SBURHPCGERHBD | ap-northeast-1 |
| AmazonS3 | API Request / S3-API-Tier2（GET） | APN1-Requests-Tier2 | 9C7873KU3ZQBD6BJ | ap-northeast-1 |
| AmazonS3 | API Request / S3-API-Tier1（LIST） | APN1-Requests-Tier1 | XAKHUSC46NWC9PPM | ap-northeast-1 |
| AmazonSES | Sending Email / Send | APN1-Recipients | 824QF4XUVN48GVC9 | ap-northeast-1 |

### Web 取得で補った項目

AWS Pricing MCP は単価を返すが、常時無料枠の量や無料試用の条件は返さない。以下は AWS 公式サイト（aws.amazon.com）から取得した（2026-10-15）。サードパーティの情報は使っていない。

| 項目 | 値 | 出典 URL |
| --- | --- | --- |
| Bedrock v2 の Claude | 5.5 の単価表（USD / 100 万トークン）。新モデルは Pricing MCP 未収載のためブリーフ提供の Web 表データ（2026-10-14 公開分） | https://aws.amazon.com/bedrock/pricing/ |
| CloudWatch 無料枠 | Logs 5 GB（ingestion, archive storage, and data scanned by Logs Insights queries）、アラーム 10（標準分解能）、メトリクス 10（Custom Metrics and Detailed Monitoring Metrics）、API リクエスト 100 万（GetMetricData は対象外で常に課金） | https://aws.amazon.com/cloudwatch/pricing/ |
| GuardDuty 無料試用 | 30 日（"This free trial applies to each new AWS account in each Region"）。基盤の脅威検出は無効化できない（"The default threat detection in GuardDuty cannot be disabled"）。Runtime Monitoring のエージェントが動く EC2 の VPC フローログ分析は課金されない | https://aws.amazon.com/guardduty/pricing/ |
| Lambda 無料枠 | 100 万リクエスト + 400,000 GB-秒 / 月 | https://aws.amazon.com/lambda/pricing/ |
| EventBridge の AWS イベント | "AWS management events are ingested by the event bus for free" | https://aws.amazon.com/eventbridge/pricing/ |
| SQS 無料枠 | 100 万リクエスト / 月（基盤の見積の Web 取得値、2026-08-28） | https://aws.amazon.com/sqs/pricing/ |

### 読み取りで確認した値（2026-10-15、AWS の状態は変えていない）

| 項目 | 値 | 確認方法 |
| --- | --- | --- |
| alarmhuba-backend Resolver のログ量 | 1 日数 GB（2026-10-08〜14 JST） | `cloudwatch get-metric-statistics`（`AWS/Logs` `IncomingBytes`） |
| GuardDuty の検出器と機能 | 3 リージョンに検出器あり。東京は 2026-10-15 03:05 UTC 作成、5.1 の 5 機能が有効 | `guardduty list-detectors` / `get-detector` |
| GuardDuty の使用量 | 2026-10-15 03:14 UTC 時点で空（有効化直後のため） | `guardduty get-usage-statistics` |
| CloudTrail の証跡とバケット | 証跡の対象リージョン・S3 キー接頭辞と、`cloudtrail-example` の場所（ap-northeast-1）・暗号化の方式（証跡は 2026-10-15 にマルチリージョン化。DESIGN 14.1） | `cloudtrail describe-trails` / `s3api get-bucket-location` / `get-bucket-encryption` |
| EC2 | 稼働中の台数とインスタンスタイプ（VPC フロー・DNS の量の見込みに使用） | `ec2 describe-instances` |

---

## 11. 改訂履歴

| バージョン | 日付 | 改訂内容 | 改訂者 |
| --- | --- | --- | --- |
| v2.2 | 2026-10-19 | #1022。S3 Protection は 2026-10-19 停止として全シナリオ $0。3.9 日の GuardDuty 使用量 API 実測を記録。レビュー指摘で S3 取得量を実測 数十バケット・約 175 メトリクス/日（月 5,250、$0.0525）に修正。既存の要求メトリクスは見積外、任意追加は追加候補の 1 バケットのみ（低 $0 / 中・高 $4.80、prod 配布時に判断）。Excel の数式再計算値を小計・合計・構成比へ反映（月額 低 $38.63 / 中 $82.48 / 高 $217.75）。両 Markdown の HTML を再生成（PDF は呼び出し元が別環境で再生成） | 開発部 |
| v2.1 | 2026-10-19 | #1021。AI 判定の入力を本番実測（2026-10-16、34,853 トークン）に合わせ 35,000 に更新し、既定 Opus を $5.28 → $7.92、各合計を再計算（中 $95.79 → $98.42）。「ログを見る」の都度課金（5.6）、DynamoDB の日次集計サイズ（約 90 KB）、maxTokens 8,000 を追記。Excel と HTML / PDF も同じ内容で再生成 | 開発部 |
| v2 | 2026-10-16 | #1020。Bedrock の系統別単価・日次 / プレビューの見積、既定 Opus $5.28 を各合計へ追加。集計 Lambda 900 秒・AI 150 秒、IAM・ACTOR・PREVIEW を反映。Sonnet 概算は式に従い $2.64 に修正。派生ファイルは指揮側が再生成する。 | 開発部 |
| v0.1 | 2026-10-15 | 初版作成。AWS Pricing MCP（Price List API）で ap-northeast-1 / us-east-1 / us-west-2 の単価を取得し、無料枠の量は AWS 公式サイトで補った。アプリ本体は dev / prod の 1 本、GuardDuty と Logs Insights は低 / 中 / 高の 3 シナリオで試算した。GuardDuty の VPC フロー・DNS は DESIGN 付録 B に合わせて低 5 / 中 20 GB、高は悲観値 100 GB とした。Excel 版を併設（公開版では省略）。 | 開発部 |
