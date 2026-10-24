# AWS日次セキュリティレポート（daily-security-report）

本番 AWS アカウント 210000000001 全体を読み取り専用で点検するアプリ。
dev / prod とも同じ本番アカウントの GuardDuty・CloudTrail・CloudWatch・Logs Insights・EC2・Lightsail を読む。
書き込み先は自テーブル・自関数のログ・通知キュー・失敗キューだけで、CloudTrail や添付バケットへの書き込み権限は持たない。
業務仕様は [アプリ設計書](../../Documents/apps/daily-security-report/DESIGN.md) を参照する。

## 構成

| パス | 内容 |
| --- | --- |
| `template.yaml` | HTTP API、3 関数と個別 IAM ロール、DynamoDB、Scheduler、GuardDuty ルール、2 キュー、4 アラーム、SSM 契約 |
| `samconfig.toml` | default=dev / production=prod。SSM はキー名を上書きする。TargetEnv は持たない |
| `functions/daily_security_report_api/app.py` | `/api/daily-security-report` を strip する API。health / settings / slack/directory / days |
| `functions/daily_security_report_batch/handlers/` | report / guardduty_alert の集計・通知ハンドラ |
| `functions/daily_security_report/` | 設定・配布先・除外・国判定・基準比較・通知組み立ての純粋関数。集計 5 種と DynamoDB repo を含む |
| `functions/daily_security_report/data/` | AWS / RIR の IPv4・IPv6 範囲表（gzip）。実行時の外部問い合わせなし |
| `tools/build_ip_tables.py` | 入力ディレクトリを引数で受ける表生成ツール。Lambda の CodeUri に含まない |
| `tests/unit/` / `tests/integration/` / `tests/fixtures/` | 純粋関数、API、架空データ |
| `edge-snippet.yaml` | edge 用の origin / behavior の断片 |

日次は 07:00 JST。GuardDuty の severity >= 7 は東京の EventBridge ルールから即時通知する。
Step Functions は使用しない。dev は自動実行・即時通知とも既定で無効で、prod も初回設定で明示的に有効にする。
配布トグルは既定で両方無効。有効な配布先がなくても集計と保存は行う。

## 純粋関数の受け渡し

- `settings.default_settings(env="dev")` / `normalize_settings(item=None, env="dev")`: 毎回独立した dict を返す。sections の未保存キーは True。
- `settings.update_settings(current, patch)`: 部分更新と全体検証。version は必須。`validate_settings(values)` も利用可。エラーは `SettingsValidationError.code` / `.message`。version の照合・増分・監査は repo が担う。
- `distribution.channels(settings)`: email / slack の配列。全通知経路で使用する。
- `exclusions.is_excluded(record, exclusions, scope, on_date)`: 集計から除外するか。CloudTrail 形式、または ip / principal の短縮形を受ける。root と GuardDuty は除外しない。
- `exclusions.is_office(ip, exclusions, scope, on_date)` / `instance_is_excluded(instance, exclusions, on_date)`: 赤判定だけの除外。集計値は残す。on_date は JST の対象日。
- `ip_country.classify_ip(ip, exclusions=None, scope="cloudtrail", on_date=<必須の対象日>)`: category / label / country / is_japan / is_office。AWS は region も返す。対象日は必須。
- `baseline.compare(summary, history, settings)`: summary のコピーに red / red_count / red_counts / comparisons / no_baseline を付加。history は日次 summary の配列か、status=succeeded と summary を持つ DAILY レコードの配列。対象日前 baseline_days 日だけを使い、同じ日は重複計上しない。
- `report.build_report(summary, settings, env="prod", sent_count=1)` / `build_failure_report(date, settings, env="prod")` / `build_alert(finding, settings, env="prod")`: channels / email / slack の dict。入出力は dict / str のみで AWS を呼ばない。email / slack は共通 notify の Email / Slack に展開できる。env と sent_count はキーワード引数。

レポートの入力は設計 7.2 の summary。比較が付加する comparisons は項目別の value / baseline / days / ratio / red / label、no_baseline は項目キーの配列。
red の明細は 100 件で切り、red_count と red_counts（区分別）に切る前の件数を残す。通知件名と「ほか n 件」は切る前の件数を使う。
GuardDuty の表示タイトルは `top.guardduty`（title を持つ dict の配列）、ログイン失敗の主体別件数は `top.console_login_failures`（principal / count）。
CloudTrail の個別規則（国外ログイン成功・IAM 変更・SG 変更など）の明細は集計側が red に置き、baseline は常時赤・倍率・初見・主体別失敗を追加する。
インスタンスの new / gone と操作の突き合わせはインスタンス集計が担う。

Slack の text と先頭 section は表示用メンションを含む。notifier の追加による二重表示を避けるため、この場合の戻り値 `slack.mention` は空にする。
メール本文にはメンションを含めない。通知の message_id、meta と DAILY の通知履歴は日次ハンドラと repo が担う。

metric_targets は waf / apigw / alb / cloudfront をキーにした配列。ALB 以外の要素は名前文字列、または name / id・任意の region / label を持つオブジェクト。
ALB は id（ARN 末尾の `app/<名前>/<hex>`）が必須。name と label は表示用で、GPS 代替元は label=gps で選ぶ。
region は ap-northeast-1 / us-east-1。Lambda とインスタンスは対象を自動列挙するため登録しない。
管理 API の正規表現の初期値は `^/api/admin(?:/|$)`（架空のパス）。導入時に実際の管理 API パスを確認して登録する。
環境変数 INSIGHTS_LOG_GROUPS は `{"alarmhuba":"<Resolver log group>","gps":"<Receiver log group>"}` の JSON。
GPS 拒否件数を ALB 4XX で補う場合は、対象 ALB の metric_targets の label を gps にする（1 対象のみ）。未指定・系列なし・指標区分の停止/失敗時は未取得と表示する。
検出器は GUARDDUTY_DETECTORS の `region=id` のカンマ区切りから読む。

## 集計モジュールと永続化の契約

- red.section は guardduty / cloudtrail / metrics / insights / instances の 5 区分だけ。alarmhuba / gps は insights にまとめる。
- 常に赤の code は baseline.ALWAYS_RED に合わせる: GUARDDUTY_HIGH / ROOT / HUMAN_FOREIGN / DIRECT / ADMIN_EXTERNAL / LIGHTSAIL_WORLD_OPEN / IMAGE_PUBLIC。同じ事象を集約の赤で重複させない。
- 成功した区分の counters / seen は、対象が無くても 0 / 空配列を必ず書く。失敗・停止の区分は値を持たず、基準から外す。GPS 拒否の値を取得できない場合だけ counters を省略し、gps_rejected_source=unavailable として未取得と表示する。
- metrics_summary.missing_targets は登録対象に系列が 1 つも存在しない対象名の配列。summary.sources.metrics に引き継ぎ、入口の行に「指標なし: 対象名」と表示する。系列はあるが当日の点が無い場合は 0。
- repo は読み出し時に Decimal を int / float（整数 / 非整数）へ再帰変換し、書き込み時は float を Decimal に変換する。純粋関数も Decimal の設定・日次集計を受理する。

DAILY の sources は設計 7.2 の項目（共通の status / error / duration_sec と区分ごとの項目）だけを保存する。target_counters や生の取得結果は保存しない。
cloudtrail の details の各一覧と config_changes、insights の details の各一覧は sources.<区分> に 20 行まで保存し、落とした行数を sources.<区分>.details_truncated に一覧名ごとに記録する。
Insights は全クエリ共通の期限（Lambda の残り時間 − 60 秒、最大 480 秒）を使い、Q4 も残り時間内で待つ。期限を超えたら StopQuery して error=TIMEOUT にする。パスの ID 区間は Python で `{id}` に正規化してから再集計する。
CloudTrail で sourceIPAddress が IP でない事象は actor=aws_service（表示は「AWS サービス」）とし、国外判定と IAM 変更の赤から外す。
失敗通知は sent_count を進めず、レポートの投函に成功したときだけ進める。ログには秘密を伏せた例外とトレースバック、DAILY にはエラーコードだけを残す。

## 検証

リポジトリルートで実行する。

```bash
uv run pytest apps/daily-security-report tests/infra -q
COVERAGE_FILE=/tmp/daily-security-report.coverage uv run pytest apps/daily-security-report --cov=daily_security_report --cov-report=term-missing --cov-fail-under=80
uv run ruff check apps/daily-security-report tests/infra
uv run ruff format --check apps/daily-security-report tests/infra
uv run cfn-lint apps/daily-security-report/template.yaml
```

IP 表の更新方法・生成日・出典・入力のハッシュは [data/README.md](functions/daily_security_report/data/README.md)。
月 1 回を目安に作り直して再デプロイする。

## デプロイと運用

デプロイ順は shared → core → app → edge。SSM `/example-backoffice/{env}/apps/daily-security-report/api-domain` を app が書く。
AlarmActions は core の OpsTopicArn を参照する。Scheduler DLQ、非同期実行の失敗キュー、Lambda Errors の合算、EventBridge FailedInvocations を監視する。

```bash
cd apps/daily-security-report
sam build --use-container
sam deploy
sam deploy --config-env production
```

ローカル検証は AWS を呼ばず、moto と botocore Stubber を使う。実 AWS の疎通は導入時に行う。
導入時の通知先・除外初期値・手動実行は設計 14.1 / 14.3 を参照する。
