<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { fetchHealth } from '../api'
import { extractApiError } from '@/services/http'

const status = ref<string | null>(null)
const errorMessage = ref('')
const loading = ref(false)

async function load(): Promise<void> {
  loading.value = true
  errorMessage.value = ''
  try {
    status.value = (await fetchHealth())?.status ?? null
  } catch (error) {
    errorMessage.value = extractApiError(error)?.message ?? 'API の呼び出しに失敗しました'
  } finally {
    loading.value = false
  }
}

onMounted(load)
</script>

<template>
  <v-card
    variant="flat"
    border
  >
    <v-card-item>
      <v-card-title>一覧</v-card-title>
      <v-card-subtitle>アプリ雛形の初期画面。この中身を業務画面に置き換える</v-card-subtitle>
    </v-card-item>
    <v-card-text>
      <v-alert
        v-if="errorMessage"
        type="error"
        variant="tonal"
        data-testid="sample-app-error"
      >
        {{ errorMessage }}
      </v-alert>
      <div
        v-else
        data-testid="sample-app-health"
      >
        API の状態: {{ loading ? '確認中…' : (status ?? '不明') }}
      </div>
    </v-card-text>
    <v-card-actions>
      <v-btn
        :loading="loading"
        variant="tonal"
        data-testid="sample-app-reload"
        @click="load"
      >
        再読み込み
      </v-btn>
    </v-card-actions>
  </v-card>
</template>
