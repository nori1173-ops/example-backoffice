<script setup lang="ts">
import { ref, watch } from 'vue'
import QRCode from 'qrcode'
import type { SubmitEventPromise } from 'vuetify'

const props = withDefaults(
  defineProps<{
    setupUri: string
    sharedSecret: string
    loading?: boolean
  }>(),
  { loading: false },
)

const emit = defineEmits<{
  submit: [code: string]
}>()

const qrImage = ref('')
const code = ref('')

const codeRules = [
  (value: string) => !!value || '認証コードを入力してください',
  (value: string) => /^\d{6}$/.test(value) || '6 桁の数字を入力してください',
]

watch(
  () => props.setupUri,
  async (uri) => {
    if (!uri) {
      qrImage.value = ''
      return
    }
    const svg = await QRCode.toString(uri, { type: 'svg', margin: 1, width: 192 })
    qrImage.value = `data:image/svg+xml;utf8,${encodeURIComponent(svg)}`
  },
  { immediate: true },
)

async function onSubmit(event: SubmitEventPromise): Promise<void> {
  const { valid } = await event
  if (!valid || props.loading) {
    return
  }
  emit('submit', code.value)
}
</script>

<template>
  <div>
    <p class="text-body-2 mb-4">
      認証アプリ（Google Authenticator など）で QR コードを読み取り、表示された 6 桁のコードを入力してください。
    </p>

    <div class="d-flex justify-center mb-4">
      <img
        v-if="qrImage"
        :src="qrImage"
        width="192"
        height="192"
        alt="認証アプリ登録用の QR コード"
      >
    </div>

    <div class="text-caption text-medium-emphasis text-center">
      QR コードを読み取れない場合は次のキーを手入力してください
    </div>
    <div class="text-body-2 text-center font-weight-medium mb-4">
      {{ sharedSecret }}
    </div>

    <v-form @submit.prevent="onSubmit">
      <v-text-field
        v-model="code"
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
        :loading="loading"
        :disabled="loading"
      >
        登録する
      </v-btn>
    </v-form>
  </div>
</template>
