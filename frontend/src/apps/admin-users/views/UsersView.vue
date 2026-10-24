<script setup lang="ts">
import { computed, onActivated, ref } from 'vue'
import ConfirmDialog from '@/components/ConfirmDialog.vue'
import { useUiStore } from '@/stores/ui'
import { DASH, formatDateTime } from '@/utils/format'
import { useUsersStore } from '../stores/users'
import type { AdminUser } from '../types'

/** API は UTC の ISO 8601 を返す。`formatDateTime` は壁時計として読むので JST に寄せてから渡す。 */
const JST_OFFSET_MS = 9 * 60 * 60 * 1000

const SELF_REASON = '自分自身には操作できません'
/** 発行済み ID トークンの寿命（core の `IdTokenValidity: 60` 分）。無効化・削除の効き方に関わる */
const TOKEN_LIFETIME_NOTE =
  '発行済みのサインイン状態は最大 60 分残ります（このユーザー管理 API は即時に拒否します）。'

const STATUS_LABELS: Record<string, string> = {
  CONFIRMED: '確認済み',
  UNCONFIRMED: '未確認',
  FORCE_CHANGE_PASSWORD: '初期パスワード',
  RESET_REQUIRED: 'リセット要',
}

interface PendingAction {
  title: string
  message: string
  warning?: string
  confirmLabel: string
  confirmColor: string
  success: string
  failure: string
  run: () => Promise<boolean>
}

const store = useUsersStore()
const ui = useUiStore()
const dialog = ref(false)
const working = ref(false)
const pending = ref<PendingAction | null>(null)

const isEmpty = computed(() => !store.loading && store.users.length === 0)

function isSelf(user: AdminUser): boolean {
  return user.sub === store.selfSub
}

function statusLabel(user: AdminUser): string {
  return STATUS_LABELS[user.status] ?? user.status
}

function formatJst(value: string | null): string {
  if (!value) {
    return DASH
  }
  const parsed = Date.parse(value)
  return Number.isNaN(parsed) ? value : formatDateTime(new Date(parsed + JST_OFFSET_MS).toISOString())
}

/** 自分への admin 付与・解除、無効化、削除はサーバも 403 で断る（画面はその予告）。 */
function adminDisabled(user: AdminUser): boolean {
  return isSelf(user)
}

function enabledDisabled(user: AdminUser): boolean {
  return isSelf(user) && user.enabled
}

function adminTooltip(user: AdminUser): string {
  if (adminDisabled(user)) {
    return SELF_REASON
  }
  return user.is_admin ? 'admin を解除' : 'admin を付与'
}

function enabledTooltip(user: AdminUser): string {
  if (enabledDisabled(user)) {
    return SELF_REASON
  }
  return user.enabled ? '無効化' : '有効化'
}

function ask(action: PendingAction): void {
  pending.value = action
  dialog.value = true
}

async function confirm(): Promise<void> {
  const action = pending.value
  if (!action) {
    return
  }
  working.value = true
  const succeeded = await action.run()
  working.value = false
  dialog.value = false
  pending.value = null
  ui.notify(succeeded ? action.success : (store.error ?? action.failure), succeeded ? 'success' : 'error')
}

function askAdmin(user: AdminUser): void {
  const admin = !user.is_admin
  ask({
    title: admin ? 'admin を付与しますか?' : 'admin を解除しますか?',
    message: admin
      ? `${user.email} に管理者権限（ユーザー管理・アプリ設定の編集）を付与します。`
      : `${user.email} から管理者権限を外します。`,
    warning: '反映は本人の次回サインイン（ID トークンの再取得）からです。',
    confirmLabel: admin ? '付与する' : '解除する',
    confirmColor: 'primary',
    success: admin ? `${user.email} に admin を付与しました` : `${user.email} の admin を解除しました`,
    failure: admin ? 'admin の付与に失敗しました' : 'admin の解除に失敗しました',
    run: () => store.setAdmin(user.sub, admin),
  })
}

function askEnabled(user: AdminUser): void {
  const enabled = !user.enabled
  ask({
    title: enabled ? 'ユーザーを有効化しますか?' : 'ユーザーを無効化しますか?',
    message: enabled
      ? `${user.email} がふたたびサインインできるようになります。`
      : `${user.email} はサインインできなくなります（アカウントは残ります）。`,
    warning: enabled ? undefined : TOKEN_LIFETIME_NOTE,
    confirmLabel: enabled ? '有効化する' : '無効化する',
    confirmColor: enabled ? 'primary' : 'warning',
    success: enabled ? `${user.email} を有効化しました` : `${user.email} を無効化しました`,
    failure: enabled ? '有効化に失敗しました' : '無効化に失敗しました',
    run: () => store.setEnabled(user.sub, enabled),
  })
}

function askPasswordReset(user: AdminUser): void {
  ask({
    title: 'パスワードをリセットしますか?',
    message: `${user.email} 宛にリセットコードをメールします。新しいパスワードは本人が設定します。`,
    confirmLabel: 'リセットする',
    confirmColor: 'primary',
    success: `${user.email} にリセットコードを送りました`,
    failure: 'パスワードリセットに失敗しました',
    run: () => store.resetPassword(user.sub),
  })
}

function askRemove(user: AdminUser): void {
  ask({
    title: 'ユーザーを削除しますか?',
    message: `${user.email} を User Pool から削除します。`,
    warning: `この操作は取り消せません。サインインできなくするだけなら無効化を使ってください。${TOKEN_LIFETIME_NOTE}`,
    confirmLabel: '削除する',
    confirmColor: 'error',
    success: `${user.email} を削除しました`,
    failure: 'ユーザーの削除に失敗しました',
    run: () => store.remove(user.sub),
  })
}

// keep-alive 配下では onMounted が再発火しないため、画面に入るたびに取り直す（UI-04）
onActivated(() => store.fetch())
</script>

<template>
  <div>
    <div class="d-flex align-center flex-wrap ga-3 mb-4">
      <div>
        <h1 class="text-h5 mb-1">
          ユーザー管理
        </h1>
        <p class="text-body-2 text-medium-emphasis mb-0">
          サインアップ済みユーザーの権限・有効/無効・パスワード・削除を管理します。
        </p>
      </div>
      <v-spacer />
      <v-btn
        data-test="refresh"
        variant="text"
        prepend-icon="mdi-refresh"
        :loading="store.loading"
        @click="store.fetch()"
      >
        更新
      </v-btn>
    </div>

    <v-alert
      v-if="store.error"
      data-test="error"
      type="error"
      variant="tonal"
      class="mb-4"
    >
      {{ store.error }}
    </v-alert>

    <v-alert
      v-if="isEmpty"
      data-test="empty"
      type="info"
      variant="tonal"
      class="mb-4"
    >
      ユーザーが 1 人もいません。
    </v-alert>

    <v-card
      variant="flat"
      border
    >
      <v-progress-linear
        :active="store.loading"
        indeterminate
        color="primary"
      />
      <v-table density="comfortable">
        <thead>
          <tr>
            <th>メールアドレス</th>
            <th class="text-no-wrap">
              状態
            </th>
            <th class="text-no-wrap">
              権限
            </th>
            <th class="text-no-wrap d-none d-lg-table-cell">
              登録日時
            </th>
            <th class="text-no-wrap d-none d-lg-table-cell">
              最終更新
            </th>
            <th class="text-no-wrap">
              操作
            </th>
          </tr>
        </thead>
        <tbody>
          <tr
            v-for="user in store.users"
            :key="user.sub"
            :data-test="`row-${user.sub}`"
          >
            <td class="text-no-wrap">
              {{ user.email }}
              <v-chip
                v-if="isSelf(user)"
                data-test="self-chip"
                size="x-small"
                class="ms-2"
              >
                自分
              </v-chip>
            </td>
            <td class="text-no-wrap">
              <v-chip
                :data-test="`enabled-chip-${user.sub}`"
                :color="user.enabled ? 'success' : 'grey'"
                size="small"
              >
                {{ user.enabled ? '有効' : '無効' }}
              </v-chip>
              <div class="text-caption text-medium-emphasis">
                {{ statusLabel(user) }}
              </div>
            </td>
            <td class="text-no-wrap">
              <v-chip
                :data-test="`admin-chip-${user.sub}`"
                :color="user.is_admin ? 'primary' : undefined"
                size="small"
              >
                {{ user.is_admin ? 'admin' : '一般' }}
              </v-chip>
              <v-tooltip
                :text="adminTooltip(user)"
                location="top"
              >
                <template #activator="{ props }">
                  <span v-bind="props">
                    <v-btn
                      :data-test="`admin-${user.sub}`"
                      :disabled="adminDisabled(user)"
                      :icon="user.is_admin ? 'mdi-shield-off-outline' : 'mdi-shield-account-outline'"
                      size="small"
                      variant="text"
                      @click="askAdmin(user)"
                    />
                  </span>
                </template>
              </v-tooltip>
            </td>
            <td class="text-no-wrap d-none d-lg-table-cell">
              {{ formatJst(user.created_at) }}
            </td>
            <td class="text-no-wrap d-none d-lg-table-cell">
              {{ formatJst(user.updated_at) }}
            </td>
            <td class="text-no-wrap">
              <v-tooltip
                :text="enabledTooltip(user)"
                location="top"
              >
                <template #activator="{ props }">
                  <span v-bind="props">
                    <v-btn
                      :data-test="`enabled-${user.sub}`"
                      :disabled="enabledDisabled(user)"
                      :icon="user.enabled ? 'mdi-account-off' : 'mdi-account-check'"
                      size="small"
                      variant="text"
                      @click="askEnabled(user)"
                    />
                  </span>
                </template>
              </v-tooltip>
              <v-tooltip
                text="パスワードリセット"
                location="top"
              >
                <template #activator="{ props }">
                  <span v-bind="props">
                    <v-btn
                      :data-test="`reset-${user.sub}`"
                      icon="mdi-lock-reset"
                      size="small"
                      variant="text"
                      @click="askPasswordReset(user)"
                    />
                  </span>
                </template>
              </v-tooltip>
              <v-tooltip
                :text="isSelf(user) ? SELF_REASON : '削除'"
                location="top"
              >
                <template #activator="{ props }">
                  <span v-bind="props">
                    <v-btn
                      :data-test="`delete-${user.sub}`"
                      :disabled="isSelf(user)"
                      color="error"
                      icon="mdi-delete"
                      size="small"
                      variant="text"
                      @click="askRemove(user)"
                    />
                  </span>
                </template>
              </v-tooltip>
            </td>
          </tr>
        </tbody>
      </v-table>
      <div
        data-test="table-note"
        class="text-caption text-medium-emphasis px-4 py-2"
      >
        パスワードリセットを行うと本人にリセットコードがメールで届きます。サインイン画面の「パスワードを忘れた場合」から新しいパスワードを設定してもらってください。操作は CloudWatch Logs に記録されます。
      </div>
    </v-card>

    <ConfirmDialog
      v-if="pending"
      v-model="dialog"
      :title="pending.title"
      :message="pending.message"
      :warning="pending.warning"
      :confirm-label="pending.confirmLabel"
      :confirm-color="pending.confirmColor"
      :loading="working"
      @confirm="confirm"
    />
  </div>
</template>
