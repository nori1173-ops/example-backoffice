# AWS日次セキュリティレポート アプリ設計書（daily-security-report）

弊社 AWS アカウント（210000000001）全体を毎日点検し、不正アクセスの兆候を Slack とメールにレポートとして出す example-backoffice のアプリ。検出は AWS の GuardDuty に任せ、自前で作るのは「GuardDuty が見ない部分の集計」「Slack とメールへのレポート」「AI による判定と見解」「設定画面・レポート一覧・詳細」に絞る。

作成日: 2026-10-15
状態: v2.2 正本（#1022、S3 監視追加）。v1 は 2026-10-15 に prod 配布済み
dev-tracker: #1022（ブランチ `feature/#1022`）。v2 は #1020、v2.1 は #1021、#1019 は v1 として終了
改訂（2026-10-15）: 単独リポ example-daily-security-report から移管し、backoffice アプリ設計書（ADD-APP-GUIDE ステップ 1）の体裁へ書き直した
改訂（2026-10-15 レビュー反映）: 通知にメールを追加、設定画面とレポート一覧を第 1 段階に含める、即時通知は東京のみ、GuardDuty 有効化済み
改訂（2026-10-15）: EC2 / Lightsail の制限事項、構成変更の事象とインスタンス指標の追加、監視区分ごとの ON/OFF（`sections`）
改訂（2026-10-15 実装追随）: 集計・通知・保存・API の実装と、dev の初回実行（2026-10-14 分）で分かったことを反映した
改訂（2026-10-16、v2）: 証拠と判定の分離、3 段階の判定、Bedrock の AI 判定・見解、休眠主体・週次棚卸し、レポート詳細・プロンプト試行を追加
改訂（2026-10-16、dev 実機）: Bedrock の制約への対応（`toolChoice = auto`、思考ブロック、`maxTokens` 8000、content_filtered の再試行と global 経路への切替）と、通知の所見行・先頭行の体裁（件数は 1 回、code を出さない、0 件の段階を省く）
改訂（2026-10-19、v2.2）: GuardDuty S3 Protection の停止、CloudWatch AWS/S3 による S3 区分、データ契約・規則・画面・設定・IAM・費用を追加（#1022）
親設計書: [../../DESIGN.md](../../DESIGN.md)（example-backoffice プラットフォーム）
注記: 図は Python diagrams で生成した PNG（`images/gen_*.py`）。再生成は `/home/devA/.local/venvs/ref-project-b/bin/python images/gen_xxx.py`（出力先はスクリプト内の相対パス）

---

## 1. 目的

### 1.1 背景

- 弊社には AWS アカウント全体を継続して見る仕組みが無く、不審な出来事に翌朝までに気づける状態を作る必要があった
- 開発者本人がアカウントの状態を読み取りで調べた結果（付録 A）、入口ごとに防御とログの取得状況にばらつきがあり、全体を継続して見る仕組みが無いことを確かめた
- 本アプリは、その「見る仕組み」を最小構成で作る
- 2026-10-19 の決定（#1022）: GuardDuty S3 Protection は 2026-10-19 に停止した。判断理由は費用対効果で、S3 データイベントの解析が GuardDuty の費用の大半を占め、特定バケットだけを対象から外す設定も無いこと。代わりに CloudWatch AWS/S3 の容量・件数・要求メトリクスをレポートに加え、大量削除・持ち出しを翌朝に検知する（2.13・4.8）。使用量 API の費目別実測は COST-ESTIMATE 5.1

### 1.2 目的と範囲

目的: 毎朝 1 通のレポート（Slack とメール）で「昨日、弊社 AWS に不審なことがあったか」が分かる状態にする。東京リージョンの重大な検出はレポートを待たずに即時通知する。

範囲に含める:

- GuardDuty の検出（ap-northeast-1、us-east-1、us-west-2）
- CloudTrail の管理イベント（コンソールログイン、root 使用、IAM 変更、セキュリティグループ変更、EC2 / Lightsail の構成変更、AccessDenied の多発、人の操作の国と IP）
- CloudWatch 指標（WAF の許可/ブロック、API Gateway と ALB の 4XX/5XX、CloudFront の入口別件数、Lambda エラー）
- CloudWatch Logs Insights（alarmhuba-backend Resolver の社外からの管理 API 呼び出し・直接 URL 到達・403・国別・上位 IP、GPS モックサーバーの受信元）
- EC2 / Lightsail のインスタンス指標（CPU 使用率と NetworkOut）
- S3（CloudWatch `AWS/S3` 名前空間）の全バケットの日次ストレージ指標と、要求メトリクス有効バケットのダウンロード量・削除要求（2.13）
- 7 日平均との比較と 3 段階の判定、Amazon Bedrock の Claude による判定と見解
- IAM の休眠主体・初見の主体・他アカウントからのアクセス、週次の認証情報棚卸し
- admin 限定の設定画面（通知先・しきい値・除外リスト）と、全ユーザーが見られるレポート一覧・詳細

範囲に含めない:

- 各システム側の構成変更（ログの有効化・入口の制限・ポートの見直しなど）。本アプリは読み取りと通知だけを行う
- 検出への自動対処（遮断など）。本アプリは通知のみ

制限事項（2026-10-15 決定）:

- EC2 / Lightsail: レポートで見るのは GuardDuty の検出、CloudTrail の構成変更（2.3）、インスタンス指標（2.9）まで。インスタンス内部（OS・アプリのログ）の監視は本アプリの範囲外とする
- S3（v2.2）: 日次ストレージ指標は 1 日 1 点で反映が遅れる。要求メトリクスは有効化したバケットだけ。S3 Protection の代替として翌朝に検知する方式とした（4.8・13 章）。空にされたか削除されたバケットは日次指標が止まるため、指標が止まった時点で `S3_METRICS_STALE` / `S3_BUCKET_GONE` として検知する（2.13）

段階:

| 段階 | 内容 |
|---|---|
| v1（#1019、終了） | 日次の集計 5 種、東京の即時通知、Slack・メール通知、設定画面、レポート一覧。2026-10-15 に prod 配布済み |
| v2（#1020、本書の実装範囲） | 3 段階と AI 判定、通知と明細の分離、レポート詳細、休眠主体と棚卸し、設定画面でのプロンプト試行と「今すぐ実行」 |

v1 では通知に判定と明細を混在させ、一覧だけを提供した。v2 で通知は判定と該当項目に絞り、証拠は詳細画面に分離する形に変更（2026-10-16）。

### 1.3 決定事項（2026-10-15）

- example-backoffice のアプリ daily-security-report として作る（単独リポ example-daily-security-report から移管）。スタックは `example-backoffice-app-daily-security-report-{env}`
- GuardDuty を 3 リージョンで有効化する（2026-10-15 実施済み）。30 日無料（2026-11-14 まで）で試用する。2026-10-19 に S3 Protection を停止（1.1）。Lambda Protection の要否は実測で判断する
- レポートは 07:00 JST に送る
- dev-tracker チケットは #1019（example-backoffice プロジェクト）。ブランチは feature/#1019

レビューでの決定（2026-10-15）:

- 即時通知は東京（ap-northeast-1）のみ。us-east-1 / us-west-2 の重大な検出は翌朝のレポートで拾う（3.2）
- 通知は Slack とメールの両方。設定の仕様と設定画面は saas-a-billing（3.3・3.6・7.1 の `AUDIT`・8〜10 章）に倣い、admin 限定の設定画面を第 1 段階に含める（3.3・9 章）
- Slack は課金アプリと同じ仕組み（core の notifier、Bot ExampleBot）。dev の宛先は開発者本人の DM とメール `devA@example.co.jp`（3.7）
- GuardDuty の 3 リージョン有効化は実施済み（2026-10-15。4.1）。料金見積は [COST-ESTIMATE.md](COST-ESTIMATE.md)
- インスタンス内部の監視は範囲外（1.2）。CloudTrail の構成変更の事象とインスタンス指標はレポートに含める（2.3・2.9）
- 監視は区分ごとに設定画面で ON/OFF できる（3.3 の `sections`）。コストの高い区分（Logs Insights）を止められるようにするため

設計の既定として確定した事項（2026-10-15）: 除外の初期値（3.4）、dev は自動実行・即時通知とも既定で無効（3.7）、alarmhuba の直接 URL の判定は `X-Amz-Cf-Id` の有無（6.2）、国の判定は AWS の IP 範囲 + RIR 委任統計で MaxMind は使わない（2.5）、保持期間は日次集計 400 日・即時通知の記録 90 日（7.1）、しきい値と人 / 自動の判定規則は既定値で始めて調整期間に見直す（2.6）、Name タグ値 `Rule` の追加（16 章）、Lambda Protection の判断は 2026-11-10 ごろ（14.4）、第 2 段階へ移る目安（1.2）。

追加決定（2026-10-15）:

- prod の通知先は当面、開発者本人の DM と `devA@example.co.jp`。本番チャンネルは設定画面で後から選ぶ
- 管理 API のパスは正規表現（`alarmhuba_admin_path_regex`）で設定する。値は実ログのパス調査で決め、導入時に登録した（コードの既定値は架空のパス）
- backend.example-cloud.com の Web ACL は `WebACL-00000001`

単独リポからの移管で変わった点（原本 9 章）は本文に反映した: 通知は共通通知 `notify.send()`（3.6。Slack とメール。自前の Incoming Webhook と SSM SecureString は使わない）、日次集計・設定（除外リストを含む）・監査は DynamoDB 単一テーブル（7.1。S3 の state バケットは使わない）、日次バッチは ScheduleV2 から集計 Lambda を直接（3.1）、即時通知の EventBridge ルールは template.yaml（3.2）、集計対象は dev / prod とも本番アカウント（3.7）、読み取り専用 IAM でアカウント全体を読む（4 章・12.2）。

### 1.4 決定事項（2026-10-16、v2）

1. 判定は `critical` / `warning` / `ok`（表示は 🔴 Critical / 🟡 Warning / 🟢 OK）。規則を出発点とし、AI が理由付きで上下する。ただし規則で critical の所見を含む区分は下げない（2.10・2.11）
2. Slack・メールは判定と該当項目だけを載せ、明細は詳細ページへ分離する。Slack は絵文字、メールは `[Critical]` / `[Warning]` / `[OK]`。両方に詳細リンクを付け、OK の日も総括 1 行と AI の見解を送る
3. AI の見解は通知に常に載せ、一覧にも履歴として残す。読み手（管理者層）に、毎日点検していることが伝わるようにするため。取得できない日は「AI の見解: 取得できず（理由）」と明記する
4. AI は Amazon Bedrock の Claude 固定。ON/OFF、系統（opus / fable / sonnet / haiku、既定 opus）、ピン留め、プロンプトの上書きを設定できる。系統内の版は自動追従し、切替日にレポートへ 1 行出す
5. プロンプトは設定画面で過去の日を選び「この文面で試す」で確認してから保存する。試行はレポートの保存・送信と分離する
6. 30 日以上ぶりの IAM ユーザー / ロール、初見の主体、他アカウントのアクセスを検出する。社内 IP でも出す。毎週月曜に 90 日以上未使用のユーザー・有効キー、MFA 無しのコンソールユーザーを棚卸しする
7. プライベート IP（RFC 1918・ループバック・リンクローカル・IPv6 ULA）または `vpcEndpointId` 付きの CloudTrail は「社内（VPC 内）」とし、国外・社外・初見の判定から外す。GuardDuty Low は規則では ok とし、件数のみを残す
8. レポート一覧と詳細は全ログインユーザー、設定は admin。#1019 は v1 として終了し、v2 は #1020 で扱う
9. 設定画面に admin 用の「今すぐ実行」を置く。07:00 を待たずにレポートを出すためで、動作は Scheduler 起動と同じ（同じ集計・AI 判定・通知先・日次集計の保存。`trigger = manual`、`force = true`）。押す前に確認ダイアログを必ず出す（2026-10-16 ユーザー指示。3.3・8・9 章）
10. 既定プロンプトの本文を設定画面のテキストボックスに表示し、追記・修正と「既定に戻す」をできるようにする。既定本文と同じ文面・空白だけの文面は空に正規化して保存し、以後の既定改訂に追従する
11. submit_verdict で返す・本文テキストを書かない・呼び出し 1 回の固定末尾は本文から分離し、プログラムが結合する。固定末尾とユーザーメッセージの固定前置きは画面で編集不可のラベルとして見せる
12. 名前付きプロンプトは本文のみを最大 20 件、1 件 8,000 字・本文合計 20,000 字（両端の空白を除いた code point）まで保存し、カードで新規保存・上書き保存・名前変更・削除・適用する。「既定」は削除できない固定カードとし保存配列には含めない
13. レポートの Slack メンションは Critical のみ / Warning 以上 / 常にから選び、既定は Critical のみ。既存設定も項目が無ければ Critical のみとなり、即時通知は 3 値とも常に付ける。メールには付けない
14. 設定全体は保存するメタデータ込みの JSON（`ensure_ascii=False`）の UTF-8 バイト長を 180KB（180,000 バイト）までに制限する。AUDIT の before / after の複製に備える保険とし、超過と DynamoDB の項目・トランザクションのサイズ超過は 400 `SETTINGS_TOO_LARGE` で設定も監査も保存しない

#### 費用と工数の見込み（旧 1.4）

| 項目 | 見込み |
|---|---|
| 月額合計（低 / 中 / 高） | $38.63 / $82.48 / $217.75（¥5,795 / ¥12,372 / ¥32,663。1 USD = 150 円）。v2.2、S3 Protection 停止後・GuardDuty 無料期間後の定常月。中・高は任意の要求メトリクス $4.80 を含む |
| うち dev（アプリ） | $2.00 / $2.00 / $2.00。ほぼ全部がアラームと HTTP API 詳細メトリクスの固定費 |
| うち prod（アプリ。Logs Insights・Bedrock を含む） | $14.67 / $23.12 / $28.59（既定 Opus $7.92、S3 区分の GetMetricData $0.05 を含む）。中・高は任意の要求メトリクス $4.80 を含む。Logs Insights は中 $8.21/月、クエリは 5 本以内（6.2） |
| うち GuardDuty（アカウント単位・3 リージョン） | $21.95 / $57.35 / $187.15。S3 Protection は全シナリオ $0。3.9 日の使用量 API 実測は S3 以外で月換算約 $18。短期実測のため VPC フロー・DNS、Lambda Protection の既存のシナリオ幅を保持 |
| GuardDuty の無料期間 | 2026-11-14 まで（期間中の合計は低 / 中 / 高で $16.68 / $25.13 / $30.60）。S3 Protection は 2026-10-19 停止済み。Lambda Protection は実測で判断（14.4） |
| 工数 | 準備 0.5 日（14.1 の前提作業。GuardDuty の有効化と CloudTrail の見直しは実施済み）、実装 4.5 日（集計 5 種・レポート・メール本文を TDD で 3 日、設定とレポート一覧の API・画面・edge の追加で 1.5 日）、調整 1 日分（3〜4 日流してうるさい項目を削り、しきい値を決める。数日に分散）。合計 5.5〜6.5 人日 |

上表の工数は v1 設計時の見込みとして保持する。v2 の追加工数は別途管理する。Bedrock は Opus 5.5 jp で日次 30 回 $7.92/月、試行 20 回を追加すると +$5.28/月（v2.1 の入力 35,000 トークン予算。合計は日次分のみ）。

金額は [COST-ESTIMATE.md](COST-ESTIMATE.md)（同じフォルダ）の 7 章の月額合計。単価・無料枠・Protection の構成別の額も同書にある。v2.2 の金額は Excel の再計算値を転記し、S3 区分の GetMetricData 月 5,250 メトリクス × $0.00001 = $0.0525（表示 $0.05）/月と、任意の 追加候補のバケット の要求メトリクス（低 $0 / 中・高 $4.80、prod 配布時に判断）を反映した。インスタンス指標の追加で GetMetricData が月 約 500 メトリクス（約 $0.005）増えるが、誤差なので上の表には含めない（Lightsail の API は無料）。

## 2. 業務ルール

### 2.1 集計の単位と範囲

- レポートの対象は前日 1 日分。1 日 = JST 00:00:00 から翌日 00:00:00 の直前まで（翌日 00:00 は含まない）
- 対象アカウントは 210000000001 の 1 つだけ。dev / prod のどちらのサイトから動かしても同じデータを読む（3.7）
- 対象リージョン: GuardDuty は ap-northeast-1 / us-east-1 / us-west-2。CloudTrail は証跡が記録した全リージョン（2026-10-15 にマルチリージョン化済み）。入口の指標は ap-northeast-1 と us-east-1（CloudFront と CloudFront 用 WAF の指標は us-east-1 に出る）。S3 の指標は `GUARDDUTY_DETECTORS` の 3 リージョン（ap-northeast-1 / us-east-1 / us-west-2）。Logs Insights は ap-northeast-1。EC2 / Lightsail のインスタンスは ap-northeast-1（2026-10-15 時点で us-east-1 / us-west-2 は 0 台。ほかのリージョンに作られたら CloudTrail の構成変更で分かる）

### 2.2 GuardDuty の重大度の扱い

| 区分 | 重大度 | レポート | 即時通知 |
|---|---|---|---|
| Low | 1.0〜3.9 | 件数のみ | しない |
| Medium | 4.0〜6.9 | 新規は warning。検出ごとに 1 行の所見（同じ題名でも ID が違えば別行）。題名ごとの件数を上位表へ | しない |
| High | 7.0〜8.9 | 新規は critical。タイトルは詳細へ | する |
| Critical | 9.0〜10.0 | 新規は critical。タイトルは詳細へ | する |

- 新規 = 初回検出日時（`createdAt`）が対象日に入る検出。継続 = 初回検出が対象日より前で、更新日時（`updatedAt`）が対象日に入る検出（同じ検出の再発。件数だけ出す）
- アーカイブ済みの検出（抑制ルールで自動アーカイブされたものを含む）と、サンプル検出（`service.additionalInfo.sample` が真）は数えない
- 「正常」と判断した GuardDuty の検出は、アプリの除外リストではなく GuardDuty の抑制ルール（suppression rule）で自動アーカイブする。抑制された検出は EventBridge にも送られないため、即時通知にも効く。アプリの除外リストは CloudTrail・指標・Logs Insights の側にだけ効かせる

### 2.3 CloudTrail で拾う事象

| 事象 | 定義 | 所見の規則（段階は 2.10） |
|---|---|---|
| コンソールログイン | `eventSource = signin.amazonaws.com` かつ `eventName = ConsoleLogin`。成否は `responseElements.ConsoleLogin`、MFA の有無は `additionalEventData.MFAUsed` | 日本以外の IP からの成功、同じ主体の失敗が `login_failure_min`（既定 3）回以上、初見の IP（2.6） |
| root の使用 | `userIdentity.type = Root` かつ `invokedBy` なし | 1 件以上で critical。除外リストで消せない |
| IAM 変更 | `eventSource = iam.amazonaws.com` かつ `readOnly = false` | root または日本以外の IP からの変更（送信元が IP でない AWS サービスは国外とみなさない。2.4）。それ以外は主体と操作名を件数で出すだけ |
| セキュリティグループ変更 | `ec2.amazonaws.com` の `AuthorizeSecurityGroupIngress` / `AuthorizeSecurityGroupEgress` / `RevokeSecurityGroupIngress` / `RevokeSecurityGroupEgress` / `CreateSecurityGroup` / `DeleteSecurityGroup` / `ModifySecurityGroupRules` | `0.0.0.0/0` または `::/0` への受信許可を含む追加。それ以外は件数だけ |
| AccessDenied の多発 | `errorCode` が `AccessDenied` / `AccessDeniedException` / `UnauthorizedOperation` / `Client.UnauthorizedOperation` の件数を主体別に数える | 主体ごとの件数が `access_denied_min`（既定 20）以上で、かつその主体の 7 日平均の 3 倍を超える |
| 人の操作で日本以外の IP | 2.4 で「人」と判定した操作のうち、送信元が 2.5 で日本以外（不明を含む） | 1 件以上で warning（社内・VPC 内を除く） |
| 構成変更（Lightsail） | `eventSource = lightsail.amazonaws.com` のポートの変更 `OpenInstancePublicPorts` / `PutInstancePublicPorts` / `CloseInstancePublicPorts`、インスタンスの作成・削除 `CreateInstances` / `CreateInstancesFromSnapshot` / `DeleteInstance`、キーの操作 `CreateKeyPair` / `ImportKeyPair` / `DeleteKeyPair` / `DownloadDefaultKeyPair` | ポートの開放（Open / Put）で CIDR に `0.0.0.0/0` または `::/0` を含むものは常に critical（CIDR の指定が無いものも全世界への開放として扱う）。それ以外は表示のみ |
| 構成変更（EC2） | `eventSource = ec2.amazonaws.com` のインスタンスの作成・削除 `RunInstances` / `TerminateInstances`、AMI・スナップショットの公開・共有 `ModifyImageAttribute` / `ModifySnapshotAttribute`、キーペアの作成・取り込み `CreateKeyPair` / `ImportKeyPair` | AMI / スナップショットの公開（`all` への起動・ボリューム作成の許可の追加）は常に critical。それ以外は表示のみ |

- 対象は管理イベントのみ
- `sourceIPAddress` が IP でない事象（`*.amazonaws.com` など）は「AWS サービス」として表示に残し、国外判定（IAM 変更を含む）の対象から外す（2.4）
- 構成変更はレポートでは CloudTrail 欄の「構成変更」にまとめ、誰が（主体）・どこから（IP・国）を添える

### 2.4 人の操作と自動処理の区別

上から順に当てはめ、最初に当たった規則で決める。判定は `cloudtrail_summary.classify_actor()`（純粋関数）に閉じ、運用開始後の 3〜4 日で調整する（14.4）。

1. `sourceIPAddress` が IP アドレスでない（`lambda.amazonaws.com` などのサービス名、`AWS Internal`）→ AWS サービス。人・自動のどちらでもない第 3 の区分で、表示には残し、国外判定（IAM 変更を含む）の対象から外す
2. `invokedBy` がある、または `userIdentity.type` が `AWSService` → 自動
3. `userIdentity.type` が `Root` / `IAMUser` → 人（アクセスキーで動くスクリプトもここに入る。正規の機械ユーザーは除外リストの `principal` で外す）
4. `AssumedRole` のうち、`userAgent` がマネジメントコンソール由来、または `sessionContext.attributes.mfaAuthenticated` が真 → 人（スイッチロール）
5. それ以外の `AssumedRole`（Lambda・ECS・Step Functions などの実行ロール。`userAgent` に `exec-env/AWS_Lambda` を含むものなど）→ 自動

CloudTrail の 1 日数万件のほぼ全部が自動処理と AWS サービスで、人の操作は 1 日数件〜数十件の想定である。人の操作の明細は同じ主体・IP・イベント名を 1 行にまとめ、`{actor, ip, country, event, count, first_at, last_at}` を持つ。`first_at` / `last_at` はその組の最初 / 最後の日時（JST）である。自動処理と AWS サービスは件数で出し、所見に当たったものは明細にも残す。

v1 では人の操作を 1 イベント 1 行で並べた。v2 で主体・IP・イベント名ごとの件数付きの行に変更（2026-10-16）。

### 2.5 国と IP の判定

送信元は次の順で分類する（`ip_country.py`）。CloudTrail では IP に加えて `vpcEndpointId` を渡す。

1. VPC 内: RFC 1918（`10.0.0.0/8`、`172.16.0.0/12`、`192.168.0.0/16`）、ループバック（`127.0.0.0/8`、`::1/128`）、リンクローカル（`169.254.0.0/16`、`fe80::/10`）、IPv6 ULA（`fc00::/7`）の IP、または空でない `vpcEndpointId` 付きのイベントは「社内（VPC 内）」。国外・社外・初見の国 / IP の判定から外す。IP は標準の `ipaddress` で解析し、広すぎる `is_private` だけでは判定しない。IPv4-mapped IPv6 は内包 IPv4 で同じ規則を適用する
2. 社内: `exclusions` の `kind = ip, category = office` に一致する IP（初期値 `203.0.113.10`）は「社内」。集計値には残し、国外・社外・初見の国 / IP の判定から外す
3. AWS: 同梱の `ip-ranges.json` に一致した IP は「AWS（リージョン名）」。東京・大阪なら日本（CloudShell などは RIR 登録国が米国になるため）
4. 国コード: 同梱の RIR 委任統計から登録国を引く（所在地ではない点は許容）。引けない IP は「不明」とし、日本以外と同じ扱いにする。IP でない AWS サービス名は 2.4 に従い国外判定しない

- AWS 範囲と RIR 表は月 1 回を目安に更新して再デプロイする。実行時に外部へ問い合わせず、MaxMind は使わない（2026-10-15 確定）
- alarmhuba は `CloudFront-Viewer-Country` を使い、空なら IP で補う。ただし上記 1・2 は国ヘッダより先に適用する。WAF の国は `Country` ディメンションを使う
- 社内・VPC 内でも休眠主体・初見の主体・他アカウントは検出する（2.12）。社内扱いにより消すのは送信元に関する国外・社外・初見の所見だけである

v1 ではプライベート IP を国不明として国外扱いにした。v2 で VPC 内を社内扱いに変更（2026-10-16、付録 A）。

### 2.6 7 日平均との比較

- 基準 = 対象日の前 7 日（`baseline_days`。D-7〜D-1）の日次集計のうち、その項目の集計が成功した日の平均
- 倍率による warning = 当日値 > 基準 × `ratio`（既定 3）かつ 当日値 >= `min_count`（既定 10）。基準が 0 の項目は当日値が `min_count` 以上なら warning になる
- 基準に使える日が `min_baseline_days`（既定 3）日未満のとき（導入直後）は倍率でも初見でも warning にせず、「基準なし」と出す
- 初見による warning = 当日に現れた国または IP が、前 7 日の日次集計の既知の集合に無い。対象は CloudTrail の人の操作の IP と国、alarmhuba-backend の国、alarmhuba-backend の 401/403 の相手 IP、社外からの管理 API の呼び出し元 IP。GPS モックの受信元と alarmhuba-backend の上位 IP は入れ替わりが激しいので初見の対象にしない
- 比較する項目の一覧は 6.3
- しきい値（3.3）と 2.3・2.4 の規則は本書の既定値で始め、調整期間（14.1 の 12）で見直す（2026-10-15 確定）

### 2.7 除外リストの適用順

1. 集計範囲（JST の 1 日、対象リージョン、対象ロググループ）で絞る
2. GuardDuty のアーカイブ済み・サンプルの検出を外す（2.2）
3. 除外リストに一致したレコードを集計値から抜き、区分ごとに「除外 n 件」として別に数える（除外した事実はレポートの末尾に件数で残す）。ただし `category = office`（社内）の項目は集計値から抜かず、2.5 の「社内」として国外・社外・初見の判定だけから外す。`kind = instance` も集計値から抜かず、インスタンスの warning 判定だけから外す（2.9）
4. 7 日平均との比較と初見の判定をする（日次集計に保存する値は除外後の値なので、基準も除外後の値で比べる）
5. 2.10 の規則で所見と区分の段階を決め、続いて 2.11 の AI 判定を合成する。root の使用は 3 の除外を通さない。休眠主体は除外前の CloudTrail で観測し、2.12 に従う

除外リストは設定の `exclusions`（3.3・3.4）。種類は `ip`（IP または CIDR）、`principal`（IAM / STS の ARN、ユーザー名またはロール名）、`event`（`eventSource:eventName`）、`instance`（インスタンス名）の 4 つ。適用先 `scope`（`cloudtrail` / `insights` / `gps` / `instances` / `all`）と期限 `expires_at` を持てる。`principal` は `userIdentity.arn` / `userName` に加え、AssumedRole の `sessionContext.sessionIssuer.arn` とロール名（`sessionIssuer.userName`）にも照合する。`category = office` は `kind = ip` だけに指定でき、`kind = instance` の `scope` には `instances` または `all` が必要である（8 章）。

### 2.8 異常なしの定義

| 状態 | 総括の表示 |
|---|---|
| 最終段階が ok、有効な集計がすべて成功 | 🟢 OK「異常なし」。OFF の区分は詳細に「停止中（設定）」と出し、未集計とは区別する |
| 一部の集計が失敗 | 該当区分を少なくとも warning にし「未集計（理由）」を表示する。異常なしとは書かない |
| 最終段階が warning 以上 | 🔴 Critical / 🟡 Warning と所見件数を出す（2.10・6.4） |

AI の失敗だけでは段階を上げない。規則による段階を使い、AI の見解の欄に取得できない理由を出す（2.11）。

### 2.9 インスタンス（EC2 / Lightsail）

- 対象は ap-northeast-1 で running の EC2 と Lightsail のインスタンス（4.5）。指標は CPU 使用率（日の平均と最大）と NetworkOut（日の合計）
- 倍率による warning: CPU の日平均または NetworkOut の日合計が、そのインスタンスの 7 日平均の `ratio`（既定 3）倍を超える。倍率と基準日数（`min_baseline_days`）は 2.6 と同じで、`min_count` は件数の項目のためのものなので使わない。7 日平均が 0 の台は「基準なし」とし、倍率で warning にしない。CPU の低い台は小さな変化でも倍率が大きくなるので、調整期間（14.1 の 12）で見直す
- 新しく現れたインスタンス（前日の一覧に無い）と消えたインスタンス（前日の一覧にあり当日に無い）は warning の所見にする。CloudTrail の構成変更（2.3 の作成・削除）と突き合わせ、対応する操作があれば主体と日時を添える。前日の日次集計が無いときは、前 7 日で最も新しい成功日の一覧と比べる
- 除外リストの `kind = instance` に一致したインスタンスは warning 判定から外す（テスト機など。表示はする）

### 2.10 判定の段階

段階は `critical > warning > ok`。表示は 🔴 Critical / 🟡 Warning / 🟢 OK。v1 では該当事象を一律に赤として扱った。v2 で次の 3 段階に変更（2026-10-16）。

| 規則による段階 | 2.2〜2.9・2.12・2.13 の対象 |
|---|---|
| critical（常に） | GuardDuty 新規 High 以上、root 使用、SG の `0.0.0.0/0` / `::/0` への受信許可の追加、Lightsail ポートの全世界開放、AMI / スナップショットの公開、直接 URL 到達（`alarmhuba.direct`）、休眠主体または初見の主体が日本以外の IP から操作、`S3_MASS_DELETE`・`S3_BUCKET_GONE`（2.13） |
| warning | GuardDuty 新規 Medium、人の操作で日本以外の IP（社内・VPC 内を除く）、社内以外からの管理 API（`alarmhuba.admin_external`）、2.6 の倍率超過・初見の国 / IP、インスタンスの CPU / NetworkOut の倍率、新しく現れた台・消えた台、AccessDenied の多発、コンソールログイン失敗（2.3 のしきい値以上）、休眠主体の利用、初見の主体、他アカウントからのアクセス、S3 の件数・容量の減少、要求の急増、日次指標の更新遅れ（2.13 の warning 5 コード） |
| ok | 上記以外。GuardDuty Low（ポート探査など）は件数のみで、所見を作らない |

- 「所見」（`finding`）は `section` / `code` / `level` / `text` / `count` を持つ。`section` は集計 6 種（S3 を含む）のキー、主体の所見は `cloudtrail`。`level` は critical / warning、`count` はまとめた事象数（1 以上）。同じ主体・IP・操作はまとめる。`text` は件数を含めず（「n 件」「n 回」を書かない）、事象数は `count` だけに持つ（2026-10-16、通知で件数が二重に出たため）
- 区分の規則段階（`rule_levels`）はその区分の所見の最大、規則による全体段階は区分の最大。所見無しは ok。最終区分段階（`levels`）は AI と合成し、全体（`level`）はその最大とする
- `finding_counts = {critical, warning}` は規則で作った所見行の総数（`count` の合計ではない）。AI の区分評価で所見の事実・段階・件数を書き換えない。通知は最終区分段階と所見の段階をそれぞれ明示する。AI だけで上がった区分も理由を出す
- 集計失敗は `SOURCE_FAILED` の warning 所見（`count = 1`）とし、AI が ok と返しても該当区分は少なくとも warning に保つ（監視できていない事実を優先）。disabled は所見無し・ok とし、AI に評価させない
- 基準なし（2.6）の扱いは変えない。倍率・初見の国 / IP では warning にしない。通知には末尾に「基準なし: 区分名」を 1 回だけ出す。主体の初見は 7 日基準とは独立（2.12）

### 2.11 AI 判定

AI は集計と規則の後、通知の前に 1 回呼ぶ。`ai_enabled = false` なら呼ばず規則の段階を使い、「AI の見解: 取得できず（設定で無効）」と出す。

#### 入出力と合成

- S3 の AI 入力は `s3.*` の counters と comparisons、および `top.s3_buckets` の容量上位 20 行（`bucket / objects_diff / objects_pct / size_pct / bytes_downloaded / delete_requests / level`）。根拠キーは `s3.*` と `s3.bucket.<name>` を受け付け、表示名は「S3」とする（v2.2）
- 入力は 7.2 の集計済み要約（`sources` の取得状況・上限付きの明細、`counters` / `seen` / `top` / `instances` / `findings` / `actors` / `excluded` / `rule_levels`）と文脈だけ。生ログ・リクエスト本文・ヘッダは渡さない
- 文脈は設定の除外リスト（種類・値・理由）、`metric_targets` のラベル、インスタンス一覧の名前、直近 7 日の各日の最終段階・所見件数・AI の見解（各日 200 字まで）、当日の規則による全体段階。AI へ渡す前に秘密を除き、過去の `ai` の入出力は再帰的に含めない
- ユーザーメッセージの JSON は `{summary, context, glossary}`。`glossary` は当日の `counters` にあるキーだけの用語表（`{キー: {label, description}}`。`glossary.py` の `glossary_for`。8 章の `GET /days/{date}` と同じ表）。所見・明細のコンソールのリンク（`link`）とログの参照（`ref`）は判定に使わないので AI へは渡さない（`where` と時刻は渡す）（v2.1、2026-10-16）
- Converse API の `toolConfig` に `submit_verdict` を 1 つ定義し（JSON Schema は必須プロパティ・enum・配列・文字列型、追加プロパティ禁止を指定。字数・文数・区分の重複はアプリでも検証）、`toolChoice = {"auto":{}}` とする。`tool` / `any` は Opus 5.5 で `ValidationException`（未対応）になるため使わない（2026-10-16 実測、付録 A）。強制の代わりに、system prompt の固定末尾とユーザーメッセージの固定の前置き（本文を差し替えても両方とも常に付く）に「回答は必ず submit_verdict ツールの呼び出しで返す。本文テキストは書かない」を入れる。ツールは外部操作をせず、返却された `toolUse.input` を検証して結果として採用する
- 応答の content の先頭には思考ブロック（`reasoningContent`。本文が空で signature だけのこともある）が付くので読み飛ばし、`toolUse` を探す。`submit_verdict` の呼び出しが無く本文テキストだけのときは、本文から最初の JSON オブジェクトを取り出して同じ検証をし、通れば採用、通らなければ NO_TOOL_USE とする
- `stopReason = content_filtered` は同じプロファイルで 1 回だけやり直し、再び content_filtered なら同じ系統・同じ版の `global` プロファイル（`ListInferenceProfiles` の一覧に ACTIVE で存在するときだけ）で 1 回やり直す（12.4）。どちらも 150 秒の予算内で、残り時間が足りなければやり直さない。global で成功したら `ai.note` に「AI: jp 経路で content_filtered のため global 経路で判定」を足し、`model_id` と `AI_STATE.last_good_model_id` は実際に成功した ID にする。global でも content_filtered なら refused（`error = content_filtered`）。`ai_model_pin` があるときは一覧を読まないため経路を切り替えない
- ツール入力は `sections[{section, level, reason, keys?}]`、`overall_opinion`、`adjustments[{section, from, to, reason}]`。`section` は有効な 6 種（`s3` を含む）のキー、段階は 3 値のみ。sections は有効区分を重複なく全件返す。`reason` は空でない 60 字以内。adjustments は規則段階と違う区分だけを含み、`from` / `to` は sections と規則段階に一致させる（理由も 60 字以内）
- `keys`（省略可）は根拠にした指標キー（`counters` のキー）・所見コード・上位表の名前（`top` のキー）の文字列配列。スキーマは 12 個まで・各 80 字までだが、アプリの検証は「文字列の配列」だけで、個数・長さの超過は失敗にせず先頭 12 個・各 80 字に切り詰めて `ai.sections[].keys` に保存する。文字列の配列でなければ SCHEMA。省略した区分は `keys` を持たない。試行（`POST /ai/preview`）の結果にも同じ `sections[].keys` が入る
- `overall_opinion` は管理者層向けに専門用語を避けた 2〜4 文、400 字以内。総評 1 文、気になる点は最大 3 点、必要なら推奨行動 1 文で、全体の文数・字数にも収める。400 字を超えたら文の境界で切り詰める（400 字以内の最後の「。」まで、無ければ 400 字）。保存時は `ai.opinion` に対応させる
- 成功時は規則 critical の所見を含む区分を critical のまま保ち、それ以外は AI の区分段階を採用する。ただし未集計の warning と disabled の ok は 2.10 を優先する。全体は最終区分段階の最大。AI が求めた降格が critical 固定で採用されない場合は `ai.note` に残す
- 例外・時間切れ・`stopReason = refusal`・`stopReason = max_tokens`（failed、`error = MAX_TOKENS`。途中で切れた `toolUse.input` は使わない）・ツール呼び出し無し・ツールの重複 / スキーマ不一致は規則の段階へ戻し、「AI の見解: 取得できず（理由）」を必ず出す。`ai.status` は成功 ok、無効 disabled、一般失敗 failed、拒否 refused、時間切れ timeout、呼ぶ時間不足 skipped（7.2）

#### モデルの解決と切替

1. `ai_model_pin` が空でなければその ID をそのまま使う（系統からの探索はしない）。空なら ap-northeast-1 の `ListInferenceProfiles(typeEquals = SYSTEM_DEFINED)` を全ページ読む
2. `status = ACTIVE` で、ID が `^(jp|apac|global)\.anthropic\.claude-(opus|fable|sonnet|haiku)-(\d+)-(\d+)(?:-\d{8})?(?:-v\d+:\d+)?$` に一致し、設定の系統と一致する候補を残す。`preview` / `mythos` を含む ID は除外する
3. major / minor を整数として比較し最大を選ぶ（文字列の辞書順にはしない）。同じ版は jp → apac → global の順に優先。同じ経路・版の候補が複数なら日付降順、v の数値降順、最後に ID の辞書順で確定する。候補ゼロ・探索失敗は failed とし規則判定で続ける
4. 前回成功 ID は `AI_STATE/current.last_good_model_id`。解決 ID が前回成功 ID と違う日は「AI モデルを {前} → {後} に切替」をレポートに出す。初回は切替扱いにしない。経路だけが違う同じ系統・同じ版（例: jp と global の Opus 5.5）は切替扱いにしない（content_filtered で global に切り替えた翌日に毎回切替と出さないため）
5. 新 ID の呼び出しが AccessDenied / AccessDeniedException / ValidationException / ResourceNotFound / ResourceNotFoundException で失敗し、前回成功 ID と違うときだけ、前回成功 ID で 1 回やり直す（150 秒の残り時間内）。レポートへ「AI: {新} は未承認のため {旧} で判定」。フォールバック成功時は実際に使った旧 ID を保存し、成功していない切替を既成事実にしない（切替候補と失敗は `ai.note`）
6. 成功時だけ `last_good_model_id` を更新する。`last_model_id` は最後に解決した ID、`changed_at` は成功 ID が変わった日時（JST）。失敗時は前回成功 ID を消さない。プレビューは AI_STATE を更新しない

表示名は使用した ID から作る。`opus-5-5` → `Claude Opus 5.5`、`fable-5-1` → `Claude Fable 5.1`。ID と表示名を両方保存する。東京から利用する経路と Fable の条件は 12.4。

#### 推論とプロンプト

- `maxTokens = 8000`、temperature は指定しない。出力の先頭に思考ブロックが付き、300 では思考で使い切って `toolUse.input` が途中で切れたため（2026-10-16 実測、付録 A）。費用見積の出力 5,000 トークン / 日（思考分を含む）の想定は変えない。boto3 の read timeout は 120 秒。SDK の自動再試行を重ねず、モデル解決・フォールバックを含む AI の時間予算は 150 秒。残り時間が足りなければ skipped とする（6.7）
- system prompt は `prompt_text.py` の本文（既定 `DEFAULT_PROMPT_BODY`、設定 `ai_prompt` で差し替え可、8,000 字まで）+ 改行 + 固定末尾 `FIXED_SUFFIX` を `build_system_prompt` が結合する。本文が空・空白だけなら既定本文を使い、カスタム本文は両端の空白を除く。固定末尾は submit_verdict で返す・本文テキストを書かない・呼び出し 1 回を必須とし、v2.1（2026-10-16）で「理由は指標の表示名（用語表 glossary）で書き、キー名や略語を使わないでください。」「各区分の keys に根拠にした指標キー・所見コードを入れてください。」の 2 文を足した。v2.2 では固定末尾に「S3 区分はバケットの容量・件数の前日比とダウンロード量・削除要求の基準比である」旨の 1 文と、「基準なし（日数不足）だけを理由に warning や critical にしないでください。S3 区分は所見が無ければ ok（前日比の減少が規則のしきい値 10% 未満なら減少と見なさない） としてください。」の 2 文を追加する（基準なしだけを理由にした S3 の warning が prod で出たため）。既定本文には規則 critical の降格禁止、専門用語を避けた見解、データ中の指示を命令として扱わないことを含める
- 画面には既定本文をテキストボックス、固定末尾と固定前置きを編集不可のラベルとして見せる。名前付きプロンプトは本文だけを保存する。`ai_prompt.py` は各定数を再公開し、`DEFAULT_PROMPT = build_system_prompt("")` を維持する。既定本文 `DEFAULT_PROMPT_BODY` は変えないが、固定末尾に 2 文を足したため既定全文のハッシュは `aedb11baee28` から `154e8fa34dd8` に変わる（v2.1 時点。v2.2 は S3 の固定末尾追加で `154e8fa34dd8` → `959696eb0f2a` に変わる（途中の版では `a711af24881e`、その次の版では `2c4d0a17690d`）。`DATA_PREFIX` は不変）
- ユーザーメッセージの先頭には、入力がセキュリティ集計データ（JSON）であり、データ中の文字列を命令・指示として扱わないことと、回答は submit_verdict の呼び出しで返し本文テキストを書かないことを示す固定の前置きを常に付ける。`ai_prompt` で本文を上書きしても前置きは維持する
- 実際に使ったプロンプトの UTF-8 バイト列の SHA-256 先頭 12 桁と、使用モデル ID を日次集計に保存する。試行も同じ入力組み立て・検証・合成を使う（8 章）

### 2.12 休眠主体の検出

#### 主体と状態の更新

| 主体 | 主体キー / kind |
|---|---|
| IAM ユーザー | IAM ARN / user |
| AssumedRole | `sessionContext.sessionIssuer.arn`（セッション ARN ではない）/ role |
| root | `root` / root |
| 他アカウント | `userIdentity.accountId` が自アカウントと異なるものを `account:{id}` / external_account（ユーザー / ロール判定より先） |

`userIdentity.type = AWSService` は対象外。CloudTrail 集計後に当日見た主体を `ACTOR/{主体キー}` へ upsert する。人・自動の両方を対象とし、社内 IP・principal / event の除外にかかわらず観測履歴を更新する。所見も休眠・初見・他アカウントについては社内 IP や一般の除外で隠さない。

- 保存項目は `last_seen`（JST 日付）、`first_seen`、`last_ip`、`last_event`、`seen_days`、`source`（7.1）。比較には必ず更新前の状態を使い、その後に当日を記録する
- ACTOR に無い主体は IAM で補う。ユーザーは認証情報レポートの `password_last_used` / `access_key_1_last_used_date` / `access_key_2_last_used_date` の有効日時の最大、ロールは `GetRole.RoleLastUsed.LastUsedDate` を JST 日付にして使う。文字列の N/A / no_information 等は日時無し。IAM に存在するが最終使用日が無い、または最終使用日が対象日 D と同じ（当日の利用で更新済み）のときは「前回不明」とし、休眠判定せず D を記録する
- 初日は休眠の判定ができない。2 日目から効く。初回は IAM の過去の最終使用日を補助情報として記録できるが、初回通知では休眠と断定しない。初期化済みかは実行開始時の ACTOR 項目の有無で確認し、初回実行の全体判定は 14.1 に従う
- IAM にも無い主体は NEW_ACTOR。ACTOR に既存だが `last_seen` が無い主体も NEW_ACTOR。IAM に存在する「前回不明」は NEW_ACTOR と区別する
- 同一日再実行で `seen_days` を重複加算せず、過去日の再実行で `last_seen` を後退させない（更新は日付比較付き。last_ip / last_event も後退させず、当日の最後の eventTime の値を使う）。`first_seen` は観測した最古日。履歴を持たないため過去の休眠判定を現在の ACTOR で再現はできず、再実行は前回保存の actors を再利用する。プレビューは保存済み actors のみを使う

#### 判定と週次棚卸し

| 条件（対象日 D） | code / 規則段階 |
|---|---|
| 更新前 `last_seen < D − dormant_days` の主体が D に動いた | `DORMANT_ACTOR` / warning。送信元が日本以外（不明を含む、社内・VPC 内を除く）の IP なら critical |
| ACTOR に無く IAM にも無い、または既存 ACTOR の last_seen が無い主体が動いた | `NEW_ACTOR` / warning。同じ国外条件なら critical |
| 自アカウント以外の accountId の主体が動いた | `EXTERNAL_ACCOUNT` / warning（初見にも該当すれば両方を残す） |

境界は判定式を優先し、ちょうど D−30 は休眠にしない（既定 30）。国判定は 2.5、休眠 / 初見は基準なしでも検出する。IAM 読み取りが失敗したら IAM 補完が必要な主体を推測で NEW_ACTOR にせず `actors.status = unavailable` として「休眠判定: 取得できず」を通知末尾へ出す（11.1）。

`inventory_weekday` は Python の曜日（0=月曜〜6=日曜）、null なら無効。該当曜日だけ認証情報レポートで次を一覧にし、`actors.inventory` に保存する。棚卸し自体には段階を付けない。

- 最終使用日が `D − inventory_dormant_days` 以前の IAM ユーザー、同じ条件の有効なアクセスキー（既定 90 日以上）。一度も使っていないものはユーザー / キーの作成日からの日数を用いる。期間不明は未使用と断定せず「不明」とする
- `password_enabled = true` かつ `mfa_active = false` のコンソールユーザー
- 通知はその日だけ「棚卸し: 90 日以上未使用 ユーザー n・キー n、MFA なし n（詳細へ）」の 1 行（90 は設定値）。明細（ユーザー、キーのスロット 1 / 2、最終使用日、MFA）は詳細ページへ。アクセスキー ID や秘密は保存しない

### 2.13 S3（CloudWatch AWS/S3、v2.2）

| 情報源 | 対象 | 用途・費用 |
|---|---|---|
| S3（CloudWatch `AWS/S3` 名前空間）の日次ストレージ指標 | 全バケットの `NumberOfObjects`（`AllStorageTypes`）と `BucketSizeBytes`（StorageType ごとに取得して合算） | 容量・オブジェクト数の前日比。日次ストレージ指標は全バケット無料。GetMetricData の取得費は別途計上 |
| 同名前空間の要求メトリクス | 有効化したバケットのみ | Get / Put / Delete 要求、ダウンロード量、4xx。対象の追加は prod 配布時に判断（COST-ESTIMATE 5 章） |

- ストレージの減少と要求の急増を `s3_summary.py` のモジュール定数で判定する。しきい値は settings に追加しない。基準は `baseline_days`（既定 7 日）・`min_baseline_days` に従い、日数不足の要求は判定しない。S3 の除外は今回追加せず、`SCOPE_VALUES` は変更しない
- 日次ストレージ指標が前日行の `measured_at` と同じ点なら前日比較による判定をしない。前日行の点が古ければ、その行の値を前日値とする（4.8）。
- 「前日行」は history のうち D の 3 日前以降・D より前で最も新しい成功日（集計が succeeded、S3 区分が ok）の行とする。その日の `sources.s3.latest`（全バケット、容量上位 500 件まで）を優先し、無い日（`latest` を持たない v2.2 初版の日）は `top.s3_buckets`（上位 50 行）の行を使う。途中の日が失敗していても、この行との比較で減少・GONE / STALE を判定する。
- 取得と前日値の選び方は 4.8・6.2、出力は 7.2、規則は下表。1 GiB = 1,073,741,824 バイト。減少率の判定には丸め前の値を使う

| 所見コード | 段階 | 条件（バケット単位） |
|---|---|---|
| `S3_MASS_DELETE` | critical | 前日値があり、オブジェクト減少数 ≥ 1,000 件かつ減少率 ≥ 50%、または容量減少 ≥ 1 GiB かつ減少率 ≥ 50% |
| `S3_OBJECTS_DROP` | warning | 前日値があり、オブジェクト減少数 ≥ 1,000 件かつ減少率 ≥ 10%（critical 未満） |
| `S3_SIZE_DROP` | warning | 前日値があり、容量減少 ≥ 1 GiB かつ減少率 ≥ 10%（critical 未満）。同じバケットで `S3_OBJECTS_DROP` と重なるときはそちらだけ |
| `S3_DOWNLOAD_SPIKE` | warning | ダウンロード量 ≥ 基準 × 3 かつ ≥ 1 GiB。基準なしなら判定しない |
| `S3_DELETE_SPIKE` | warning | 削除要求数 ≥ 基準 × 3 かつ ≥ 1,000 件。基準なしなら判定しない |
| `S3_BUCKET_GONE` | critical | 前日行にあり、現在の対象リージョンに属し、前日の件数 ≥ 1,000 件または容量 ≥ 1 GiB のバケットが、当日は期間内（D の 3 日前〜翌日 UTC）に日次指標の点が無い（ListMetrics に無い場合を含む）。表にも当日値・`measured_at` を null、前日値を前日行の値にした行を残す。文は「S3 バケット x の日次指標が止まった（前日 N 件、M GB。空にされたか削除された可能性）」 |
| `S3_METRICS_STALE` | warning | 前日行と同じ点（前日比較しない）で、その点の UTC 日が D の 2 日前以前、かつ前日の件数 ≥ 1,000 件または容量 ≥ 1 GiB。文は「S3 バケット x の日次指標が 2 日以上更新されていない（最終 YYYY-MM-DD、前日 N 件）」 |

S3 は空のバケット・削除済みバケットの日次指標を出さなくなるため、中身を全部消されたバケットは点が止まる。最後の点の 2 日後・3 日後に `S3_METRICS_STALE`、点が期間から外れる 4 日後に `S3_BUCKET_GONE` を 1 回出す（GONE の行は当日値が null なので、翌日は前日行の条件を満たさない）。同じバケットで両方は出さず（GONE 優先）、どちらかを出したバケットでは減少・急増を判定しない。前日行が無い日（v2.2 初日・history 空・直近 3 日以内に成功日が無い）は出さない。

`levels.py` の `CRITICAL` に `S3_MASS_DELETE` と `S3_BUCKET_GONE`、`WARNING` に残り 5 コードを加え、規則段階・最終段階の合成は既存区分と同じにする。`count` は減少数・要求数、または 1（GONE / STALE は件数を文に入れるので 1）。S3 は主体・操作ログを持たないため、翌朝の検知とコンソールのメトリクス確認までとする。

## 3. 機能要件

### 3.1 日次自動実行

- EventBridge Scheduler が毎日 07:00 JST に集計 Lambda（`report`）を直接起動する。処理は通常 2〜6 分なので Step Functions と trigger Lambda は使わない
- 前日分を集計 6 種（GuardDuty / CloudTrail / 指標 / Logs Insights / インスタンス / S3）→ 主体の更新と休眠判定 → 7 日平均との比較・規則の段階 → AI 判定 → レポートの組み立て → `notify.send()` → 日次集計を DynamoDB に保存、の順に行う
- 同じ日の二重送信は日次集計の項目への条件付き書き込みで防ぐ（11.2）
- 失敗しても自動では再実行しない。アラーム（14.2）を見て、設定画面の「今すぐ実行」か CLI で手動で起動する（14.3）
- 設定 `auto_run_enabled` が偽なら Scheduler からの起動は何もせずに終わる（dev の既定。3.7）。手動実行（3.3 の「今すぐ実行」と 14.3）はこの設定に関わらず動く
- 有効な配布先（3.6）が無ければ、集計と日次集計の保存だけ行い送らない
- 設定 `sections` で OFF にした区分は集計しない（API も呼ばない）。通知のその区分は省略し、詳細に「停止中（設定）」を出す（2.8・6.2）

### 3.2 即時通知

- EventBridge ルール（`AWS::Events::Rule`、アプリの template.yaml）が GuardDuty の検出イベント（`source = aws.guardduty`、`detail-type = GuardDuty Finding`、`detail.severity >= 7`）を受け、即時通知ハンドラ（`guardduty_alert`）を呼び、設定のトグル（3.6）に従って Slack とメールへ送る
- 同じ検出 ID は 1 回だけ通知する（6.6）。同じ検出の再発（件数の増加）は通知せず、翌朝のレポートで件数を出す
- 即時通知は東京（ap-northeast-1）の検出だけにする（2026-10-15 決定）。EventBridge ルールは同じリージョンのイベントしか受けないため、アプリの template.yaml のルールに届くのは東京の検出だけである。us-east-1 / us-west-2 の重大な検出は翌朝のレポートで拾う（付録 B のとおり両リージョンの件数は小さい）
- 即時通知の Slack 先頭に 🔴 を付け、Slack・メールの末尾にレポート一覧（`PORTAL_URL`）へのリンクを付ける。即時通知の検出条件・配信・重複防止は変えない
- 設定 `alert_enabled` が偽なら送らない（dev の既定）。即時通知は `alert_enabled` だけに従い、監視する区分の設定（`sections`。`guardduty` を含む）には関係しない

### 3.3 設定画面

admin 限定の設定画面（9 章）で編集する。設定は DynamoDB の `SETTINGS/current` に置き、項目が無いときはコードの既定値を使う（7.1）。項目の名前と挙動は saas-a-billing 3.3 に揃える。

| 設定 | 内容 | 既定値（コード） |
|---|---|---|
| 集計対象 | 表示のみ。画面上部のサマリカードに「集計対象: 本番アカウント 210000000001（固定）」と出し、「このサイトでは固定です（画面からは変更できません）」の注記を添える（3.7）。保存しない | — |
| 今すぐ実行 | 操作のみで保存しない。対象日（既定は JST の前日、当日〜400 日前）を選び、確認ダイアログの「実行する」で `POST /days/{date}/run` を呼ぶ。Scheduler 起動と同じ処理で、送信済みの日も集計し直して再送する（`force = true`）。保存済みの設定で動き、未保存の変更は使わない | — |
| `auto_run_enabled` | レポートの自動実行の有効 / 無効（無効時は Scheduler の起動を空振りさせる。dev は無効運用） | 無効 |
| `alert_enabled` | 即時通知の有効 / 無効 | 無効 |
| `sections` | 監視する区分（集計 6 種）ごとの有効 / 無効。キーは `guardduty` / `cloudtrail` / `metrics` / `insights` / `instances` / `s3`、値は BOOL。OFF の区分は集計せず、通知のその区分は省略し、詳細に「停止中（設定）」を出す（2.8・6.2）。レポート全体の ON/OFF は `auto_run_enabled`、即時通知は `alert_enabled` のまま | すべて有効 |
| `mail_enabled` / `slack_enabled` | メール配布 / Slack 配布の有効 / 無効。互いに独立のトグル | 無効 / 無効 |
| `mail_to` / `mail_cc` | To / Cc のアドレスリスト。メール配布が有効なときのみ To が必須。`@example.co.jp` だけ | To=`["devA@example.co.jp"]` / Cc=空（dev / prod とも） |
| `slack_channel_id` | 送信先の ID。Slack 配布が有効なときのみ必須。欄に `#` を打つと候補が出て、選ぶと名前のチップになる（#1011）。画面には ID を出さない。開発者本人の DM（ID は `D` で始まる）は候補に出ないため、初回は API（`PUT /settings`）で入れる（14.1 の 6） | 空 |
| `slack_channel_label` | 画面表示用の人向けメモ。投稿先の決定には使わない。候補から選んだ名前を自動でセットする | 空 |
| `slack_mention` | `slack_mention_level` 以上のレポートと即時通知の Slack の先頭に付ける文字列（`<!here>` / `<!channel>` / `<@U0123>` / `<@W0123>` / `<!subteam^S0123>` を空白区切り）。欄に `@` を打つと候補が出る。メールには付けない | 空 |
| `slack_mention_level` | レポートの最終段階の下限。`critical`（Critical のみ）/ `warning`（Warning 以上）/ `always`（OK の日も常に）。即時通知は 3 値とも常に付け、メールには付けない。既存 SETTINGS に無ければ critical で補う | critical |
| `ratio` / `min_count` / `baseline_days` / `min_baseline_days` | 7 日平均との比較の規則（2.6） | 3 / 10 / 7 / 3 |
| `access_denied_min` / `login_failure_min` | AccessDenied の主体別の最小件数 / コンソールログイン失敗の最小回数（2.3） | 20 / 3 |
| `top_n` / `list_limit` | GuardDuty の上位タイトル数 / 通知の所見を区分ごとに出す行数（超過は「ほか n 件」、詳細は 7.2） | 3 / 5 |
| `ai_enabled` | Bedrock の AI 判定を有効 / 無効。無効でも規則判定のレポートと AI 取得不能の理由を出す | 有効 |
| `ai_model_family` | opus / fable / sonnet / haiku。系統内の最新を自動解決。選択肢に経路と月額目安を添える（下記） | opus |
| `ai_model_pin` | 空なら自動解決、値があればそのプロファイル ID を使用 | 空 |
| `ai_prompt` | 編集できる本文のみの上書き（8,000 字まで）。空なら `DEFAULT_PROMPT_BODY`。空白だけ、または両端の空白を除いて既定本文と同じなら保存時に空へ正規化する | 空 |
| `ai_prompt_presets` | 名前付きプロンプトの配列、20 件まで。各要素は `{id, name, prompt, updated_at}` の 4 キーちょうど。id は小文字 UUID で配列内一意、name は両端の空白を除いて 1〜40 字・一意、prompt は本文のみで両端の空白を除いて 1〜8,000 字、本文の合計は 20,000 字まで（trim 後の code point）。updated_at は時差付き ISO 8601（末尾 Z も可）。「既定」は固定カードで配列に保存しない。設定全体には 180KB（180,000 バイト）の保険も適用する（8 章） | `[]` |
| `dormant_days` | 休眠主体の閾値（日、2.12） | 30 |
| `inventory_dormant_days` | 週次棚卸しの未使用期間（日） | 90 |
| `inventory_weekday` | 棚卸し曜日（0=月曜〜6=日曜、null は無効） | 0 |
| `metric_targets` | 指標を取る対象。`waf` / `apigw` / `alb` / `cloudfront` の配列で、要素は名前文字列か `{name, id, region, label}`。ALB は `id` が必須（4.3） | 空（4.3 の初期値を 14.1 で登録） |
| `alarmhuba_admin_path_regex` | alarmhuba-backend の「管理 API」とみなすパス（正規表現）。実際の値は導入時に実ログのパスを調べて登録する（付録 C） | `^/api/admin(?:/\|$)`（架空のパス） |
| `exclusions` | 除外リスト（3.4）。要素は `kind`（ip / principal / event / instance）、`value`、`scope`、`category`、`reason`、`expires_at`。画面の表で行を追加・削除する | 空（14.1 で 3.4 の初期値 2 件を登録） |
| `version` / `updated_by` / `updated_by_email` / `updated_at` | 楽観ロックと変更の記録（saas-a-billing と同じ。`updated_by` は Cognito の sub） | — |

- 「AI 判定」の節で ON/OFF、系統、ピン留め、プロンプトを編集する。経路と月額目安は Opus 5.5 jp（国内）約 $5.3、Fable 5.1 global 約 $12、Sonnet 5.5 global 約 $2.64、Haiku 5.5 jp（国内）約 $0.13（COST-ESTIMATE 5.5、日次 30 回）。系統は固定し版は自動追従するので、金額は選択時の目安であり上限ではない
- 「AI 判定」の節は既定本文をテキストボックスに表示し、固定部分は編集不可のラベルで表示する。名前付きプロンプトのカードで新規保存・上書き保存・名前変更・削除・適用を行い、「既定」は固定カードとする。カードの変更も設定の保存で反映し AUDIT に残る（画面の体裁は 9 章）
- 過去の日と未保存の文面・系統・ピンを選んで「この文面で試す」を押す。POST で受付後に GET をポーリングし、結果を見てから通常の保存操作で設定を保存する。試行では設定・DAILY・ACTOR・AI_STATE を変更せず、通知もしない。試行失敗でも現在の設定を保持する（8・9 章）
- 「今すぐ実行」は独立したカードに置く（admin のみ操作可。読み取り専用のときはカードを出したまま入力とボタンを無効にする）。当日を選ぶと「当日は途中までの集計になります」と注記する。ボタンを押すと確認ダイアログ「{date} 分を実行し、設定の宛先（Slack / メール）に送ります。よろしいですか？」を出し、「実行する」を押すまで API を呼ばない。未保存の設定があるときはカードとダイアログに「未保存の変更は反映されません（保存してから実行してください）」を出す（保存は強制しない）
- 受け付けたらスナックバー「受け付けました。1〜3 分で届きます」を出し、`GET /days/{date}` を 5 秒ごとに最大 15 分ポーリングする。`started_at` が受付時刻（`accepted_at`）以降で `status` が succeeded / failed になった記録だけを今回の結果とみなし（それより前の記録と 404 は待ち続ける）、同じカードに成否、段階チップ、AI の見解の先頭 120 字、詳細へのリンクを出す。409 は「同じ日を実行中です」。15 分で確認できなければ止めてレポート一覧での確認を案内する。画面を離れるとポーリングを止める（実行は続く）
- 「今すぐ実行」は常に送信する（画面・API から `notify` は渡せない）。送らずに集計だけ確かめるときは CLI の直接 Invoke で `"notify": false` を付ける（6.1）
- 「今すぐ実行」は設定の変更ではないため `AUDIT` に残さない。実行者は API の INFO ログと DAILY の `request_history.requested_by` に残る（7.1）
- 変更は誰がいつ何を変えたかを `AUDIT` に残す（7.1。saas-a-billing と同じ形）
- 監視する区分は「監視する区分」として 6 つのトグルを並べ、各行に費用の目安を添える。Logs Insights（`insights`）は「走査量課金。月 約 $5〜14（COST-ESTIMATE 5.3）」、GuardDuty（`guardduty`）は「読み取りは無料。GuardDuty 自体の料金はアカウント側で、ここを OFF にしても止まらない（14.4）」、CloudTrail・指標・インスタンス（`cloudtrail` / `metrics` / `instances`）は「ほぼ無料」。S3（`settings.sections.s3`）は既定 true、旧設定に無ければ true で補完し、「取得費 月約 $0.05。要求メトリクス追加は別途」と添える。保存済みの設定に無いキーは読み出し時に有効で補う
- 配布は既定で両方無効。両方無効でも集計と日次集計の保存は行い、レポート一覧にも出る（送らないだけ）。既存の設定レコードに無い項目は読み出し時に既定値で補う
- Slack が Webhook フォールバック（プラットフォーム設計 8.5）で動作している間は、選んだチャンネルが無視され（投稿先は Webhook 側で固定）、Block Kit も落ちて `text` だけになる旨を画面に表示する
- チャンネル・メンションは候補から選ぶ（`GET /slack/directory`）。自由入力は保存しない。候補に無い保存値（改称・退職・取得失敗、API で入れた DM）は失わず、チャンネルは `slack_channel_label`、メンションは `@不明なユーザー` / `@グループ` に読み替えてチップで見せる（`slack_channel_label` も空のときに限り `#<ID>` を最後の手段として出す）。候補の取得に失敗しても警告と「再取得」ボタンを出すだけで、設定の編集と保存は続けられる
- 候補は設定画面を開いた時点で（admin のときだけ・未取得のときだけ）1 回取得し、「再取得」は `?refresh=1` で呼ぶ。取得元・キャッシュ・エラーは 8 章
- 除外リストは表（種類・値・適用先・区分・理由・期限）で見せ、行の追加・削除をして設定全体と一緒に保存する。期限切れの行は「期限切れ」と出す（適用はしない。3.4）
- 保存できないときは理由を画面に表示する（入力エラーの列挙。除外リストは何行目のどの欄か）
- 未保存の変更があるまま画面を離れようとしたら確認ダイアログで止める（`onBeforeRouteLeave` + `beforeunload`）。「変更を破棄」でサーバの値に戻す
- 保存結果はスナックバーで通知する。設定画面は admin 限定。権限を失った場合は編集を止め、403 の案内を出してレポート一覧へ戻れるようにする
- どこを読むか（GuardDuty の検出器、CloudTrail のバケットと接頭辞、Logs Insights のロググループ）は IAM の Resource と同じ値から作るため、設定ではなくスタックパラメータと template.yaml に置く（10 章）

### 3.4 除外リスト

- 「正規の機器の IP」「既知の無視対象」を設定の `exclusions`（配列。3.3）に置く。適用規則は 2.7。件数は数十件の想定で、`SETTINGS` の 1 項目（400KB）に収まる（7.1）
- 設定画面の表で追加・削除する（admin）。変更は設定の変更として `AUDIT` に残る
- 期限付きの除外は `expires_at` を過ぎた日から適用しない。要素は自動では消えない（画面に「期限切れ」と出し、人が消す）
- 初期値（2026-10-15 確定。14.1 の 6 / 11 で登録する）:

| 種類 | 値 | 適用先 | 理由 |
|---|---|---|---|
| `ip`（`category = office`） | `203.0.113.10` | `all` | 社内の固定 IP |
| `ip` | `198.51.100.23` | `insights` | 正規機器の固定 IP（正規の投稿） |

### 3.5 レポートの内容

通知は全体の段階、該当する区分と所見、AI の見解、詳細リンクを伝える。証拠となる全区分の集計・人の操作・指標・棚卸しはサイトのレポート詳細（9 章）に出す。通知の組み立て・上限は 6.4。

- warning 以上の区分だけを通知し、所見は区分ごとに `list_limit`（既定 5）行まで。同じ主体・IP・操作は 1 行にまとめる。集計できなかった区分も通知に明記する
- OK の日も総括 1 行と AI の見解を常に送る。AI を無効にした日・取得できない日も理由を出す
- 週次棚卸しは該当日に 1 行だけ通知し、段階には加えない
- AI の見解・モデル ID・プロンプトのハッシュと規則 / 最終の段階は日次集計に残す。レポート一覧にも AI の見解を履歴として載せる

Slack の例（warning の日。数値と主体は説明用）:

```text
🟡 AWS日次セキュリティレポート 10/16（金） Warning 1 件
🟡 CloudTrail: 休眠していた主体が国内から操作
🟡 user-example 203.0.113.5（JP）ListBuckets 3 件
AI（Claude Opus 5.5）の見解
長く使われていなかった利用者の操作がありました。想定した作業か担当者に確認してください。
詳細: https://backoffice.example-cloud.com/daily-security-report/days/2026-10-16
集計 10/16 00:00〜24:00 JST・除外 12 件・所要 145 秒
```

Slack の例（ok の日）:

```text
🟢 AWS日次セキュリティレポート 10/16（金） 異常なし
AI（Claude Opus 5.5）の見解
本日の点検で気になる動きは見つかりませんでした。引き続き日々の変化を確認します。
詳細: https://backoffice.example-cloud.com/daily-security-report/days/2026-10-16
集計 10/16 00:00〜24:00 JST・除外 12 件・所要 140 秒
```

例の 10/16 は対象日の例。通常は翌朝 07:00 に送る。設定のメンションがあれば `slack_mention_level` 以上のレポートの先頭に付く（既定 critical、always は OK の日も付く）。

### 3.6 通知

- 共通通知 `notify.send(app="daily-security-report", channels=[有効なものだけ], email=Email(to, cc, subject, text, html), slack=Slack(text, channel, mention, blocks), meta=...)` で投函する。送信は core の notifier が行う（Slack は Bot ExampleBot。課金アプリと同じ仕組み）。自前の Incoming Webhook と SSM SecureString は持たない
- 配布先はメール（`mail_enabled`）と Slack（`slack_enabled`）の独立トグルで決まる（saas-a-billing 3.6 と同じ。既定は両方無効）。有効な配布先が無ければ集計と保存だけ行い、送らない（6.5）
- 即時通知も、`alert_enabled` が真のとき同じトグルと宛先に従って 2 経路へ送る
- メール: レポートの件名は段階を先頭に `[Warning] AWS日次セキュリティレポート 10/16（金）`（critical / ok は `[Critical]` / `[OK]`）。本文はテキストで Slack と同じ判定・所見・AI の見解・詳細リンクを出し、絵文字を段階の文字表記に置換する。添付なし。即時通知の件名は `[セキュリティ即時] {重大度} {タイトル}` のまま（6.5）
- Slack: Block Kit + `text`。メンションはレポートの最終段階が設定 `slack_mention_level` 以上のとき（既定 critical、always は OK も含む）に付け、即時通知は常に付ける
- Block Kit が効くのは notifier が Bot token 方式のときだけで、Webhook フォールバック中は `text` だけが投稿される（プラットフォーム設計 8.5）。そのため `text` にも 6.4 と同じ判定・所見・AI の見解・リンクを入れる
- `report.py` がメンションを Slack の `text` と先頭の section に入れ、`slack.mention` は空で渡す（6.5）。notifier の二重付与を避けるためである。メール本文には入れない
- メールの宛先は共通 notify が送信直前に `@example.co.jp` だけに絞る（プラットフォーム設計 8.2）
- dev サイトから送るレポート・即時通知は、Slack の本文とメールの件名・本文の先頭に `【検証環境】` を付ける
- dev の宛先は ExampleBot（ユーザー ID `U0000BOT01`）と開発者本人の DM（チャンネル ID `D0000DEVA1`）、メールは `devA@example.co.jp`。2026-09-01 の dev 疎通テストで Bot がこの DM に投稿済みなので投稿できる。DM のチャンネル ID は `D` で始まり、共通通知の検証（`^[CGD][A-Z0-9]{8,}$`）は D を通す。候補入力（`GET /slack/directory`）は DM を返さないため、初回は API（`PUT /settings`）で `slack_channel_id` を入れる（14.1 の 6）
- dev から本番チャンネルへ送らないことは、「dev の設定に本番チャンネルを選ばない」運用と本文の `【検証環境】` で担保する（saas-a-billing と同じ考え方）。チャンネル ID をスタックパラメータや samconfig に置かない（ADD-APP-GUIDE 4 章のチェックリスト）

### 3.7 集計対象は常に本番アカウント

| サイト | 集計対象 | 通知先（設定） | 自動実行・即時通知（設定） |
|---|---|---|---|
| dev（`example-backoffice-app-daily-security-report-dev`） | 210000000001 | ExampleBot と開発者本人の DM（Slack。チャンネル ID `D0000DEVA1`）と `devA@example.co.jp`（メール） | 既定で無効。確認のときだけ有効にする（2026-10-15 確定） |
| prod（`example-backoffice-app-daily-security-report-prod`） | 210000000001 | 当面は開発者本人の DM（Slack）と `devA@example.co.jp`（メール）。本番チャンネルは設定画面で後から選ぶ（14.1 の 11） | 有効（14.1 の 11 で設定する） |

- dev と prod は同じアカウントの同じデータ（GuardDuty、CloudTrail、指標、ログ）を読む。saas-a-billing の `TargetEnv` のような集計対象環境の切り替えは持たない。設定画面のサマリカードに「集計対象: 本番アカウント 210000000001（固定）」と出す（3.3）
- dev で毎日レポートを流すと Logs Insights の費用が倍になり、即時通知も同じ検出を 2 か所へ送ることになる。そのため dev は既定で止め、確認のときだけ `auto_run_enabled` / `alert_enabled` を真にする
- 日次集計（7 日平均の基準）はサイトごとのテーブルに別々に持つ。dev を止めている間は dev の基準が空になる（「基準なし」で困らない）

## 4. 参照する AWS サービスと API

既存アプリは自分のスタックのリソースしか触らないが、このアプリはアカウント全体を読む唯一のアプリである。外部リソースの変更 API は呼ばない。v2 は IAM の認証情報レポート生成と Bedrock 推論も行うが、IAM ユーザー / ロールや監視対象の設定は変えない。保存の書き込みは自分の DynamoDB テーブル、自分の CloudWatch Logs、通知キュー（SQS SendMessage）だけにする（12.2）。

### 4.1 GuardDuty

| 用途 | API | 備考 |
|---|---|---|
| 検出器 ID | （呼ばない） | 下の表の ID を template.yaml の Mappings に固定し、IAM の Resource と環境変数 `GUARDDUTY_DETECTORS` を同じ値から作る |
| 検出の一覧 | `ListFindings` | `FindingCriteria` は `updatedAt >= 対象日 00:00 JST`（epoch ミリ秒、`GreaterThanOrEqual`）と `service.archived = false`（`Equals`）。更新日時の上限は置かない。1 回 50 件、`NextToken` でページング |
| 検出の詳細 | `GetFindings` | 1 回 50 ID まで。Python で `CreatedAt` / `UpdatedAt` を対象日の範囲と照合し、新規 / 継続を分ける（2.2）。対象日に作成され翌日に更新された検出も新規として拾う |
| 即時通知 | EventBridge の `GuardDuty Finding` イベント | 新規の検出は数分で発行される。同じ検出の再発は更新イベントとして検出の公開頻度（このアカウントは 15 分）ごとに出る |

2026-10-15 に 3 リージョンで有効化した（AWS を読み取って確認済み）:

| リージョン | 検出器 ID |
|---|---|
| ap-northeast-1 | `00000000000000000000000000000001` |
| us-east-1 | `00000000000000000000000000000002` |
| us-west-2 | `00000000000000000000000000000003` |

- 有効化した機能: CLOUD_TRAIL / DNS_LOGS / FLOW_LOGS（基本）、S3_DATA_EVENTS（S3 Protection）、LAMBDA_NETWORK_LOGS（Lambda Protection）
- 検出の公開頻度は FIFTEEN_MINUTES。検出器のタグは Project=example-backoffice / App=daily-security-report / Name=Detector（検出器はアプリのスタックの外で作ったので、template.yaml のタグ検査の対象外）
- 30 日の無料期間は 2026-11-14 まで。上の有効機能は 2026-10-15 の状態。S3_DATA_EVENTS は 2026-10-19 に停止（#1022）。Lambda Protection の要否は実測で判断する（14.4）
- 検出器を作り直したら Mappings を直して再デプロイする（古い ID のままだと ListFindings が失敗し、レポートの GuardDuty 欄が未集計になる）
- GuardDuty 自体の検出対象（CloudTrail 管理イベント・VPC フロー・DNS）と費用は付録 B

### 4.2 CloudTrail（S3 のログファイルを直接読む）

- `LookupEvents` API は使わない。毎秒 2 回の制限があり、1 日数万件の集計に使えない
- S3 `cloudtrail-example` のキー `AWSLogs/210000000001/CloudTrail/{region}/{YYYY}/{MM}/{DD}/` 以下の `*.json.gz`（中身は `{"Records": [...]}`）を読む
- 日付フォルダは UTC で分かれる。JST の 1 日（D 00:00〜24:00）は UTC の D-1 15:00〜D 15:00 なので、UTC の D-1 と D の 2 prefix を読んで `eventTime` で絞る
- 証跡は 2026-10-15 にマルチリージョン化し、グローバルサービスイベントを ON にした（14.1 の 2。管理イベント All）。以後は他リージョンと IAM・CloudFront・WAF の操作も S3 に入り、リージョンごとにフォルダが分かれる。グローバルサービスイベント（IAM など）は us-east-1 のフォルダに入る
- リージョンの一覧は固定で持たず、毎回 `ListObjectsV2`（`Delimiter = /`）でリージョン フォルダを全部列挙し、全リージョン × 2 prefix を読む（その日にログの無いリージョンは 0 件として扱う）
- 量: 東京だけで 1 日数百ファイル・数十 MB gz（マルチリージョン化の前の値）。2 prefix でその倍になり、他リージョンの分がこれに加わるので 16 並列（S3 クライアントの `max_pool_connections = 32`）で読み、ファイルごとに展開 → 集計 → 破棄する（全件をメモリに持たない）。`CloudTrail-Digest/` などログ以外のフォルダは読まない
- 証跡の S3 キー接頭辞・暗号化・バケットポリシーを確認し、同じアカウントのロールが IAM ポリシー（12.2）だけで読めることを確かめた（2026-10-15）
- ログファイルは API 呼び出しから平均約 5 分で配信されるので、07:00 の時点で前日分は揃っている

### 4.3 CloudWatch 指標

| 対象 | 名前空間・指標 | リージョン | 備考 |
|---|---|---|---|
| WAF | `AWS/WAFV2` の `AllowedRequests` / `BlockedRequests` | CloudFront 用 Web ACL は us-east-1 | Web ACL 単位の合計に加え、国別（`Country`）、脅威カテゴリ別（`VulnerabilityCategory`）、ルール別（`Rule`）。いずれも `WebACL` との組で出ている（2026-10-15 確認） |
| API Gateway | `AWS/ApiGateway` の `Count` / `4XXError` / `5XXError` | ap-northeast-1 | `metric_targets` の名前は REST API の `ApiName`、`id` は HTTP API の `ApiId` として使う |
| ALB | `AWS/ApplicationELB` の `RequestCount` / `HTTPCode_ELB_4XX_Count` / `HTTPCode_Target_4XX_Count` / `HTTPCode_ELB_5XX_Count` / `HTTPCode_Target_5XX_Count` | ap-northeast-1 | `id` に ARN 末尾 `app/<名前>/<hex>` が必須。`label = gps` の 1 対象の 4XX を GPS 拒否件数に使う |
| CloudFront | `AWS/CloudFront` の `Requests`（ディストリビューション別） | us-east-1 | 入口別件数。alarmhuba-backend の前の 4 本を初期登録する（下記） |
| Lambda | `AWS/Lambda` の `Errors` | ap-northeast-1 | `SEARCH` 式で全関数をまとめて取り、合計と上位 20 関数（時間帯つき）を出す |
| EC2 | `AWS/EC2` の `CPUUtilization` / `NetworkOut`（`InstanceId` 別） | ap-northeast-1 | 対象は毎日 `DescribeInstances` で列挙する（4.5）。`metric_targets` には登録しない |

- `GetMetricData` を使う（1 回の呼び出しで最大 500 クエリ）。JST の日付境界に合わせるため期間 3600 秒で 24 点を取り、Python で合計する。同じ 24 点を `Timestamps` で JST の 0〜23 時台に配って時間帯（`sources.metrics.hourly`）にも残す（v2.1）
- `SEARCH` のスキーマは次元の組の完全一致である。WAF は `{AWS/WAFV2,Rule,WebACL}`（`Rule = ALL` が合計）、`{AWS/WAFV2,Country,WebACL}`、`{AWS/WAFV2,VulnerabilityCategory,WebACL}` の 3 本に分ける。CloudFront の存在確認は `{AWS/CloudFront,DistributionId,Region}`（`Region = Global`）を使う
- データ点が無い指標は 0 とする。ただし登録した指標が 1 つも見つからない対象は `missing_targets` に残し、「指標なし: 対象名」と出す。系列のある対象の 0 件と区別する
- us-east-1 の Web ACL は 7 つ（`WebACL-00000001`、`WebACL-00000002`、`WebACL-00000003`、`WebACL-00000004`、`WebACL-00000005`、`WebACL-00000006`、`WebACL-00000007`）。どれをレポートに出すかは `metric_targets` で選ぶ。backend.example-cloud.com は `WebACL-00000001`（2026-10-15 確認）

`metric_targets` は `waf` / `apigw` / `alb` / `cloudfront` の 4 キーに対象の配列を置く。要素は名前の文字列、または `name` / `id` と任意の `region` / `label` を持つオブジェクトである。次元の値は WAF が `name`（Web ACL 名）、ほかが `id` で、無いときはもう一方を使う。ALB は文字列では書けず、`id` に ARN 末尾 `app/<名前>/<hex>`（次元 `LoadBalancer` の値）が必須で、`name` / `label` は表示用（`label = gps` の 1 対象は GPS 拒否件数にも使う）。リージョンは CloudFront が常に us-east-1、WAF は既定 us-east-1、API Gateway / ALB は既定 ap-northeast-1 で、`region` に ap-northeast-1 / us-east-1 を指定できる。Lambda とインスタンスは自動で列挙するので登録しない。

導入時の初期値（2026-10-15 調査。コードの既定値は空）:

```json
{
  "waf": ["WebACL-00000001"],
  "apigw": ["AlarmHubA-Backend-Resolver-API"],
  "alb": [{"name": "GpsUnitAMock-Server-Alb", "id": "app/GpsUnitAMock-Server-Alb/00000000000000a1", "label": "gps"}],
  "cloudfront": ["E0000000000001", "E0000000000002", "E0000000000003", "E0000000000004"]
}
```

CloudFront の `E0000000000005` は系列が無いため初期対象から外す。GPS の対象が未指定・系列なし、または指標区分が停止・失敗したときは、GPS 拒否件数を 0 とせず「未取得」と出す。

### 4.4 CloudWatch Logs Insights

- `StartQuery` で始め、`GetQueryResults` を状態が `Complete` になるまで 2 秒間隔で読む。全クエリ共通の期限（6.2）を超えたら、実行中のクエリを `StopQuery` して未集計（`error = TIMEOUT`）にする
- 走査量課金（東京 $0.0076/GB）。alarmhuba-backend Resolver は 1 日数 GB で、Q1〜Q4 の 4 本で月 約 $8（COST-ESTIMATE 5.3 の中シナリオ）になる。クエリは 5 本以内に抑える（6.2）
- 1 クエリの結果は最大 10,000 行（上限に達したらレポートに「上位 10,000 組のみ」と出す）。クエリ文字列は 10,000 文字までなので、request_id を `in [...]` で渡すクエリは 200 件までに切る（6.2）
- 時間範囲は JST の 1 日（epoch 秒。`endTime` の扱いは 6.2）。対象のロググループはスタックパラメータに置き、IAM の `logs:StartQuery` の Resource と同じ値から作る
- ログの形式とクエリの勘所は付録 C（`[INFO] {json}` は正規表現で抜く、実クライアント IP は `X-Forwarded-For` の先頭、リクエスト行とレスポンス行は別イベント、国は `CloudFront-Viewer-Country`）

### 4.5 EC2 / Lightsail のインスタンス

| 対象 | 列挙 | 指標 | 備考 |
|---|---|---|---|
| EC2（2026-10-15 時点で数台） | `ec2:DescribeInstances`（`instance-state-name = running`） | CloudWatch `AWS/EC2` の `CPUUtilization`（Average）と `NetworkOut`（Sum）。4.3 と同じ方法（`GetMetricData`、期間 3600 秒で 24 点）で 1 回呼ぶ | 1 台 2 メトリクス（台数 × 2 が 1 日に増える） |
| Lightsail（2026-10-15 時点で数台） | `lightsail:GetInstances`（状態が running のもの） | `lightsail:GetInstanceMetricData` で `CPUUtilization`（`unit = Percent`、Average）と `NetworkOut`（`unit = Bytes`、Sum）。期間 3600 秒で 24 点。台ごと・指標ごとに呼ぶ（台数 × 2 回） | Lightsail の指標は CloudWatch の名前空間に無いので専用の API で読む。API は無料 |

- 読むのは ap-northeast-1 だけ。us-east-1 / us-west-2 は EC2・Lightsail とも 0 台（2026-10-15 確認）。ほかのリージョンに作られたら CloudTrail の構成変更（2.3）で分かる
- 一覧は毎回取り直すので、台数の増減に設定の変更は要らない。EC2 は Name タグを表示名にし、同じ名前の台がありうるためキーはインスタンス ID にする。Lightsail はインスタンス名がキー
- 日の平均は 24 点の平均、日の最大は 24 点（1 時間平均）の最大、NetworkOut は 24 点の合計
- 費用: GetMetricData が月 約 500 メトリクス（約 $0.005）増える。Lightsail の API は無料（1.4）

### 4.6 IAM

| 用途 | API | 備考 |
|---|---|---|
| ユーザー・キーの最終使用と MFA | `GenerateCredentialReport` → `GetCredentialReport` | 生成は非同期。`ReportInProgress` / 未完成なら 2 秒ごとに最大 30 秒待つ。COMPLETE 後に CSV を解析する。既存レポートを返す場合の生成日時も記録する |
| ロールの一覧と最終使用 | `ListRoles`（ページング）→ `GetRole` | 各ロールの `RoleLastUsed.LastUsedDate`。1 実行で取得結果を共有し、主体ごとに同じロールを再取得しない |

認証情報レポートには root 行もあるが、棚卸しの「ユーザー」は IAM ユーザー行のみ。利用の読み取りだけを目的とし、ユーザー・キーの作成 / 変更 / 削除はしない。レポート CSV はメモリで解析して破棄する（2.12・12.4）。

### 4.7 Amazon Bedrock

- ap-northeast-1 固定。jp / apac / global のどのプロファイルも東京のエンドポイントから呼ぶ
- `bedrock-runtime` の `Converse`（toolConfig 付き、2.11）、`bedrock` の `ListInferenceProfiles`（SYSTEM_DEFINED）を使う。Converse の IAM アクションは `bedrock:InvokeModel`（`bedrock:Converse` ではない）
- `bedrock:InvokeModel` は `arn:aws:bedrock:ap-northeast-1:210000000001:inference-profile/*.anthropic.claude-*` と `arn:aws:bedrock:*::foundation-model/anthropic.claude-*`、`bedrock:ListInferenceProfiles` は `*` に限る（12.2）
- モデルの選択は 2.11、料金と系統別の経路は COST-ESTIMATE 5.5、データ保護と利用条件は 12.4

### 4.8 S3（CloudWatch AWS/S3）

| 用途 | API・指標 | 備考 |
|---|---|---|
| バケットとストレージ種別の列挙 | `ListMetrics(Namespace="AWS/S3", MetricName="NumberOfObjects")` と `BucketSizeBytes` | 3 リージョンをページング。BucketName で一意化し、件数は `StorageType=AllStorageTypes`、容量は列挙した全 StorageType を合算 |
| 日次ストレージ指標 | `GetMetricData`、`Period=86400`、`Stat=Average` | D の 3 日前 00:00 UTC〜D の翌日 00:00 UTC。最新の点を当日値として `measured_at` に記録する。history の前日行（直近 3 日以内の最新成功日、2.13）と同じ点なら前日比較をしない。前日行の点が古ければその値を前日値とし、前日行を使えないときは前の点、それも無ければ null。期間内に点が無いバケットは集計から外し、前日行にあれば 2.13 の `S3_BUCKET_GONE` の行にする |
| 要求メトリクスの有効バケット | `ListMetrics(MetricName="AllRequests")` | `(BucketName, FilterId)` を列挙。同じバケットに複数の FilterId があれば、名前に All / Entire を含むものを優先し、無ければ名前順の先頭を 1 つ選ぶ |
| 要求・ダウンロード | `GetMetricData`、`GetRequests / PutRequests / DeleteRequests / BytesDownloaded / 4xxErrors` | `Period=3600`、`Stat=Sum` で 24 点を合算。D 00:00〜翌 00:00 JST。有効化済みバケットのみ取得 |

GetMetricData は最大 500 クエリ/回に分割する。日次ストレージ指標は全バケット無料だが、1 日 1 点で反映が遅れるため、画面の日付がデータの即時反映を保証しない。要求メトリクスは有効化したバケットだけ。有効なバケットは `ListMetrics(MetricName="AllRequests")` で調べ、既存の要求メトリクスの費用は他システム側（本見積の対象外）とする。任意追加は 1 バケット $4.80/月で、prod 配布時に判断する。S3 API は使わず、既存の `cloudwatch:GetMetricData` に `cloudwatch:ListMetrics` を足すだけで、S3 の権限は追加しない。

## 5. アーキテクチャ

### 5.1 スタックと配置

| 項目 | 値 |
|---|---|
| スタック名 | `example-backoffice-app-daily-security-report-{env}`（env ∈ {dev, prod}） |
| リージョン | ap-northeast-1（読む先は 2.1 のとおり 3 リージョン + CloudTrail の全リージョン） |
| ランタイム | Python 3.13 / SAM |
| Lambda | `api`（設定・レポート一覧 / 詳細・AI 試行受付 / 取得・Slack 候補。timeout 29 秒、512MB）、`report`（日次集計。timeout 900 秒、1024MB）、`guardduty-alert`（即時通知。timeout 60 秒、256MB） |
| ステートマシン | なし |
| ストレージ | DynamoDB `example-backoffice-daily-security-report-data-{env}`。S3 は書かない（添付なし） |
| スケジュール | EventBridge Scheduler `cron(0 7 * * ? *)`、`ScheduleExpressionTimezone: Asia/Tokyo`、FlexibleTimeWindow OFF → `report` Lambda |
| イベント | EventBridge ルール（GuardDuty Finding、severity >= 7）→ `guardduty-alert` Lambda |
| 通知 | core の共通 notify SQS（shared レイヤの `notify.send`）。Slack とメール（設定の独立トグル） |
| ポータル URL | prod `https://backoffice.example-cloud.com/daily-security-report`、dev `https://backoffice-dev.example-cloud.com/daily-security-report` |
| API ルート | `ANY /api/daily-security-report/{proxy+}`（JWT authorizer = Cognito）。`/health`・`/settings`・`/slack/directory`・`/days`・`/days/{date}`・`/days/{date}/run`・`/ai/preview`・`/ai/preview/{id}`（8 章） |

![全体構成](images/architecture.png)

### 5.2 バッチ経路

![日次バッチ経路](images/batch-flow.png)

Scheduler（07:00 JST、`{"trigger":"scheduled"}`）が `report` Lambda を起動し、次の順に処理する（図の線の番号はこの手順の番号）。

1. 対象日 = JST の前日。`DAILY/{対象日}` を条件付きで取得（11.2）。取れなければ空振り
2. `SETTINGS/current`（除外リストを含む）を読む（無い項目はコードの既定値）
3. Logs Insights の Q1 / Q2 / Q3 / Q5 を開始（サーバー側で並行に走る）
4. GuardDuty（3 リージョン）、指標（2 リージョン）、CloudTrail（S3 を 16 並列）、インスタンス（EC2 / Lightsail）、S3（3 リージョン）を集計。S3 は既存の executor でほかの区分と並列に収集（図は v2 時点、S3 はこの手順に追加）
5. Q3 が終わったら Q4 を開始し、全クエリの結果を待つ
6. 除外の適用と ACTOR 更新前の休眠判定 → ACTOR 更新 → DAILY の前 7 日と比較 → 規則段階（`baseline.py` / `actors.py` / `levels.py`）→ AI 判定・合成（`ai.py`）
7. Block Kit・Slack の text・メール本文を組み立て（`report.py`）→ 有効な配布先へ `notify.send()`
8. `DAILY/{対象日}` に集計結果・最終段階・所見件数・AI の見解・`notify_message_id` を書いて終了

### 5.3 即時通知経路

![即時通知経路](images/alert-flow.png)

- GuardDuty（ap-northeast-1）が検出 → EventBridge 既定バス → ルール（`source = aws.guardduty`、`detail-type = GuardDuty Finding`、`severity >= 7`）→ `guardduty-alert` Lambda（非同期呼び出し。手順は 6.6）→ notify キュー → core notifier → Slack / メール（設定のトグルに従う。東京の検出だけ）
- us-east-1 / us-west-2 の検出は即時通知せず、翌朝のレポートで拾う（3.2）

### 5.4 API 経路

![API・画面経路](images/api-flow.png)

- 雛形どおり HTTP API + `api` Lambda を置く。ルートは `/health`、`/settings`、`/slack/directory`、`/days`、`/days/{date}`、`/days/{date}/run`、`/ai/preview`、`/ai/preview/{id}`（8 章）
- `POST /ai/preview` は API Lambda が PREVIEW を作成して `report` Lambda を `InvocationType = Event`（`action = ai_preview`）で非同期 Invoke し、202 を返す。29 秒の API 制限内で AI 完了を待たず、画面が `GET /ai/preview/{id}` をポーリングする（6.2c・8 章）
- `POST /days/{date}/run` も同じ経路で、API Lambda が `report` Lambda を `InvocationType = Event`（`{"trigger":"manual","date":…,"force":true,"requested_by":…}`）で非同期 Invoke して 202 を返す。集計は最長 900 秒なので待たず、画面が `GET /days/{date}` をポーリングする（8・9 章）
- edge の origin / behavior の追加と `scripts/deploy-edge.sh` への追記（ADD-APP-GUIDE ステップ 6）、フロントのマニフェスト（ステップ 7）を第 1 段階で行う。behavior には `FunctionAssociations`（viewer-request → `EdgeGuardFunction`）を必ず入れる。`README.md` のアプリ一覧にも足す
- 疎通確認は、まず `execute-api` への直接アクセス（ID トークンと `x-origin-verify`。ステップ 5）、edge の追加後に `https://backoffice-dev.example-cloud.com/api/daily-security-report/health`

### 5.5 プラットフォームとの契約（SSM）

| 方向 | キー | 用途 |
|---|---|---|
| 読む（テンプレートパラメータ `AWS::SSM::Parameter::Value<String>`） | `/example-backoffice/{env}/cognito/issuer` | JWT authorizer の issuer |
| 同上 | `/example-backoffice/{env}/cognito/client-id` | JWT authorizer の audience |
| 同上 | `/example-backoffice/{env}/notify/queue-url` | `notify.send` の投函先（環境変数 `NOTIFY_QUEUE_URL`） |
| 同上 | `/example-backoffice/{env}/notify/queue-arn` | IAM `sqs:SendMessage` の Resource |
| 同上 | `/example-backoffice/{env}/attachments/bucket` | 雛形どおり環境変数 `ATTACHMENTS_BUCKET` に入れる（添付は使わない） |
| 同上 | `/example-backoffice/{env}/shared/layer-arn` | shared レイヤ |
| 同上 | `/example-backoffice/{env}/ops/topic-arn` | パラメータ `OpsTopicArn`。全アラームの `AlarmActions`（14.2） |
| 読む（Lambda 実行時、SecureString） | `/example-backoffice/{env}/edge/origin-verify-secret` | shared の `origin_verify` ミドルウェア（api のみ） |
| 読む（Lambda 実行時、String） | `/example-backoffice/{env}/shared/slack/mode` | `GET /settings` が返す `slack_mode`（Webhook フォールバック中の注記。3.3）。api のみ |
| 読む（Lambda 実行時、SecureString） | `/example-backoffice/{env}/shared/slack/bot-token` | `GET /slack/directory` の候補検索だけに使う読み取り（api のみ。saas-a-billing と同じ）。投稿は core の notifier が行い、アプリは Slack の書き込み系 API を呼ばない |
| 書く（`AWS::SSM::Parameter`） | `/example-backoffice/{env}/apps/daily-security-report/api-domain` | edge が origin として読む（`scripts/deploy-edge.sh` が SSM から読んで渡す） |

- アプリ固有の秘密は無い（`/apps/daily-security-report/` 配下に SecureString を置かない）。Slack の Bot token は投稿には core の notifier が使い、アプリは api の候補検索で読むだけ
- prod は `samconfig.toml` の `[production.deploy.parameters]` でキー名（値ではない）を `/example-backoffice/prod/...` に上書きし、`tests/infra/test_samconfig.py` に新アプリを登録する（ADD-APP-GUIDE ステップ 3）

### 5.6 コード構成

モジュール名はアプリ id 由来にする（ADD-APP-GUIDE ステップ 4 の 7。admin-users の `admin_users` と同じ考え方）。

```text
apps/daily-security-report/
  template.yaml                    # HttpApi, api / report / guardduty-alert Function, Table, ScheduleV2,
                                   # AWS::Events::Rule + Lambda::Permission, 失敗キュー, アラーム, SSM 書出し,
                                   # Mappings（GuardDuty の検出器 ID）
  samconfig.toml                   # [default]=dev, [production]
  edge-snippet.yaml                # infra/edge/template.yaml に転記する origin / behavior（ステップ 6）
  README.md                        # アカウント全体を読み取り専用で読むことを明記する
  functions/                       # 全関数が共有する 1 パッケージ（CodeUri は 1 つ、Handler だけを分ける）
    requirements.txt               # requests==2.34.2（Slack 候補検索。boto3 はランタイム、Powertools・shared はレイヤ）
    daily_security_report_api/app.py # APIGatewayHttpResolver。ルート定義のみ（8 章）
    daily_security_report_batch/handlers/
      report.py                    # 日次集計の入口（5.2）
      guardduty_alert.py           # 即時通知の入口（5.3）
    daily_security_report/
      guardduty_summary.py         # 検出の重大度別集計・新規/継続・上位タイトル
      cloudtrail_summary.py        # S3 ログの読み込み・JST 絞り込み・事象の抽出・人/自動の判定
      metrics_summary.py           # GetMetricData の組み立てと集計
      instances_summary.py         # EC2 / Lightsail の列挙・指標の取得と集計（判定部は純粋関数）
      s3_summary.py                # AWS/S3 の日次ストレージ・要求メトリクスと減少・急増の判定
      insights_queries.py          # クエリ文（定数）・開始・待機・結果の整形
      log_queries.py               # 「ログを見る」の問い合わせ（kind ごとの検証・取得・整形。v2.1 第 2 段階）
      baseline.py                  # 7 日平均・初見の判定（純粋関数）
      levels.py                    # 規則から段階への振り分け・最大値・合成（純粋関数）
      ai.py                        # Bedrock 呼び出し・検証・判定の合成
      prompt_text.py               # 既定本文・固定末尾・固定前置きと system prompt の結合（依存なし）
      ai_prompt.py                 # 文面の再公開・DEFAULT_PROMPT・submit_verdict のツール定義
      model_resolver.py            # 系統内の最新プロファイル・ピン留め・表示名
      actors.py                    # 休眠主体・初見・他アカウント・棚卸し
      iam_snapshot.py              # 認証情報レポート・ListRoles / GetRole
      report.py                    # v2 の段階・所見・AI 見解・詳細リンクを組み立て（純粋関数）
      settings.py                  # 設定の既定値・読み出し時の補完・PUT の検証（純粋関数）
      distribution.py              # 配布先判定（mail_enabled / slack_enabled → channels。写し）
      slack_directory.py           # 設定画面の #/@ 候補（写し）
      repo.py                      # DynamoDB（SETTINGS / AUDIT / DAILY / ALERT / ACTOR / AI_STATE / PREVIEW / LOGS）
      exclusions.py                # 除外リストの照合（IP/CIDR・主体・イベント・scope・期限。純粋関数）
      ip_country.py                # 社内 / AWS / 国コード / 不明 の分類（純粋関数）
      diagnostics.py               # 集計の失敗を、秘密を伏せて WARNING に残す（14.2）
      data/                        # ip-ranges.json の抜粋と RIR 委任統計から作った範囲表（14.4 で更新）
  tests/
    unit/ integration/ fixtures/   # 13 章

frontend/src/apps/daily-security-report/
  index.ts                         # マニフェスト（9 章）
  routes.ts                        # レポート一覧 / 詳細 / 設定
  api.ts                           # createAppApi('daily-security-report')
  views/                           # DayListView.vue、DayDetailView.vue、SettingsView.vue（AI 判定の節）
  utils/slackMention.ts            # 写し
  __tests__/
```

- 3 つの関数は同じ `CodeUri: functions/` を共有する。Handler は `daily_security_report_api.app.handler`、`daily_security_report_batch.handlers.report.handler`、`daily_security_report_batch.handlers.guardduty_alert.handler`
- 関数名は `example-backoffice-daily-security-report-{api,report,guardduty-alert}-{env}`（プラットフォーム設計 14.1）
- `pyproject.toml` の `pythonpath` に `apps/daily-security-report/functions` を足す（全アプリを 1 つの pytest セッションで走らせるため）
- すべての関数に shared レイヤ、社内レイヤ ExamplePowertoolsPython、AWS 公開の Powertools レイヤを付ける（雛形どおり）
- 「写し」と書いたものは saas-a-billing からコピーし、アプリ名・用語だけを置き換える。共通化は行わない（既存の課金アプリと同じ方針）。画面の Slack 候補入力は共通部品 `frontend/src/components/TriggerSuggestField.vue` を使う

## 6. バッチ処理の詳細

### 6.1 起動イベント

| trigger | 発行元 | 入力 | 対象日 | force |
|---|---|---|---|---|
| `scheduled` | EventBridge Scheduler（毎日 07:00 JST）→ `report` | `{"trigger":"scheduled"}` | JST の前日 | なし |
| `manual` | 人が `aws lambda invoke` で起動（14.3）、または設定画面の「今すぐ実行」から API Lambda が非同期 Invoke（8 章） | `{"trigger":"manual","date":"2026-10-14","force":true}`。画面からは `requested_by`（実行者のメール）を足す。送らずに確かめるときは CLI で `"notify":false` | `date`（省略時は JST の前日） | 指定（既定 false。画面からは常に true） |
| `ai_preview`（action） | API Lambda の非同期 Invoke | `{"action":"ai_preview","preview_id":"<uuid>"}` | PREVIEW の date | DAILY を変更しない |
| `logs`（trigger） | API Lambda の非同期 Invoke（`POST /days/{date}/logs`） | `{"trigger":"logs","date":"2026-10-14","query_id":"<16 桁>","kind":"lambda","target":{"function":"…"},"requested_by":"<メール>"}` | `date`（受付日の当日〜90 日前） | DAILY を変更しない。LOGS を running → succeeded / failed にする |
| GuardDuty Finding | EventBridge ルール → `guardduty-alert` | GuardDuty の検出イベント（`detail` に検出全体） | — | — |

- `requested_by` は手動実行だけに指定でき、文字列でなければ ValueError で取得前に止める。scheduled に付いていたら同じく止める。値は `request_history` の該当要素に残す（7.1）
- `notify`（BOOL、既定 true）も手動実行だけに指定できる（BOOL 以外や scheduled に付いていたら ValueError で取得前に止める）。false のときは集計・AI 判定・DAILY の保存までを行い、レポートも集計失敗の知らせも Slack / メールへ送らない。DAILY は `notify_channels = []`、`notify_message_id` / `notified_at` は空、`notify_note = "送信なし（検証）"`、`sent_count` は増やさず、`request_history` の該当要素に `notify: false` を残す。`POST /days/{date}/run` からは渡せない（直接 Invoke だけ。v2.1）
- `trigger = logs` は日次のロック・集計・通知を通らず、API が受け付けた LOGS 項目（7.1）が running のときだけ `log_queries.run` を実行して結果を保存する（6.2d）。`date` / `kind` / `target` の検証と `query_id` の一致確認に失敗したら ValueError（API の検証を通ったものしか来ない）
- `report` は Scheduler からの非同期呼び出しで、`EventInvokeConfig.MaximumRetryAttempts: 0`、`OnFailure` は失敗キュー（SQS）。Scheduler 側は `RetryPolicy`（2 回・1 時間）と DLQ を持つ（雛形のコメントアウト部分と同じ形）
- Scheduler の `Name` は `example-backoffice-daily-security-report-daily-{env}` と明示する（省くと dev / prod で名前が衝突する）

### 6.2 集計 6 種

6 種はそれぞれ例外を捕まえて「その区分だけ未集計」にし、ほかの区分は続ける（11.1）。設定 `sections` で OFF の区分は集計せず API も呼ばない（`sources.{区分}.status = disabled`）。`instances` が有効で `cloudtrail` が OFF のときは、新しく現れた台・消えた台に操作を添えない。

#### GuardDuty（guardduty_summary.py）

1. 3 リージョンの検出器（4.1 の ID）で `ListFindings`（`updatedAt` の下限に `GreaterThanOrEqual`、アーカイブ条件に `Equals`。上限なし）→ `GetFindings`。アーカイブ済み・サンプルを外し、Python で `createdAt` / `updatedAt` を照合して重大度区分 × 新規 / 継続で数える（2.2）
2. 新規 High 以上は critical、新規 Medium は warning の所見とし、`ALERT` 項目があれば「即時通知済み」と添える。所見は検出ごとに 1 行（同じ題名でも ID が違えば別行。重大度の高い順に 100 行まで、超過は `findings_truncated`。区分の所見件数は切る前の High / Medium の件数）。通知に出す行数は従来どおり `list_limit` で切る。Medium・Low のタイトルは重大度・件数の順で上位 `top_n` 件を上位表へ残す
3. 各所見に `first_at`（`Service.EventFirstSeen`、無ければ `CreatedAt`）/ `last_at`（`UpdatedAt`）、`where = GuardDuty {region}`、`ref = {kind: guardduty, region, id}`、`link`（コンソールの検出画面）を付ける。上位表の行は題名ごとの集約（題名・重大度・件数）に、最新 `UpdatedAt` の代表検出の `id / region / first_seen / last_seen / resource / remote_ip / link` を添える。新規検出の明細は `sources.guardduty.details.findings`（重大度の高い順・最新順に 50 行、超過は `details_truncated.findings`）に `id / region / severity / title / type / resource / remote_ip / first_seen / last_seen / count / link` で残す。`resource` はインスタンス ID・アクセスキーのユーザー名・バケット名などの短い識別子、`remote_ip` は `Service.Action` のどこかにある相手 IP（無ければ省く）（v2.1）

#### CloudTrail（cloudtrail_summary.py）

1. リージョン フォルダを全部列挙し（マルチリージョン化済みなので東京に限らない）、各リージョンの UTC D-1 と D のキーを集め、16 並列（接続プール 32）で読んで `eventTime` が JST の対象日に入るレコードだけを残す（4.2）
2. 2.3 の事象（構成変更を含む）と 2.4 の人 / 自動の判定を 1 回の走査で行い、件数と明細（人の操作・所見の候補だけ。集計中は区分ごとに最大 100 件、保存時は各 20 行まで。7.2）を作る。AccessDenied は主体別の件数を全件持ち、上位 20 主体を日次集計に残す
3. 明細行（console_logins / iam_changes / sg_changes / config_changes）に `event_id`（`eventID`）と `link`（`https://{region}.console.aws.amazon.com/cloudtrailv2/home?region={region}#/events/{event_id}`。リージョンはイベントの `awsRegion`）を付ける。集約行（human_operations）は最初のイベントの `event_id / region / link`。所見（ROOT / HUMAN_FOREIGN / IAM_CHANGE / SG_WORLD_OPEN / LIGHTSAIL_WORLD_OPEN / IMAGE_PUBLIC / LOGIN_FOREIGN）は `first_at / last_at`、`where = CloudTrail {region}`、`ref = {kind: cloudtrail, region, event, actor, event_id, from, to}`（`from` / `to` は first_at / last_at）、最初のイベントの `link` を持つ。ファイルをまたいだ合算でも最初・最後と最初のイベントを保つ。上位表 `access_denied_principals` / `console_login_failures` の各行には主体ごとの `first_at / last_at` と最初のイベントの `region` を足し、6.3 の ACCESS_DENIED / LOGIN_FAILURE の所見に使う。ID やリージョンが無いイベントでは該当項目とリンクを省く（v2.1）

#### CloudWatch 指標（metrics_summary.py）

1. `metric_targets` と Lambda の `SEARCH` 式から `MetricDataQueries` を組み立て、リージョンごと（ap-northeast-1 / us-east-1）に呼ぶ。`SEARCH` は次元の組の完全一致で引くので、WAF は `{Rule,WebACL}`（`Rule = ALL` が合計）/ `{Country,WebACL}` / `{VulnerabilityCategory,WebACL}` の 3 本に分け、CloudFront の存在確認は `{DistributionId,Region}`（`Region = Global`）で引く（4.3）。24 点を対象ごとに合計し（合計が整数なら整数で持つ）、Lambda は合計と上位 20 関数を出す。登録した対象に系列が 1 つも無ければ「指標なし: 対象名」と出す
2. 時間帯は `sources.metrics.hourly = {counters のキー: [24 個]}`（JST の 0 時台〜23 時台。`cloudfront.requests.*` を含み、値の無い時間帯は 0）。`Timestamps` を JST に直して時間帯へ配るので、応答の並び順（既定は降順）に依存しない。対象日の外の時刻は捨てる。`top.lambda_errors` の各行は `{function, count, hourly, where = CloudWatch Logs /aws/lambda/{function}, ref = {kind: lambda, function}}`（v2.1）

#### Logs Insights（insights_queries.py）

クエリは次の 5 本。クエリ文は定数で持ち、文字列そのものをテストで固定する。社内 IP や除外の照合はクエリに埋め込まず、結果を受けた Python 側で行う（クエリを固定できる）。

| # | 対象 | 抜き出す項目 | 集計 | 使い道 |
|---|---|---|---|---|
| Q1 | alarmhuba-backend Resolver のリクエスト行 | `X-Forwarded-For` の先頭（送信元 IP）、`CloudFront-Viewer-Country`、CloudFront 経由の印（`X-Amz-Cf-Id` の有無） | 送信元 IP × 国 × 経由 の件数（多い順、上限 10,000 組） | 国別件数、直接 URL 到達（CloudFront を経由しない件数）、上位 IP、初見の国 |
| Q2 | 同上のうちパスが `alarmhuba_admin_path_regex` に一致する行 | 送信元 IP、国、パス | 送信元 IP × 正規化したパス × 国 | 社内 IP 以外からの管理 API 呼び出し |
| Q3 | alarmhuba-backend Resolver のレスポンス行で状態 401 / 403 | request_id、状態 | request_id の一覧と状態別件数 | Q4 の入力 |
| Q4 | alarmhuba-backend Resolver のリクエスト行で request_id が Q3 の一覧に含まれる行 | 送信元 IP、国、パス | 送信元 IP × 正規化したパス × 国 | 401/403 の相手。Q3 が 0 件なら走らせない。request_id は新しい順に 200 件まで（4.4） |
| Q5 | GPS モックの Receiver のロググループ | 「受信しました。method path sourceIp body」の method・path・sourceIp | 受信元 IP × method の件数 | GPS モックの受信元 IP。弾いた件数は `metric_targets.alb` の `label = gps` の 4XX 指標で補う（4.3） |

- 走査量は Q1〜Q4 が alarmhuba-backend Resolver（1 日数 GB）を 1 日分ずつ、Q5 は小さい。Q4 を走らせない日は 4 本になる
- Q1 / Q2 / Q3 / Q5 は同時に始め、Q4 だけ Q3 の完了後に始める。全体の期限は開始時の `min(残り − 60 − 150, 480)` 秒（0 以下なら直ちに打切り）で 1 つに定め、Q4 も残り時間で待つ。期限を超えたら実行中のクエリを `StopQuery` し、Insights を `status = failed` / `error = TIMEOUT` にする
- Q2 / Q4 はパスの ID 部分（数字 4 桁以上の区間、`LoggerD-` + 英数字、`@` を含む区間、UUID）を `{id}` に正規化してから IP × パス × 国で集計し、所見の本文にも正規化後のパスを使う
- `StartQuery.endTime` は終端を含むため、翌日 00:00:00 の epoch 秒から 1 秒引く
- Q1〜Q5 の `stats` に `min(@timestamp) as first_at, max(@timestamp) as last_at` を足す（同じクエリで走査量は変わらない）。UTC の `YYYY-MM-DD HH:MM:SS.mmm` を JST の ISO 8601（秒まで）に直し、明細（alarmhuba_admin / alarmhuba_denied）・上位表（alarmhuba_ips / gps_ips）・所見（DIRECT / ADMIN_EXTERNAL）に `first_at / last_at` と `where = CloudWatch Logs {ロググループ名}`（環境変数 `INSIGHTS_LOG_GROUPS`）を付ける。正規化で同じ組にまとめた行は最初・最後を取る。`ref` は alarmhuba が `{kind: alarmhuba, ip, path?}`、GPS が `{kind: gps, ip}`。Logs Insights のコンソールへのリンクは URL が複雑なため付けない（v2.1）
- 直接 URL 到達の判定は `X-Amz-Cf-Id` ヘッダの有無（CloudFront を経由したリクエストに付く。2026-10-15 確定）。管理 API のパスは正規表現（`alarmhuba_admin_path_regex`）で設定し、実際の値は実ログの調査で決めた（付録 C）
- GPS モックは Receiver（`/aws/lambda/GpsUnitAMock-Receiver-XXXXXXXXXXX3-Function-XXXXXXXXXXX4`）だけを対象にする。Viewer（`/aws/lambda/GpsUnitAMock-Viewer-XXXXXXXXXXX5-Function-XXXXXXXXXXX6`、保持 90 日）はリクエストのログを出さないので対象外とし、4XX は ALB の指標（4.3）で見る

#### インスタンス（instances_summary.py）

1. EC2 を `DescribeInstances`、Lightsail を `GetInstances` で列挙する（4.5）
2. EC2 は `GetMetricData` を 1 回（16 クエリ）、Lightsail は `GetInstanceMetricData` を台ごと・指標ごとに呼び、台ごとに `cpu_avg` / `cpu_max` / `net_out` を出す
3. 前日の日次集計の一覧と比べて新しく現れた台・消えた台を出し、その日の CloudTrail の構成変更と突き合わせる（2.9）。判定は純粋関数に閉じる

#### S3（s3_summary.py）

1. `s3_summary.collect(target_date, clients, history, settings)` を既存の executor から呼ぶ。`clients` は `GUARDDUTY_DETECTORS` のリージョンをキーとする CloudWatch client（ap-northeast-1 / us-east-1 / us-west-2）、`history` は instances と同じ、対象日より前の DAILY 一覧（最大 `baseline_days` 日）
2. 4.8 の ListMetrics と GetMetricData で日次ストレージ指標と有効な要求メトリクスを集計する。API 呼び出し対象は 500 バケット以内とし、超過分は容量順に切って `sources.s3.truncated` に記録する
3. 件数・容量の前日値を 4.8 の順で選び、`objects_diff` と件数・容量の増減率を算出する。バケットごとの要求基準は history の `top.s3_buckets` から同じ bucket の `bytes_downloaded / delete_requests / get_requests` を集め、`baseline_days / min_baseline_days` で平均する
4. 2.13 のモジュール定数で findings を作る。バケット表は容量降順で最大 50 行、超過は `sources.s3.truncated` に記録する。出力は 7.2。失敗は既存 `_collect` 経路で `sources.s3.status=failed` とし、`SOURCE_FAILED` の warning 所見と「未集計」通知を出して、ほかの集計を続ける。設定で OFF なら収集せず `disabled`

### 6.2a 主体の更新と休眠の判定

CloudTrail の集計で人・自動の主体を除外前に集め、ACTOR を 100 件単位で BatchGet する。未登録主体を IAM スナップショットで補い、更新前の last_seen で 2.12 を判定する。その後に ACTOR を 25 件単位で BatchWrite し、UnprocessedKeys / Items は残り時間内で再試行する。当日以前への後退や同日加算を防ぐ読み書きの扱いは 2.12。初期化・週次棚卸しも同じ IAM スナップショットを共有する。CloudTrail が disabled / failed の日は主体更新・利用の判定をせず、actors.status を disabled / unavailable として理由を記録する。棚卸しは CloudTrail の ON/OFF とは独立に対象曜日の IAM レポートから行う。

### 6.2b 規則による段階

集計 6 種の完了後に 6.3 で比較し、2.10 に従って findings、finding_counts、rule_levels を確定する。休眠・初見・他アカウントの所見は cloudtrail に加える。基準不足は倍率と初見の国 / IP だけを抑止する。集計失敗は SOURCE_FAILED の warning、設定で停止した区分は ok とする。規則段階と所見は AI が失敗してもそのまま通知できる状態にする。

### 6.2c AI 判定

2.11 に従ってモデル解決 → 要約と文脈の組み立て → Converse → 検証 → 規則 critical の固定を含む合成を行い、levels / level と ai を得る。AI の 150 秒を予約し、終わったら 6.4 の通知を作る。AI の失敗はレポートを止めない。

`action = ai_preview` の起動は日次のロック・集計・通知を通らず、PREVIEW に保存した対象日の DAILY 要約と未保存の文面 / 系統 / ピンを読み、同じ AI 判定だけを行う。開始時に running でなければ空振りし、成功は result（区分段階・理由、最終段階、見解、モデル、ハッシュ、トークン数、所要時間、adjustments）、失敗は error を PREVIEW に保存する。DAILY・ACTOR・AI_STATE・設定を変更しない。

### 6.2d 「ログを見る」の問い合わせ（log_queries.py、v2.1 第 2 段階）

所見・明細・上位表の `ref`（7.2）と同じ形（`{kind, …}` の平らな形。`{kind, target}` の入れ子も可）を受け、当日分（JST 0:00〜翌 0:00）を kind ごとに引く。利用者の入力は形式検証してから使い、Insights のクエリ文字列へ埋め込むのは検証済みの IP（`^[0-9A-Fa-f:.]{2,45}$` に完全一致し `ipaddress` で読めるもの。スコープ ID の `%` は拒否。入力の表記のまま埋め込み、文字列リテラルの `\` と `"` はエスケープ）、パス（空白と `"` と制御文字を除く印字可能文字 1〜200 字。正規表現のメタ文字をエスケープ）、関数名（`^[A-Za-z0-9_\-]{1,140}$`。ロググループ名にだけ使う）に限る。CloudTrail の event / actor / event_id は LookupEvents の属性値としてそのまま渡し、クエリ文字列にはしない。region は ap-northeast-1 / us-east-1 / us-west-2 のみ、GuardDuty の id は `^[0-9a-f]{32}$`、metric の target は `^[a-z]+:[a-z0-9\-]+:[A-Za-z0-9/_\-]+$`。結果は `{source（人が読む取得元）, range: {from, to}, rows, truncated, scanned_bytes?, note?}`、失敗は `{error: コード}`（例外の本文は WARNING ログだけ）。行は最大 200、`text` は 300 字、文字列は `diagnostics._redact` を通し、ヘッダ・本文・Cookie・トークンは出さない。

| kind | target | 取得 | 行 |
|---|---|---|---|
| `lambda` | `function` | Logs Insights `/aws/lambda/{function}`、`filter @message like /(?i)error\|exception\|timed out\|traceback/`、`fields @timestamp, @message`、昇順 | `{time, text}` |
| `alarmhuba` | `ip`、`path`（任意。正規化した `{id}` は `[^/]+` の正規表現に戻す） | Resolver ロググループのリクエスト行（`headers` あり）を `trim(src) = ip` で絞り、request_id を抜く。状態コードは応答行を `filter rid in [...]` で引いて突き合わせる（2 本目。失敗しても行は返し `note`） | `{time, method, path, status}` |
| `gps` | `ip` | Receiver ロググループの「受信しました。」行を `src = ip` で絞る | `{time, method, path}` |
| `cloudtrail` | `region`、`event_id` / `event` / `actor` のいずれか、`from` / `to`（任意。無ければ当日） | `cloudtrail.lookup_events`（イベント履歴 90 日）。属性は event_id > event > actor の優先で 1 つ（actor は ARN の末尾の名前。root は `root`）。50 件ずつ 200 行まで | `{time, event, actor, ip, event_id, link}` |
| `guardduty` | `region`、`id` | `get_findings`（検出器は `GUARDDUTY_DETECTORS`）。未知のリージョンは `UNKNOWN_REGION` | `{time, type, severity, title, description（300 字）, resource, remote_ip, remote_country, first_seen, last_seen, count, link}` の 1 行 |
| `metric` | `key`、`target`（任意、`kind:region:name`） | `GetMetricData`（Period は指定日の古さで選ぶ: 15 日以内 60、63 日以内 300、それより前 3600。`lambda.errors` は `SUM(SEARCH(...))`、WAF は `SUM(SEARCH(...))`、API Gateway / ALB / CloudFront は MetricStat の Sum。`gps.rejected` は label=gps の ALB の 4xx。`instances.ec2.*` は CPUUtilization 平均 / NetworkOut 合計、`instances.lightsail.*` は `GetInstanceMetricData`。instances の Period は 600 / 600 / 3600）。0 でない分だけ。60 以外のときは `note` に「5 分ごとの値」「1 時間ごとの値」（instances は「10 分ごとの値（CPU は平均、通信量は合計）」など）を書く | `{time（HH:MM）, count}` |

- Insights は `start_query` → `get_query_results` を 2 秒間隔で最大 60 秒（alarmhuba の 2 本で共有）。超過は `stop_query` して `TIMEOUT`。Failed / Cancelled は状態名、`ClientError` はエラーコードを `error` に残す
- `metric` で引けるのは CloudWatch の指標だけ（`waf.* / apigw.* / alb.* / cloudfront.requests.* / lambda.errors / gps.rejected / instances.*`）。`alarmhuba.*` / `gps.received` / `guardduty.*` / `cloudtrail.*` はログ件数なので API の検証で 400 にする。さらに API は `key` が指定日の DAILY の `summary.counters` か `summary.comparisons` にあるものだけ受け、任意のキーから SEARCH 式を組み立てさせない
- 所要時間は Lambda の残り時間 − 60 秒と 150 秒の小さい方を予算にする（Insights の上限 60 秒はこの中）

S3 区分は「ログを見る」の対象外。`top.s3_buckets` と S3 の所見には `ref` を付けず、S3 コンソールの Metrics タブへの `link` で確認する。`s3.*` の集計値にもログボタンを出さない。

### 6.3 7 日平均との比較（baseline.py）

日次集計の `counters`（7.2）を項目ごとに 2.6 の規則で比べる。

| 所見の規則 | 項目（`counters` / `seen` のキー） |
|---|---|
| 1 件以上で critical | `guardduty.new.high`（High 以上）、`cloudtrail.root`、SG の全世界受信許可、`alarmhuba.direct`、`cloudtrail.lightsail_world_open`、`cloudtrail.image_public` |
| 1 件以上で warning | `guardduty.new.medium`、`cloudtrail.human_foreign`、`alarmhuba.admin_external`、`instances.new` / `.gone`。主体は 2.12 |
| 2.3 の個別の規則 | `cloudtrail.console_login.success` / `.failure`、`cloudtrail.iam_change`、`cloudtrail.sg_change`、`cloudtrail.access_denied`（主体別は `top.access_denied_principals`） |
| 倍率（2.6） | `waf.blocked`、`apigw.4xx` / `.5xx`、`alb.4xx` / `.5xx`、`cloudfront.requests.{distribution_id}`（入口別）、`lambda.errors`、`alarmhuba.requests`、`alarmhuba.denied`（401 / 403）、`gps.received` / `gps.rejected` |
| 倍率（2.9。`min_count` は使わない） | `instances` の各台の `cpu_avg` / `net_out`（EC2 はインスタンス ID、Lightsail は名前で前 7 日と突き合わせる） |
| 初見（2.6） | `seen.cloudtrail_human_ips` / `seen.cloudtrail_human_countries`、`seen.alarmhuba_countries`、`seen.alarmhuba_denied_ips`、`seen.alarmhuba_admin_ips` |
| 表示のみ | `guardduty.new.low`、`guardduty.continuing`、`waf.allowed`、`top.alarmhuba_ips`、`top.lambda_errors`、`cloudtrail.config_change`（構成変更の件数）、`instances` の `cpu_max` |

- 集計できなかった区分の項目は、その日の基準から外す（0 として平均に入れない）
- v2.2: `RATIO_KEYS` に `s3.bytes_downloaded / s3.delete_requests / s3.get_requests` を追加し、`source_for` は prefix `s3` を `s3` へ対応させる。RATIO の最低値は順に 1 GiB / 1,000 件 / 1,000 件とし、`min_count` の代わりに適用する。`comparisons` の平均・比率・`red` は既存の仕組みを使い、追跡情報の `where` は「CloudWatch メトリクス AWS/S3」。バケット単位の S3 固有所見は 2.13、全体の `s3.buckets / s3.objects` は表示用
- baseline.py が作る所見も追跡情報を持つ（v2.1）。倍率（RATIO / INSTANCE_RATIO）は `key`、`where`（`glossary.where_for`。例 `CloudWatch 指標 AWS/Lambda（全関数）`、`CloudWatch 指標 AWS/ApplicationELB app/…`、`CloudWatch 指標 AWS/EC2 i-…`）、`ref = {kind: metric, key, target?}`（`target` は対象が 1 つのときだけ `kind:region:name`。`gps.rejected` は `label = gps` の ALB）。ログ件数の `alarmhuba.*` / `gps.received` の倍率は指標でないので `ref` を付けない（`where` はロググループ）。初見は seen の出所（`SEEN_SOURCES`）の場所で、alarmhuba の IP には `ref = {kind: alarmhuba, ip}`。ACCESS_DENIED / LOGIN_FAILURE は上位表の行の `first_at / last_at / region` から `where = CloudTrail {region}` と `ref = {kind: cloudtrail, region, actor, event?, from, to}`（古い記録で無ければ `where = CloudTrail` だけ）。件数による always red（行の無いとき）と台の増減にも `where` を付ける。v2 の比較で既存の所見の追跡情報は保つ
- 設定で OFF の区分（`sources.{区分}.status = disabled`）は、その日の判定をせず、基準にも使わない。再び ON にした区分は、基準が `min_baseline_days` 日分溜まるまで「基準なし」になる

### 6.4 レポートの組み立て（report.py）

v1 では全区分と証拠の明細を通知に列挙した。v2 で段階・該当所見・AI の見解・詳細リンクを中心に組み立てる形に変更（2026-10-16）。Slack の mrkdwn と Block Kit / text、メールの本文は同じデータから作る。

| 順 | 内容 |
|---|---|
| 1 | 先頭行 `{🔴 / 🟡 / 🟢} AWS日次セキュリティレポート 10/16（金） {件数 または 異常なし}`。件数は 0 件の段階を省き、`Warning 10 件`、両方あれば `Critical 1 件 / Warning 10 件` とする。絵文字は最終 level、件数は finding_counts。メンションは設定 `slack_mention_level` 以上のとき先頭（既定 critical、always は OK の日も付く）。即時通知は常に。AI だけで昇格し所見 0 件なら件数の代わりに「AI 判定: Warning」等を出し、異常なしにはしない |
| 2 | warning 以上の区分だけ `{絵文字} {区分}: {1 行の要約}`。その区分の所見を critical → warning、同じ段階なら code / text の安定順で `list_limit`（既定 5）行まで表示する。同じ主体・IP・操作は 1 行。所見行は `{絵文字} {text} {count} 件` で、count が 1 なら「1 件」を省く。text は件数を含まない（2.10）。code の括弧書きは通知に出さず、詳細ページに出す。超過は「ほか n 件」（所見行数） |
| 3 | 棚卸し対象曜日のみ 2.12 の「棚卸し: …（詳細へ）」1 行 |
| 4 | `AI（{表示名}）の見解` の見出しと本文。失敗時は「AI の見解: 取得できず（理由）」を表示。切替 / フォールバックがあれば 2.11 の 1 行を付ける |
| 5 | `詳細: {PORTAL_URL}/days/{date}`（アプリのパスを重ねない。6.5） |
| 6 | `集計 10/16 00:00〜24:00 JST・除外 n 件・所要 n 秒`。基準なしの区分は `（基準なし: 区分名）` をここに 1 回だけ付ける。再送、Insights の上限、休眠判定の取得不能もここに添える |

- S3 はインスタンスの後に区分名「S3」で加え、Slack・メールとも既存と同じ区分段階・所見・AI 理由の生成に従う（v2.2）
- `sources.*.status = failed`（S3 を含む）は `⚠️ {区分}: 未集計（理由）` を出し、2.10 に従い warning 以上に扱う。disabled は通知に独立行を出さず、詳細ページの取得状況に「停止中（設定）」を残す
- OK の日は先頭の「異常なし」1 行と AI の見解、リンク、末尾情報のみ。棚卸し日なら棚卸しの 1 行を足す
- 規則所見を AI が降格した区分は通知に出さず詳細に残す。AI が昇格し所見の無い区分は区分の要約に AI の理由を使う。規則と最終の段階は詳細で両方表示する
- Block Kit は先頭と区分 / 所見 / AI / 詳細を section、末尾を context とし、text も同じ情報を同じ順で載せる。50 ブロック以内、header を使う場合は 150 字、section の本文は 3,000 字以内。Slack text 全体は 2,900 字の予算を維持し、Block Kit も同じ省略内容にする
- 文字予算を超える場合は所見を末尾から減らして「ほか n 件」を更新し、次に区分要約を縮める。見解（400 字まで）、AI 取得不能の理由、切替 / フォールバック、詳細リンク、未集計の区分、棚卸し件数、末尾情報は残す。件数・段階は切り詰めない。リンク先は事実の明細を表示する
- 数値は 3 桁区切り、整数の件数に小数部を付けない。日時は JST `MM/DD HH:mm`。動的な text は Slack mrkdwn 用にエスケープし、AI 文面にメンションを解釈させない
- メール本文は同じ組み立てをテキストで使い、🔴 / 🟡 / 🟢 / ⚠️ を `[Critical]` / `[Warning]` / `[OK]` / `[Warning]` に置換する。メンションは付けない。HTML と添付は使わない

### 6.5 通知

共通通知のスキーマは `example-backoffice.notify.v1` のまま（日次集計の v2 とは独立）。`notify.send()` に `Email(to, cc, subject, text)` と `Slack(text, channel, mention="", blocks)` を渡す。meta は date / trigger / attempt / level / finding_counts。Slack の text と先頭 section には report.py が、レポートは設定 `slack_mention_level` 以上のとき（既定 critical）、即時通知は常にメンションを入れ、notifier の二重付与を避ける。メールには付けない。

| 種別 | メール件名 | Slack |
|---|---|---|
| レポート critical | `[Critical] AWS日次セキュリティレポート 10/16（金）` | 🔴、6.4、3 値ともメンションあり（文字列設定時） |
| レポート warning（未集計ありを含む） | `[Warning] AWS日次セキュリティレポート 10/16（金）` | 🟡、6.4、warning / always のときメンションあり（文字列設定時） |
| レポート ok | `[OK] AWS日次セキュリティレポート 10/16（金）` | 🟢、6.4、always のときメンションあり（文字列設定時） |
| 即時通知 | `[セキュリティ即時] High 8.0 {タイトル}` | 🔴、6.6、slack_mention_level に関わらず常にメンションあり（文字列設定時） |
| 全集計失敗 / 組み立て失敗 | `[Warning] AWS日次セキュリティレポート 10/16（金） 集計失敗` | ⚠️「本日のレポートは集計に失敗しました。担当が確認します」+「AI の見解: 取得できず（集計失敗）」+ 一覧リンク。メンションは段階 warning として判定（warning / always のとき文字列設定があれば付ける） |

- `PORTAL_URL` はスタックパラメータ `PortalUrl`。samconfig の dev は `https://backoffice-dev.example-cloud.com/daily-security-report`、prod は `https://backoffice.example-cloud.com/daily-security-report`。すでにアプリのパスを含む。末尾 `/` を除いて `/days/{YYYY-MM-DD}` を付ける。即時通知・全失敗は URL そのもの（レポート一覧）を付ける
- dev は件名・Slack 本文・メール本文の先頭に `【検証環境】`。対象日は JST の date。曜日はその対象日の曜日を日本語で出す
- `channels` は `distribution.channels(settings)`（mail_enabled、slack_enabled と空でない channel）。レポート・失敗通知・即時通知で共通。有効な配布先が無ければ投函せず `notify_note = 配布先が無効のため送付なし`
- 投函したチャンネルを `notify_channels`、戻り値の message_id を `notify_message_id` に保存し notifier のログと突合する。メール宛先のドメイン制限は 3.6

### 6.6 即時通知ハンドラ（guardduty_alert.py）

- イベントの `detail` から検出 ID・種類・タイトル・重大度・リージョン・対象リソース・初回検出日時を取り、Slack は 1 通（`section` 2〜3 個）、メールは同じ内容の本文にする。Slack の先頭は「🔴【GuardDuty 重大検出】High 8.0」の形で、メンションを付ける。メールの件名は `[セキュリティ即時] {重大度} {タイトル}`（6.5）
- `alert_enabled` が偽なら送らない（`sections` には関係しない。3.2）。送り先はレポートと同じトグルと宛先（6.5 の `distribution.channels`）
- 重複防止: `ALERT/{region}#{finding_id}` を `attribute_not_exists(SK)` で書けたときだけ送る。同じ検出の更新イベント、EventBridge の重複配信、Lambda の再試行はここで止まる
- `notify.send()` が失敗したら `ALERT` 項目を消してから例外を投げる。Lambda の非同期再試行（`MaximumRetryAttempts: 2`）で送り直し、それでも失敗したら失敗キューに入る（14.2 のアラーム）
- 送信後の `finish_alert`、送信失敗時の `delete_alert` が失敗したら、WARNING に残してから例外を投げる
- Slack・メール本文の末尾にはレポート一覧のリンク（`PORTAL_URL`）を付ける。ほかの即時通知の挙動は v1 と同じ
- レポートの GuardDuty 欄は `ALERT` 項目の `notify_message_id` を見て「即時通知済み」と添える

EventBridge ルール（template.yaml の抜粋）:

```yaml
GuardDutyAlertRule:
  Type: AWS::Events::Rule
  Properties:
    Name: !Sub ${ProjectName}-daily-security-report-guardduty-alert-${Env}
    EventPattern:
      source: [aws.guardduty]
      detail-type: [GuardDuty Finding]
      detail:
        severity: [{numeric: [">=", 7]}]
    Targets:
      - Id: guardduty-alert
        Arn: !GetAtt GuardDutyAlertFunction.Arn
    Tags: [{ Key: Project, Value: !Ref ProjectName }, { Key: Name, Value: Rule }]
```

- 呼び出し許可は `AWS::Lambda::Permission`（`Principal: events.amazonaws.com`、`SourceArn` はこのルール）
- ルールの配信失敗は指標 `AWS/Events` `FailedInvocations` のアラームで拾う（14.2）。ターゲットの DLQ は置かない（SQS のキューポリシーが要り、タグ検査の対象外の型を増やすため）

### 6.7 所要時間

| 項目 | 見込み |
|---|---|
| CloudTrail（S3 読み込み） | 20〜40 秒（東京分の見込み。全リージョンの実測は下記） |
| Logs Insights（Q1 / Q2 / Q3 / Q5 を並行） | 30〜90 秒。Q4 は Q3 の後に走るので最大でさらに 90 秒 |
| GuardDuty・指標 | 数秒 |
| インスタンス（EC2 / Lightsail） | 数秒（Lightsail は台数 × 2 回の呼び出し） |
| IAM スナップショット・主体 | レポート生成待ち最大 30 秒 + ListRoles / GetRole・ACTOR の読み書き |
| AI 判定 | モデル解決・フォールバックを含め最大 150 秒 |
| 合計 | 通常 2〜6 分/日。v1 の timeout 600 秒（現状 template.yaml）を v2 で 900 秒へ引き上げる |

- メモリは 1024MB。CloudTrail は 16 並列（S3 接続プール 32）でファイル単位に読み捨てるので、メモリはログの総量には比例しない
- 初回実測（dev、2026-10-14 分）: 全体 51 秒、CloudTrail 42 秒・数十万件（全リージョン）、Insights 13 秒・走査 数十 GB、指標 4.6 秒、インスタンス 7.7 秒。Insights の走査量は設計の想定（1 日数 GB × 4 本）と同程度である
- Insights の締切は開始時の `min(残り − 60 − 150, 480)` 秒。AI の 150 秒を先に引き、通知・保存に 60 秒を残す。AI を OFF にしても上限計算は同じで安全側にする。AI の処理も 150 秒を超えず、残りが不足したら skipped とする

## 7. データ設計

### 7.1 DynamoDB テーブル

テーブル `example-backoffice-daily-security-report-data-{env}`（プラットフォーム設計 14.1）。キーは `PK`（String, HASH）/ `SK`（String, RANGE）、PAY_PER_REQUEST、SSE 有効、prod は PITR と `DeletionProtectionEnabled: true`、TTL 属性 `ttl`（epoch 秒）、GSI なし（いずれも雛形どおり）。

| 種別 | PK | SK | 主な属性 | TTL |
|---|---|---|---|---|
| 設定 | `SETTINGS` | `current` | `auto_run_enabled`(BOOL)、`alert_enabled`(BOOL)、`mail_enabled`(BOOL)、`slack_enabled`(BOOL)、`mail_to`(L)、`mail_cc`(L)、`slack_channel_id`(S)、`slack_channel_label`(S)、`slack_mention`(S)、`slack_mention_level`(S)、`sections`(M: 6 キーの BOOL、`s3` を含む)、しきい値（3.3）(N)、`ai_enabled`(BOOL)、`ai_model_family`(S)、`ai_model_pin`(S)、`ai_prompt`(S)、`ai_prompt_presets`(L of M)、`dormant_days`(N)、`inventory_dormant_days`(N)、`inventory_weekday`(N または NULL)、`metric_targets`(M)、`alarmhuba_admin_path_regex`(S)、`exclusions`(L of M: `kind`, `value`, `scope`(L), `category`, `reason`, `expires_at`)、`version`(N)、`updated_at`(S)、`updated_by`(S)、`updated_by_email`(S) | なし |
| 日次集計 | `DAILY` | `2026-10-14`（JST の対象日） | `status`(S: running / succeeded / failed)、`trigger`(S)、`attempt`(N)、`started_at`(S)、`finished_at`(S)、`summary`(M: 7.2)、`level`(S)、`finding_counts`(M: critical / warning)、`ai`(M)、`ai_opinion`(S)、`ai_status`(S)、`ai_model_label`(S)、`sent_count`(N)、`notify_message_id`(S)、`notify_channels`(L)、`notified_at`(S)、`notify_note`(S)、`error`(S)、`request_history`(L: 直近 10 件の `trigger` / `force` / `requested_at`、画面からの実行だけ `requested_by`) | 400 日後 |
| 監査 | `AUDIT` | `2026-10-20T10:00:00+09:00#<uuid>` | `target`(S: `settings`)、`actor`(S)、`actor_email`(S)、`before`(M)、`after`(M)、`at`(S)。saas-a-billing と同じ形 | なし |
| 主体 | `ACTOR` | `{主体キー}`（2.12） | `kind`(S: user / role / root / external_account)、`last_seen`(S: JST 日付)、`first_seen`(S)、`last_ip`(S)、`last_event`(S)、`seen_days`(N: 観測日数)、`observed_dates`(L: 重複加算防止用の JST 日付、最新の last_seen を含む直近 dormant_days 日分)、`source`(S: cloudtrail / iam) | なし |
| AI の状態 | `AI_STATE` | `current` | `last_good_model_id`(S)、`last_model_id`(S)、`changed_at`(S: JST 日時) | なし |
| 試行受付の排他 | `PREVIEW` | `limit#{受付日}` | `owner`(S: UUID)、`lock_until`(N: epoch 秒)。試行本体の件数には含めない | 受付日から 2 日後（ttl） |
| AI の試行 | `PREVIEW` | `{uuid}` | `status`(S: running / succeeded / failed)、`date`(S)、`prompt_hash`(S)、`model_id`(S)、`result`(M)、`error`(M: code / message)、`requested_by`(S: sub)、`requested_at`(S: JST 日時)、`prompt`(S: 本文のみ、空は既定本文。固定末尾はワーカーが結合)、`model_family`(S)、`model_pin`(S) | 受付から 1 日後 |
| ログの問い合わせ | `LOGS` | `{date}#{query_id}`（`query_id` は `{date, kind, target}` を `sort_keys` の JSON にした sha256 の先頭 16 桁。同じ問い合わせは同じ項目） | `status`(S: running / succeeded / failed)、`date`(S)、`query_id`(S)、`kind`(S)、`target`(M: 8 章)、`requested_by`(S: ID トークンのメール)、`requested_at`(S)、`started_at`(S)、`finished_at`(S)、`result`(M: source / range / rows / truncated / scanned_bytes / note)、`error`(S: コード) | 受付から 1 日後 |
| 即時通知 | `ALERT` | `{region}#{finding_id}` | `finding_type`(S)、`severity`(N)、`title`(S)、`created_at`(S)、`notified_at`(S)、`notify_message_id`(S) | 90 日後 |

| 用途 | 操作 |
|---|---|
| 設定の取得（バッチ） | GetItem `SETTINGS/current`。無い項目はコードの既定値で補う（書き戻さない） |
| 設定の取得（`GET /settings`） | GetItem。未作成なら既定値（3.3）を `version = 0` で seed して返す（saas-a-billing と同じ） |
| 設定の更新（`PUT /settings`） | TransactWriteItems: Update `SETTINGS/current`（`version = :expected` の楽観ロック、version+1）+ Put `AUDIT/...` |
| 日次集計の取得（実行開始） | UpdateItem `DAILY/{date}`（条件は 11.2） |
| 7 日分の基準 | Query PK=`DAILY`、SK BETWEEN `{D-7}` AND `{D-1}`、`status = succeeded` のものだけ使う |
| レポート一覧（`GET /days`） | Query PK=`DAILY`、ScanIndexForward=False、Limit=30。ProjectionExpression は一覧属性と v1 の件数属性だけ（summary と request_history は読まない）。v1 の読み替えは 7.2 |
| レポート詳細（`GET /days/{date}`） | GetItem `DAILY/{date}`。request_history を除いて返す |
| 主体の取得・更新 | BatchGetItem `ACTOR/{主体キー}`（100〜300 主体を 100 件以下 / 回）、BatchWriteItem（25 件以下 / 回）。未処理分を再試行。初回判定は Query PK=ACTOR、Limit=1 |
| AI の前回成功 ID | GetItem / UpdateItem `AI_STATE/current`。成功時だけ last_good_model_id を更新 |
| プレビューの受付・取得・完了 | PutItem / GetItem / UpdateItem `PREVIEW/{uuid}`、上限確認は当日の limit ロック内で Query PK=PREVIEW（ConsistentRead=true、全ページ、requested_at の JST 当日、UUID の試行項目のみを数える）。TTL の物理削除遅延に依存しない。ロックは条件付き PutItem / DeleteItem |
| 即時通知の重複防止 | PutItem `ALERT/...`（`attribute_not_exists(SK)`）、失敗時 DeleteItem |
| 即時通知済みの確認（レポート） | BatchGetItem `ALERT/{region}#{finding_id}` |
| ログの問い合わせの受付・取得・完了 | PutItem（受付、上書き）/ GetItem / UpdateItem（`attribute_exists(PK)`）`LOGS/{date}#{query_id}`。同時実行数は Query PK=LOGS（ConsistentRead=true、全ページ、`requested_by` が同じで `requested_at` が 10 分以内の running を数える）。受付から 10 分を過ぎても running なら GET 時に failed / TIMEOUT へ直す |

- DAILY の `level` / `finding_counts` / `ai_opinion`（400 字まで）/ `ai_status` / `ai_model_label` は一覧用にトップレベルにも持ち、summary / ai と同時に保存する。一覧は summary を返さない
- ACTOR の observed_dates は最新の last_seen を基準に直近 dormant_days 日分だけ保持し、過去日再実行でも保持範囲を広げない。保持範囲外の過去日では seen_days を加算しない（前回保存の actors を再利用する）。
- ACTOR は TTL なし。seen_days は CloudTrail で観測した重複しない日数（IAM の初期情報だけなら 0）。last_seen / first_seen は IAM 日付で補えても source は IAM 初期値なら iam、実際の観測更新後は cloudtrail
- PREVIEW の prompt とモデル設定は非同期ワーカー用の受付時スナップショット。管理者にだけ返し、ログには出さない。result は保存済み DAILY を書き換えない
- 数値は DynamoDB の Number として保存し、`repo.py` が書き込み時に float を Decimal に、読み出し時に Decimal を整数なら int、非整数なら float に再帰変換する。設定・日次集計の計算と JSON 応答に Decimal を持ち出さない
- `summary.sources` には 7.2 の項目だけを保存する（指標の対象別の値や取得した生のデータは持ち込まない）
- 設定の既定値はコードに持ち、`SETTINGS` に無い項目は読み出し時に補う
- 除外リストは `SETTINGS` の `exclusions` に入れる（除外だけの項目種別は持たない）。件数は数十件の想定で、1 要素 300 バイトとしても 200 件で 60KB と、1 項目 400KB に収まる。`PUT /settings` は 200 件を上限にする（8 章）
- 監査は追記のみで TTL を設けない（saas-a-billing と同じ）。書くのは設定の変更だけで、閲覧画面は持たない（`aws dynamodb query` で見る）
- 保持期間は日次集計 400 日、即時通知の記録 90 日（2026-10-15 確定）。400 日はプラットフォーム設計 11.2 の添付の保持期間に合わせた

### 7.2 日次集計 JSON のスキーマ

`DAILY.summary` は `daily-security-report.daily.v2`。日次集計を 400KB 以内に収めるため、事実の明細・集合は上限を設ける。以下は構造例（架空の主体・数値）で、任意項目の null は不明、空配列は該当無しを意味する。

```json
{
  "schema": "daily-security-report.daily.v2",
  "date": "2026-10-16",
  "range": {"from": "2026-10-16T00:00:00+09:00", "to": "2026-10-17T00:00:00+09:00"},
  "sources": {
    "guardduty": {"status": "ok", "regions": {"ap-northeast-1": "ok", "us-east-1": "ok", "us-west-2": "ok"},
      "details": {"findings": [{"id": "0123abcd", "region": "ap-northeast-1", "severity": 5.0, "title": "Example port probe", "type": "Recon:EC2/PortProbeUnprotectedPort",
        "resource": "i-0example", "remote_ip": "198.51.100.30", "first_seen": "2026-10-16T09:30:00+09:00", "last_seen": "2026-10-16T13:00:00+09:00", "count": 5,
        "link": "https://ap-northeast-1.console.aws.amazon.com/guardduty/home?region=ap-northeast-1#/findings?macros=current&fId=0123abcd"}]}},
    "cloudtrail": {"status": "ok", "files": 1000, "records": 50000,
      "details": {"human_operations": [{"actor": "user-example", "ip": "203.0.113.5", "country": "JP", "event": "ListBuckets", "count": 3,
        "first_at": "2026-10-16T10:00:00+09:00", "last_at": "2026-10-16T10:05:00+09:00"}]}},
    "metrics": {"status": "ok", "missing_targets": [], "hourly": {"lambda.errors": [0, 0, 0, 5, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0]}},
    "insights": {"status": "ok", "truncated": []},
    "instances": {"status": "ok", "ec2": 8, "lightsail": 8},
    "s3": {"status": "ok", "regions": ["ap-northeast-1", "us-east-1", "us-west-2"], "buckets": 40, "metrics_buckets": 3, "truncated": false}
  },
  "counters": {"guardduty.new.high": 0, "guardduty.new.medium": 0, "cloudtrail.root": 0, "waf.blocked": 231, "alarmhuba.direct": 0, "cloudtrail.config_change": 0,
    "s3.buckets": 40, "s3.objects": 1000000, "s3.bytes_downloaded": 0, "s3.delete_requests": 0, "s3.get_requests": 0},
  "seen": {"cloudtrail_human_ips": ["203.0.113.5"], "cloudtrail_human_countries": ["JP"], "alarmhuba_countries": ["JP"], "alarmhuba_denied_ips": [], "alarmhuba_admin_ips": []},
  "top": {"access_denied_principals": [{"principal": "user-example", "count": 12, "first_at": "2026-10-16T09:00:00+09:00", "last_at": "2026-10-16T18:00:00+09:00", "region": "ap-northeast-1"}],
    "alarmhuba_ips": [],
    "lambda_errors": [{"function": "example-fn", "count": 5, "hourly": [0, 0, 0, 5, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0],
      "where": "CloudWatch Logs /aws/lambda/example-fn", "ref": {"kind": "lambda", "function": "example-fn"}}],
    "s3_buckets": [{"bucket": "example-bucket", "region": "ap-northeast-1", "objects": 1000000, "objects_prev": 1000000, "objects_diff": 0, "objects_pct": 0.0,
      "size": 1073741824, "size_prev": 1073741824, "size_pct": 0.0, "metrics": true, "get_requests": 0, "put_requests": 0, "delete_requests": 0,
      "bytes_downloaded": 0, "errors_4xx": 0, "download_baseline": 0, "delete_baseline": 0, "level": "ok", "key": "s3.bucket.example-bucket",
      "measured_at": "2026-10-16T00:00:00Z", "first_at": "2026-10-16T00:00:00Z", "last_at": "2026-10-17T00:00:00Z", "where": "CloudWatch メトリクス AWS/S3 (ap-northeast-1)",
      "link": "https://ap-northeast-1.console.aws.amazon.com/s3/buckets/example-bucket?region=ap-northeast-1&tab=metrics"}]},
  "instances": {"ec2": [{"id": "i-example", "name": "example-server", "cpu_avg": 3.1, "cpu_max": 12.0, "net_out": 152000000}], "lightsail": [], "new": [], "gone": []},
  "findings": [{"section": "cloudtrail", "code": "DORMANT_ACTOR", "level": "warning", "text": "user-example 国内から ListBuckets", "count": 3, "where": "CloudTrail"},
    {"section": "insights", "code": "ADMIN_EXTERNAL", "level": "warning", "text": "198.51.100.21（JP）/api/admin/{id}/settings", "count": 5,
      "first_at": "2026-10-16T09:30:00+09:00", "last_at": "2026-10-16T11:00:00+09:00", "where": "CloudWatch Logs /aws/lambda/AlarmHubA-Backend-Resolver-…",
      "ref": {"kind": "alarmhuba", "ip": "198.51.100.21", "path": "/api/admin/{id}/settings"}}],
  "finding_counts": {"critical": 0, "warning": 1},
  "rule_levels": {"guardduty": "ok", "cloudtrail": "warning", "metrics": "ok", "insights": "ok", "instances": "ok", "s3": "ok"},
  "levels": {"guardduty": "ok", "cloudtrail": "warning", "metrics": "ok", "insights": "ok", "instances": "ok", "s3": "ok"},
  "level": "warning",
  "ai": {"status": "ok", "model_id": "jp.anthropic.claude-opus-5-5", "model_label": "Claude Opus 5.5", "prompt_hash": "0123456789ab",
    "sections": [{"section": "guardduty", "level": "ok", "reason": "新たな重大検出なし", "keys": ["guardduty.new.high"]},
      {"section": "cloudtrail", "level": "warning", "reason": "長期間使われていなかった利用者の操作"},
      {"section": "metrics", "level": "ok", "reason": "大きな変化なし"},
      {"section": "insights", "level": "ok", "reason": "気になる接続なし"},
      {"section": "instances", "level": "ok", "reason": "大きな変化なし"},
      {"section": "s3", "level": "ok", "reason": "容量・件数と要求に大きな変化なし"}],
    "opinion": "長く使われていなかった利用者の操作がありました。想定した作業か担当者に確認してください。",
    "adjustments": [], "input_tokens": 15000, "output_tokens": 1200, "duration_sec": 60, "error": null, "note": null},
  "actors": {"status": "ok", "dormant": [{"actor": "arn:aws:iam::123456789012:user/user-example", "kind": "user", "previous_last_seen": "2026-09-01", "ip": "203.0.113.5", "country": "JP", "event": "ListBuckets", "count": 3}], "new": [], "external": []},
  "excluded": {"cloudtrail": 0, "insights": 12, "gps": 0},
  "baseline_missing": [], "duration_sec": 145
}
```

- `findings` は所見行で、count は元の事象数。保存は最大 100 行（critical → warning を優先）、`finding_counts` は切る前の総所見行数。区分別の超過は `findings_truncated`（区分ごとの省略行数）に保存し、「ほか n 件」を正確に出す。AI の区分段階と所見件数の関係は 2.10
- 所見の項目は `section / code / text / key / count / level` に v2.1 で `first_at` / `last_at`（JST の ISO 8601。分かるときだけ）、`where`（人が読むログの場所。collector と baseline の所見は必ず持つ）、`ref`（第 2 段階の「ログを見る」の問い合わせパラメータ。`kind` は lambda / alarmhuba / gps / cloudtrail / guardduty / metric。無い所見は省く。`instances.*` の `{kind: metric, key}` は `target` を持たず、第 2 段階でキー `instances.{ec2|lightsail}.{ID・名前}.{cpu_avg|net_out}` から EC2 / Lightsail の指標へ解決する前提）、`link`（AWS コンソールの URL。無ければ省く）を足した。各区分の作り方は 6.2・6.3。古い記録は新しい項目を持たない
- `sources.metrics.hourly` は `counters` の指標キーごとの 24 個（JST の時間帯）。保存時に指標 60 個 × 24 個を上限とし、超えた分は捨てて `sources.metrics.hourly_truncated = true`。`top.lambda_errors` は上位 20 関数で各行に `hourly / where / ref`。`top.guardduty` の行は題名ごとの集約に代表検出の `id / region / first_seen / last_seen / resource / remote_ip / link` を足し、検出ごとの明細は `sources.guardduty.details.findings`（50 行）
- `ai.sections[].keys` は AI が根拠にした指標キー・所見コード・上位表の名前（2.11。省略可）
- 「ログを見る」の結果は DAILY に入れず、別項目 `LOGS`（7.1）に置く。行の形は 6.2d と 8 章
- `seen` の各集合は 500 件、`top` の各一覧は 50 件、instances は全台（十数台で数 KB）。社内・VPC 内の IP / 国は初見用 seen には入れない。省略した事実を sources の notes / details_truncated に残す
- `sources` 共通は status / error / duration_sec。固有項目は GuardDuty の regions / severity / details、CloudTrail の files / records / regions、指標の missing_targets / hourly / hourly_truncated、Insights の truncated / query_ids / bytes_scanned / gps_rejected_source / notes / request_ids_truncated、インスタンスの ec2 / lightsail / inventory_baseline、S3 の regions / buckets / metrics_buckets / truncated / note と上限付きの明細のみ。中間値や生ログは保存しない
- CloudTrail の details の各一覧・config_changes、Insights の details の各一覧は各 20 行（GuardDuty の details.findings は 50 行）、超過は details_truncated。human_operations は 2.4 の集約行を件数降順で残す（集計中の候補は各 100 行まで）。通知の所見上限と証拠の保存上限は別
- `sources.*.status` は ok / failed / disabled（S3 も同じ。取得失敗は `failed`）。ok でない区分の counters は比較に使わない。失敗区分の最終段階は warning 以上。baseline_missing は基準不足の区分キー配列で重複無し
- `ai.status` は ok / disabled / failed / refused / timeout / skipped。使用モデル ID・表示名・実プロンプトハッシュ、検証済み sections / opinion / adjustments、usage の input_tokens / output_tokens、duration_sec、秘密を除いた error、切替等の note を持つ。不明なモデル / usage は null、失敗時 sections / adjustments は空、opinion は「取得できず（理由）」。note に切替 / フォールバックの 1 行を保存する
- actors は status（ok / unavailable / disabled / initializing）、dormant / new / external の集約一覧（主体キー・kind・前回日・IP・国・イベント・件数）を持つ。各一覧 100 行、超過行数を truncated に残す。inventory は対象曜日だけ追加し、`{status, users, keys, mfa_missing, counts, truncated, generated_at}`。明細は各 100 行、counts は切る前の各件数。初回 / 未取得の理由は actors.note に残す
- DynamoDB のサイズ検査は summary 単体でなくトップレベルの ai 複製を含む DAILY 全体で行う。400KB を超える場合は取得済みの証拠表・top / seen を末尾から縮め、各省略件数を更新する。所見総件数・段階・AI の見解・取得状況は維持する
- DAILY のトップレベル ai は summary.ai と同じ内容。level / finding_counts / ai_status / ai_model_label / ai_opinion は同時に更新する（7.1）

v1 では `red` / `red_count` / `red_counts` と「赤の明細」を保存したが、v2 で findings / finding_counts / levels / level に変更し、既存 v1 の一覧・詳細は `level = red_count > 0 ? warning : ok` に読み替える（2026-10-16）。

v1 は `legacy = true`、finding_counts は critical=0 / warning=旧件数、旧所見行は level=warning / count=1 とし、区分の段階は旧区分件数で補う。元データは変更しない。AI は ai_status=disabled、ai_model_label は空、ai_opinion は「取得できず（v1 は AI 判定なし）」。詳細は旧明細を対応する区分に表示する。v2 は legacy=false。

#### S3 区分のデータ契約（v2.2）

| 項目 | 契約 |
|---|---|
| `sources.s3` | `status` は `ok / failed / disabled`（取得失敗は `failed`、`SOURCE_FAILED` の warning と「未集計」通知）、`regions` はリージョン配列、`buckets` はバケット数、`metrics_buckets` は要求メトリクス有効数、`truncated` は BOOL、`note` は任意の注記、`latest` は全バケットの当日値 `{bucket: {objects, size, measured_at, region}}`（容量上位 500 件まで。翌日の前日値・`S3_BUCKET_GONE`・`S3_METRICS_STALE` の判定に使う。`S3_BUCKET_GONE` のバケットは入れない） |
| `counters` の `s3.buckets / s3.objects` | 全対象バケット数 / 全対象バケットのオブジェクト数合計（`S3_BUCKET_GONE` の行は数えない） |
| `counters` の `s3.bytes_downloaded / s3.delete_requests / s3.get_requests` | 要求メトリクス有効バケットのダウンロード量合計（バイト）/ 削除要求数 / 取得要求数。前日平均・比率は `comparisons` |
| `top.s3_buckets` | 1 行 1 バケット、最大 50 行。critical → warning の行を先に置き、その後に ok の行を容量降順で置く。`S3_BUCKET_GONE` の行（当日値・`measured_at` は null）も先頭の critical 群に含める。収集上限 500 バケットまたは表示上限で切った事実は `sources.s3.truncated`。表の上限注記の全件数は GONE を除き、GONE があれば「表は 50 バケットまで（全 60、停止 1）」のように停止数を併記する |
| `findings` | `section="s3"`、2.13 のコード・段階と既存の `text / count / key`、追跡情報 `first_at / last_at / where / link`。`ref` は付けない |

`top.s3_buckets` の列は次のとおり。要求メトリクスの無いバケットの要求値は 0 と区別して null とし、前日値が無いときの差・増減率も null とする。

| 列 | 内容 |
|---|---|
| `bucket / region` | バケット名 / リージョン |
| `measured_at` | 当日値に使った日次ストレージ指標の点の時刻（UTC ISO 8601）。前日行と同じ点なら前日比較せず、前日行の点が古ければその行の値を前日値にする。`S3_BUCKET_GONE` の行は null |
| `objects / objects_prev / objects_diff / objects_pct` | 当日件数 / 前日件数 / 件数差 / 前日比増減率（%、小数 1 桁。減少は負） |
| `size / size_prev / size_pct` | 当日容量 / 前日容量（バイト）/ 前日比増減率（%、小数 1 桁。減少は負） |
| `metrics` | 要求メトリクスが有効なら true、無効なら false |
| `get_requests / put_requests / delete_requests / bytes_downloaded / errors_4xx` | 当日の取得 / 書込 / 削除要求数、ダウンロード量（バイト）、4xx。要求メトリクス無しは null |
| `download_baseline / delete_baseline` | 同バケットの前 `baseline_days` 日の平均。日数不足は null |
| `level / key` | `ok / warning / critical`、`"s3.bucket." + bucket` |
| `first_at / last_at / where` | `measured_at` の点が属する UTC 日の 00:00 / 翌日 00:00 を JST 表記の ISO 8601 で（例 `2026-10-18T09:00:00+09:00` 〜 翌 09:00）、`CloudWatch メトリクス AWS/S3 (region)` |
| `link` | `https://{region}.console.aws.amazon.com/s3/buckets/{bucket}?region={region}&tab=metrics`（Metrics タブ）。`ref` は付けない |

`glossary.py` の表示名は `s3.buckets` =「S3 バケット数」、`s3.objects` =「オブジェクト数（全バケット）」、`s3.bytes_downloaded` =「ダウンロード量（要求メトリクス有効分）」、`s3.delete_requests` =「削除要求数」、`s3.get_requests` =「取得要求数」。`s3.bucket.<name>` は「`S3 バケット <name>`」、説明は「容量・件数の前日比とダウンロード量・削除要求の基準比」、`where_for` は「CloudWatch メトリクス AWS/S3」とする。

## 8. API 設計

共通仕様はプラットフォーム設計 10.2 と saas-a-billing 8 章に従う（プレフィックス `/api/daily-security-report` を Lambda 側で strip、Cognito ID トークン、`x-origin-verify`、権限判定は shared の `auth`（Cognito グループ）、日時は ISO 8601（JST オフセット付き））。応答エンベロープは shared の `response.py`（成功 `{"ok": true, "data": {...}, "error": null}`、失敗 `{"ok": false, "data": null, "error": {"code": "...", "message": "..."}}`）。HTTP API の `DefaultRouteSettings` は saas-a-billing と同じ burst 20 / rate 10。

| メソッド | パス | 権限 | リクエスト要点 | レスポンス要点 |
|---|---|---|---|---|
| GET | `/health` | 認証済み全ユーザー | なし | `{"ok": true, "data": {"status": "ok"}, "error": null}`（雛形どおり） |
| GET | `/settings` | 認証済み全ユーザー | なし | 7.1 の設定属性（未作成なら既定値を `version = 0` で seed して返す）+ `ai_prompt_default`（`DEFAULT_PROMPT_BODY`）+ `ai_prompt_fixed`（`{system_suffix: FIXED_SUFFIX, data_prefix: DATA_PREFIX}`。両項目は読み取り専用で PUT に含めると「不明な項目」の 400）+ `target_account`（`210000000001`。表示用の固定値）+ `slack_mode`（SSM `/shared/slack/mode` の値。`bot` / `webhook`） |
| PUT | `/settings` | `admin` | 設定属性（`version` 必須） | 更新後の設定。`version` 不一致は 409 `VERSION_CONFLICT`。検証は下記。設定の更新と `AUDIT` は 1 トランザクション |
| GET | `/slack/directory` | `admin` | `?refresh=1`（キャッシュを捨てて取り直す） | saas-a-billing 8 章と同じ仕様（`conversations.list` と `users.list` から `{channels, users, fetched_at}`、Lambda コンテナ内に 300 秒キャッシュ、エラーコード）。DM は返さない |
| GET | `/days` | 認証済み全ユーザー | `?limit=30`（1〜90） | 新しい順に date / status / level / finding_counts / ai_status / ai_model_label / ai_opinion（400 字まで）/ legacy / notify_channels / notified_at / notify_note / sent_count / error。summary と request_history は返さない。running の失効は 11.2 |
| GET | `/days/{date}` | 認証済み全ユーザー | JST の YYYY-MM-DD | DAILY の 1 件（summary と ai を含み request_history を除く）に `glossary` を足す。無ければ 404 NOT_FOUND。v1 の読み替えは 7.2 |
| POST | `/days/{date}/run` | `admin` | JST の YYYY-MM-DD（当日〜400 日前）。本文は無しか `{}` | 202 `{date, accepted_at}`（`accepted_at` は JST の ISO 8601）。集計 Lambda を非同期 Invoke（`trigger = manual`、`force = true`、`requested_by`）。実行中は 409 RUN_IN_PROGRESS |
| POST | `/ai/preview` | `admin` | `{date, prompt?, model_family?, model_pin?}` | 202 `{preview_id}`（共通エンベロープの data）。DAILY が無ければ 404。集計 Lambda を非同期 Invoke |
| GET | `/ai/preview/{id}` | `admin` | UUID | PREVIEW の 1 件（running / succeeded / failed）。無ければ / TTL 超過なら 404 NOT_FOUND。失敗の詳細は PREVIEW.error |
| POST | `/days/{date}/logs` | `admin` | JST の YYYY-MM-DD（当日〜90 日前）。本文は 7.2 の所見・明細の `ref` と同じ形（`{kind, …}`。`{kind, target}` の入れ子も可） | 202 `{query_id, status: "running", requested_at}`。同じ問い合わせの成功結果が残っていれば 200 で `result` 付き、実行中なら 202 で同じ `query_id`（Invoke しない）。集計 Lambda を非同期 Invoke（`trigger = logs`） |
| GET | `/days/{date}/logs/{query_id}` | `admin` | 16 桁の query_id | LOGS の 1 件 `{query_id, status, requested_at, finished_at?, result?, error?}`。無ければ / 受付から 1 日を過ぎていれば 404 NOT_FOUND。失敗の理由は `error`（コード） |

- `glossary` は `{キー: {label, description}}` で、`summary.counters` と `summary.comparisons` の全キー分（v2.1）。`glossary.describe` が前方一致で解決し（`cloudfront.requests.{id}` は設定 `metric_targets` の `label` / `name` を表示名に使う。`instances.{ec2|lightsail}.{名前}.{cpu_avg|net_out}` は「{名前} CPU 平均 / NetworkOut」）、未知のキーは `{label: キー, description: ""}`。summary の無い記録（実行中など）は空。画面は静的な写しを持たず、この表を使う
- 「ログを見る」の `POST /days/{date}/logs` と `GET /days/{date}/logs/{query_id}` の契約は下記（v2.1 第 2 段階）

`PUT /settings` の検証（不正なら設定も監査も保存しない。本文に無い項目は現在の値を保つ）:

- トグルと宛先の整合: `mail_enabled` なら `mail_to` が 1 件以上、`slack_enabled` なら `slack_channel_id` が必須
- `slack_channel_id` は `^[CGD][A-Z0-9]{8,}$`（共通通知と同じ。`D` は DM）。`slack_mention` は `<!here>` / `<!channel>` / `<@U…>` / `<@W…>` / `<!subteam^S…>` の空白区切りを許容する
- `slack_mention_level` は critical / warning / always の文字列のみ
- 宛先はメール形式かつ `@example.co.jp`（許可ドメイン外は 400 `MAIL_DOMAIN_NOT_ALLOWED`。判定は notifier と同じ規則）
- `exclusions` の各要素: `kind` は ip / principal / event / instance、`value` は `kind` に合う形（ip は IPv4 / IPv6 のアドレスか CIDR、principal は IAM / STS の ARN またはユーザー名・ロール名（AssumedRole の `sessionIssuer.arn` / `userName` も照合）、event は `eventSource:eventName`、instance はインスタンス名。EC2 で同名の台を区別したいときはインスタンス ID `i-...` も可）、`scope` は cloudtrail / insights / gps / instances / all の 1 つ以上、`category` は `office` か空（`office` は `kind = ip` だけ）、instance の `scope` は instances か all を含むこと、`reason` は必須、`expires_at` は日付か空。`kind` + `value` の重複なし、200 件まで
- `sections` は `guardduty` / `cloudtrail` / `metrics` / `insights` / `instances` / `s3` の 6 キーで、値は BOOL（ほかのキーや BOOL 以外は 400）。`settings.sections.s3` は既定 true、旧設定に無い場合も true で補完する。6 つすべてを無効にはできない（レポートを止めるときは `auto_run_enabled` を無効にする）。変更はほかの設定と同じく `AUDIT` に残る
- `ai_enabled` は BOOL、`ai_model_family` は opus / fable / sonnet / haiku。`ai_model_pin` は空か `^(jp|apac|global|us)\.anthropic\.claude-[a-z0-9.-]+(:\d+)?$`。`ai_prompt` は本文のみの文字列で 8,000 字まで（空 = 既定本文）。空白だけ、または両端の空白を除いて `DEFAULT_PROMPT_BODY` と同じなら空へ正規化して保存する。ピン留めは us 経路も入力可能だが東京からの利用承認は実際の Converse で判定する
- `ai_prompt_presets` は配列で 20 件まで。各要素は `{id, name, prompt, updated_at}` の 4 キーちょうど。id は `^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$` に一致し配列内一意、name は両端の空白を除いて 1〜40 字で一意、prompt は両端の空白を除いて 1〜8,000 字の本文（固定部分を含めない）で、合計は trim 後の code point で 20,000 字まで。超過時は「ai_prompt_presets の本文の合計は 20,000 字までです（現在 n 字）」。updated_at は `datetime.fromisoformat` で読める時差付き ISO 8601 文字列（末尾 Z も可）。時差なし・日付だけは「ai_prompt_presets[n] updated_at は時差付きの ISO 8601 です」で 400。エラーは `ai_prompt_presets[2] name は 1〜40 字です` のように 0 起点の添字と項目名を示す。「既定」カードは配列に保存しない
- 保存する設定全体（version・更新者・更新日時を含む）を `json.dumps(..., ensure_ascii=False)` した UTF-8 バイト長が 180KB（180,000 バイト）を超えたら、AUDIT の before / after の複製に備える保険として 400 `SETTINGS_TOO_LARGE`「設定が大きすぎます。名前付きプロンプトか除外リストを減らしてください」を返し、保存しない。TransactWriteItems の `ValidationException` のうち Item size / Transaction size の超過も同じ 400 に変換し、ほかの ClientError は従来どおり扱う
- `dormant_days` は整数 7〜365、`inventory_dormant_days` は整数 30〜365、`inventory_weekday` は null か整数 0〜6（BOOL は整数とみなさない）
- しきい値の範囲: `ratio` は 1 より大きい数、整数のしきい値は 1 以上、`min_baseline_days` <= `baseline_days` <= 30、`top_n` / `list_limit` は 1〜20（list_limit の既定は 5）
- `alarmhuba_admin_path_regex` は正規表現としてコンパイルできること。`metric_targets` は 4.3 の対象の形で、alb の各要素には `^app/[^/]+/[0-9a-f]+$` に一致する `id` が必須（名前だけの行は 400）

プレビューの契約:

- date は実在する JST 日付で DAILY が必要。summary を持たない実行中 / 失敗レコードは 409 VERSION_CONFLICT（集計完了後に試す）。v1 は読み替えた要約で試せるが、保存されていない主体等の証拠は補わない
- prompt / model_family / model_pin が省略なら現在の設定を使う。prompt は本文のみで、空は既定本文。API は本文を空のままでも PREVIEW に保存し、固定末尾は集計 Lambda の `run_judgment` が結合する。空 model_pin は自動解決。PUT と同じ検証を適用し、利用中の設定値と混同しない
- 1 日あたり 20 回まで（リクエスト受付日の JST、環境全体）。当日の受付ロックを取得して PREVIEW を全ページ照会し、requested_at が当日の試行を status にかかわらず数え、20 未満なら UUID の running を保存してロックを解放する。超過は 429 PREVIEW_LIMIT。月 20 回は費用見積の想定であり API の日次上限とは別
- 受付ロックは PREVIEW/limit#{JST 受付日} の条件付き PutItem（未作成または lock_until < 現在）、owner=UUID、lock_until=現在+45秒。件数確認と試行保存だけを保護し、Invoke 前に owner 一致の条件で DeleteItem。API timeout 29 秒より長い lease で異常終了時も回復する。競合時は受付を作らず 502 AI_UNAVAILABLE（再試行を案内）。この内部項目は UUID の GET や日次件数に含めない
- API は running の PREVIEW（UUID、prompt / モデル設定のスナップショット、requested_by、requested_at、ttl）を保存して非同期 Invoke。Invoke 受付に失敗したら failed / AI_UNAVAILABLE を保存し HTTP 502。正常受付後の AI 失敗は GET の HTTP 200 の PREVIEW.error（code=AI_UNAVAILABLE、http_status=502、message=理由）に記録する
- フロントは受付後 2 秒ごとに GET、succeeded / failed で停止。150 秒の処理予算 + 起動待ちを考慮し、受付から 5 分経過の running は GET 時に failed / AI_UNAVAILABLE（処理時間超過）へ更新する。期限後のワーカーはその失敗を上書きしない。TTL は受付から 1 日
- プレビューで ai_enabled が無効でも、明示的な試行操作として AI を呼ぶ。設定の保存、通常のレポート送信、AI_STATE 更新は行わない

今すぐ実行の契約:

- date は実在する JST 日付で、受付日（JST）の当日から 400 日前まで（DAILY の TTL と同じ）。未来日・400 日より前・形式不正、本文が空でも `{}` でもないときは 400 VALIDATION_ERROR
- `DAILY/{date}` が running で stale でない（`started_at` が 20 分以内。11.2 と同じ `repo.is_stale`）なら 409 RUN_IN_PROGRESS を返し、Invoke しない。stale の running・成功・失敗・未作成は受け付ける
- ペイロードは `{"trigger":"manual","date":date,"force":true,"requested_by":<ID トークンの email。無ければ sub>}`。集計 Lambda は既存の手動経路（6.1）で受け、`auto_run_enabled` に関わらず動く。同じ日の取得は 11.2 の条件付き UpdateItem が守るので、API の 409 判定との間の競合でも二重送信しない
- Invoke の受付に失敗（例外、または StatusCode が 202 でない）したら 502 RUN_UNAVAILABLE。DAILY は API からは書かない
- 設定の変更ではないので `AUDIT` に書かない。INFO ログに date・実行者のメール・sub を残す（Invoke 失敗時は WARNING に例外の型だけ）
- 1 日あたりの回数上限は設けない（admin 限定。プレビューの 20 回とは別）

「ログを見る」の契約（v2.1 第 2 段階）:

- date は実在する JST 日付で、受付日（JST）の当日から 90 日前まで（CloudTrail のイベント履歴と同じ）。範囲外・形式不正は 400 VALIDATION_ERROR
- 本文は `ref` そのもの（`kind` がトップレベル、ほかの項目が対象）か `{kind, target}` の入れ子のどちらか（混在は 400）。`kind` は lambda / alarmhuba / gps / cloudtrail / guardduty / metric、対象は 6.2d の必須項目を持つこと（余分な項目、IP・リージョン・関数名・パス・ID の形式不正、`from` > `to`、CloudWatch で引けない指標キー、指定日の集計（`summary.counters` / `summary.comparisons`）に無い指標キーは 400 INVALID_QUERY）。例（平らな形）: `{"kind":"lambda","function":"example-fn"}`、`{"kind":"alarmhuba","ip":"198.51.100.21","path":"/api/admin/{id}/settings"}`、`{"kind":"gps","ip":"198.51.100.23"}`、`{"kind":"cloudtrail","region":"us-east-1","event":"CreateUser","actor":"arn:aws:iam::123456789012:user/example","event_id":"ev-1","from":"2026-10-16T09:00:00+09:00","to":"2026-10-16T18:00:00+09:00"}`、`{"kind":"guardduty","region":"ap-northeast-1","id":"0123456789abcdef0123456789abcdef"}`、`{"kind":"metric","key":"alb.4xx","target":"alb:ap-northeast-1:app/example/0123456789abcdef"}`（metric の `target` は文字列なので平らな形とみなす）
- `query_id` は `{date, kind, target}` の正規化 JSON の sha256 先頭 16 桁。同じ `(date, kind, target)` の succeeded が残っていれば（受付から 1 日）Invoke せず 200 で `result` を返す。running（受付から 10 分以内）なら 202 で同じ `query_id` を返す。failed・10 分超過の running・無しなら新しく受け付ける
- 同時実行はユーザー（ID トークンのメール）ごとに 3 件まで。10 分以内の running が 3 件あれば 429 TOO_MANY_QUERIES「同時に見られるのは 3 件までです」
- API は running の LOGS（kind / target / requested_by / requested_at / ttl）を保存して集計 Lambda を `InvocationType = Event`（`{"trigger":"logs","date","query_id","kind","target","requested_by"}`）で起動する。Invoke の受付に失敗（例外、または StatusCode が 202 でない）したら LOGS を failed / LOGS_UNAVAILABLE にして 502 LOGS_UNAVAILABLE
- 結果の `result` は `{source, range: {from, to}, rows: [...], truncated, scanned_bytes?, note?}`。行は `{time（JST の ISO 8601。metric は HH:MM）, ...kind ごとの列}`（6.2d）で最大 200 行、`text` は 300 字、既存の伏せ字処理（`diagnostics._redact`）を通す。ヘッダ・本文・Cookie・トークンは出さない。失敗は `error` にコード（TIMEOUT / AccessDeniedException / UNKNOWN_REGION / NO_TARGET など）
- 応答には `kind` と正規化後の `target`（入れ子）を含め、`requested_by` は含めない。フロントは受付後 2 秒ごとに GET、succeeded / failed で停止（最大 90 秒）
- 設定の変更ではないので `AUDIT` に書かない。INFO ログに date・query_id・kind・実行者のメールを残す（Invoke 失敗時は WARNING に例外の型だけ）

主なエラーコード（saas-a-billing 8 章の表のうち、このアプリで使うもの）:

| code | HTTP | 意味 |
|---|---|---|
| `VALIDATION_ERROR` | 400 | 入力の形式不正（`message` に項目名と、除外リストなら何行目か） |
| `MAIL_DOMAIN_NOT_ALLOWED` | 400 | `mail_to` / `mail_cc` に `@example.co.jp` 以外の宛先がある |
| `SETTINGS_TOO_LARGE` | 400 | 設定全体が 180,000 バイトを超過、または設定・監査トランザクションが DynamoDB の項目 / トランザクションのサイズ上限を超過。設定も監査も保存しない |
| `INVALID_QUERY` | 400 | `POST /days/{date}/logs` の本文が `ref` の形（`{kind, …}`）でも `{kind, target}` でもない、両方が混在する、各項目の形式不正、CloudWatch で引けない / 指定日の集計に無い指標キー、指定日の記録に無い function / ip / GuardDuty の id（lambda / alarmhuba / gps / guardduty は所見・明細・上位表の `ref` と照合する。cloudtrail は形式検証のみ） |
| `NOT_FOUND` | 404 | 対象日の DAILY / 有効な PREVIEW / 有効な LOGS が無い |
| `TOO_MANY_QUERIES` | 429 | `POST /days/{date}/logs` で同じユーザーの実行中（10 分以内の running）が 3 件ある |
| `PREVIEW_LIMIT` | 429 | JST の受付日あたりの試行が 20 回に達した |
| `AI_UNAVAILABLE` | 502 | プレビューの失敗（非同期処理の結果は PREVIEW.error に記録し、取得 GET 自体は 200） |
| `VERSION_CONFLICT` | 409 | 設定の楽観ロック不一致 |
| `RUN_IN_PROGRESS` | 409 | `POST /days/{date}/run` で同じ日を実行中（running で 20 分以内） |
| `RUN_UNAVAILABLE` | 502 | `POST /days/{date}/run` で集計 Lambda の非同期 Invoke を受け付けられなかった |
| `LOGS_UNAVAILABLE` | 502 | `POST /days/{date}/logs` で集計 Lambda の非同期 Invoke を受け付けられなかった（LOGS は failed） |
| `SLACK_API_ERROR` | 502 | `GET /slack/directory` で Slack が `ok:false` を返した |
| `SLACK_UNAVAILABLE` | 502 | `GET /slack/directory` で Slack に到達できなかった |
| `SLACK_NOT_CONFIGURED` | 503 | Bot token（`/shared/slack/bot-token`）が未登録 |

- レポートの再実行は `POST /days/{date}/run`（画面の「今すぐ実行」）か 14.3 の CLI で行う。送信だけの再送（集計し直さない）の API は持たない

## 9. 画面一覧

SPA 配置: `frontend/src/apps/daily-security-report/`。マニフェスト（プラットフォーム設計 9.4 の `AppManifest`）は `id: "daily-security-report"`、`title: "AWS日次セキュリティレポート"`（サイドバー名）、`icon` は任意（例 `mdi-shield-search`）、`order: 30`、`requiredGroups: []`（全ログインユーザー。設定はルート `meta` と nav で `['admin']`）、`routes`（`component` はすべて `() => import()`）、`nav`（レポート一覧 / 設定。詳細は一覧のボタンから遷移）とする。API は `createAppApi('daily-security-report')`。UI は saas-a-billing の設定画面に揃える。

| 画面 | パス | 主要項目 | 使う API |
|---|---|---|---|
| レポート一覧 | `/daily-security-report` | 新しい順に 30 日。列は対象日 / 段階（チップ）/ AI の見解（120 字で切り、全文は詳細）/ 送信先 / 送信時刻 / 詳細ボタン。段階の近くに実行状態（成功 / 失敗 / 実行中）を表示し、未完了の段階は未確定とする。legacy の段階は「v1」と注記 | `GET /days` |
| レポート詳細 | `/daily-security-report/days/:date`（全員。「ログを見る」は admin だけ） | 上部に対象日・最終段階・AI の見解全文・表示名・モデル ID・プロンプトハッシュ。区分ごとのカードに、見出し横の HelpTip（区分の説明）、規則 / 最終の段階チップ、AI の区分理由とその下の「根拠: 表示名、…」、所見表（段階 / 内容（下に所見コード。AI が `keys` に挙げた所見コードの行に「AI の根拠」チップ）/ 件数 / 時刻（`first_at`〜`last_at` を HH:MM、同じなら 1 つ、無ければ —）/ 場所（`where`。`link` があれば「コンソール」を新しいタブで））、明細表、上位表（`summary.top` を区分へ割り当て）、集計値の表（指標の表示名 / 件数 / 基準 / 比率 / 判定 / 時間帯）、取得状況（未集計の理由 / 停止中）。休眠主体・初見・他アカウントと棚卸しの表、末尾に集計範囲・除外・所要時間・省略件数。admin には所見・明細・上位表・集計値の行に「ログを見る」ボタンと、ログのダイアログ | `GET /days/{date}`、`POST /days/{date}/logs`（admin）、`GET /days/{date}/logs/{id}`（admin） |
| 設定（admin） | `/daily-security-report/settings` | 集計対象サマリ、「今すぐ実行」のカード（対象日・確認ダイアログ・結果）と 3.3 の既存項目。「AI 判定」のカードにトグル、系統（経路・月額目安）、ピン留め、プロンプト（編集できる部分。既定の本文を表示）と「既定 / カスタム」のチップ、「既定に戻す」、固定部分（編集不可）の表示、名前付きプロンプトのカード（「既定」の固定カード + 最大 20 件。適用・新規保存・上書き保存・名前変更・削除）。過去の日を選び「この文面で試す」→ 結果パネルに区分ごとの段階と理由、見解、トークン数、所要時間、使用モデル。通知先に Slack メンションと「メンションを付ける段階」。しきい値のカードに休眠の 3 項目 | `PUT /settings`、`GET /slack/directory`、`GET /days`、`POST /ai/preview`、`GET /ai/preview/{id}`、`POST /days/{date}/run`、`GET /days/{date}` |

- インスタンスの後に「S3」カード（`data-test="detail-s3"`）を置く。`CounterTable` で `s3.*` の counters / comparisons を表示し、続けて `top.s3_buckets` のバケット表を置く。列は「バケット」「リージョン」「オブジェクト数」「前日差（件・%）」「容量」「前日比」「ダウンロード量」「削除要求」「判定」。容量・ダウンロード量は 1,024 基数で GB / MB 表示、判定は `LevelChip`、要求メトリクスの無いバケットは「—」。バケットの `link` は既存の AWS コンソールのホスト制限で描画する。「ログを見る」は S3 の所見・集計値・バケット表すべてで対象外
- 設定の有効 / 無効と一覧・段階表示に `s3` を加える。型は `Section` に `'s3'`、`S3BucketRow`、`summary.top.s3_buckets` を追加し、バイト整形関数は utils に置く。HelpTip `section_s3`:「全バケットの容量とオブジェクト数の前日比、要求メトリクスが有効なバケットのダウンロード量と削除要求の前 7 日平均との比較です。大量削除と持ち出しの検知用です。」
- 表は横スクロール可能にし、段階は文字とチップ色の両方で示す。保存結果はスナックバーで通知。詳細の 404 / 取得失敗は画面に理由と一覧への戻りを表示する
- プロンプトのテキストボックス（ラベル「プロンプト（編集できる部分）」）には、保存値 `ai_prompt` が空なら `GET /settings` の `ai_prompt_default`（既定の本文）を入れて表示し、そのまま追記・修正できるようにする（2026-10-16 ユーザー指示。既定が画面に出ず直しようがなかったため）。開いた直後の表示は未保存の変更に数えない
- 保存（`PUT /settings`）と試行（`POST /ai/preview`）に送る `ai_prompt` は本文だけとし、既定の本文と前後の空白を除いて同じか空白だけなら空文字、違えば入力のまま送る（サーバの正規化と同じ規則。3.3）。脇のチップで「既定」/「カスタム」を示す
- 「既定に戻す」はテキストボックスを既定の本文に戻す（未保存。保存すると `ai_prompt` は空になり、既定の改訂に追従する）。試行結果を見てから明示的に保存する。試行中はボタンを無効にして二重受付を防ぎ、失敗時は理由をパネルに出す。文面変更後は以前の結果を「変更前の試行」と表示する
- 固定部分（`ai_prompt_fixed`。submit_verdict で返す指示と、データの扱いの前置き）はテキストボックスの外、下に読み取り専用のブロックで出す。見出しは「固定部分（編集不可。保存時にプログラムが結合します）」、1 行目「system prompt の末尾: {system_suffix}」、2 行目「データの前置き: {data_prefix}」。プレーンテキストで描画し（`v-html` を使わない）、入力欄を置かない。結合は集計 Lambda が行い、画面は本文だけを送る。`ai_prompt_fixed` を取得できていないときはブロックを出さない
- 名前付きプロンプトはカードで並べる。先頭は「既定」の固定カード（チップ「既定」、本文の先頭 120 字、ボタンは「適用」だけ。削除・上書き保存・名前変更は無く、`ai_prompt_presets` には入れない）。続けて `ai_prompt_presets` の配列順にカード（名前、更新日時（JST）、本文の先頭 120 字、「適用」「上書き保存」「名前変更」「削除」）。テキストボックスの文面と前後の空白を除いて一致するカードに「使用中」のチップを付ける（文面が空白だけのときは、保存すると既定になるので既定のカード）。「適用」はそのカードの本文をテキストボックスに入れる
- 「現在の文面を新規保存」は名前のダイアログ（必須、前後の空白を除いて 1〜40 字、既存の名前と重複不可）で確定するとカードを末尾に足す（`id` は小文字の UUID v4、`updated_at` は操作時刻の ISO 8601）。文面が空白だけ、20 件に達した（「20 件まで」と表示）、読み取り専用のときは押せない。「上書き保存」は確認ダイアログ「{name} を現在の文面で上書きします」→「上書きする」で本文と更新日時を置き換える。「名前変更」は同じ検証の名前ダイアログ。「削除」は確認ダイアログ「{name} を削除します」→「削除する」。どの操作も確認のボタンを押すまでカードの配列を変えない
- カードの変更は設定のフォーム（`ai_prompt_presets`）に入れ、通常の「保存」（`PUT /settings`）でほかの設定と一緒に反映する（別の API は持たない。変更は `AUDIT` に残る）。カードの下に「カードの変更は設定の「保存」で反映されます」と注記する。保存前に件数（20 件まで）・名前・本文（前後の空白を除いて 1〜8,000 字）・名前の重複を検証し、通らなければ「3 件目の名前は 1〜40 字にしてください」のように何件目かを入力エラーに出して保存できない。読み取り専用のときもカードは見せ、ボタンをすべて無効にする
- 通知先の Slack メンション欄の隣に「メンションを付ける段階」（`slack_mention_level`）を置き、「Critical のみ」（既定）/「Warning 以上」/「常に（OK の日も）」から選ぶ（2026-10-16 ユーザー指示）。ヒントは「即時通知（GuardDuty 重大検出）は Critical 扱いで常に付きます。メールには付けません」。読み取り専用のときは無効
- 詳細は機密ヘッダ・生ログを出さず、保存済みの集計・証拠だけを表示。AI の文面はプレーンテキストで描画する（v-html を使わない）
- 所見表の「場所」の `link` は `https://` で始まりホストが `console.aws.amazon.com`（またはそのサブドメイン）の URL だけを `<a target="_blank" rel="noopener">コンソール</a>` にし、それ以外の文字列はそのまま表示する（`v-html` は使わない）。`where` も `link` も無い行は —。時刻は `@/utils/format` の `toWallClock` で JST の壁時計として組み直す（環境の時差に依存しない）
- 集計値は別部品 `components/CounterTable.vue`（`counters` / `comparisons` / `hourly` / `glossary` / `aiKeys` / `label` を受ける）。列は 指標（`glossary` の表示名。説明は HelpTip、表示名とキーが違うときだけ下にキーを小さく）/ 件数 / 基準（`comparisons[key].baseline`。`label` が「基準なし」ならその文言）/ 比率（`×1.29` の形。null は —）/ 判定（`exceeded` → Warning の LevelChip、`label` あり → 灰色のチップ「基準なし」、それ以外は空）/ 時間帯（`sources.metrics.hourly[key]` の最多 2 つを「03 時 80 件、17 時 12 件」。全部 0 か無ければ —。24 時間の内訳は同じセルの v-tooltip）。`exceeded` の行は背景を warning 色の 12% にする。AI の `keys` に含まれるキーの行に「AI の根拠」チップ（x-small、tonal）。`glossary` が無い応答はキー名をそのまま出す
- 上位表は `summary.top` の表名で区分へ割り当てる: `guardduty` → GuardDuty、`access_denied_principals` / `console_login_failures` → CloudTrail、`waf_countries` / `waf_vulnerabilities` / `waf_rules` / `waf_dimensions` / `lambda_errors` → 入口の指標、`alarmhuba_statuses` / `alarmhuba_ips` / `alarmhuba_countries` / `gps_ips` → Logs Insights、`s3_buckets` → S3（前述の専用バケット表）。割り当てに無い表名は表示しない。見出しは `utils/trace.ts` の `TABLE_LABELS`（例「Lambda エラーの関数（上位）」「GuardDuty の検出（上位）」「alarmhuba の上位 IP」「AccessDenied の主体（上位）」）。`summary.top_truncated[表名]` があれば表の下に「ほか n 件」。明細と同じ `EvidenceTable` で描き、`lambda_errors` の `hourly` は「時間帯」列に最多の時間帯、`guardduty` の `link` は「コンソール」列のリンクになる
- `EvidenceTable` の列見出しに 時間帯（`hourly`）/ 場所（`where`）/ コンソール（`link`）/ イベント ID（`event_id`）/ 対象（`resource`）/ 相手 IP（`remote_ip`）/ 関数（`function`）/ 国（`cc`）/ Web ACL（`acl`）/ 分類（`category`）/ ルール（`rule`）/ 指標（`metric`）/ 遮断（`blocked`）/ 許可（`allowed`）を足し、`first_seen` / `last_seen` は ISO 日時なら「YYYY-MM-DD HH:MM」に整形する（日付だけの値はそのまま）。`ref`（第 2 段階の「ログを見る」の問い合わせパラメータ）は列に出さない
- AI の区分理由の下の「根拠: …」は `ai.sections[].keys` を 1 つずつ、`glossary` の表示名 → 所見コードに一致する所見の `text` → 上位表の見出し → そのまま、の順で表示名に変える。`keys` が無い・空の区分には出さない
- 区分カードの見出しの HelpTip は `help.ts` の `section_guardduty` / `section_cloudtrail` / `section_metrics` / `section_insights` / `section_instances` / `section_s3`（S3 は前述、GuardDuty = 当日新しく出た検出、CloudTrail = 人の操作・構成変更・ログイン、入口の指標 = WAF / API Gateway / ALB / CloudFront / Lambda の CloudWatch 指標（日合計）、Logs Insights = alarmhuba-backend Resolver と GPS 受信口（GPS モックサーバー）の CloudWatch Logs。GPS は GPS 転送ユニット（GpsUnitA）の受信口、受信 = Receiver のログ件数、拒否 = ALB の 4xx、インスタンス = EC2 / Lightsail の台数と CPU / 通信量）
- 旧記録（`glossary` / `comparisons` / `hourly` / `top` / 所見の `first_at` / `where` / `link` / AI の `keys` が無い）でも同じ画面で描画し、無い項目は — か空にする。v1 の読み替え応答は API が `comparisons` の `red` を `exceeded` に直した形（`value` は無い）を前提にする
- 型（`types.ts`）: `Finding` に `first_at? / last_at? / where? / ref? / link?`、`AiSection` に `keys?`、`SourceResult` に `hourly? / hourly_truncated?`、`DailySummary` に `comparisons? / top_truncated?`、`DayDetail` に `glossary?`、`Comparison`、`Glossary`、`LogRef`（`kind` で判別する合併型: lambda / alarmhuba / gps / cloudtrail / guardduty / metric）。第 2 段階の「ログを見る」ボタンは `Finding.ref` と明細行の `ref` を使う
- 純粋関数は `utils/trace.ts`（`formatTimeRange` / `isConsoleLink` / `peakHours` / `hourlyBreakdown` / `formatRatio` / `counterRows` / `counterSection` / `evidenceNames` / `TABLE_LABELS` / `TOP_SECTIONS`）に置き、画面と部品から使う
- 「ログを見る」ボタン（x-small、tonal、`data-test="log-button"`）は admin（`useAuthStore().groups` に `admin`。設定画面と同じ判定）だけに出す。出す行は、所見表（場所の列の下）で `ref` が問い合わせに使える形のもの、明細表と上位表（`EvidenceTable` の末尾の列「ログ」）で `ref` があるもの、集計値の表（`CounterTable` の末尾の列「ログ」）で CloudWatch 指標で引けるキー（`waf.` / `apigw.` / `alb.` / `cloudfront.` で始まるもの、`lambda.errors`、`gps.rejected`、`instances.ec2|lightsail.{名前}.cpu_avg|net_out`）の行。集計値の行は `{kind: "metric", key}` を問い合わせる。`ref` の無い行・必須項目の欠けた `ref`（lambda は `function`、alarmhuba / gps は `ip`、cloudtrail は `region` と `event_id` / `event` / `actor` のどれか、guardduty は `region` と `id`、metric は `key`）・旧記録の行には出さない。列「ログ」は出せる行が 1 つも無い表には足さない
- ボタンを押すと 1 つのダイアログ（`components/LogDialog.vue`）を使い回して開き、`POST /days/{date}/logs` に `ref` と同じ形の本文（例 `{"kind": "alarmhuba", "ip": "198.51.100.21", "path": "/api/admin/{id}/settings"}`。API は `{kind, target}` の入れ子も受ける）を送る。応答が `running` なら 2 秒ごとに `GET /days/{date}/logs/{query_id}` を確認し、最大 90 秒で打ち切る（`composables/useLogQuery.ts`。状態は idle / running / succeeded / failed）。200（24 時間以内の同じ問い合わせ）はそのまま結果を出す。ダイアログを閉じる・画面を離れる・日付が変わる・admin でなくなったときはポーリングを止め、遅れて届いた応答は捨てる
- ダイアログの見出しは「ログを見る」（HelpTip `help.ts` の `logs`: 取得元・200 行・60 秒・同時 3 件・90 日・24 時間キャッシュ・Logs Insights の走査量課金・ヘッダ等を出さないこと）と問い合わせの対象（例「alarmhuba 198.51.100.21 /api/…」「Lambda fn」「CloudTrail us-east-1 CreateUser」「GuardDuty ap-northeast-1 abc」「指標 WAF 遮断」）。本文は 取得元（`result.source`）/ 期間（`range` を JST の YYYY-MM-DD HH:MM）/ 行数（`truncated` なら「先頭 n 行。上限で打ち切り」）/ 走査量（`scanned_bytes` があれば MB、1 MB = 1,048,576 バイト、小数 1 桁）/ 注記（`note`）と表
- 表の列は kind ごと: lambda = 時刻（JST）/ 内容、alarmhuba = 時刻 / メソッド / パス / 状態、gps = 時刻 / メソッド / パス、cloudtrail = 時刻 / 操作 / 主体 / IP / イベント ID（行の `link` が AWS コンソールの URL ならイベント ID をそのリンクにする）、metric = 時刻（HH:MM）/ 件数、guardduty = 項目 / 値の縦の表（種別 / 重大度 / 題名 / 説明 / 対象 / 相手 IP / 相手の国 / 初回観測 / 最終観測 / 回数。知らない項目は項目名のまま後ろに足し、`link` と `time` は出さない）。時刻は JST の YYYY-MM-DD HH:MM:SS（metric は HH:MM）。内容はプレーンテキストで描画し（`v-html` は使わない）、長い内容は折り返す。0 行は「該当するログはありません。」
- 「コンソールで開く」は、押した行の `link`、結果の `link`、guardduty なら結果の行の `link` の順で、`https://` かつホストが `console.aws.amazon.com`（またはそのサブドメイン）の URL があるときだけ出し、新しいタブ（`rel="noopener"`）で開く
- running 中は進捗バーと「問い合わせ中（2 秒ごとに結果を確認、最大 90 秒）」。失敗は「取得できませんでした: 理由（コード）」。理由はコードから: `TOO_MANY_QUERIES`（または HTTP 429）=「同時に見られるのは 3 件まで」、`TIMEOUT` =「60 秒で打ち切り」、画面側の 90 秒切れ = コード `TIMEOUT`・「90 秒待っても結果が返りませんでした」、API がメッセージ付きで返したもの（400 `INVALID_QUERY` 等）=「メッセージ（コード）」、それ以外はコードのまま
- 型（`types.ts`）: `LogKind`、`LogQueryRequest`（= `LogRef`）、`LogQueryStatus`、`LogRow`、`LogQueryResult`（`source / range / rows / truncated / scanned_bytes? / note? / link?`）、`LogQueryError`（コードの文字列か `{code, message?}`）、`LogQueryResponse`（`query_id? / status / requested_at? / result? / error?`。POST の 202 / 200 と GET で共通）。`LogRef` の cloudtrail の `from` / `to` は任意（無ければ当日）。API は `api.ts` の `startLogs(date, body)` / `getLogs(date, id)`（`createAppApi` の共通エンベロープ）
- 純粋関数は `utils/logs.ts`（`isLogRef` / `isMetricCounter` / `logErrorMessage` / `formatScanned` / `formatLogCell` / `describeLogRef` / `LOG_COLUMNS` / `GUARDDUTY_FIELDS`）
- 「今すぐ実行」は確認ダイアログを経てから API を呼び、受付後は 5 秒ごとに最大 15 分 `GET /days/{date}` を確認して結果（成否・段階・見解の先頭 120 字・詳細リンク）を同じカードに出す（3.3）。カードは別部品（`components/RunNowCard.vue`、ポーリングは `composables/useRunNow.ts`）
- 名前付きプロンプトのカードは別部品（`components/PromptPresetCards.vue`。`presets` / `currentPrompt` / `defaultPrompt` / `editable` を受け、`update:presets`（新しい配列）と `apply`（本文）を返す）。既定との比較・名前の検証・UUID の生成は `utils/prompt.ts`（`crypto.randomUUID` が無い環境では `crypto.getRandomValues` で v4 を組み立てる）

## 10. 設定項目と保管先

| 区分 | 項目 | 保管先 | 変更方法 |
|---|---|---|---|
| 画面設定 | 3.3 の項目（配布・実行トグル、監視する区分 `sections`、メール宛先、Slack チャンネル・表示名・メンション・`slack_mention_level`、しきい値、`ai_enabled` / `ai_model_family` / `ai_model_pin` / `ai_prompt` / `ai_prompt_presets`、`dormant_days` / `inventory_dormant_days` / `inventory_weekday`、`metric_targets`、`alarmhuba_admin_path_regex`、除外リスト `exclusions`） | DynamoDB `SETTINGS/current`（無い項目はコードの既定値） | 設定画面（`admin`）。監査記録あり。開発者本人の DM の `slack_channel_id` は初回に API（`PUT /settings`。14.1 の 6） |
| 固定（スタックパラメータ） | `CloudTrailBucket`（既定 `cloudtrail-example`）、`CloudTrailPrefix`（既定 `AWSLogs/210000000001/CloudTrail/`）、`AlarmhubAResolverLogGroup`（既定 `/aws/lambda/AlarmHubA-Backend-Resolver-XXXXXXXXXXXX1-Function-XXXXXXXXXXX2`）、`GpsReceiverLogGroup`（既定 `/aws/lambda/GpsUnitAMock-Receiver-XXXXXXXXXXX3-Function-XXXXXXXXXXX4`）、`PortalUrl`（5.1 のポータル URL）、`OpsTopicArn`、SSM 型の Cognito / notify / attachments / shared-layer 各キー | template.yaml + samconfig.toml | デプロイ。IAM の Resource と環境変数を同じ値から作る。`tests/infra/test_samconfig.py` が dev / prod の値を検査する |
| 固定（IaC） | GuardDuty の 3 リージョンと検出器 ID（Mappings。4.1）、Scheduler の cron `cron(0 7 * * ? *)`、EventBridge ルールのパターン（severity >= 7）、timeout / メモリ、失敗キュー、アラーム、HTTP API のスロットリング（burst 20 / rate 10） | template.yaml | デプロイ |
| 固定（環境変数） | `ENV`、`LOG_LEVEL`、`APP_NAME=daily-security-report`、`TABLE_NAME`、`NOTIFY_QUEUE_URL`、`ATTACHMENTS_BUCKET`、`ACCOUNT_ID`、`SSM_PREFIX`（`/example-backoffice/{env}`）、`PORTAL_URL`（パラメータ `PortalUrl`、v2 から詳細リンクに使用）、`REPORT_FUNCTION_NAME`（API Lambda がプレビューと今すぐ実行で Invoke する集計 Lambda 名）、`GUARDDUTY_DETECTORS`（`region=検出器 ID` のカンマ区切り）、`CLOUDTRAIL_BUCKET`、`CLOUDTRAIL_PREFIX`、`INSIGHTS_LOG_GROUPS`、`POWERTOOLS_SERVICE_NAME` | template.yaml | デプロイ |
| 秘密 | アプリ固有の秘密なし。origin-verify シークレットと Slack Bot token（候補検索の読み取りのみ） | SSM SecureString `/example-backoffice/{env}/edge/origin-verify-secret`、`/example-backoffice/{env}/shared/slack/bot-token` | core / shared 担当 |

- 「どこを読むか」はスタックパラメータと template.yaml、「どう判定して誰に送るか」は DynamoDB、と分ける。前者は IAM と同時に変わる必要があり、後者は設定画面から変えるため
- `PORTAL_URL` はアプリパスを含む URL（6.5）。`REPORT_FUNCTION_NAME` は `!Ref ReportFunction` から API Lambda に渡す。`SSM_PREFIX` は雛形どおり保持し、Slack のキーは ENV から組み立てる
- `functions/requirements.txt` は `requests==2.34.2`（Slack 候補検索）を持つ。boto3 はランタイム、Powertools と shared はレイヤで供給する
- チャンネル ID などの値をスタックパラメータや samconfig に置かない（ADD-APP-GUIDE 4 章のチェックリスト。送信先の既定値と送信拒否リストのパラメータは置かない）

- v2.2 の `settings.sections.s3` は DynamoDB の画面設定（既定 true）。S3 の減少率・最低件数・最低バイト数・急増倍率は `s3_summary.py` のモジュール定数で、設定画面のしきい値には追加しない。S3 の対象リージョンは既存 `GUARDDUTY_DETECTORS` のリージョン一覧を使う

## 11. エラー処理・冪等性・再実行

### 11.1 事象別の挙動

| 事象 | 検知 | 挙動 | 記録 | 通知 |
|---|---|---|---|---|
| 集計 6 種のうち一部が失敗（権限不足、API エラー、Insights の全体期限超過など） | 各集計の例外 | その区分を未集計にして続ける。該当区分を warning 以上にし、通知に「未集計（理由）」を出す（2.8） | DAILY の `summary.sources`、`status = succeeded` | レポートそのもの（アラームは鳴らさない） |
| 集計 6 種がすべて失敗、レポートの組み立て失敗 | `report` | 短い失敗通知（6.5 の「集計の失敗」）を設定で有効な配布先へ送ってから例外を投げる | DAILY `status = failed`、`error` | Lambda Errors アラーム、失敗キューのアラーム |
| `notify.send()` の失敗（SQS） | `report` | 例外を投げる | DAILY `status = failed`、`error` | 同上 |
| 区分が OFF（設定 `sections`） | `report`（設定の読み出し） | その区分を集計しない（API も呼ばない） | DAILY の `summary.sources.{区分}.status = disabled` | 通知の該当区分行は省略。詳細に「停止中（設定）」。アラームは鳴らさない |
| 有効な配布先が無い（トグルが両方無効、または Slack のチャンネル ID が空） | `report` / `guardduty-alert` | 送らずに正常終了 | DAILY の `notify_note`、INFO ログ | なし |
| Scheduler の Invoke 失敗 | Scheduler の RetryPolicy（2 回・1 時間）後に DLQ | その日のレポートは出ない | Scheduler DLQ | DLQ のアラーム |
| `report` の timeout（900 秒） | Lambda | `status = running` のまま残る。20 分後には次の起動が奪える（11.2） | DAILY | Lambda Errors アラーム、失敗キューのアラーム |
| 即時通知の送信失敗 | `guardduty-alert` | `ALERT` 項目を消して例外。非同期再試行 2 回の後、失敗キューへ | ログ | Lambda Errors アラーム、失敗キューのアラーム |
| EventBridge からの配信失敗 | EventBridge の再試行（既定） | 再試行が尽きたら捨てられる | なし | `FailedInvocations` のアラーム |
| notifier 側の失敗（Slack API・メール） | core | core の再試行・DLQ（プラットフォーム設計 8.6） | core | core の DLQ アラーム |
| AI の失敗・拒否・時間切れ | ai.py | レポートは止めず rule_levels を採用。「AI の見解: 取得できず（理由）」 | DAILY.ai の status / error | 通常のレポートに必ず表示 |
| IAM の読み取り失敗 | iam_snapshot.py | actors.status=unavailable、推測で休眠 / 初見にしない。既に得た ACTOR 観測は保存可能 | actors.note、WARNING（秘密を除く） | 末尾に「休眠判定: 取得できず」 |
| プレビューの失敗 | api / ai_preview | 通常のレポートに影響させない。PREVIEW.status=failed、error に AI_UNAVAILABLE と理由 | PREVIEW.error | 設定画面の結果パネル |
| 今すぐ実行の受付不可（同じ日を実行中、Invoke 失敗） | `api` | 409 RUN_IN_PROGRESS / 502 RUN_UNAVAILABLE を返し、DAILY を書かない | INFO / WARNING ログ | 設定画面のカードに理由を表示 |
| ログの問い合わせの失敗（Insights の 60 秒超過、権限、API エラー、検出なし） | `report`（trigger=logs） | レポートに影響させない。LOGS.status=failed、error にコード（TIMEOUT / AccessDeniedException / UNKNOWN_REGION / NO_TARGET など）。受付から 10 分を過ぎても running なら GET が TIMEOUT に直す | LOGS.error、WARNING（秘密を除く） | 詳細ページのダイアログに理由 |
| 設定の保存の失敗（検証エラー、`version` の競合） | `api` | 400 / 409 を返し、設定も監査も保存しない | なし | 画面に理由を表示（3.3） |

### 11.2 冪等性

- 業務キー = 対象日。`DAILY/{date}` の取得を次の条件付き UpdateItem で行う（`status = running`、`attempt + 1`、`request_history` に追記）
  - scheduled / manual（`force = false`）: `attribute_not_exists(PK) OR #status = failed OR (#status = running AND started_at < :stale)`。`:stale` は 20 分前（v2 の timeout 900 秒より長くとる）。v1 の 15 分から変更し、実行中の 900 秒バッチを奪わない
  - manual（`force = true`）: `NOT (#status = running AND started_at >= :stale)`。成功済みの日も集計し直して送り直す。画面の「今すぐ実行」は常にこの条件で取得する
- 取れなかったときは空振り（`ALREADY_DONE` / `IN_PROGRESS`）として INFO ログだけ残す。Scheduler の重複起動や手動実行との重なりで同じ日を 2 回送らない
- レポートの `notify.send()` が成功したときだけ `sent_count` を 1 つ進める。送信失敗・配布先なし・集計失敗の通知では進めない。2 回目以降のレポートは `context` に「再送（第 n 回）」と出す
- 再取得時（`force` を含む）は前回の `notify_message_id` / `notify_channels` / `notified_at` を消す。送信しなかった実行の `fail_daily` も通知情報を空にする
- 送信後の `finish_daily` に失敗したら WARNING を残し、今回の通知情報を保持したまま `fail_daily` を試みる（送信済みなので `sent_count` も進める）
- 即時通知は `ALERT/{region}#{finding_id}` の条件付き書き込みで 1 検出 1 回（6.6）
- `report` の非同期再試行は 0 回にする（再試行で 2 回送らない）。`guardduty-alert` は重複防止があるので 2 回再試行してよい

### 11.3 再実行

| ケース | 操作 | 結果 |
|---|---|---|
| テストや出し直しで今すぐ流す | 設定画面の「今すぐ実行」（確認ダイアログで「実行する」） | `force = true` と同じ。集計し直して設定の宛先へ送る（送信済みなら「再送（第 n 回）」）。当日は途中までの集計 |
| 失敗した日 | 14.3 の `aws lambda invoke`（`force` 不要） | `attempt + 1`。成功すればレポートが出る |
| 成功済みの日を出し直す（しきい値や除外を直した後など） | `force: true` | 集計し直して「再送（第 n 回）」で送る。翌日以降の基準も新しい値になる |
| 過去の日を後から埋める | `date` を指定して `force: false` | 前 7 日の基準はその時点の DAILY で計算する |

- 画面からは常に `force = true` になる。`force: false`（失敗した日だけ取り直す）で動かしたいときは 14.3 の CLI を使う

## 12. セキュリティ

### 12.1 認証・認可

- API は雛形どおり: HTTP API の JWT authorizer（Cognito ID トークン）、Lambda 側の `token_use == "id"` の再検証、`origin_verify`、edge の behavior の `EdgeGuardFunction`（社内 IP 制限）。`GET/PUT /settings`、`GET /slack/directory`、`POST /days/{date}/run`、`POST /ai/preview`、`GET /ai/preview/{id}` は `auth.require_group(claims, "admin")` で保護する。`/health`・`GET /days`・`GET /days/{date}` は認証済み全ユーザー
- 画面は設定の nav と route に `requiredGroups: ['admin']` を付ける（9 章）
- バッチに外からの入口は無い。`report` は Scheduler のロールと API Lambda の限定 IAM（プレビューと今すぐ実行。Resource は集計 Lambda だけ）、`guardduty-alert` は `AWS::Lambda::Permission`（`SourceArn` = このアプリのルール）からしか呼ばれない

### 12.2 IAM 最小権限

このアプリはアカウント全体（GuardDuty、CloudTrail の S3、CloudWatch、Logs Insights、EC2 / Lightsail のインスタンスの一覧と指標）を読む。既存アプリと違い自分以外のリソースに触るため、監視対象の変更権限は付けず、IAM のレポート生成・読み取りと Bedrock 推論を追加する。README にも明記する。ロールは関数ごとに作る。

`report` ロール:

| アクション | Resource | 備考 |
|---|---|---|
| `guardduty:ListFindings`, `guardduty:GetFindings` | `arn:aws:guardduty:{region}:210000000001:detector/{検出器 ID}/findings`（ListFindings）と `.../findings/get`（GetFindings）の 6 本（4.1 の ID。template.yaml の Mappings から作る） | 実機で `/findings` が必要と確認済み。`ListDetectors` は呼ばない |
| `s3:ListBucket` | `arn:aws:s3:::cloudtrail-example`（条件 `s3:prefix` が `AWSLogs/210000000001/CloudTrail/*`） | リージョンと日付のフォルダの列挙 |
| `s3:GetObject` | `arn:aws:s3:::cloudtrail-example/AWSLogs/210000000001/CloudTrail/*` | 書き込み・削除の権限は付けない。KMS の権限は要らない（4.2） |
| `cloudwatch:GetMetricData`, `cloudwatch:ListMetrics` | `*` | Resource を限定できないアクション。ListMetrics は S3 のバケット・StorageType・要求メトリクス列挙用（v2.2）。S3 区分に S3 の権限は追加しない |
| `logs:StartQuery` | alarmhuba-backend Resolver と GPS モックのロググループの ARN（スタックパラメータから作る）と `log-group:/aws/lambda/*:*`（「ログを見る」の Lambda エラー。v2.1 第 2 段階） | 日次集計は既存 2 つだけを使う |
| `cloudtrail:LookupEvents` | `*` | 「ログを見る」のイベント履歴（90 日）。Resource を限定できないアクション（v2.1 第 2 段階） |
| `logs:GetQueryResults`, `logs:StopQuery` | `*` | Resource を限定できないアクション |
| `ec2:DescribeInstances` | `*` | Resource を限定できないアクション |
| `lightsail:GetInstances`, `lightsail:GetInstanceMetricData` | `*` | 毎日列挙した全台を読むため限定しない。読み取りだけで、ポートやインスタンスを変える権限は付けない |
| `iam:GenerateCredentialReport`, `iam:GetCredentialReport`, `iam:ListRoles` | `*` | レポート生成と読取、ロール列挙。権限変更はしない |
| `iam:GetRole` | `arn:aws:iam::210000000001:role/*` | RoleLastUsed を読む |
| `bedrock:InvokeModel` | `arn:aws:bedrock:ap-northeast-1:210000000001:inference-profile/*.anthropic.claude-*`、`arn:aws:bedrock:*::foundation-model/anthropic.claude-*` | Converse の推論とプロファイルの経路先モデル |
| `bedrock:ListInferenceProfiles` | `*` | 系統内最新の自動解決 |
| `dynamodb:GetItem, Query, BatchGetItem, BatchWriteItem, PutItem, UpdateItem` | 自テーブル | ACTOR、AI_STATE、PREVIEW、LOGS（GetItem / UpdateItem）を追加 |
| `sqs:SendMessage` | notify キュー ARN、自スタックの失敗キュー（`OnFailure`） | |
| `logs:CreateLogStream, PutLogEvents` | 自関数のロググループ | |

`guardduty-alert` ロール: `dynamodb:GetItem, PutItem, DeleteItem`（自テーブル）、`sqs:SendMessage`（notify キュー、失敗キュー）、自関数のログだけ。GuardDuty の権限は持たない（イベントに検出が入っている）。

`api` ロール:

| アクション | Resource | 備考 |
|---|---|---|
| `dynamodb:GetItem, Query, PutItem, UpdateItem, DeleteItem, TransactWriteItems` | 自テーブル | 設定の seed・更新と AUDIT、レポート一覧 / 詳細、PREVIEW、LOGS（受付の PutItem、取得の GetItem、同時実行数の Query、失敗の UpdateItem） |
| `lambda:InvokeFunction` | 集計 Lambda の ARN のみ | プレビュー・今すぐ実行・ログの問い合わせを InvocationType=Event で起動 |
| `ssm:GetParameter(s)` | `parameter/example-backoffice/{env}/edge/origin-verify-secret`、`parameter/example-backoffice/{env}/shared/slack/mode`、`parameter/example-backoffice/{env}/shared/slack/bot-token` | bot-token は `GET /slack/directory` の候補検索だけに使う。読み取りだけで投稿はしない（saas-a-billing と同じ） |
| `kms:Decrypt` | AWS 管理キー `alias/aws/ssm`（雛形と同じ。`kms:ViaService` を SSM に限る） | SSM SecureString の復号に必要 |
| `logs:CreateLogStream, PutLogEvents` | 自関数のロググループ | |

雛形の api ロールにある添付バケットへの書き込みと notify キューへの投函は外す（api は送らない）。Scheduler ロール（SAM `ScheduleV2` が生成）は `lambda:InvokeFunction` を `report` に、`sqs:SendMessage` を Scheduler DLQ に限定する。

- 書き込めるのは自テーブル、自関数の CloudWatch Logs、notify キューと自スタックの失敗キューだけ。S3（添付バケットを含む）への書き込み権限は付けない。`Resource: "*"` は既存 6 アクション（GetMetricData、GetQueryResults、StopQuery、DescribeInstances、GetInstances、GetInstanceMetricData）に v2.2 の ListMetrics と GenerateCredentialReport / GetCredentialReport / ListRoles / ListInferenceProfiles、LookupEvents（v2.1 第 2 段階）。foundation-model の ARN のリージョン `*` は cross-region 推論の経路先のため

### 12.3 秘密情報

- アプリ固有の秘密は持たない。Incoming Webhook URL も持たない
- Slack の Bot token は投稿には core の notifier が使い（プラットフォーム設計 8.5）、アプリは `GET /slack/directory` の候補検索でだけ読む。`Authorization` ヘッダにしか渡さず、応答・ログ・例外メッセージに載せない（saas-a-billing 12.3 と同じ）
- ルートロガーを DEBUG にしない（プラットフォーム設計 12.4）

### 12.4 データ保護

- レポートと日次集計には IP アドレス、IAM のユーザー名・ロール名、操作名が入る。送り先は社内の Slack チャンネル・DM と `@example.co.jp` のメールに限り（メールは notifier が送信直前に絞る）、dev からは本番チャンネルへ送らない（3.6）
- レポート一覧は段階・件数・AI の見解と通知情報だけを返す。詳細は認証済み全ユーザーに保存済みの明細を返す。設定・試行は admin のみ（8 章）
- CloudTrail のレコードそのものはテーブルにもログにも残さない。残すのは件数と、所見の候補の短い明細（7.2 の件数上限つき）だけ
- DynamoDB は SSE、prod は PITR。日次集計は 400 日、即時通知の記録は 90 日で TTL により消える

- Bedrock に渡すのは集計済み要約と 2.11 の文脈。IP・主体名・除外理由等の社内情報を含むが、認証トークン・秘密・リクエスト本文は含めない。system prompt と除外理由も管理者が秘密を入れない運用とし、送信前に秘密を除く
- Bedrock はプロンプト・応答をモデル学習に使わない。jp プロファイルは国内完結、Opus / Haiku は jp を利用できる。Sonnet は global のみ。自動解決は jp → apac → global の優先で、海外経路が選ばれる可能性を設定画面で示す
- jp プロファイルの Opus 5.5 は、セキュリティ判定の入力（集計要約）を断続的に `stopReason = content_filtered`（出力 0 トークン）で返す。2026-10-16 の dev 実測で同じ入力が jp は 5 回中 3 回 filtered、global は 5 回とも正常、無害な入力（天気）は jp でも通った。国内経路のフィルタに掛かりやすいとみる。国内完結を優先して jp を先に使い、同じプロファイルで 1 回やり直しても filtered のときだけ、同じ系統・同じ版の global へ 1 回だけ切り替えて判定を落とさない（2.11）。切り替えた日はレポートの注記（`ai.note`）に出る。国外経路を一切使わない場合は `ai_model_pin` に jp の ID を指定する（そのときは切り替えず refused）
- Fable は東京から global 経由のみ。利用にはデータ保持の `aws_review` オプトインが必要で、通常の学習不使用と区別する。サイバーセキュリティの分類器による拒否率が高く、refusal は規則判定へフォールバックする。モデルカードと利用条件は付録 D。既定は国内経路の Opus
- IAM 認証情報 CSV はメモリのみで破棄し、棚卸しにはキーのスロットと最終使用日だけを残す。PREVIEW は 1 日 TTL、ACTOR と AI_STATE は TTL なし（7.1）

## 13. テスト方針

| 層 | 対象 | ツール | 内容 |
|---|---|---|---|
| unit | `guardduty_summary.py` | pytest | 重大度の境界（3.9 / 4.0、6.9 / 7.0、8.9 / 9.0）、新規 / 継続、アーカイブ・サンプルの除外、上位 `top_n` の並び、即時通知済みの印 |
| unit | `cloudtrail_summary.py` | pytest + gz フィクスチャ | JST の境界（00:00:00 を含む、翌日 00:00:00 を含まない）、UTC 2 フォルダの読み分け、2.3 の各事象、`0.0.0.0/0` の検出（SG と Lightsail のポート。CIDR の指定なしを含む）、AMI / スナップショットの公開、構成変更の件数、AccessDenied の主体別、2.4 の人 / 自動の判定（規則 1〜5 を 1 件ずつ） |
| unit | `metrics_summary.py` | pytest | クエリの組み立て（リージョン分け、`SEARCH` 式）、24 点の合計、データ点なしの 0 と「指標なし」の区別 |
| unit | `instances_summary.py` | pytest + botocore Stubber | EC2 の 24 点の平均・最大・合計、Lightsail の応答の整形（`unit` の指定）、同名の EC2 をインスタンス ID で区別、新しく現れた台・消えた台と構成変更の突き合わせ、倍率による warning（基準日数不足は判定しない）、`instance` の除外で warning にならず表示はされる |
| unit | `insights_queries.py` | pytest | クエリ文の固定（スナップショット）、結果の整形、10,000 行の上限到達、request_id の 200 件切り、Q3 が 0 件なら Q4 を走らせない |
| unit | `baseline.py` | pytest | 3 倍と最小件数の境界、基準 0、基準日数不足（「基準なし」）、初見の国・IP、未集計の日を基準から外す |
| unit | `exclusions.py` / `ip_country.py` | pytest | IP / CIDR / 主体 / イベント / インスタンスの照合、`scope`、期限切れ、root は除外できない、社内 / AWS / 国コード / 不明の分類、IPv6 |
| unit | `report.py` | pytest | 3 段階、warning 以上の区分のみ、所見と「ほか n 件」、OK の総括 1 行 + 常時 AI 見解、失敗の理由と詳細リンク、50 ブロック・3,000 文字・150 文字の上限、メンションの 3 モード × 最終段階 3 値（9 通り）、即時通知は 3 モードとも先頭ブロックと `text` に入る、全集計失敗は warning / always のみ付く（critical は付かない）、空メンションとメールには付かない、2,900 字予算への反映、`【検証環境】`、再送の表記、メール本文が Block Kit と同じ集計結果から作られること（総括・該当区分・所見・見解の一致と絵文字の文字置換）、件名の段階先頭（Critical / Warning / OK、未集計は Warning）・集計失敗と即時通知の件名、dev の件名と本文の `【検証環境】`、メールにメンションが入らないこと |
| unit | `settings.py` / `distribution.py` | pytest | 既定値と読み出し時の補完、`PUT` の検証（トグルと宛先の整合、`slack_channel_id` の形式（`C` / `G` / `D`）、`@example.co.jp` 以外の拒否、`exclusions` の各要素と重複・上限、`sections` の補完（無いキーは有効）と 6 キーの検証・すべて無効の拒否、slack_mention_level の 3 値、プリセットのキー過不足・UUID 形式 / 重複・名前の空 / 41 字 / 重複・本文の空 / 8,001 字・本文合計 20,000 字（trim 後の code point）の境界・日時不正 / 時差なし / 日付だけ・21 件、ai_prompt の既定 / 空白だけの正規化、しきい値の範囲、正規表現のコンパイル）、トグル × 宛先の組み合わせで決まる `channels` |
| integration | `handlers/report.py` + `repo.py` | moto（S3 / DynamoDB / SQS）+ botocore Stubber（GuardDuty / CloudWatch / Logs / EC2 / Lightsail） | 一気通貫、一部の集計の失敗、11.2 の条件付き取得（二重起動・stale・`force`）、`auto_run_enabled` 偽（scheduled は空振り、manual は動く）、配布先なし（送らずに `notify_note`）、メールだけ / Slack だけ / 両方の投函内容 |
| integration | 区分の OFF（`sections`） | moto + botocore Stubber | OFF の区分の API を呼ばない（Stubber に応答を積まない）、通知にその区分を出さず、詳細には「停止中（設定）」、`sources.{区分}.status = disabled`、OFF の日を 7 日平均の基準に使わない、OFF と残り区分の ok だけなら総括が「異常なし」、`sections.guardduty` が OFF でも即時通知は `alert_enabled` に従う |
| integration | `guardduty_alert.py` | moto | 重複防止、送信失敗時の項目の削除、`alert_enabled` 偽、トグルに従うメール / Slack の投函 |
| integration | `daily_security_report_api/app.py` | moto + Powertools のイベント生成 | ルートごとの権限（admin / 一般）、`GET /settings` の seed（`version = 0`）と `slack_mode`、`PUT /settings` の楽観ロック（409）・検証（400）・`AUDIT` の同時書き込み、設定全体の 180KB（180,000 バイト）の保険（保存メタデータ込み・UTF-8 の境界・再保存）と DynamoDB の Item size / Transaction size 超過が 400 `SETTINGS_TOO_LARGE` となり設定も監査も変わらないこと、ほかの ClientError の維持、`GET /slack/directory`（saas-a-billing と同じエラーコードとキャッシュ）、`GET /days` の並び・件数・`summary` を返さないこと |
| infra | template.yaml / samconfig / edge | pytest（`tests/infra/`） | タグ（`Rule` を `test_template_tags.py` の許容値に足す）、IAM の Resource の形（`cloudtrail-example` への書き込みが無いこと、GuardDuty が検出器 3 本の `/findings` に限られること、`*` が 12.2 の列挙だけ、IAM レポート・Bedrock の ARN、API から Invoke できるのが集計 Lambda だけ、timeout=900、PORTAL_URL にアプリパス、REPORT_FUNCTION_NAME の参照、EC2 / Lightsail は読み取りのアクションだけ、bot-token を読むのが api だけ）、`test_samconfig.py` への登録、Scheduler の `Name`、edge の behavior に `FunctionAssociations` があること |
| frontend | `frontend/src/apps/daily-security-report/` | Vitest、`npm run build`、Playwright | `registry.test.ts`（マニフェスト）、設定画面（保存・破棄・未保存離脱の確認・admin 以外の設定へのアクセス拒否・保存失敗の理由・除外リストの行の追加と削除・候補に無い保存値のチップ）、一覧の段階・見解 120 字・v1 注記、全員の詳細閲覧、設定の AI カード・試行中 / 失敗 / 結果と保存、未保存の結果表示。既定本文の表示と開いた直後に dirty にならないこと、既定 / カスタムの送信値、固定部分、カード操作が確認前に配列を変えないこと、UUID v4、メンション段階、非 admin の操作無効、本文合計 20,000 字の検証。所見の追跡（v2.1）: `trace.test.ts`（純粋関数）、`CounterTable.test.ts`、`EvidenceTable.test.ts`（コンソールのリンクだけが `<a>` になること、`ref` を出さないこと）、詳細の時刻・場所・AI の根拠チップ・集計値の表・上位表の割り当てと「ほか n 件」・「根拠: …」・区分の HelpTip・旧記録と v1 の互換（フィクスチャは `makeTraceDetail()` と `makeDetail()` を使い分ける）。ログを見る（v2.1 第 2 段階）: `logs.test.ts`（純粋関数）、`useLogQuery.test.ts`（フェイクタイマー。202 → 2 秒ごとの GET → succeeded で停止、200 のキャッシュは GET しない、90 秒で `TIMEOUT`、429 と `TIMEOUT` の文言、閉じたら停止して遅れた応答を捨てる）、`LogDialog.test.ts`（kind ごとの列、guardduty の縦の表、`truncated`、コンソールのリンクの制限、running と失敗、`v-html` を使わないこと）、`EvidenceTable` / `CounterTable`（`logs` のときだけ `ref` のある行・指標の行にボタン）、`DayDetailView.test.ts`（admin 以外に出ない、`ref` の無い行・壊れた `ref`・旧記録に出ない、押すとダイアログが 1 つ開き `startLogs` が `ref` と同じ本文で呼ばれる、閉じるとポーリングが止まる）、`api.test.ts`。今すぐ実行のカード（admin / 非 admin、当日の注記、範囲外の日を押せない）、確認ダイアログを経ずに API を呼ばないこと、202 後の 5 秒ポーリング（受付前の記録と 404 を待つ、15 分で停止）、409 の表示、未保存の注意。E2E（ログイン → 一覧 → 詳細 → admin 設定 → 過去日試行 → 保存） |
| unit | `levels.py` | pytest | 2.10 の全規則の振り分け、区分 / 全体の最大、GuardDuty Low は ok、SOURCE_FAILED は warning 固定、AI 上下・critical 降格禁止、規則所見件数と最終段階の区別 |
| unit | `model_resolver.py` | Stubber | ACTIVE / SYSTEM_DEFINED、全ページ、系統、整数版比較（5.9 / 5.10）、jp→apac→global、日付と v の同点規則、preview / mythos の除外、pin、候補無し、表示名 |
| unit | `ai.py` / `ai_prompt.py` | Stubber | toolChoice auto とツール呼び出しの指示（前置きにも）、思考ブロックの読み飛ばし、本文の JSON の検証、max_tokens は MAX_TOKENS、content_filtered の再試行と global 切替（ピン時は切り替えない）、maxTokens=8000・temperature 無指定、文脈と生ログ無し、本文と固定末尾の結合・既定全文とハッシュの完全互換・空白とカスタム本文、プロンプト上書き / ハッシュ、成功・例外・timeout・refusal・ツール無し・重複・不正スキーマ、上限字数、150 秒、指定 3 エラーのみ旧 ID で 1 回、切替メッセージと AI_STATE |
| unit | `actors.py` / `iam_snapshot.py` | pytest + Stubber | IAM ユーザー / ロール / root / 外部 account の正規化、AWSService 除外、最終日最大・JST、当日更新 / 不明 / 初日、D−30 と前日境界、NEW と IAM 前回不明の区別、社内でも検出、国外 critical、週次 / null / 未使用 90 日境界 / MFA、再実行の後退・加算防止 |
| unit | `ip_country.py` / `report.py` | pytest | RFC 1918 全範囲・ループバック・リンクローカル・ULA・IPv4-mapped、vpce（公開 IP / 非 IP でも優先）、社内 VPC と初見除外、2,900 字・各所見 5 行・400 字見解・基準なし末尾 1 回、棚卸しとリンク重複無し |
| unit | 追跡情報（v2.1） | pytest + Stubber | Q1〜Q5 の最初・最後の時刻と JST 変換・読めない値の省略、正規化でまとめた行の最初・最後、明細・上位表・所見の `where / ref`。GuardDuty の検出ごとの所見（High / Medium、同じ題名で別 ID は別行）・100 行の上限と区分件数・上位表の代表検出・明細 50 行・`resource` / `remote_ip`。CloudTrail の `event_id / region / link`、集約行と所見の最初のイベント、ファイルをまたぐ合算、ID・リージョンなし、AccessDenied / ログイン失敗の主体ごとの期間。指標の時間帯（降順の Timestamps・文字列の時刻・対象日外の除外）と Lambda 上位 20 行。baseline の倍率・初見・ACCESS_DENIED / LOGIN_FAILURE・台の所見の `where / ref` と既存の追跡情報の維持、`from_result` が既知の項目だけを写すこと |
| unit | `glossary.py` | pytest | 固定キーの表示名・説明、前方一致（CloudFront の label / name / id、AccessDenied の主体、インスタンス）、未知のキー、`where_for`（ロググループ・指標・GPS の ALB・環境変数なし / 不正）、`metric_ref` の target |
| unit | AI の根拠キー | pytest + jsonschema | `keys` のスキーマ（省略可・12 個・80 字）、超過の切り詰め、文字列の配列以外は SCHEMA、固定末尾の 2 文と既定本文の不変、ユーザーメッセージの `glossary`（counters のキーだけ）、`link` / `ref` を渡さないこと |
| integration | API・preview・repo | moto + Stubber | 一覧トップレベルだけ、詳細に request_history 無し、404、admin、設定各範囲・ピン、202→ポーリング・成功 / 失敗 / Invoke 失敗 / 5 分失効、日次 20 回と同時受付の排他、v1 読み替え、試行で設定 / DAILY / ACTOR / AI_STATE / 通知が変わらない、今すぐ実行の 202（Invoke の関数名・Event・ペイロード）/ 400（形式・未来日・400 日超・本文）/ 409（running かつ 20 分以内のみ）/ 403 / 502、AUDIT と DAILY を書かないこと、`requested_by` が request_history に残ること。`GET /days/{date}` の `glossary`（counters と comparisons、summary 無しは空）、保存時の時間帯の上限（60 個 × 24 と `hourly_truncated`）と lambda_errors 20 行 |
| integration | `handlers/report.py` の手動実行（v2.1） | moto + Stubber | `notify = false` で `notify.send` を呼ばず（集計失敗の知らせも送らない）、`notify_note = 送信なし（検証）`・`notify_channels = []`・`sent_count = 0`・`request_history` の `notify: false`、BOOL 以外や scheduled の `notify` は取得前に ValueError。`sources.metrics.hourly` と `sources.guardduty.details` の保存、休眠等の所見の `where = CloudTrail` |
| unit | `log_queries.py`（v2.1 第 2 段階） | pytest + MagicMock | kind ごとの検証（必須項目・余分な項目・IP / リージョンの 3 値 / 関数名 / パスの文字種 / 32 桁の ID / target の形式・`from` > `to`・CloudWatch で引けない指標キーの拒否）、パスのメタ文字のエスケープ、query_id の安定性、Lambda エラーのクエリ文・JST・伏せ字と 300 字・201 行目での切り詰め、Insights の 60 秒打ち切りと stop_query・Failed / ClientError のコード・予算の上限、alarmhuba の IP と正規化パスの絞り込み・request_id による状態コードの突き合わせ・ID の 10,000 字切り・状態クエリ失敗時の note、GPS の IP 絞り込み、CloudTrail の属性の優先（event_id > event > actor。ARN は末尾の名前、root）・from / to・50 件ページングと 200 行・壊れた JSON、GuardDuty の 1 行と相手国・未知のリージョン・見つからない検出、指標の Period 60（Lambda 合計の SEARCH、WAF の SEARCH、API Gateway / ALB / CloudFront の MetricStat と対象の絞り込み、gps.rejected の ALB、対象なし、PartialData のページング、0 の除外と 200 行、Messages / 未完了の失敗）、EC2 / Lightsail の 10 分値、不正な target で AWS を呼ばないこと |
| integration | 「ログを見る」の API と LOGS（v2.1 第 2 段階） | moto + Mock | 202（Invoke の関数名・Event・ペイロード、LOGS の running、INFO ログ、応答に requested_by が無いこと）、成功結果のキャッシュ 200、実行中の再要求 202（Invoke しない）、failed 後の再受付、400（未来日・90 日超・形式・本文・kind・平らな形と入れ子の混在・不正な ip / path / function / region / id・不明な項目・集計に無い指標キー）、入れ子の本文が同じ query_id になること、90 日境界、403、404（無い・16 桁でない）、429（ユーザーごと 3 件。別ユーザーは数えない）、502（Invoke 失敗で failed / LOGS_UNAVAILABLE）。repo の put / update（無い項目は False、ほかの ClientError は再送出）/ get / 10 分の失効 / 1 日 TTL / 同時実行数のページング。report の trigger=logs（running → succeeded / failed、例外、running でなければ空振り、query_id 不一致や不正は ValueError、DAILY・通知を触らない、予算 150 秒） |
| infra | template.yaml（v2.1 第 2 段階） | pytest | `logs:StartQuery` の Resource が既存 2 つ + `/aws/lambda/*`、`cloudtrail:LookupEvents`（`*`）が report ロールだけ、TTL 属性 `ttl`、api / report ロールの DynamoDB アクション |

- TDD: 純粋関数（`baseline` / `report` / `exclusions` / `ip_country` と各 summary の判定部）のテストを先に書き、失敗を確認してから実装する
- カバレッジ 80% 以上（`pytest --cov=daily_security_report`）
- 検証実績（2026-10-15）: 単体・結合 309 件以上、カバレッジ 97%、infra 320 件、Vitest 86 件、E2E 1 本が dev で通過した
- 検証実績（2026-10-16、v2.1 第 1 段階のバックエンド）: `pytest apps/daily-security-report tests/infra` 1,183 件合格、daily_security_report のカバレッジ 97.5%（行・分岐）
- 検証実績（2026-10-16、v2.1 第 2 段階のバックエンド）: `pytest apps/daily-security-report tests/infra` 1,293 件合格、カバレッジ 97.6%（行・分岐、log_queries.py は 100%）、ruff check / format 合格
- フィクスチャは実ログを縮小して作り、IP とユーザー名は架空の値に置き換える

#### S3 の検証と制限（v2.2）

| 対象 | 検証する内容 |
|---|---|
| `s3_summary.py` | ListMetrics のページング、StorageType 合算、GetMetricData の 500 クエリ分割、JST / UTC 境界、All / Entire 優先の FilterId 選択、measured_at による同一点の比較抑止・古い前日行の採用、UTC 日の追跡時刻、基準不足、2.13 の境界値と重複抑止、指標が止まったバケットの GONE / STALE（しきい値・排他・前日行無し・STALE から GONE への推移）、500 バケット / 上位 50 行、failed・SOURCE_FAILED・「未集計」通知 |
| 設定・IAM・AI・通知 | `sections.s3` の既定 true と後方互換、ListMetrics と既存 GetMetricData、S3 権限の追加無し、S3 の規則 / 最終段階と根拠キー、固定末尾のハッシュ、Slack / メールの S3 区分 |
| フロント | S3 fixture、CounterTable とバケット表の描画、1,024 基数の整形、要求値 null の「—」、既存一覧・設定・段階表示、S3 にログボタンが無いこと |

日次ストレージ指標は 1 日 1 点で反映が遅れ、要求メトリクスは有効化したバケットだけ。S3 Protection の代替として翌朝のレポートで検知する。要求基準は保存済み `top.s3_buckets` に依存し、過去の上位 50 行に無いバケットや日数不足のバケットは「基準なし」となる。空にされたか削除されたバケットは指標が止まった時点で `S3_METRICS_STALE` / `S3_BUCKET_GONE` として検知する。前日値と GONE / STALE は直近 3 日以内の最新成功日の `sources.s3.latest`（容量上位 500 バケット）で判定するので表の上位 50 行に無いバケットも対象だが、`latest` の無い v2.2 初版の日を参照するときだけは表の行で判定する。GONE は通常、最後の点の 4 日後になり、その日や前日の集計が失敗しても直近 3 日以内に成功行があれば次の成功日に検知する。設定から外したリージョンのバケットは GONE の対象外とする。

## 14. 運用

### 14.1 初回導入手順

v1 の 1〜11 は実施済み（2026-10-15、prod 配布済み。1・2 はアカウント共通）。v2 は 13・14 を追加し dev → prod の順で検証・配布する。

新フロントは旧バックエンドに `slack_mention_level` / `ai_prompt_presets` を送ると 400 になるため、バックエンド → フロントの順に配布する。v2.1（#1021）の第 1 段階は新フロントが新項目を読むだけ（旧バックエンドでも — 表示で動く）だが同じ順を標準とし、第 2 段階は「ログを見る」が新 API（`POST /days/{date}/logs`）を呼び、集計ロールに `logs:StartQuery`（`/aws/lambda/*`）と `cloudtrail:LookupEvents` が要るためバックエンド → フロントの順が必須（`sam deploy` → `scripts/deploy-frontend.sh`）。

| # | 作業 | 内容 |
|---|---|---|
| 1 | GuardDuty の有効化 | 実施済み（2026-10-15。3 リージョンの検出器 ID と有効な機能は 4.1）。30 日の無料期間は 2026-11-14 まで。S3 Protection は 2026-10-19 停止済み（#1022）。Lambda Protection は実測で要否を判断する（14.4） |
| 2 | CloudTrail 証跡の見直し | 実施済み（2026-10-15）。証跡「CloudTrail」をマルチリージョン化し、グローバルサービスイベントを ON にした（`aws cloudtrail update-trail --name CloudTrail --is-multi-region-trail --include-global-service-events`）。管理イベント All。以後は他リージョンと IAM・CloudFront・WAF の操作も記録され、レポートの IAM 変更欄に出る |
| 3 | Slack の準備 | 当面の dev / prod の宛先は ExampleBot と開発者本人の DM（`D0000DEVA1`）。候補には出ないので 6 の API で入れる。本番チャンネルに移すときは Bot を招待し、設定画面で `#` から選ぶ |
| 4 | 読み取り先の確認 | 証跡の読み取り条件（4.2）、GPS モックのロググループ（6.2）を確認済み。backend.example-cloud.com の Web ACL は `WebACL-00000001` と特定し、`metric_targets` の初期値は 4.3、管理 API の正規表現は実ログのパスから決めた（3.3） |
| 5 | デプロイ（dev） | `cd apps/daily-security-report && sam build --use-container && sam deploy`（core が `/ops/topic-arn` を書いた後であること）。`/apps/daily-security-report/api-domain` が書かれたら、edge に origin / behavior を足して `scripts/deploy-edge.sh dev`、続けて `scripts/deploy-frontend.sh dev`。`/api/daily-security-report/health` が 200 になり、サイドバーに「AWS日次セキュリティレポート」が出ることを確かめる |
| 6 | dev の初期設定 | `GET /settings` で既定値を seed し（`version = 0`）、`PUT /settings` で開発者本人の DM（`slack_channel_id = D0000DEVA1`）、メール宛先（`devA@example.co.jp`）、メール / Slack の有効、除外リストの初期値（3.4）、`metric_targets`、`alarmhuba_admin_path_regex` を登録した。自動実行は無効のまま 7 を手動で行い、即時通知は確認の間だけ有効にした |
| 7 | dev で手動実行 | 14.3 のコマンドで前日分を実行し、DM とメールの v2 表示（3 段階・常時 AI 見解・詳細リンク）、レポート一覧、所要時間を確かめる |
| 8 | 即時通知の確認（dev） | `aws guardduty create-sample-findings` で東京の High のサンプルを作り、DM とメールに 1 回だけ届くことを確かめる。サンプルはレポートに数えない（2.2） |
| 9 | dev を止める | 設定画面で自動実行・即時通知を無効に戻す（3.7） |
| 10 | デプロイ（prod） | `sam deploy --config-env production` → `scripts/deploy-edge.sh prod` → `scripts/deploy-frontend.sh prod`。v1 実施済み（2026-10-15）。v2 は #1020 で実施状況を管理 |
| 11 | prod の初期設定 | API で開発者本人の DM を登録し、設定画面でメンションとメール宛先（当面 `devA@example.co.jp`。3.6）、除外リスト、`metric_targets`、`alarmhuba_admin_path_regex` を登録し、自動実行・即時通知を有効にする。本番チャンネルは設定画面で後から選ぶ。Ops トピックの購読が Confirmed であることを確かめる。v1 実施済み（2026-10-15）。v2 は #1020 で実施状況を管理 |
| 12 | 調整 | 3〜4 日流して、うるさい項目を削り、しきい値と除外を決める（14.4） |
| 13 | ACTOR の初期化 | v2 の初回実行で IAM レポートと ListRoles / GetRole を読み ACTOR を初期化する。開始時 Query PK=ACTOR、Limit=1 が空なら初回と判定し actors.status=initializing、休眠の通知は抑止して「前回不明」を記録。以後は更新前状態で判定（2.12）。初日には判定できず、2 日目から効くことを運用者へ示す |
| 14 | プロンプトの調整 | 設定画面で系統と文面を編集 → 保存済みの過去日を選ぶ →「この文面で試す」→ 段階・理由・見解・モデル・usage を確認 → 保存。設定の AUDIT と日次のハッシュで変更を追う |

6 の DM の登録は AWS CLI ではなく API で行った。`GET /settings` で得た `version` と変更する項目（`slack_channel_id`、`slack_channel_label` など）を `PUT /settings` に渡すので、8 章の検証を通り、`AUDIT` にも同じトランザクションで残る。prod（11）も同じ手順で DM を入れる。

- 画面は候補に無い DM を `slack_channel_label` で見せる（空なら `#<ID>`。3.3）

### 14.2 監視

| 対象 | 手段 | しきい値 |
|---|---|---|
| Lambda の失敗 | CloudWatch Alarm（`report` / `guardduty-alert` / `api` の `Errors` の合算） | ≥ 1、期間 300 秒 |
| Scheduler DLQ | `ApproximateNumberOfMessagesVisible` | ≥ 1 |
| 失敗キュー（`report` / `guardduty-alert` の `OnFailure`） | `ApproximateNumberOfMessagesVisible` | ≥ 1 |
| EventBridge ルールの配信失敗 | `AWS/Events` `FailedInvocations`（`RuleName`） | ≥ 1 |
| アラームの通知先 | 上記すべての `AlarmActions` を core の Ops トピック（パラメータ `OpsTopicArn`）に繋ぐ | — |
| ログ | LambdaLogger（JSON）、365 日 | `date`、`attempt`、区分ごとの所要秒数と件数、Insights の走査バイト数 |

- ログの所要秒数と走査バイト数は、初回実測（dev、2026-10-14 分。全体 51 秒、Insights の走査 数十 GB。内訳は 6.7）と比べて見る
- 集計の失敗は WARNING に例外の型・メッセージ・トレースバックを残す（秘密は伏せる。`diagnostics.py`）。DAILY にはエラーコードだけを保存する

### 14.3 失敗時の再実行

1. アラームのメール、またはレポートの `context`（未集計の区分と原因）で状態を見る。ロググループ `/aws/lambda/example-backoffice-daily-security-report-report-{env}` で原因を確かめる
2. 原因別の対処: AccessDenied → 12.2 の Resource（ロググループ名、バケット接頭辞、検出器 ID）とスタックパラメータを見直して再デプロイ。GuardDuty の検出器が見つからない → 4.1 の検出器 ID と template.yaml の Mappings を見直す。Insights の時間切れ → 走査範囲とクエリを見直す
3. 前日分を出し直す。画面からは設定の「今すぐ実行」で対象日を選び、確認ダイアログで「実行する」を押す（`force = true` で集計し直して送る）。CLI で行うときは:

```bash
aws lambda invoke --function-name example-backoffice-daily-security-report-report-prod \
  --invocation-type Event --cli-binary-format raw-in-base64-out \
  --payload '{"trigger":"manual","date":"2026-10-14","force":false}' /dev/null
```

成功済みの日を出し直すときは `"force":true`（11.3）。失敗した日だけを取り直したいとき（`force: false`）は CLI を使う。放置しても自動では再実行しない。

### 14.4 定常運用

| 作業 | 手順 |
|---|---|
| しきい値の調整 | 設定画面で変える（admin。変更は `AUDIT` に残る）。翌日のレポートから効く |
| 除外の追加・削除 | 設定画面の除外リストの表で追加・削除する（理由を必ず入れ、一時的なものは期限も入れる）。期限切れの行は適用されないので、気づいたら消す |
| GuardDuty の検出を「正常」にする | GuardDuty の抑制ルールで自動アーカイブする（2.2）。アプリ側の変更は不要 |
| GuardDuty の Protection の判断 | S3 Protection は 2026-10-19 に停止（#1022）。追加候補のバケット の要求メトリクス追加（任意）は見送り |
| AI の版の自動追従と切替の確認 | レポートの切替行と詳細の model_id / prompt_hash を確認。未承認のフォールバックが続く場合は権限・モデル承認・12.4 の利用条件を確認し、必要なら前回成功 ID をピン留めする。新しい版は過去日で試してから運用する |
| 国判定の表の更新 | 月 1 回をめどに `ip-ranges.json` と RIR 委任統計から `functions/daily_security_report/data/` を作り直して再デプロイする |
| 費用の確認 | 月 1 回、Cost Explorer で GuardDuty と CloudWatch Logs（Insights の走査）を見る。dev のレポートを止め忘れていないか確かめる。Logs Insights の費用を下げたいときは設定画面で `insights` の区分を OFF にする（3.3） |
| 日次集計の参照 | レポート一覧（9 章）で段階・AI の見解・送信先を見る。詳細ボタンで全区分の証拠と AI 情報を見る |

## 15. 要確認事項

1. Fable を将来の既定にするか。現時点の既定は Opus。global 経路、aws_review のデータ保持条件、拒否率、月額を過去日の試行で評価してから判断する（12.4）
2. EXTERNAL_ACCOUNT の所見に出る他アカウントは、業務上の関係と管理者を確認してから扱いを決める。判定からの自動除外はせず、証拠を詳細で確認する

## 16. 他アプリとの差分

| 項目 | 既存の課金アプリ（saas-a-billing） | daily-security-report |
|---|---|---|
| 画面 | あり | admin 限定の設定・AI 試行・今すぐ実行と、全ユーザーのレポート一覧・詳細 |
| 読む先 | 自分のスタックのリソースと外部 API | アカウント全体（GuardDuty、CloudTrail の S3、CloudWatch、Logs Insights）と、EC2 / Lightsail のインスタンスの一覧と指標（EC2 は CloudWatch、Lightsail は `GetInstanceMetricData`）。読み取り専用 |
| 集計対象 | dev は検証、prod は本番（`TargetEnv`） | dev / prod とも本番アカウント。通知先だけ変える |
| バッチ | Scheduler → trigger Lambda → Step Functions | Scheduler → 集計 Lambda を直接（初回実測 51 秒、dev） |
| イベント駆動 | なし | EventBridge ルール（GuardDuty の検出）→ 即時通知 |
| 通知 | メール（Excel 添付）+ Slack テキスト。独立のトグル | メール（添付なし）+ Slack（Block Kit）。独立のトグルは同じ。即時通知もある |
| AI 判定 | なし | AI 判定（Bedrock）を持つ唯一のアプリ。Claude 固定、規則 critical を固定して合成し、見解を毎日通知・保存 |
| 秘密情報 | 外部 API の認証情報（SSM SecureString） | アプリ固有の秘密なし（Slack Bot token を候補検索で読むのは同じ） |
| S3 | core の添付バケットに書く | 書かない |

プラットフォーム設計書への反映（実施済み）: 1.3 のアプリ一覧、4.2 の `/apps/{app}/api-domain` の書き手、14.2 の `Name` タグ値 `Rule`（2026-10-15 確定。あわせて `tests/infra/test_template_tags.py` の許容値に足す）。`README.md` のアプリ一覧にも追加済み。

## 付録 A. 調査の要約と実測（設計の根拠）

開発者本人がアカウントの状態を読み取りで調べた。入口ごとに防御とログの取得状況にばらつきがあり、アカウント全体を継続して見る仕組みが無いと分かったことが、本アプリの出発点になった。調査の明細は社内資料に残し、本書には設計に効いた点だけを本文（2.3〜2.6・3.4・4 章・6.2）として反映した。

v1 の初回レポート（2026-10-16）は強調項目が数十件になった。大半は SSM の VPC エンドポイント経由の操作を「国不明 = 日本以外」として強調したもので、v2 ではプライベート IP と `vpcEndpointId` 付きの記録を先に社内（VPC 内）扱いにした（2.5）。残りは正規の相手を除外リストで扱い、AI が既知の相手と判断すれば理由付きで下げる（2.10・2.11）。

### 2026-10-16 の dev 実機での Bedrock の実測

dev の 10/15 分の手動実行と、同じ入力（集計要約 502 トークン）を CLI の Converse で送った検証の結果。2.11・12.4 の根拠。

1. `toolChoice` の `tool` / `any` は Opus 5.5 で `ValidationException: tool_choice: type "tool" and "any" are not supported for this model` になる。`auto` なら system の指示（「回答は必ず submit_verdict ツールの呼び出しで返し、本文テキストは書かない」）でツールを呼ぶ
2. 応答の content は `[{reasoningContent: {...}}, {toolUse: {...}}]` のように思考ブロックが先頭に付く（`reasoningText.text` が空で `signature` だけのこともある）。`maxTokens` 300 では思考で使い切って `stopReason = max_tokens` になり、`toolUse.input` が途中で切れた（`reason` 欠落）。同じ入力で出力は約 310 トークン
3. `jp.anthropic.claude-opus-5-5` は同じ入力で断続的に `stopReason = content_filtered`（出力 0 トークン）を返す（5 回中 3 回）。`global.anthropic.claude-opus-5-5` は 5 回とも正常。天気のような無害な入力では jp でも通る。セキュリティ判定の文面が国内経路のフィルタに掛かりやすい
4. Fable 5.1（`global.anthropic.claude-fable-5-1`）は `data retention mode 'default' is not available for this model`（ValidationException）で呼べない。`aws_review` のオプトインが要る（12.4）

あわせて、dev の 10/15 分の通知（Slack DM）で所見行が `198.51.100.45（JP）/api/admin/{id}/settings 1 件 1 件（ADMIN_EXTERNAL）` のように件数が二重に出て code が括弧で付き、先頭行が `Critical 0 件 / Warning 10 件` だった。所見の text から件数を外し、通知の体裁を 6.4 のとおりに直した

### 2026-10-16 の本番 10/15 分の Warning が追えなかった事実（v2.1 の根拠）

本番 10/15 分のレポートで、入口の指標（metrics）は規則では ok、AI が「比較基準なし。Lambda エラー（数百件）と GPS の受信なし・拒否（数十件）を要確認」の趣旨で warning に上げた。担当者が「どのログの何時何分か」まで追えなかった原因は次のとおり。

- AI が上げた根拠が理由文にしか無く、集計値の表のどの行かが分からなかった（`ai.sections` に根拠のキーが無い）
- 接続元（alarmhuba の社外管理 API・拒否）は IP × パス × 件数だけで時刻が無かった。GuardDuty は題名と件数だけで検出 ID も時刻も無かった。入口の指標は日合計だけで時間帯が無かった
- Lambda エラーは上位 3 関数しか保存しておらず、数十指標分の基準との比較や WAF の内訳は保存されていたが画面に出ていなかった
- 「GPS」の説明が無かった。GPS は 10/6 に構築した GPS モックサーバー（GPS 転送ユニット GpsUnitA の受信口）で、受信は Receiver のログ件数、拒否はその ALB の 4xx

v2.1 第 1 段階で、所見・明細・上位表に時刻・場所・参照・リンク、指標の時間帯、Lambda 上位 20 関数、用語表（`glossary`）、AI の根拠キー（`keys`）を保存するようにした（2.11・6.2・6.3・7.2・8 章）。ログそのものを画面から引く「ログを見る」は第 2 段階（同日。`POST /days/{date}/logs` と `LOGS` 項目、6.2d・8 章）で実装した。

## 付録 B. GuardDuty 費用試算（2026-10-15、単価は AWS Pricing MCP の東京公式値）

初版の数量幅を保持し、S3 Protection の停止決定だけ v2.2 へ更新した。2026-10-19 の 3.9 日の使用量 API 実測と現在の低 / 中 / 高の合計は COST-ESTIMATE 5.1・7 章を参照する。

| 項目 | 単価 | 弊社の量 | 月額 |
|---|---|---|---|
| CloudTrail 管理イベント解析 | $0.00000472/件 | 1 日数万件（月 数百万件） | 約 $10 |
| VPC フロー・DNS 解析 | $1.18/GB（500GB まで） | 未計測（EC2 数台、VPC 内 Lambda 数本） | $1〜24 |
| S3 Protection（停止済み） | $1.04/100 万イベント | 2026-10-19 に停止（#1022）。実測では GuardDuty の費用の大半を占めた（COST-ESTIMATE 5.1） | $0（残す場合は月 数十ドル） |
| Lambda Protection（任意） | $1.18/GB | 未計測 | 試用で実測 |
| RDS / EKS | — | 0 台 | $0 |

us-east-1 と us-west-2 の管理イベントは件数が小さく誤差。

## 付録 C. Logs Insights クエリの勘所

- example-powertools の LambdaLogger は `[INFO] {json}` 形式で JSON が自動展開されない。`parse @message /"sourceIp": "(?<src>[^"]+)"/` のように正規表現で抜く
- CloudFront 経由の実クライアント IP は X-Forwarded-For の先頭。`parse @message /"X-Forwarded-For": "(?<xff>[^",]+)/`
- 機器の投稿と管理 API はパスの接頭辞で分かれる。管理 API のパスは実ログで確かめてから正規表現（`alarmhuba_admin_path_regex`）に登録する
- リクエスト行とレスポンス行は別イベントで join できない。request_id を一度抜いて `filter rid in [...]` で引く
- 国は `"CloudFront-Viewer-Country": "(?<cc>[A-Z]{2})"`。ヘッダが無い記録は国を空として扱う

## 付録 D. 出典

本改訂では URL の到達確認をしていない（文書のみの作業でネットワークを使えないため）。実装時に確認する。

### 社内資料

- 単独リポ example-daily-security-report の設計書ドラフト（2026-10-15。本書の原本）、プラットフォーム設計書、ADD-APP-GUIDE、saas-a-billing の設計書
- 共通通知 `shared/layers/example_backoffice/notify.py`（`Slack.blocks`）と notifier `infra/core/src/notifier/slack_sender.py`（Bot 方式のときだけ `blocks` を送る、メンションは `text` の先頭）

### AWS 公式

- GuardDuty の EventBridge 連携: https://docs.aws.amazon.com/guardduty/latest/ug/guardduty_findings_eventbridge.html
- GuardDuty の重大度: https://docs.aws.amazon.com/guardduty/latest/ug/guardduty_findings-severity.html
- GuardDuty の抑制ルール: https://docs.aws.amazon.com/guardduty/latest/ug/findings_suppression-rule.html
- GuardDuty のサンプル検出: https://docs.aws.amazon.com/guardduty/latest/ug/sample_findings.html
- GuardDuty ListFindings / GetFindings: https://docs.aws.amazon.com/guardduty/latest/APIReference/API_ListFindings.html 、https://docs.aws.amazon.com/guardduty/latest/APIReference/API_GetFindings.html
- GuardDuty の料金: https://aws.amazon.com/guardduty/pricing/
- CloudTrail のログファイルの場所と名前: https://docs.aws.amazon.com/awscloudtrail/latest/userguide/cloudtrail-find-log-files.html
- CloudTrail のレコードの内容 / userIdentity: https://docs.aws.amazon.com/awscloudtrail/latest/userguide/cloudtrail-event-reference-record-contents.html 、https://docs.aws.amazon.com/awscloudtrail/latest/userguide/cloudtrail-event-reference-user-identity.html
- CloudTrail のコンソールサインインイベント: https://docs.aws.amazon.com/awscloudtrail/latest/userguide/cloudtrail-event-reference-aws-console-sign-in-events.html
- CloudTrail LookupEvents: https://docs.aws.amazon.com/awscloudtrail/latest/APIReference/API_LookupEvents.html
- CloudWatch GetMetricData / 検索式: https://docs.aws.amazon.com/AmazonCloudWatch/latest/APIReference/API_GetMetricData.html 、https://docs.aws.amazon.com/AmazonCloudWatch/latest/monitoring/using-search-expressions.html
- AWS WAF の指標とディメンション: https://docs.aws.amazon.com/waf/latest/developerguide/waf-metrics.html
- CloudWatch Logs Insights のクエリ構文 / StartQuery / クォータ: https://docs.aws.amazon.com/AmazonCloudWatch/latest/logs/CWL_QuerySyntax.html 、https://docs.aws.amazon.com/AmazonCloudWatchLogs/latest/APIReference/API_StartQuery.html 、https://docs.aws.amazon.com/AmazonCloudWatch/latest/logs/cloudwatch_limits_cwl.html
- CloudWatch の料金（Logs Insights の走査量課金、GetMetricData）: https://aws.amazon.com/cloudwatch/pricing/
- EventBridge のイベントパターン（数値の比較）: https://docs.aws.amazon.com/eventbridge/latest/userguide/eb-create-pattern-operators.html
- EventBridge Scheduler のスケジュール種別・タイムゾーン: https://docs.aws.amazon.com/scheduler/latest/UserGuide/schedule-types.html
- SAM ScheduleV2: https://docs.aws.amazon.com/serverless-application-model/latest/developerguide/sam-property-function-schedulev2.html
- AWS の IP アドレス範囲（ip-ranges.json）: https://docs.aws.amazon.com/vpc/latest/userguide/aws-ip-ranges.html
- サービス認可リファレンス（GuardDuty / CloudWatch / CloudWatch Logs）: https://docs.aws.amazon.com/service-authorization/latest/reference/list_amazonguardduty.html 、https://docs.aws.amazon.com/service-authorization/latest/reference/list_amazoncloudwatch.html 、https://docs.aws.amazon.com/service-authorization/latest/reference/list_amazoncloudwatchlogs.html

### Bedrock のモデルカード・料金（v2）

2026-10-16 のブリーフで提供された AWS 公式料金ページの表データ（2026-10-14 公開分）とモデル条件を採用する。新しい Claude は料金 MCP に未収載のため Web 取得値であり、本作業ではネットワーク不可のため再取得していない。単価は USD / 100 万トークン（入力 / 出力 / キャッシュ読取）で、計算は COST-ESTIMATE 5.5。

- [Amazon Bedrock 料金](https://aws.amazon.com/bedrock/pricing/): Fable 5.1 global 10.00 / 50.00 / 0.275、Opus 5.5 jp 4.40 / 22.00 / 0.22、Sonnet 5.5 global 2.20 / 11.00 / 0.11、Haiku 5.5 jp 0.11 / 0.55 / 0.011
- [Claude Fable 5.1 のモデルカード（Bedrock）](https://docs.aws.amazon.com/bedrock/latest/userguide/model-card-anthropic-claude-fable-5-1.html)（東京は global のみ、`aws_review` のオプトイン、サイバーセキュリティの分類器、2026-10-16 参照）
- [Bedrock モデルの詳細・モデルカード入口](https://docs.aws.amazon.com/bedrock/latest/userguide/models-supported.html) / [モデル利用の前提](https://docs.aws.amazon.com/bedrock/latest/userguide/model-access.html): 系統の提供経路と Fable の aws_review・拒否に関する条件（12.4）。個別のモデルカード URL は提供資料に無いため、デプロイ時に公式一覧から該当版を確認する
- [Converse](https://docs.aws.amazon.com/bedrock/latest/APIReference/API_runtime_Converse.html) / [ツール呼び出し](https://docs.aws.amazon.com/bedrock/latest/userguide/tool-use-inference-call.html) / [ListInferenceProfiles](https://docs.aws.amazon.com/bedrock/latest/APIReference/API_ListInferenceProfiles.html)
- [クロスリージョン推論](https://docs.aws.amazon.com/bedrock/latest/userguide/cross-region-inference.html) / [データ保護](https://docs.aws.amazon.com/bedrock/latest/userguide/data-protection.html): jp / global の経路、学習不使用の原則とモデル固有の保持条件を区別する
- [IAM 認証情報レポート](https://docs.aws.amazon.com/IAM/latest/UserGuide/id_credentials_getting-report.html) / [GetRole](https://docs.aws.amazon.com/IAM/latest/APIReference/API_GetRole.html): 最終使用と MFA の読み取り

### その他

- Slack Block Kit のブロックと上限: https://api.slack.com/reference/block-kit/blocks
- Slack chat.postMessage（`text` と `blocks`）: https://api.slack.com/methods/chat.postMessage
- APNIC の RIR 委任統計: https://ftp.apnic.net/stats/

## 改訂履歴

| バージョン | 日付 | 改訂内容 |
|---|---|---|
| v2.2 | 2026-10-19 | #1022。S3 Protection の 2026-10-19 停止、CloudWatch AWS/S3 による S3 区分、データ契約・所見コード・モジュール定数・画面・設定・IAM・制限を追加。レビュー指摘に合わせ failed・FilterId 選択・measured_at による前日比較・UTC 日・コンソールリンク・RATIO 最低値を明記。要求メトリクス既存 3 バケットと 追加候補のバケット の任意追加 $4.80/月、S3 取得実測 数十バケット・175 メトリクス/日を反映し、費用は Excel 再計算値（低 $38.63 / 中 $82.48 / 高 $217.75）へ更新。指標が止まったバケットの `S3_BUCKET_GONE`（critical）/ `S3_METRICS_STALE`（warning）と、前日値に使う全バケットの `sources.s3.latest` を追加。AI の固定末尾に基準なしの扱いを追加 |
