# ユーザー管理 アプリ設計書（admin-users）

作成日: 2026-09-03
ステータス: 実装済み（#1007）
親設計書: [../../DESIGN.md](../../DESIGN.md)（example-backoffice プラットフォーム）
関連: [アプリ追加手順](../../ADD-APP-GUIDE.md) / [scripts/README.md](../../../scripts/README.md)

---

## 1. 目的

`admin` グループの付与が `scripts/add-admin.sh`（AWS CLI）頼みで、管理者以外は状況も見えない。
サインアップ済みユーザーの棚卸しと権限・アカウント操作を画面から完結させる。

- 対象利用者: `admin` グループの数人（プラットフォーム設計 1.2）
- 非対象: サインアップ（本人がセルフサインアップする。設計 6.2）、TOTP の解除（CLI runbook のまま。設計 6.3）、操作ログの画面表示

## 2. 画面

`/admin-users`（サイドバー「ユーザー管理」。`requiredGroups: ['admin']` なので admin 以外にはメニューが出ず、URL 直打ちは `/forbidden`）。

| 列 | 内容 |
| --- | --- |
| メールアドレス | 自分の行には「自分」チップ |
| 状態 | 有効（緑）/ 無効（灰）のチップ + Cognito の `UserStatus`（確認済み / 未確認 / 初期パスワード / リセット要） |
| 権限 | `admin` / 一般のチップ + 切替ボタン |
| 登録日時・最終更新 | JST 表示（API は UTC で返す） |
| 操作 | 無効化 / 有効化、パスワードリセット、削除 |

- **状態を変える操作はすべて確認ダイアログを挟む**（共通部品 `frontend/src/components/ConfirmDialog.vue`）。削除は「この操作は取り消せません」を明記する。
- 自分の行は admin 解除・無効化・削除のボタンを `disabled` にし、ツールチップで「自分自身には操作できません」と出す（サーバも 403 で断る二重防御）。
- 結果はスナックバー（成功 / 失敗）。失敗時はストアが和文化したメッセージを出す。
- 画面末尾に注記: パスワードリセットは本人にコードがメールされること、操作が CloudWatch Logs に残ること。

## 3. API

ベースは `/api/admin-users`。応答は共通エンベロープ（`shared/layers/example_backoffice/response.py`）。
**全ルートが `admin` 専用**（`auth.require_group(claims, "admin")`）。ユーザーの識別子は `sub`。

| メソッド / パス | 用途 | 応答 |
| --- | --- | --- |
| `GET /users` | 一覧（email 昇順） | `{"users": [...], "self_sub": "<呼出者の sub>"}` |
| `PUT /users/{sub}/admin` | `{"admin": true\|false}` | `{"sub", "is_admin"}` |
| `PUT /users/{sub}/enabled` | `{"enabled": true\|false}` | `{"sub", "enabled"}` |
| `POST /users/{sub}/password-reset` | 本人へリセットコードをメール | `{"sub", "email"}` |
| `DELETE /users/{sub}` | 削除 | 204（本文なし） |
| `GET /health` | 疎通確認（admin 専用） | `{"status": "ok"}` |

`users[]` の 1 件: `{sub, email, status, enabled, is_admin, created_at, updated_at}`。
`status` は Cognito の `UserStatus` そのまま、`created_at` / `updated_at` は UTC の ISO 8601。

## 4. エラー

| HTTP | code | 契機 |
| --- | --- | --- |
| 400 | `VALIDATION_ERROR` | 本文が JSON オブジェクトでない、`admin` / `enabled` が真偽値でない |
| 401 | `UNAUTHORIZED` | JWT クレームが無い（通常は API Gateway が先に落とす） |
| 403 | `FORBIDDEN` | `admin` グループでない |
| 403 | `ORIGIN_FORBIDDEN` | `x-origin-verify` 不一致（`execute-api` 直叩き） |
| 403 | `SELF_OPERATION_FORBIDDEN` | 自分の admin 解除・無効化・削除 |
| 404 | `USER_NOT_FOUND` | `sub` に一致するユーザーが居ない（`sub` の形でない場合も含む） |
| 409 | `LAST_ADMIN` | admin グループの最後の 1 人から admin を外そうとした |
| 409 | `PASSWORD_RESET_NOT_ALLOWED` | Cognito がリセットを拒否（未確認・初期パスワード・無効化など） |

メッセージは日本語。画面側は既知のコードを辞書で置き換え、未知のコードは API の文言をそのまま出す。

## 5. 認可と自己保護

| 層 | 判定 |
| --- | --- |
| API Gateway | JWT authorizer（issuer / audience / 有効期限） |
| Lambda | `auth.require_group(claims, "admin")` を全ルートで実行する。ここだけが権限の実体 |
| Lambda（現在状態） | **毎リクエストで Cognito の現在状態を確認する**。呼び出し元（`claims.sub` から Username を解決）が `admin_get_user` で有効、かつ `admin_list_groups_for_user` で `admin` に所属していなければ 403 |
| Lambda（自己保護） | 対象 `sub` が `claims.sub` と同じなら admin **付与**・解除・無効化・削除を 403。解除・無効化・削除は自分を締め出すと `add-admin.sh` まで戻らないと復旧できないため。付与は、admin を外された本人が残ったクレームで自分へ戻すのを塞ぐため |
| Lambda（最後の admin） | `list_users_in_group("admin")` の結果が対象 1 人だけなら解除を 409。呼び出し元も現在の admin なので通常は到達しないが、多重防御として残す |
| SPA | メニュー・ルートの `requiredGroups` とボタンの `disabled`（UX。権限の実体ではない） |

**ID トークンは最大 60 分有効**（core の `IdTokenValidity: 60`）。`cognito:groups` クレームは admin を外された後・
無効化された後もその間は admin のまま残るため、他人の権限を書き換えられるこの API だけは毎リクエストで
Cognito を引き直す（Cognito 呼び出しが 1 リクエストあたり 2 回増えるが、小規模では問題にならない）。
**他アプリの API はクレームだけで判定するので、剥奪・無効化の反映は最大 60 分遅れる**（プラットフォーム設計 6.3）。

同時実行は考慮しない。最後の admin 判定は読み取りと解除の間に別の解除が挟まると擦り抜け得るが、
数人が手で押す運用であり、admin が 0 人になっても `scripts/add-admin.sh` の退路がある。

`sub` から Username を引くのは `list_users(Filter='sub = "<sub>"')`。User Pool は `UsernameAttributes: [email]` なので
Username は生成 ID で、メールは別名にすぎない（画面から来た文字列を Username として渡さない）。
`sub` は UUID の形だけを通してから Filter 式へ埋め込む。

## 6. IAM とログ

Lambda ロールに与える Cognito の権限は次の 10 アクションのみ。Resource は
`arn:aws:cognito-idp:{region}:{account}:userpool/${UserPoolId}` の 1 本で、ワイルドカードを使わない
（`tests/infra/test_admin_users_template.py` が完全一致を検査する）。

```text
ListUsers / ListUsersInGroup / AdminGetUser / AdminListGroupsForUser / AdminAddUserToGroup
AdminRemoveUserFromGroup / AdminEnableUser / AdminDisableUser / AdminResetUserPassword / AdminDeleteUser
```

| アクション | 用途 |
| --- | --- |
| `ListUsers` | 一覧（ページング）と `sub` からの Username 解決（`Filter='sub = "..."'`） |
| `ListUsersInGroup` | 一覧の `is_admin` 判定と最後の admin 判定 |
| `AdminGetUser` / `AdminListGroupsForUser` | **呼び出し元の現在状態の確認**（有効か・`admin` に所属しているか。5 章） |
| `AdminAddUserToGroup` / `AdminRemoveUserFromGroup` | admin の付与・解除 |
| `AdminEnableUser` / `AdminDisableUser` | 有効化・無効化 |
| `AdminResetUserPassword` | パスワードリセット（本人へコードをメール） |
| `AdminDeleteUser` | 削除 |

ほかに読むのは `/example-backoffice/{env}/edge/origin-verify-secret`（SecureString、`kms:Decrypt` 付き）だけ。
DynamoDB テーブル・SQS・S3・バッチ・ステートマシンは持たない。

操作の証跡は Lambda の構造化ログ（`LambdaLogger`、`/aws/lambda/example-backoffice-admin-users-api-{env}`、保持 365 日）。

```json
{"operation": "revoke_admin", "actor_sub": "...", "actor_email": "...",
 "target_sub": "...", "target_email": "...", "result": "LAST_ADMIN"}
```

`operation` は `list_users` / `grant_admin` / `revoke_admin` / `enable_user` / `disable_user` / `reset_password` / `delete_user`。
`result` は `ok` か拒否コード。トークン・秘密は出さない。画面での閲覧は今回対象外（CloudWatch Logs Insights で見る）。

## 7. 構成

| 種別 | 値 |
| --- | --- |
| スタック | `example-backoffice-app-admin-users-{env}` |
| Lambda | `example-backoffice-admin-users-api-{env}`（`Handler: admin_users.app.handler`） |
| SSM（読む） | `/cognito/issuer` `/cognito/client-id` `/cognito/user-pool-id` `/shared/layer-arn` |
| SSM（書く） | `/apps/admin-users/api-domain` |
| edge | origin `api-admin-users` + behavior `/api/admin-users/*`（`edge-guard` 付き） |

ハンドラのモジュール名を雛形の `api.app` から `admin_users.app` へ変えている。全アプリのテストを 1 つの
pytest セッションで走らせると `api` という同名パッケージが sys.modules で衝突するため（ADD-APP-GUIDE 2 章ステップ 4-7）。

## 8. 運用

- **最初の管理者は画面から作れない。** `scripts/add-admin.sh {env} {email}` で付ける（画面が使えないときの退路でもある）。
- admin の付与・解除が効くのは本人の次回サインイン（ID トークンの再取得）から。
- パスワードリセットは本人にコードをメールするだけで、新しいパスワードは本人がサインイン画面の
  「パスワードを忘れた場合」から設定する。管理者はパスワードを知らない。
- 無効化はサインインを止めるだけでアカウントは残る。削除は取り消せない（実行履歴の `sub` は残る）。
- **無効化・削除しても発行済みの ID トークンは最大 60 分有効**なので、他アプリの画面はその間動き得る（このユーザー管理 API だけは現在状態を毎回確認して即座に拒否する）。確認ダイアログにもこの旨を出す。即時失効が要るなら `AdminUserGlobalSignOut` の追加を別途検討する。
- TOTP の解除だけは画面に無い。プラットフォーム設計 6.3 の runbook（`admin-set-user-mfa-preference`）で行う。
