<script setup lang="ts">
// 取り消せない操作の直前に挟む共通ダイアログ。文言と実行ボタンの色だけを差し替える。
const open = defineModel<boolean>({ required: true })

withDefaults(
  defineProps<{
    title: string
    message: string
    /** 警告として強調したい補足（「この操作は取り消せません」など） */
    warning?: string
    confirmLabel?: string
    confirmColor?: string
    loading?: boolean
  }>(),
  { warning: '', confirmLabel: '実行', confirmColor: 'primary', loading: false },
)

const emit = defineEmits<{ confirm: [] }>()
</script>

<template>
  <v-dialog
    v-model="open"
    max-width="440"
    persistent
  >
    <v-card data-test="confirm-dialog">
      <v-card-item>
        <v-card-title data-test="confirm-title">
          {{ title }}
        </v-card-title>
      </v-card-item>
      <v-card-text>
        <p
          data-test="confirm-message"
          class="text-body-2 mb-0"
        >
          {{ message }}
        </p>
        <v-alert
          v-if="warning"
          data-test="confirm-warning"
          type="warning"
          variant="tonal"
          density="compact"
          class="mt-3"
        >
          {{ warning }}
        </v-alert>
      </v-card-text>
      <v-card-actions>
        <v-spacer />
        <v-btn
          data-test="confirm-cancel"
          variant="text"
          :disabled="loading"
          @click="open = false"
        >
          キャンセル
        </v-btn>
        <v-btn
          data-test="confirm-ok"
          :color="confirmColor"
          :loading="loading"
          variant="flat"
          @click="emit('confirm')"
        >
          {{ confirmLabel }}
        </v-btn>
      </v-card-actions>
    </v-card>
  </v-dialog>
</template>
