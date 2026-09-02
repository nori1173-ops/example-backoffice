# 月次請求バッチ（Lambda / Python 3.13 / SAM）技術調査レポート R3

調査日: 2026-08-27
対象: 外部SaaS REST API → 集計 → Excel生成 → S3保存 → SES添付送信 → Slack通知、毎月21日 06:00 JST（20日締め）、設定はDynamoDB、手動再実行あり

---

## 0. 結論サマリ（推奨構成）

| 項目 | 推奨 | 一言根拠 |
|---|---|---|
| Excel生成 | **XlsxWriter**（新規生成のみなら）。既存テンプレxlsxへの書き込みが要件に入るなら openpyxl | 両方 pure Python・約1.7〜1.9MB で Lambda 制限に無関係。Python 3.13 で実動作確認済み（下記実測）。XlsxWriter は書式API（通貨/日付/結合/条件付き書式）が素直で高速 |
| バンドル | `requirements.txt` を関数に同梱（Layer 化しない） | 依存が小さく単一関数。Layer はバージョン二重管理の手間だけ増える |
| SES | **SES API v2 `SendEmail` + `Content.Raw`**（`email.message.EmailMessage` で MIME 構築）。boto3 を固定できるなら `Simple + Attachments`（2025-04 新機能）も可 | v2 は 40MB 上限・単一API。Raw は SDK バージョン非依存で日本語件名/ファイル名を自分で制御できる |
| SES リージョン | **ap-northeast-1（東京）で完結** | ref-project-a が us-west-2 を使った理由は「既にサンドボックス解除済みのリージョンだったから」（社内 INFRA-DESIGN.md L286）。東京は 2020-07 提供開始済みで機能差なし。別リージョン運用は ref-project-a で実際にリージョン不一致の不具合を生んだ（E2E-TEST-REPORT.md） |
| サンドボックス | 東京で本番アクセス申請（24時間以内に初回応答）。宛先が検証済みドメインのみなら当面は不要 | 宛先を Web 画面で変更可能にする以上、未検証宛先で即失敗するリスクを避ける |
| 送信元ドメイン | `AWS::SES::EmailIdentity` で `backoffice.example-cloud.com` を追加、Easy DKIM CNAME×3 + カスタム MAIL FROM（MX/SPF）+ `_dmarc` TXT を Route 53 に IaC で登録 | ref-project-a の `stacks/ses/template.yaml` パターンを流用 |
| Slack | **Bot token（`chat.postMessage` + `files_upload_v2`）**。Incoming Webhook は不採用 | Webhook はチャンネル固定・ファイル不可。設定でチャンネルを変える要件と両立しない。`files.upload` は 2025-11-12 に廃止済み |
| Slack本文 | 「メール送付済み・請求書照合を」＋対象月＋合計金額＋テナント/地点数＋**ポータル画面URL**、xlsx も Slack に添付 | S3 署名付きURLは Lambda ロールの一時クレデンシャル失効で早期無効化されるため不向き |
| 秘密情報 | SSM SecureString、実行時に Powertools `get_parameter(decrypt=True)` で取得。環境変数に置かない | `{{resolve:ssm-secure}}` は Lambda 環境変数に使えない。メモリ内のみで扱う |
| スケジュール | **毎日 06:00 JST に起動し「今日 ≥ 締め日+1 かつ 対象月が未実行」なら実行**（Scheduler の cron は固定） | 締め日変更が即反映・IaC ドリフトなし・障害翌日の自動キャッチアップ・手動実行と同一コードパス |
| Scheduler 設定 | `ScheduleV2`、`FlexibleTimeWindow: OFF`、`RetryPolicy` 小さめ、Scheduler DLQ と Lambda 側 OnFailure（二層） | Scheduler→Lambda は**非同期 Invoke**。Scheduler の再試行は Invoke 失敗にしか効かず、関数内エラーは Lambda 非同期リトライ側で扱われる |
| 15分制限 | 実測 p95 が 5 分を超えたら Step Functions（Map で集計→Excel→送信を直列）へ分割。SQS ファンアウトは不採用 | 集計→生成→送信の順序依存があり、SQS だと「全件完了」判定を自前で作る必要がある |
| 設定/履歴 | DynamoDB 単一テーブル。`CONFIG#CURRENT`、`AUDIT#<ts>`、`PRICE#<種別>/EFFECTIVE#<日付>`、`RUN#<YYYY-MM>` | 単価は「対象月の締め日以前で最新」を `Query ScanIndexForward=False Limit=1` で引く。二重送信は `RUN#YYYY-MM` への条件付き PutItem で防ぐ |
| テスト | `pytest` + `moto`（AWS）+ `responses`（外部 REST）。Excel は生成後に openpyxl で読み戻して検証 | 外部依存を全てモックして handler を一気通貫で回せる |

---

## 1. Excel 生成

### 1.1 openpyxl と XlsxWriter の比較

| 観点 | openpyxl 3.1.5 | XlsxWriter 3.2.9 |
|---|---|---|
| 最終リリース | 2024-06-28（PyPI classifiers は 3.11 まで） | 2025-09-16（classifiers に 3.13 あり） |
| Python 3.13 | **実測 OK**（uv で CPython 3.13.12 を用意し import・生成・結合・条件付き書式まで動作） | 実測 OK |
| 依存 | et_xmlfile（pure Python） | なし（標準ライブラリのみ） |
| wheel サイズ | 251 KB + 18 KB | 175 KB |
| インストール後サイズ（実測） | 1.90 MB + 0.07 MB | 1.67 MB |
| 読み書き | 読み・書き・既存ファイル編集 | **書き込み専用**（既存 xlsx は開けない） |
| 速度・メモリ | 普通。`write_only` モードあり | 高速（大規模で ~10 倍）、`constant_memory` / `in_memory` あり |
| 書式 API | `number_format` 文字列、`Font/PatternFill/Alignment` を個別セルに付ける | `add_format({...})` で書式オブジェクトを作り `write(..., fmt)`。`set_column`、`merge_range`、`conditional_format` が直感的 |
| 条件付き書式 | `CellIsRule` / `FormulaRule` | `conditional_format(range, {'type': 'cell'|'formula', ...})` |
| グラフ | あり（カスタマイズ少なめ） | あり（豊富） |

**推奨**: 本件は「毎月ゼロから生成」なので XlsxWriter。ただし「会社様式のテンプレ xlsx に数値を流し込む」要件が出るなら openpyxl が必須になるため、**着手前に様式の有無を確認**すること。両方入れても 3.6MB 程度で実害はないが、迷ったら XlsxWriter 一本。

### 1.2 SAM でのバンドル

- 関数ディレクトリの `requirements.txt` に `XlsxWriter==3.2.9`（または `openpyxl==3.1.5`）を書き、`sam build` するだけでよい。pure Python なので `--use-container` は必須ではないが、プロジェクト規約（CLAUDE.md）に従い `sam build --use-container` で統一して問題ない。
- Lambda の制限は zip 50MB / 展開後 250MB（Layer 含む）。本件の依存（Excel ライブラリ + slack_sdk + Powertools）は合計しても数十 MB 未満で余裕。
- **Layer 化は不要**。Layer が効くのは「複数関数で同じ重い依存を共有」「関数コードだけ頻繁に更新」の場合。本件は関数 1〜2 本で依存も小さい。Powertools は AWS 提供 Layer（python3.13 対応）を使ってもよい。
- 実行時は `io.BytesIO` に書いて S3 `put_object` → そのまま SES 添付に使う。`/tmp` 不要。

### 1.3 実装上の注意（日本語・書式）

| 項目 | 注意点 |
|---|---|
| 日本語フォント | xlsx にフォントは埋め込まれない。`Font(name="Meiryo")` / `{'font_name': 'Meiryo'}` のように**日本語フォントを明示**する（社内ルール：中華フォント禁止）。未指定だと Excel の既定（環境依存）で開かれる。表示側にフォントが無ければ代替表示になるだけで壊れはしない |
| 列幅 | 単位は「標準フォントの半角文字数」。日本語は全角=2 として `unicodedata.east_asian_width()` で幅を数え、最大値 + 2 程度を設定する。`len()` で数えると半分に潰れる。自動調整は Excel 側で行われないので自前計算が必要 |
| 通貨 | 数値は**数値型**で書き込み、`'"¥"#,##0'` や `'#,##0"円"'` を number_format に指定する。文字列で「¥12,000」と書くと合計が取れない。DynamoDB から来る `Decimal` は `int()` に変換 |
| 日付 | `datetime`（**タイムゾーン無し**）で書き込み `'yyyy/mm/dd'` を指定。XlsxWriter は aware datetime を受け付けないので JST に変換して naive にする。文字列で書くと日付として扱われない |
| セル結合 | openpyxl `merge_cells("A1:D1")`、XlsxWriter `merge_range("A1:D1", 値, 書式)`。書式は左上セルにだけ効く。結合はソート/フィルタと相性が悪いので、明細表には使わず表題行のみに留める |
| 条件付き色分け | Excel 側で評価される（数式ベース）ため、メールのプレビューや一部ビューアでは反映されない。「必ず色を出したい」行（例：除外キーワード該当、金額 0 円）は **Python 側で判定して静的塗り**（`PatternFill` / `bg_color`）にする方が確実。閾値で動的に変えたいものだけ条件付き書式 |
| 数式 | XlsxWriter は数式のキャッシュ値を持たず Excel が開いた時に計算する。ビューアによっては空欄に見えるため、**合計は Python 側で計算して値で書く**（数式は補助的に） |
| シート名 | 31 文字以内、`\ / ? * [ ]` 不可。日本語可 |
| ファイル名 | 後述の SES 添付の都合で **ASCII 名**（例 `saas_a_billing_2026-08.xlsx`）を推奨 |

---

## 2. SES 添付送信

### 2.1 v1 `SendRawEmail` と v2 `SendEmail(Raw)` の違い

| 観点 | SES v1 `send_raw_email` | SES v2 `send_email(Content={'Raw':...})` | SES v2 `Simple + Attachments`（2025-04-04 追加） |
|---|---|---|---|
| メッセージ上限 | **10MB**（base64 後、引き上げ不可。公式に「10MB 超は v2 へ」） | **40MB**（base64 後） | 40MB |
| MIME 構築 | 自前 | 自前 | **SES が組み立てる**（`FileName` / `RawContent` / `ContentTransferEncoding: BASE64`） |
| 日本語件名 | 自前で RFC2047 | 自前で RFC2047（`EmailMessage` が自動） | `Subject.Charset: UTF-8` を指定するだけ |
| boto3 要件 | 古くから可 | 古くから可 | 2025-04 以降の boto3 が必要。Lambda ランタイム同梱 boto3 の版はリージョン/時期で異なるため **`requirements.txt` で boto3 を固定**するか、デプロイ先で `boto3.__version__` を確認 |
| IAM | `ses:SendRawEmail` | `ses:SendEmail`（Raw を使う場合は `ses:SendRawEmail` も併記しておくと安全） | `ses:SendEmail` |
| 備考 | 10MB 超は SES 全体で帯域制限（最低 40MB/s） | 1 API で Simple/Raw/Template を切替可能。ConfigurationSet、EmailTags、ListManagement 等 v2 機能が使える | boto3 固定が前提。実装は最も短い |

**推奨**: v2 `Raw`。理由は (1) 40MB、(2) SDK 版に依存しない、(3) 日本語件名・ファイル名の扱いを自分で決められる、(4) moto が MIME を解釈するのでテストしやすい。boto3 を固定する運用に抵抗が無ければ `Simple + Attachments` でコード量を減らしてもよい。

### 2.2 MIME 構築（`email.message.EmailMessage`）

`email.mime.*` の旧 API より `EmailMessage` を使う。件名の RFC2047 エンコード、`Content-Transfer-Encoding`、boundary は自動。

```python
from email.message import EmailMessage
from email.policy import SMTP
msg = EmailMessage()
msg["Subject"] = "【SaaS_A】2026年8月分 月次請求集計"
msg["From"] = "SaaS_A 請求 <billing@backoffice.example-cloud.com>"
msg["To"] = ", ".join(recipients)
msg.set_content(body_text)                       # text/plain; charset=utf-8
msg.add_attachment(xlsx_bytes, maintype="application",
                   subtype="vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                   filename="saas_a_billing_2026-08.xlsx")
sesv2.send_email(FromEmailAddress=..., Destination={"ToAddresses": recipients},
                 Content={"Raw": {"Data": msg.as_bytes(policy=SMTP)}},
                 ConfigurationSetName="saas-a-billing")
```

- `as_bytes(policy=SMTP)` で CRLF 改行・7bit 安全なヘッダになる。SES は 7bit ASCII 前提で、非 ASCII は必ずエンコードが必要（公式）。
- 添付は base64（`add_attachment` が自動で付ける）。MIME パート上限 500。
- `.xlsx` は SES の禁止拡張子リストに**含まれない**（禁止は .exe/.js/.vbs 等）。

### 2.3 日本語件名・ファイル名

- **件名**: `EmailMessage` が `=?utf-8?b?...?=` に自動変換。問題なし。
- **添付ファイル名**: Python は RFC2231（`filename*=utf-8''...`）のみを出力する。現行の Gmail/Outlook/Thunderbird は解釈できるが、古いクライアントや一部 Web メールで RFC2047（`filename="=?utf-8?B?...?="`）しか読まない事例が既知（cpython issue 31831、Django #26227）。**実務的には ASCII ファイル名に統一**して回避する。日本語名が必須なら `Content-Disposition` ヘッダを自前で組み、RFC2047 と RFC2231 を併記する。
- 本文は `set_content(text)` で UTF-8 + quoted-printable/base64 が自動選択される。

### 2.4 送信元ドメイン認証（`backoffice.example-cloud.com` を新サブドメインとして追加）

手順（ref-project-a の `stacks/ses/template.yaml` と同型）:

1. `AWS::SES::EmailIdentity`（`EmailIdentity: backoffice.example-cloud.com`、`DkimAttributes.SigningEnabled: true`、`DkimSigningAttributes.NextSigningKeyLength: RSA_2048_BIT`、`MailFromAttributes.MailFromDomain: bounce.backoffice.example-cloud.com`、`BehaviorOnMxFailure: USE_DEFAULT_VALUE`）
2. 同スタックで `AWS::Route53::RecordSet` を 3 本、`!GetAtt Identity.DkimDNSTokenName1..3` / `DkimDNSTokenValue1..3` の CNAME を登録（Easy DKIM）。検証はレコード検出後に自動完了
3. カスタム MAIL FROM 用に `bounce.backoffice.example-cloud.com` へ MX（`10 feedback-smtp.ap-northeast-1.amazonses.com`）と TXT（`v=spf1 include:amazonses.com ~all`）。これで SPF が From ドメインと整合（relaxed alignment）し DMARC の SPF 側も通る
4. `_dmarc.backoffice.example-cloud.com` TXT（例 `v=DMARC1; p=quarantine; rua=mailto:dmarc@example.co.jp`）。親 `example-cloud.com` に `_dmarc` があればサブドメインは既定で継承（`sp=` で分けられる）が、明示した方が事故が少ない
5. `ConfigurationSet` + EventDestination（Bounce/Complaint → SNS）を用意し、実行履歴に `MessageId` を残す

補足:
- 親ドメイン `example-cloud.com` を Easy DKIM 済みならサブドメインにも DKIM は継承されるが、**Identity（送信許可）はサブドメインごとに検証が必要**。サブドメイン固有の設定は親設定を上書きする。
- Identity・DKIM・サンドボックス状態・送信クォータ・SMTP 認証情報・抑制リストは**すべてリージョン単位**。東京で送るなら東京で Identity を作る。
- SES の DKIM 署名ドメインは東京も既定の `dkim.amazonses.com`（リージョン固有ドメインを使うのは大阪等の一部リージョンのみ）。

### 2.5 東京（ap-northeast-1）と us-west-2 の違い、ref-project-a が us-west-2 を使った理由

| 観点 | ap-northeast-1 | us-west-2 |
|---|---|---|
| SES 提供 | 2020-07-01 開始。API/SMTP/受信（inbound）すべて対応 | 古参リージョン |
| 機能差 | なし（v2 API、添付、Mail Manager、Tenant 等すべて可） | なし |
| サンドボックス | **リージョン別**。東京は未申請なら sandbox（200 通/日、1 通/秒、宛先は検証済みのみ） | 社内アカウントは ref-project-a 時点で本番アクセス済み |
| ref-project-a の選択理由 | — | `ref-project-a/Documents/INFRA-DESIGN.md` L286: 「SES EmailIdentity は us-west-2 リージョンにデプロイ（**SES サンドボックス解除済みリージョン**）」。つまり技術的理由ではなく**既存の本番アクセスを流用**したため |
| 副作用 | 単一スタック・同一リージョンで IAM/KMS/ログ/DLQ が揃う | ref-project-a では S3 バケットのリージョン不一致（`E2E-TEST-REPORT.md` #3）や、SES スタックだけ別リージョンに手動デプロイする運用（`OPERATION.md`）が発生 |

**推奨**: 東京で完結。追加コストは「東京での本番アクセス申請 1 回（コンソール or `aws sesv2 put-account-details --production-access-enabled --mail-type TRANSACTIONAL --website-url ... --contact-language JA`、初回応答 24 時間以内）」だけ。申請中も宛先ドメイン（`example.co.jp` 等）を Identity として検証しておけば sandbox のまま社内宛てテスト送信は可能。どうしても申請を避けたい場合は ref-project-a の Identity の隣に `backoffice.example-cloud.com` を us-west-2 で追加し、Lambda から `boto3.client("sesv2", region_name="us-west-2")` で送る構成も動作する（クロスリージョン送信自体は問題ない）。

### 2.6 サンドボックス解除の要否

- 宛先が**検証済みドメインの社内アドレスのみ**で、月 1 通 + テスト程度なら sandbox でも運用できる（200 通/日は十分）。
- ただし宛先を Web 画面で編集可能にする要件があるため、経理担当の個人アドレスや外部会計事務所を追加した瞬間に `MessageRejected` になる。**本番アクセスを取っておく**のが安全。申請時は「Transactional」「送信先は自社担当者」「バウンス/苦情は SNS で監視」を記載。

---

## 3. Slack 投稿

### 3.1 Incoming Webhook と Bot token の比較

| 観点 | Incoming Webhook | Slack App Bot token（`xoxb-`） |
|---|---|---|
| できること | テキスト/Block Kit の投稿のみ | `chat.postMessage`、`files_upload_v2`（ファイル添付）、スレッド返信、更新/削除 |
| ファイル添付 | **不可**（画像は URL 参照のみ） | 可。`files.upload` は 2025-11-12 廃止済み → `files_upload_v2`（`files.getUploadURLExternal` → POST → `files.completeUploadExternal` の 3 段を SDK が包む） |
| チャンネル | **インストール時に固定**。リクエストごとに変更不可 | `channel` 引数で可変（設定テーブルから読める）。**チャンネル ID 指定 + Bot をそのチャンネルに招待**が必要（`files_upload_v2` はチャンネル名では失敗する既知問題 #1326） |
| 必要スコープ | `incoming-webhook` | `chat:write`、`files:write`（+ 非公開チャンネルなら招待） |
| 秘密情報 | URL 自体が秘密（漏洩時は Slack が自動失効） | token が秘密 |
| 依存 | `urllib`/`requests` のみ | `slack_sdk`（pure Python、数 MB）か 3 API を直接叩く |
| レート | 1 msg/秒/チャンネル | 同左（月 1 回なので無関係） |

**推奨**: Bot token。「Slack チャンネルを設定で変える」要件は Webhook では満たせない。

### 3.2 何を投稿するのが実務的か

| 候補 | 評価 |
|---|---|
| 本文（送付済み・照合を忘れずに）+ 対象月 + 合計金額 + テナント数/地点数 + 宛先 | **必須**。Block Kit の section + fields で視認性を上げる。合計金額は `¥1,234,567` 形式 |
| S3 署名付き URL | **不向き**。Lambda 実行ロールの一時クレデンシャルで署名した URL は、`ExpiresIn` に関係なく**そのクレデンシャル失効時点で無効**（AWS 公式。ロールセッションは既定 1 時間、最長でも数時間）。Slack を翌日見た人はリンク切れ |
| ポータル画面 URL（Cognito 認証 → API → S3 から取得） | **推奨**。恒久リンク、認証あり、監査可能。Web 画面を作る計画があるので相性が良い |
| Slack に xlsx を直接添付（`files_upload_v2`） | **併用推奨**。メールを見失っても Slack から即確認できる。ただし請求データを Slack に置いてよいかの社内ポリシー確認が前提 |
| 署名付き URL を長期化する代替（CloudFront 署名 URL / IAM ユーザー鍵で 7 日署名） | 鍵の常設が必要になり本件の規模では過剰 |

投稿例（Block Kit）:「【SaaS_A 月次請求】2026年8月分（7/21〜8/20）を経理宛にメール送付しました。合計 ¥1,234,567（テナント 12 / 地点 340）。請求書との照合をお願いします。▶ ポータルで確認（URL）」＋同スレッドに xlsx 添付。

### 3.3 Bot token / Webhook URL の保管

- SSM Parameter Store **SecureString**（`/saas-a-billing/slack/bot_token`）。KMS は `aws/ssm` で十分（CMK にするならキーポリシーに Lambda ロールの `kms:Decrypt` を追加）。
- Lambda IAM: `ssm:GetParameter` を該当パス ARN に限定 + `kms:Decrypt`。
- 取得は実行時に Powertools `get_parameter(name, decrypt=True, max_age=300)`。**Lambda 環境変数には入れない**。CloudFormation の `{{resolve:ssm-secure:...}}` は Lambda 環境変数に非対応（`{{resolve:ssm:...}}` は平文 String のみで、yamadatt の事例はこの方式＝非推奨）。
- ログに token を出さない（Powertools Logger の `keys` 指定や、レスポンスの `token` フィールドをログ対象にしない）。

---

## 4. スケジュール

### 4.1 EventBridge Scheduler の基本設定（SAM `ScheduleV2`）

```yaml
Events:
  Monthly:
    Type: ScheduleV2
    Properties:
      ScheduleExpression: cron(0 6 * * ? *)          # 毎日 06:00（締め日判定は関数側）
      ScheduleExpressionTimezone: Asia/Tokyo
      FlexibleTimeWindow: { Mode: "OFF" }
      RetryPolicy: { MaximumRetryAttempts: 2, MaximumEventAgeInSeconds: 3600 }
      DeadLetterConfig: { Type: SQS }                 # SAM が Scheduler 用 DLQ を生成
      Input: '{"trigger":"scheduled"}'
```

- 固定日運用なら `cron(0 6 21 * ? *)`。cron は 6 フィールド（分 時 日 月 曜日 年）、日と曜日のどちらかは `?`。`L`（月末）も使える。
- `ScheduleExpressionTimezone` は IANA 名。JST は DST 無しなので調整なし。呼び出し精度は 60 秒（06:00:00〜06:00:59）。
- `RetryPolicy` の範囲: `MaximumRetryAttempts` 0〜185、`MaximumEventAgeInSeconds` 60〜86400（未指定時は最大側の既定）。
- Scheduler の DLQ は **標準キューのみ**（FIFO 不可）。実行ロールに `sqs:SendMessage` が必要。

### 4.2 再試行と DLQ は二層で考える

- **Scheduler → Lambda は非同期 Invoke**（AWS 公式「EventBridge Scheduler invokes your Lambda function asynchronously」）。
- したがって Scheduler の `RetryPolicy`/DLQ が効くのは「Invoke API 自体が失敗した場合」（権限不足、関数不存在、スロットル）。**関数内の例外は Lambda 非同期呼び出しの再試行（既定 2 回）と Lambda 側の DLQ / Destinations（OnFailure）に流れる**。aws-samples の公式パターン（下記 7.）も二つの DLQ を分けている。
- 推奨: 関数の `EventInvokeConfig` で `MaximumRetryAttempts: 0`（または 1）を明示し、`DestinationConfig.OnFailure` を SQS/SNS に向けて通知。自動再試行で二重送信されないよう、関数内は実行履歴（5.）で冪等化する。
- 月 1 回のバッチなので「自動で何度も叩く」より「失敗を確実に通知して手動再実行」の方が安全。Slack に失敗通知も出す。

### 4.3 15 分制限を超える場合の分割判断

判断基準（順に確認）:

1. **想定処理時間**: テナント数 × 地点数分の API 呼び出し回数 × 1 回あたりレイテンシ（p95）。例: 50 テナント × 平均 3 API × 300ms ≒ 45 秒。**p95 が 5 分（900 秒 × 0.8 の安全側のさらに半分）を超えるなら分割**を検討。
2. **外部 API のレート制限**で待ちが必要か（sleep が入るなら実時間が伸びる）。
3. **部分失敗の再開**が必要か（テナント単位で再実行したい）。

分割方式:

| 方式 | 向く場面 | 本件での評価 |
|---|---|---|
| 単一 Lambda（タイムアウト 900 秒、メモリ 1024MB 以上） | 数分で終わる | **まずはこれ**。実測ログを残し、伸びたら分割 |
| Step Functions（Map でテナント並列集計 → 集計マージ → Excel → メール → Slack） | 順序依存がある / 各段の成否を可視化したい / 再実行を段単位で | **分割するならこれ**。実行履歴が UI で追え、Map の並列度も制御できる |
| SQS ファンアウト（分割 Lambda → SQS → ワーカー Lambda） | 独立したタスクを大量並列 | 「全テナント完了」判定と集約を自前で作る必要があり、月次集計には過剰 |

### 4.4 締め日を可変にする方式の比較

| 方式 | (A) 設定保存時に `scheduler:UpdateSchedule` で cron を書換 | (B) 毎日 06:00 に起動し関数内で「今日 ≥ 締め日+1 かつ対象月が未実行」を判定 |
|---|---|---|
| 反映 | API 成功時 | 設定保存で即（次回起動時に評価） |
| IaC | CloudFormation 管理の Schedule を実行時に書き換える → **ドリフト**。`UpdateSchedule` は省略項目を既定値に戻すため `GetSchedule` → マージ → 更新が必要 | ドリフトなし。Schedule は固定 |
| 権限 | 設定 API に `scheduler:UpdateSchedule` + `iam:PassRole` | 不要 |
| 月末締め等の表現 | `L` 等 cron 変換ロジックが必要 | Python の日付計算で自由 |
| 障害時 | 21 日に失敗すると翌月まで動かない | 22 日以降も「未実行」なら自動で実行（キャッチアップ） |
| コスト | — | 空振り 29 回/月（数 ms、実質ゼロ） |
| テスト | Scheduler をモック | 純粋な日付ロジックの単体テスト |

**推奨: (B)**。判定は `target_month = 締め日で決まる対象月`、`RUN#<target_month>` が SUCCEEDED でなければ実行。手動実行は同じ関数を `{"trigger":"manual","target_month":"2026-08","force":true,"requested_by":"<cognito sub>"}` で Invoke する（同一コードパス）。

---

## 5. 設定テーブルの設計（DynamoDB 単一テーブル）

### 5.1 キー設計

| PK | SK | 内容 |
|---|---|---|
| `CONFIG#CURRENT` | `SETTINGS` | 現在の設定（`recipients[]`, `slack_channel_id`, `closing_day`, `exclude_keywords[]`, `version`, `updated_at`, `updated_by`） |
| `CONFIG#CURRENT` | `AUDIT#<ISO8601>#<uuid>` | 変更履歴（`actor`(Cognito sub/email), `source_ip`, `before`, `after`, `diff`） |
| `PRICE#<種別>`（例 `PRICE#DEVICE`、`PRICE#TENANT_BASE`） | `EFFECTIVE#<YYYY-MM-DD>` | 単価表（`unit_price`, `currency`, `note`, `created_by`, `created_at`）。**同じ PK に適用開始日ごとの行を積む** |
| `RUN#<YYYY-MM>` | `RUN` | 月次実行の最新状態（`status`: RUNNING/SUCCEEDED/FAILED, `trigger`, `started_at`, `finished_at`, `attempt`, `s3_key`, `ses_message_id`, `slack_ts`, `total_amount`, `tenant_count`, `device_count`, `error`, `price_snapshot`） |
| `RUN#<YYYY-MM>` | `ATTEMPT#<n>` | 各試行の記録（再送の監査。`requested_by`, `force`, 結果） |

- GSI は不要（アクセスパターンがすべて PK 指定 + SK 範囲）。
- 監査一覧: `Query PK=CONFIG#CURRENT, SK begins_with AUDIT#`, `ScanIndexForward=False`。
- 単価の解決: `Query PK=PRICE#DEVICE, SK <= EFFECTIVE#<対象月の締め日>, ScanIndexForward=False, Limit=1` → 対象月に適用される単価。`Limit=1` は GetItem 相当のコスト。将来の値上げは適用開始日付きで**先に登録しておける**。
- 単価種別が「機種ごと」に増えるなら `PRICE#DEVICE#<機種コード>` のように PK を細分化し、フォールバック（機種固有 → 既定）を関数側で行う。

### 5.2 監査（誰がいつ変えたか）

- 設定更新 API（Web 画面のバックエンド）で、`TransactWriteItems` により `SETTINGS` の更新（`ConditionExpression: version = :expected` の楽観ロック）と `AUDIT#` 追加を**同一トランザクション**で書く。
- DynamoDB Streams だけでは「誰が」が取れない（レコードに actor を含めないと不明）。Cognito の `sub`/`email` を API 層で確定させてから書く。
- 単価表の行は「追記のみ」で、修正は新しい適用開始日の行を追加する運用にすると履歴が自然に残る（物理削除しない）。

### 5.3 月次実行の冪等性（同じ月を二重送信しない）

- 実行開始時に `PutItem RUN#<YYYY-MM>/RUN` を `ConditionExpression: attribute_not_exists(PK) OR #status = :failed OR (#status = :running AND started_at < :stale)` で書く。`ConditionalCheckFailedException` なら「既に成功済み or 実行中」として**即終了**（Lambda の非同期リトライや二重起動に対する防御）。
- `stale` は Lambda タイムアウト（900 秒）+ 余裕（例 20 分）で判定し、途中死した RUNNING を奪取できるようにする。
- 成功時に `UpdateItem` で `SUCCEEDED` + `ses_message_id` + `slack_ts` + `s3_key` を記録。失敗時は `FAILED` + `error`。
- Powertools Idempotency は「同一ペイロードの短期重複（TTL 既定 1 時間）」向けで、レコードは一時的という設計。**業務キー（対象月）の永続レコード**が要る本件は、上記の自前条件付き書き込みが適切。Lambda 非同期リトライ対策として Powertools Idempotency を**併用**してもよい（テーブルは分ける）。

### 5.4 再実行時の扱い

| ケース | 動作 |
|---|---|
| 失敗後の再実行（自動/手動） | `FAILED` → `RUNNING` へ条件付き更新、`attempt+1`。成功すれば通常どおり送信 |
| 成功済みの再送（`force=true`、手動のみ） | 新しい `ATTEMPT#n` を追加。件名と Slack 本文に「再送（第 n 回）」を明記。S3 キーは `billing/2026-08/attempt-2/...xlsx` で上書きしない |
| 単価の扱い | 「実行時点の最新」ではなく「対象月の締め日以前で最新の EFFECTIVE 行」を引くため、再実行でも同じ金額。遡及訂正の行を後から追加した場合だけ変わる（意図どおり）。`RUN` に `price_snapshot` を残して差分を説明可能にする |
| 設定の扱い | 宛先/チャンネルは**実行時点の設定**を使う（過去月の再送を現在の担当に送りたいのが普通）。使った値は `ATTEMPT#` に記録 |

---

## 6. 外部 API 呼び出しの注意

### 6.1 認証情報の取得と保持

- SSM SecureString `/saas-a-billing/saasa-api/username`, `/password`（または API トークン）。IAM は `ssm:GetParameter`/`GetParameters` を `arn:aws:ssm:ap-northeast-1:<acct>:parameter/saas-a-billing/*` に限定 + `kms:Decrypt`（`aws/ssm` を使う場合はキーポリシー側の制御はできないが `kms:Decrypt` 自体は必要）。
- Powertools `get_parameters("/saas-a-billing/saasa-api", decrypt=True, max_age=300)`。POWERTOOLS_PARAMETERS_SSM_DECRYPT=true でも可。代替の「AWS Parameters and Secrets Lambda Extension」は言語非依存だが本件は Python 単独なので Powertools で十分。
- **Bearer トークンはハンドラ内のローカル変数のみ**で保持し、モジュールグローバルに置かない（実行環境の再利用で残留する）。処理終了時に可能ならログアウト API を呼ぶ。ログには出さない。
- SaaS_A API は「1 ユーザーにつき有効トークン 1 つ」（MCP の注意書き）。**バッチ専用 API ユーザー**を用意し、MCP や担当者のユーザーと衝突させない。

### 6.2 リトライ / タイムアウト / レート制限

- `requests` なら `HTTPAdapter(max_retries=Retry(total=3, backoff_factor=1, status_forcelist=[429,500,502,503,504], allowed_methods=["GET"], respect_retry_after_header=True))`、`timeout=(5, 30)`。POST（ログイン）はリトライ対象から外すか冪等なものだけに限定。
- boto3 は `Config(retries={"mode": "standard", "max_attempts": 5})`。
- ページネーション（`has_more`）を必ず処理し、1 テナントずつ直列に呼ぶ。429 は `Retry-After` を尊重。並列化はレート制限と「トークン 1 つ」制約から避ける。
- 全体の経過時間を計測し、`context.get_remaining_time_in_millis()` が閾値を下回ったら中断して `FAILED` にする（部分送信を防ぐ）。

### 6.3 テスト時の HTTP / AWS モック

| 対象 | ライブラリ | 備考 |
|---|---|---|
| 外部 REST（`requests`） | **`responses`** | `@responses.activate` でエンドポイントごとに JSON を登録。ページネーションも順番に登録できる |
| 外部 REST（`httpx`） | `pytest-httpx` | 非同期を使うなら `respx` |
| AWS（SSM/S3/DynamoDB/SES v2/Scheduler） | **`moto`** `mock_aws` | moto は SES v2 `send_email(Raw)` の MIME を解釈するので添付の有無・件名を検証できる |
| Slack | `responses` で `slack.com/api/*` をモック、または `WebClient` を monkeypatch | `files_upload_v2` は 3 API を叩くため WebClient 差し替えが簡単 |
| Excel | 生成後に **openpyxl で読み戻し**、値・number_format・結合範囲を assert | XlsxWriter で書いて openpyxl で検証する組み合わせが定番 |

テスト構成: `tests/unit`（集計・単価解決・締め日判定は純関数）、`tests/integration`（moto + responses で handler を一気通貫）。TDD 方針に従い、Excel の期待値は「読み戻して比較」で先にテストを書く。

---

## 7. 先行事例（GitHub）と参考パターン

| # | リポジトリ / ファイル | 本件との対応 |
|---|---|---|
| 1 | techservicesillinois/lambda-aws-monthly-rebill-tags `lambda_src/generate_report.py` — https://github.com/techservicesillinois/lambda-aws-monthly-rebill-tags/blob/3731acaf0964e991bcbb3871c86919ad537c9075/lambda_src/generate_report.py | Cost Explorer → **openpyxl** で xlsx（表・グラフ）→ `MIMEMultipart`/`MIMEApplication` → **SES v1 `send_raw_email`** の月次レポート Lambda。構成が最も近い |
| 2 | aws-samples/serverless-patterns `eventbridge-schedule-to-lambda-dlq-sam-python` — https://github.com/aws-samples/serverless-patterns/tree/main/eventbridge-schedule-to-lambda-dlq-sam-python | **公式 SAM パターン**。Scheduler → Lambda で「Scheduler 側 DLQ」と「Lambda 実行失敗 DLQ」を二層に分けた雛形 |
| 3 | yamadatt/sam-aws-cost-slack `template.yaml` — https://github.com/yamadatt/sam-aws-cost-slack/blob/eb9efd425e4229218a072757248dda04e632a285/template.yaml | `ScheduleV2` + `ScheduleExpressionTimezone: Asia/Tokyo` の実例。Slack token を `{{resolve:ssm:...}}` で環境変数に入れており（平文 String）、本件では採らない方式の反面教師 |
| 4 | Flupinochan/SlackNotification `lambda/code/ce/index.py` — https://github.com/Flupinochan/SlackNotification/blob/5cd87dcdce9929d5962cb42e4f3a46cdfb63af69/lambda/code/ce/index.py | Lambda から `slack_sdk.WebClient.files_upload_v2` で画像を添付投稿する実例 |
| 5 | 社内 `ref-project-a`（`stacks/ses/template.yaml`, `Documents/INFRA-DESIGN.md` L286, `Documents/OPERATION.md` L78） | SES EmailIdentity + DKIM + MAIL FROM + Route 53 自動登録の社内実装。us-west-2 採用理由＝「サンドボックス解除済みリージョン」 |

AWS 公式ドキュメントで確認した事項は 8. にまとめた。

---

## 8. 出典 URL 一覧

### Excel
- openpyxl PyPI: https://pypi.org/project/openpyxl/
- XlsxWriter PyPI: https://pypi.org/project/XlsxWriter/
- XlsxWriter vs OpenPyXL（XlsxWriter 公式）: https://xlsxwriter.com/xlsxwriter-vs-openpyxl/
- openpyxl 日本語列幅（east_asian_width）: https://qiita.com/Nomisugi/items/f451ad9f67d3e419c73b
- Lambda Python 3.13 ランタイム発表: https://aws.amazon.com/blogs/compute/python-3-13-runtime-now-available-in-aws-lambda/
- Lambda Python ランタイム一覧・同梱 SDK: https://docs.aws.amazon.com/lambda/latest/dg/lambda-python.html
- 実測: uv で CPython 3.13.12 を用意し openpyxl 3.1.5 / XlsxWriter 3.2.9 の import・生成・結合・条件付き書式を確認（2026-08-27、本レポート作成時）

### SES
- サービスクォータ（v1 10MB / v2 40MB）: https://docs.aws.amazon.com/ses/latest/dg/quotas.html
- 添付ファイル（Simple+Attachments、禁止拡張子）: https://docs.aws.amazon.com/ses/latest/dg/attachments.html
- 添付サポート発表（2025-04-04）: https://aws.amazon.com/about-aws/whats-new/2025/04/amazon-ses-attachments-sending-apis
- Raw メール送信（MIME/エンコード/Python 例）: https://docs.aws.amazon.com/ses/latest/dg/send-email-raw.html
- SendEmail API v2: https://docs.aws.amazon.com/ses/latest/APIReference-V2/API_SendEmail.html
- boto3 sesv2 send_email: https://boto3.amazonaws.com/v1/documentation/api/latest/reference/services/sesv2/client/send_email.html
- リージョンごとの差異（sandbox/Identity/DKIM はリージョン単位）: https://docs.aws.amazon.com/ses/latest/dg/regions.html
- SES エンドポイント・DKIM ドメイン表: https://docs.aws.amazon.com/general/latest/gr/ses.html
- 本番アクセス申請: https://docs.aws.amazon.com/ses/latest/dg/request-production-access.html
- Easy DKIM: https://docs.aws.amazon.com/ses/latest/dg/send-email-authentication-dkim-easy.html
- DMARC 準拠: https://docs.aws.amazon.com/ses/latest/dg/send-email-authentication-dmarc.html
- カスタム MAIL FROM: https://docs.aws.amazon.com/ses/latest/dg/mail-from.html
- AWS::SES::EmailIdentity: https://docs.aws.amazon.com/AWSCloudFormation/latest/TemplateReference/aws-resource-ses-emailidentity.html
- 東京リージョン提供開始（2020-07-01）: https://aws.amazon.com/about-aws/whats-new/2020/07/amazon-ses-available-ohio-singapore-tokyo-seoul-regions
- 東京リージョン解説（DevelopersIO）: https://dev.classmethod.jp/articles/new-available-amazon-ses-tokyo-region/
- Python email 例（EmailMessage.add_attachment / as_bytes）: https://docs.python.org/3/library/email.examples.html
- 非 ASCII 添付ファイル名の互換問題: https://bugs.python.org/issue31831 , https://code.djangoproject.com/ticket/26227

### Slack
- files.upload 廃止と新フロー: https://docs.slack.dev/changelog/2024-04-a-better-way-to-upload-files-is-here-to-stay
- ファイルアップロード手順: https://docs.slack.dev/messaging/working-with-files/
- Incoming Webhook の制約: https://docs.slack.dev/messaging/sending-messages-using-incoming-webhooks/
- Python でのアップロード: https://docs.slack.dev/tools/python-slack-sdk/tutorial/uploading-files/
- files_upload_v2 のチャンネル指定問題: https://github.com/slackapi/python-slack-sdk/issues/1326
- S3 署名付き URL の有効期限（一時クレデンシャル）: https://docs.aws.amazon.com/AmazonS3/latest/userguide/using-presigned-url.html

### スケジュール
- Scheduler スケジュール種別・cron・タイムゾーン: https://docs.aws.amazon.com/scheduler/latest/UserGuide/schedule-types.html
- Scheduler DLQ: https://docs.aws.amazon.com/scheduler/latest/UserGuide/configuring-schedule-dlq.html
- RetryPolicy: https://docs.aws.amazon.com/scheduler/latest/APIReference/API_RetryPolicy.html
- Lambda を Scheduler で呼ぶ（非同期 Invoke の明記）: https://docs.aws.amazon.com/lambda/latest/dg/with-eventbridge-scheduler.html
- テンプレートターゲット（Lambda Invoke の IAM）: https://docs.aws.amazon.com/scheduler/latest/UserGuide/managing-targets-templated.html
- UpdateSchedule（省略項目は既定値に戻る）: https://docs.aws.amazon.com/scheduler/latest/APIReference/API_UpdateSchedule.html
- SAM ScheduleV2: https://docs.aws.amazon.com/serverless-application-model/latest/developerguide/sam-property-function-schedulev2.html
- Lambda タイムアウト: https://docs.aws.amazon.com/en_us/lambda/latest/dg/configuration-timeout.html

### DynamoDB / 冪等性
- Query（ScanIndexForward）: https://docs.aws.amazon.com/amazondynamodb/latest/APIReference/API_Query.html
- KeyConditionExpression: https://docs.aws.amazon.com/amazondynamodb/latest/developerguide/Query.KeyConditionExpressions.html
- Limit=1 の性能（GetItem 相当）: https://repost.aws/questions/QUecwoP5rJSOWa6kSlvscfSA/dynamodb-query-performance-when-retrieving-latest-item-with-scanindexforward-and-limit
- Powertools Idempotency: https://docs.aws.amazon.com/powertools/python/latest/utilities/idempotency/
- DynamoDB バージョニングパターン: https://dynobase.dev/dynamodb-versioning/

### 秘密情報 / テスト
- Powertools Parameters: https://docs.aws.amazon.com/powertools/python/latest/utilities/parameters/
- Parameter Store SecureString と KMS 権限: https://docs.aws.amazon.com/kms/latest/developerguide/services-parameter-store.html
- 外部パラメータ取得方式の比較（AWS Compute Blog）: https://aws.amazon.com/blogs/compute/choosing-the-right-solution-for-aws-lambda-external-parameters/
- responses: https://oneuptime.com/blog/post/2025-01-06-python-mock-external-apis/view
- pytest-httpx: https://pypi.org/project/pytest-httpx/
- moto で Lambda 単体テスト: https://seifrajhi.github.io/blog/unit-testing-aws-lambda-moto/
