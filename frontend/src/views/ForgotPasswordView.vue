<script setup lang="ts">
import { ref } from 'vue'
import type { SubmitEventPromise } from 'vuetify'
import ResendCodeButton from '@/components/ResendCodeButton.vue'
import { useAuthStore } from '@/stores/auth'
import { passwordPolicyRule } from '@/utils/password'

const auth = useAuthStore()

const email = ref('')
const code = ref('')
const newPassword = ref('')
const showPassword = ref(false)
const step = ref<'request' | 'confirm' | 'done'>('request')
const notice = ref<string | null>(null)

const emailRules = [
  (value: string) => !!value || 'メールアドレスを入力してください',
  (value: string) => /^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(value) || 'メールアドレスの形式が正しくありません',
]

const codeRules = [
  (value: string) => !!value || '確認コードを入力してください',
  (value: string) => /^\d{6}$/.test(value) || '6 桁の数字を入力してください',
]

const newPasswordRules = [
  (value: string) => !!value || '新しいパスワードを入力してください',
  passwordPolicyRule,
]

async function submitRequest(event: SubmitEventPromise): Promise<void> {
  const { valid } = await event
  if (!valid) {
    return
  }
  notice.value = null
  try {
    await auth.requestPasswordReset(email.value)
  } catch {
    return
  }
  step.value = 'confirm'
}

async function resend(): Promise<void> {
  notice.value = null
  try {
    await auth.requestPasswordReset(email.value)
  } catch {
    return
  }
  notice.value = '確認コードを再送しました'
}

async function submitConfirmation(event: SubmitEventPromise): Promise<void> {
  const { valid } = await event
  if (!valid) {
    return
  }
  notice.value = null
  try {
    await auth.submitPasswordReset(email.value, code.value, newPassword.value)
  } catch {
    return
  }
  code.value = ''
  newPassword.value = ''
  step.value = 'done'
}
</script>

<template>
  <div>
    <v-alert
      v-if="auth.error"
      type="error"
      variant="tonal"
      closable
      class="mb-4"
      @click:close="auth.error = null"
    >
      {{ auth.error }}
    </v-alert>

    <v-alert
      v-if="notice"
      type="success"
      variant="tonal"
      class="mb-4"
    >
      {{ notice }}
    </v-alert>

    <v-window :model-value="step">
      <v-window-item value="request">
        <v-form @submit.prevent="submitRequest">
          <p class="text-body-2 mb-4">
            登録済みのメールアドレスに、6 桁の確認コードを送信します。
          </p>
          <v-text-field
            v-model="email"
            label="メールアドレス"
            type="email"
            autocomplete="username"
            :rules="emailRules"
            prepend-inner-icon="mdi-email-outline"
            class="mb-4"
          />
          <v-btn
            type="submit"
            color="primary"
            block
            size="large"
            :loading="auth.isLoading"
            :disabled="auth.isLoading"
          >
            確認コードを送信
          </v-btn>
        </v-form>
      </v-window-item>

      <v-window-item value="confirm">
        <v-form @submit.prevent="submitConfirmation">
          <p class="text-body-2 mb-4">
            {{ email }} 宛に送信された 6 桁の確認コードと、新しいパスワードを入力してください。
          </p>
          <v-text-field
            v-model="code"
            label="確認コード（6 桁）"
            autocomplete="one-time-code"
            inputmode="numeric"
            maxlength="6"
            :rules="codeRules"
            prepend-inner-icon="mdi-email-check-outline"
            class="mb-4"
          />
          <v-text-field
            v-model="newPassword"
            label="新しいパスワード"
            :type="showPassword ? 'text' : 'password'"
            autocomplete="new-password"
            :rules="newPasswordRules"
            prepend-inner-icon="mdi-lock-reset"
            :append-inner-icon="showPassword ? 'mdi-eye-off-outline' : 'mdi-eye-outline'"
            class="mb-4"
            @click:append-inner="showPassword = !showPassword"
          />
          <v-btn
            type="submit"
            color="primary"
            block
            size="large"
            :loading="auth.isLoading"
            :disabled="auth.isLoading"
          >
            パスワードを再設定
          </v-btn>
          <ResendCodeButton
            class="mt-2"
            :loading="auth.isLoading"
            @resend="resend"
          />
        </v-form>
      </v-window-item>

      <v-window-item value="done">
        <v-alert
          type="success"
          variant="tonal"
          class="mb-4"
        >
          パスワードを再設定しました。新しいパスワードでサインインしてください。
        </v-alert>
        <v-btn
          color="primary"
          block
          size="large"
          :to="{ name: 'login' }"
        >
          サインインへ
        </v-btn>
      </v-window-item>
    </v-window>

    <div
      v-if="step !== 'done'"
      class="text-center text-body-2 mt-6"
    >
      <RouterLink :to="{ name: 'login' }">
        サインインに戻る
      </RouterLink>
    </div>
  </div>
</template>
