<script setup lang="ts">
import { onActivated, ref } from 'vue'
import {
  fetchMFAPreference,
  setUpTOTP,
  updateMFAPreference,
  verifyTOTPSetup,
} from 'aws-amplify/auth'
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
const isTotpEnabled = ref(false)
const isConfirmingReenroll = ref(false)
const error = ref<string | null>(null)

function reset(): void {
  isEnrolled.value = false
  isConfirmingReenroll.value = false
  error.value = null
  setupUri.value = ''
  sharedSecret.value = ''
}

async function startEnrollment(): Promise<void> {
  reset()
  isLoading.value = true
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

/** 登録済みの人に QR を出すと既存の認証アプリが使えなくなるため、状態を先に確かめる */
async function load(): Promise<void> {
  reset()
  isLoading.value = true
  try {
    const preference = await fetchMFAPreference()
    isTotpEnabled.value = preference.enabled?.includes('TOTP') ?? false
  } catch {
    isTotpEnabled.value = false
    error.value = '登録状況を取得できませんでした。時間をおいて再試行してください'
    return
  } finally {
    isLoading.value = false
  }
  if (!isTotpEnabled.value) {
    await startEnrollment()
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
    isTotpEnabled.value = true
    ui.notify('認証アプリを登録しました', 'success')
  } catch {
    error.value = 'コードが正しくありません。認証アプリの表示を確認して入力し直してください'
  } finally {
    isLoading.value = false
  }
}

// ShellLayout の keep-alive 配下なので、2 回目以降の訪問でも初期化されるよう activated で開始する
onActivated(load)
</script>

<template>
  <div>
    <h1 class="text-h5 mb-1">
      セキュリティ
    </h1>
    <p class="text-body-2 text-medium-emphasis mb-6">
      サインインには認証アプリの 6 桁コード（二要素認証）が必須です。ここで使う認証アプリを登録します。
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

        <template v-else-if="isTotpEnabled">
          <v-alert
            type="success"
            variant="tonal"
            class="mb-4"
          >
            認証アプリは登録済みです。サインイン時に認証アプリの 6 桁コードを入力してください。
          </v-alert>

          <v-alert
            v-if="isConfirmingReenroll"
            type="warning"
            variant="tonal"
          >
            既存の認証アプリのエントリは使えなくなります。新しい QR コードを登録し直してください。
            <div class="d-flex ga-2 mt-3">
              <v-btn
                color="warning"
                variant="flat"
                size="small"
                data-test="reenroll-confirm"
                @click="startEnrollment"
              >
                登録し直す
              </v-btn>
              <v-btn
                variant="text"
                size="small"
                @click="isConfirmingReenroll = false"
              >
                やめる
              </v-btn>
            </div>
          </v-alert>
          <v-btn
            v-else
            variant="tonal"
            color="primary"
            block
            data-test="reenroll"
            @click="isConfirmingReenroll = true"
          >
            認証アプリを登録し直す
          </v-btn>
        </template>

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
          @click="load"
        >
          再試行する
        </v-btn>

        <p class="text-caption text-medium-emphasis mt-4 mb-0">
          認証アプリを失くしたときは情報システム担当へ連絡してください。
        </p>
      </v-card-text>
    </v-card>
  </div>
</template>
