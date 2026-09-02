<script setup lang="ts">
import { computed, ref } from 'vue'
import type { SubmitEventPromise } from 'vuetify'
import ResendCodeButton from '@/components/ResendCodeButton.vue'
import { useAuthStore } from '@/stores/auth'
import { passwordPolicyRule } from '@/utils/password'

const auth = useAuthStore()

const email = ref('')
const password = ref('')
const confirmationCode = ref('')
const confirmed = ref(false)
const notice = ref<string | null>(null)

const step = computed(() => {
  if (confirmed.value) {
    return 'done'
  }
  return auth.pending?.kind === 'confirm-signup' ? 'confirm-signup' : 'register'
})

const emailRules = [
  (value: string) => !!value || 'メールアドレスを入力してください',
  (value: string) => /^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(value) || 'メールアドレスの形式が正しくありません',
  (value: string) => value.endsWith('@example.co.jp') || '社内メールアドレス（@example.co.jp）で登録してください',
]

const passwordRules = [
  (value: string) => !!value || 'パスワードを入力してください',
  passwordPolicyRule,
]

const codeRules = [
  (value: string) => !!value || '確認コードを入力してください',
  (value: string) => /^\d{6}$/.test(value) || '6 桁の数字を入力してください',
]

async function submitRegistration(event: SubmitEventPromise): Promise<void> {
  const { valid } = await event
  if (!valid) {
    return
  }
  try {
    await auth.register(email.value, password.value)
  } catch {
    return
  }
  confirmed.value = auth.pending === null
}

async function resend(): Promise<void> {
  notice.value = null
  try {
    await auth.resendCode(email.value)
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
    await auth.confirmRegistration(email.value, confirmationCode.value)
  } catch {
    return
  }
  confirmed.value = true
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
      <v-window-item value="register">
        <v-form @submit.prevent="submitRegistration">
          <v-text-field
            v-model="email"
            label="メールアドレス"
            type="email"
            autocomplete="username"
            :rules="emailRules"
            prepend-inner-icon="mdi-email-outline"
            class="mb-4"
          />
          <v-text-field
            v-model="password"
            label="パスワード"
            type="password"
            autocomplete="new-password"
            :rules="passwordRules"
            prepend-inner-icon="mdi-lock-outline"
            class="mb-4"
          />
          <v-btn
            type="submit"
            color="primary"
            block
            size="large"
            :loading="auth.isLoading"
          >
            登録する
          </v-btn>
        </v-form>
      </v-window-item>

      <v-window-item value="confirm-signup">
        <v-form @submit.prevent="submitConfirmation">
          <p class="text-body-2 mb-4">
            {{ email }} 宛に確認コードを送信しました。届いた 6 桁のコードを入力してください。
          </p>
          <v-text-field
            v-model="confirmationCode"
            label="確認コード（6 桁）"
            autocomplete="one-time-code"
            inputmode="numeric"
            maxlength="6"
            :rules="codeRules"
            prepend-inner-icon="mdi-email-check-outline"
            class="mb-4"
          />
          <v-btn
            type="submit"
            color="primary"
            block
            size="large"
            :loading="auth.isLoading"
          >
            確認する
          </v-btn>
          <ResendCodeButton
            class="mt-2"
            :loading="auth.isLoading"
            @resend="resend"
          />
          <p class="text-caption text-medium-emphasis text-center mt-2">
            メールが届かない場合は迷惑メールフォルダもご確認ください。
          </p>
        </v-form>
      </v-window-item>

      <v-window-item value="done">
        <v-alert
          type="success"
          variant="tonal"
          class="mb-4"
        >
          アカウントを登録しました。サインインしてください。
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
      v-if="step === 'register'"
      class="text-center text-body-2 mt-6"
    >
      登録済みの場合は
      <RouterLink :to="{ name: 'login' }">
        サインイン
      </RouterLink>
    </div>
  </div>
</template>
