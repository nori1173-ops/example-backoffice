<script setup lang="ts">
import { onActivated, ref } from 'vue'
import { setUpTOTP, updateMFAPreference, verifyTOTPSetup } from 'aws-amplify/auth'
import TotpEnroll from '@/components/TotpEnroll.vue'
import { useAuthStore } from '@/stores/auth'
import { useUiStore } from '@/stores/ui'

const TOTP_ISSUER = 'example-backoffice'

const auth = useAuthStore()
const ui = useUiStore()

const setupUri = ref('')
const sharedSecret = ref('')
const isLoading = ref(false)
const isEnrolled = ref(false)
const error = ref<string | null>(null)

async function startEnrollment(): Promise<void> {
  isLoading.value = true
  isEnrolled.value = false
  error.value = null
  setupUri.value = ''
  sharedSecret.value = ''
  try {
    const details = await setUpTOTP()
    sharedSecret.value = details.sharedSecret
    setupUri.value = details.getSetupUri(TOTP_ISSUER, auth.email ?? '').toString()
  } catch {
    error.value = '認証アプリの登録を開始できませんでした。時間をおいて再試行してください'
  } finally {
    isLoading.value = false
  }
}

async function verify(code: string): Promise<void> {
  isLoading.value = true
  error.value = null
  try {
    await verifyTOTPSetup({ code })
    await updateMFAPreference({ totp: 'PREFERRED' })
    setupUri.value = ''
    sharedSecret.value = ''
    isEnrolled.value = true
    ui.notify('認証アプリを登録しました', 'success')
  } catch {
    error.value = 'コードが正しくありません。認証アプリの表示を確認して入力し直してください'
  } finally {
    isLoading.value = false
  }
}

// ShellLayout の keep-alive 配下なので、2 回目以降の訪問でも初期化されるよう activated で開始する
onActivated(startEnrollment)
</script>

<template>
  <div>
    <h1 class="text-h5 mb-1">
      セキュリティ
    </h1>
    <p class="text-body-2 text-medium-emphasis mb-6">
      サインイン時の二要素認証（認証アプリの 6 桁コード）を設定します。
    </p>

    <v-card
      max-width="480"
      border
      flat
    >
      <v-card-text class="pa-6">
        <v-alert
          v-if="error"
          type="error"
          variant="tonal"
          class="mb-4"
        >
          {{ error }}
        </v-alert>

        <v-alert
          v-if="isEnrolled"
          type="success"
          variant="tonal"
        >
          認証アプリを登録しました。次回のサインインから 6 桁のコードが必要になります。
        </v-alert>

        <TotpEnroll
          v-else-if="setupUri"
          :setup-uri="setupUri"
          :shared-secret="sharedSecret"
          :loading="isLoading"
          @submit="verify"
        />

        <div
          v-else-if="isLoading"
          class="d-flex justify-center py-8"
        >
          <v-progress-circular
            indeterminate
            color="primary"
          />
        </div>

        <v-btn
          v-else
          color="primary"
          variant="tonal"
          block
          @click="startEnrollment"
        >
          再試行する
        </v-btn>
      </v-card-text>
    </v-card>
  </div>
</template>
