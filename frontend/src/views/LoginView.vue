<script setup lang="ts">
import { computed, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import type { SubmitEventPromise } from 'vuetify'
import ResendCodeButton from '@/components/ResendCodeButton.vue'
import TotpEnroll from '@/components/TotpEnroll.vue'
import { useAuthStore } from '@/stores/auth'
import { passwordPolicyRule } from '@/utils/password'

const SESSION_FAILED = 'サインインは完了しましたが、セッションを取得できませんでした。もう一度お試しください'
const CONFIRMED = 'メールアドレスの確認が完了しました。サインインしてください'

const auth = useAuthStore()
const route = useRoute()
const router = useRouter()

const email = ref('')
const password = ref('')
const showPassword = ref(false)
const totpCode = ref('')
const newPassword = ref('')
const signupCode = ref('')
const localError = ref<string | null>(null)
const notice = ref<string | null>(null)
const unconfirmedEmail = ref<string | null>(null)

const step = computed(() => auth.pending?.kind ?? 'password')

const totpSetup = computed(() => {
  const current = auth.pending
  return current?.kind === 'totp-setup' ? current : null
})

/** サインアップ画面から持ち越された pending には username が入っているのでそれを優先する */
const confirmTarget = computed(() => {
  const current = auth.pending
  return current?.kind === 'confirm-signup' ? current.username : email.value
})

const message = computed(() => auth.error ?? localError.value)

const emailRules = [
  (value: string) => !!value || 'メールアドレスを入力してください',
  (value: string) => /^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(value) || 'メールアドレスの形式が正しくありません',
]

const passwordRules = [(value: string) => !!value || 'パスワードを入力してください']

const newPasswordRules = [
  (value: string) => !!value || '新しいパスワードを入力してください',
  passwordPolicyRule,
]

const codeRules = [
  (value: string) => !!value || 'コードを入力してください',
  (value: string) => /^\d{6}$/.test(value) || '6 桁の数字を入力してください',
]

function dismiss(): void {
  auth.error = null
  localError.value = null
}

/** どのステップからでもパスワード入力に戻れるようにする（認証アプリ紛失時などの復帰手段） */
function restart(): void {
  auth.pending = null
  auth.error = null
  localError.value = null
  notice.value = null
  unconfirmedEmail.value = null
  password.value = ''
  totpCode.value = ''
  newPassword.value = ''
  signupCode.value = ''
}

/**
 * `login()` / `submitChallenge()` はチャレンジ待ちでも解決するため、解決＝成功と見なさない。
 * `pending` が残っていれば次のステップへ進み、サインイン済みのときだけ遷移する。
 */
async function proceed(): Promise<void> {
  if (auth.pending !== null) {
    return
  }
  if (!auth.isAuthenticated) {
    localError.value = SESSION_FAILED
    return
  }
  const redirect = route.query.redirect
  const safe =
    typeof redirect === 'string' && redirect.startsWith('/') && !redirect.startsWith('//')
  await router.push(safe ? redirect : { name: 'home' })
}

async function submitPassword(event: SubmitEventPromise): Promise<void> {
  const { valid } = await event
  if (!valid) {
    return
  }
  localError.value = null
  notice.value = null
  unconfirmedEmail.value = null
  try {
    await auth.login(email.value, password.value)
  } catch (e: unknown) {
    // 未確認のまま詰まらないよう、確認コード入力へ戻れる導線を出す
    if (e instanceof Error && e.name === 'UserNotConfirmedException') {
      unconfirmedEmail.value = email.value
    }
    return
  }
  await proceed()
}

function goToConfirmSignup(): void {
  const username = unconfirmedEmail.value
  if (!username) {
    return
  }
  auth.error = null
  localError.value = null
  notice.value = null
  signupCode.value = ''
  auth.pending = { kind: 'confirm-signup', username }
}

async function resend(): Promise<void> {
  localError.value = null
  notice.value = null
  try {
    await auth.resendCode(confirmTarget.value)
  } catch {
    return
  }
  notice.value = '確認コードを再送しました'
}

async function submitChallenge(response: string): Promise<void> {
  localError.value = null
  notice.value = null
  try {
    await auth.submitChallenge(response, email.value)
  } catch {
    return
  }
  await proceed()
}

async function submitTotpCode(event: SubmitEventPromise): Promise<void> {
  const { valid } = await event
  if (valid) {
    await submitChallenge(totpCode.value)
  }
}

async function submitNewPassword(event: SubmitEventPromise): Promise<void> {
  const { valid } = await event
  if (valid) {
    await submitChallenge(newPassword.value)
  }
}

async function submitSignupCode(event: SubmitEventPromise): Promise<void> {
  const { valid } = await event
  if (!valid) {
    return
  }
  const username = confirmTarget.value
  localError.value = null
  notice.value = null
  try {
    await auth.confirmRegistration(username, signupCode.value)
  } catch {
    return
  }
  unconfirmedEmail.value = null
  // サインアップ画面から持ち越された pending にはパスワードが伴わないため、自動サインインしない
  if (!password.value) {
    auth.pending = null
    email.value = username
    signupCode.value = ''
    notice.value = CONFIRMED
    return
  }
  try {
    await auth.login(email.value, password.value)
  } catch {
    return
  }
  await proceed()
}
</script>

<template>
  <div>
    <v-alert
      v-if="message"
      type="error"
      variant="tonal"
      closable
      class="mb-4"
      @click:close="dismiss"
    >
      {{ message }}
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
      <v-window-item value="password">
        <v-form @submit.prevent="submitPassword">
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
            :type="showPassword ? 'text' : 'password'"
            autocomplete="current-password"
            :rules="passwordRules"
            prepend-inner-icon="mdi-lock-outline"
            :append-inner-icon="showPassword ? 'mdi-eye-off-outline' : 'mdi-eye-outline'"
            class="mb-2"
            @click:append-inner="showPassword = !showPassword"
          />
          <div class="text-end text-body-2 mb-4">
            <RouterLink :to="{ name: 'forgot-password' }">
              パスワードをお忘れの方
            </RouterLink>
          </div>
          <v-btn
            type="submit"
            color="primary"
            block
            size="large"
            :loading="auth.isLoading"
          >
            サインイン
          </v-btn>
          <v-btn
            v-if="unconfirmedEmail"
            variant="tonal"
            color="primary"
            block
            class="mt-3"
            prepend-icon="mdi-email-check-outline"
            @click="goToConfirmSignup"
          >
            確認コードを入力する
          </v-btn>
        </v-form>
      </v-window-item>

      <v-window-item value="totp-code">
        <v-form @submit.prevent="submitTotpCode">
          <p class="text-body-2 mb-4">
            認証アプリに表示されている 6 桁のコードを入力してください。
          </p>
          <v-text-field
            v-model="totpCode"
            label="認証コード（6 桁）"
            autocomplete="one-time-code"
            inputmode="numeric"
            maxlength="6"
            :rules="codeRules"
            prepend-inner-icon="mdi-shield-key-outline"
            class="mb-4"
          />
          <v-btn
            type="submit"
            color="primary"
            block
            size="large"
            :loading="auth.isLoading"
          >
            コードを確認
          </v-btn>
        </v-form>
      </v-window-item>

      <v-window-item value="totp-setup">
        <TotpEnroll
          v-if="totpSetup"
          :setup-uri="totpSetup.setupUri"
          :shared-secret="totpSetup.sharedSecret"
          :loading="auth.isLoading"
          @submit="submitChallenge"
        />
      </v-window-item>

      <v-window-item value="new-password">
        <v-form @submit.prevent="submitNewPassword">
          <p class="text-body-2 mb-4">
            初回サインインのため、新しいパスワードを設定してください。
          </p>
          <v-text-field
            v-model="newPassword"
            label="新しいパスワード"
            type="password"
            autocomplete="new-password"
            :rules="newPasswordRules"
            prepend-inner-icon="mdi-lock-reset"
            class="mb-4"
          />
          <v-btn
            type="submit"
            color="primary"
            block
            size="large"
            :loading="auth.isLoading"
          >
            パスワードを設定
          </v-btn>
        </v-form>
      </v-window-item>

      <v-window-item value="confirm-signup">
        <v-form @submit.prevent="submitSignupCode">
          <p class="text-body-2 mb-4">
            {{ confirmTarget }} 宛に送信された 6 桁の確認コードを入力してください。
          </p>
          <v-text-field
            v-model="signupCode"
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
            確認してサインイン
          </v-btn>
          <ResendCodeButton
            class="mt-2"
            :loading="auth.isLoading"
            @resend="resend"
          />
        </v-form>
      </v-window-item>
    </v-window>

    <div
      v-if="step === 'password'"
      class="text-center text-body-2 mt-6"
    >
      アカウントが未登録の場合は
      <RouterLink :to="{ name: 'signup' }">
        新規登録
      </RouterLink>
    </div>
    <div
      v-else
      class="text-center mt-6"
    >
      <v-btn
        variant="text"
        size="small"
        prepend-icon="mdi-arrow-left"
        @click="restart"
      >
        最初からやり直す
      </v-btn>
    </div>
  </div>
</template>
