<script setup lang="ts">
import { computed, ref } from 'vue'
import HelpTip from '@/components/HelpTip.vue'
import { formatDateTime } from '@/utils/format'
import { HELP } from '../help'
import type { PromptPreset } from '../types'
import {
  charCount, effectivePrompt, formatChars, newId, PRESET_LIMIT, PRESET_NAME_MAX_CHARS, PRESET_TOTAL_MAX_CHARS,
  presetNameError, presetTotalChars, PROMPT_MAX_CHARS, promptExcerpt, samePrompt,
} from '../utils/prompt'

const props = defineProps<{ presets: PromptPreset[]; currentPrompt: string; defaultPrompt: string; editable: boolean }>()
const emit = defineEmits<{ 'update:presets': [presets: PromptPreset[]]; apply: [prompt: string] }>()

const JST_OFFSET_MS = 9 * 60 * 60 * 1000
const nameOpen = ref(false)
const nameTarget = ref<PromptPreset | null>(null)
const nameValue = ref('')
const confirmOpen = ref(false)
const confirmAction = ref<{ kind: 'overwrite' | 'delete'; preset: PromptPreset } | null>(null)

const storable = computed(() => !!props.currentPrompt.trim() && charCount(props.currentPrompt) <= PROMPT_MAX_CHARS)
const full = computed(() => props.presets.length >= PRESET_LIMIT)
const totalChars = computed(() => presetTotalChars(props.presets))
const currentChars = computed(() => charCount(props.currentPrompt.trim()))
const overTotal = computed(() => totalChars.value > PRESET_TOTAL_MAX_CHARS)
const createFits = computed(() => totalChars.value + currentChars.value <= PRESET_TOTAL_MAX_CHARS)
const canCreate = computed(() => props.editable && storable.value && !full.value && createFits.value)
const overwriteFits = (preset: PromptPreset) =>
  totalChars.value - charCount(preset.prompt.trim()) + currentChars.value <= PRESET_TOTAL_MAX_CHARS
const canOverwrite = (preset: PromptPreset) => props.editable && storable.value && overwriteFits(preset)
const defaultActive = computed(() => effectivePrompt(props.currentPrompt, props.defaultPrompt) === '')
const nameError = computed(() => presetNameError(nameValue.value, props.presets, nameTarget.value?.id))
const confirmMessage = computed(() => {
  const action = confirmAction.value
  if (!action) return ''
  return action.kind === 'delete' ? `${action.preset.name} を削除します` : `${action.preset.name} を現在の文面で上書きします`
})
const isActive = (prompt: string) => samePrompt(props.currentPrompt, prompt)
function updatedAt(value: string): string {
  const parsed = Date.parse(value)
  return Number.isNaN(parsed) ? value || '—' : formatDateTime(new Date(parsed + JST_OFFSET_MS).toISOString())
}
const now = () => new Date().toISOString()

function apply(prompt: string): void {
  if (props.editable) emit('apply', prompt)
}
function openCreate(): void {
  if (!canCreate.value) return
  nameTarget.value = null
  nameValue.value = ''
  nameOpen.value = true
}
function openRename(preset: PromptPreset): void {
  if (!props.editable) return
  nameTarget.value = preset
  nameValue.value = preset.name
  nameOpen.value = true
}
function confirmName(): void {
  if (nameError.value || !props.editable) return
  const name = nameValue.value.trim()
  const target = nameTarget.value
  if (target) {
    emit('update:presets', props.presets.map(preset => preset.id === target.id ? { ...preset, name } : preset))
  } else if (canCreate.value) {
    emit('update:presets', [...props.presets, { id: newId(), name, prompt: props.currentPrompt, updated_at: now() }])
  }
  nameOpen.value = false
}
function openConfirm(kind: 'overwrite' | 'delete', preset: PromptPreset): void {
  if (!props.editable || (kind === 'overwrite' && !canOverwrite(preset))) return
  confirmAction.value = { kind, preset }
  confirmOpen.value = true
}
function runConfirm(): void {
  const action = confirmAction.value
  confirmOpen.value = false
  if (!action || !props.editable) return
  if (action.kind === 'delete') {
    emit('update:presets', props.presets.filter(preset => preset.id !== action.preset.id))
  } else if (canOverwrite(action.preset)) {
    emit('update:presets', props.presets.map(preset => preset.id === action.preset.id
      ? { ...preset, prompt: props.currentPrompt, updated_at: now() }
      : preset))
  }
}
</script>

<template>
  <div
    data-test="prompt-presets"
    class="mt-4"
  >
    <div class="d-flex align-center flex-wrap ga-2 mb-2">
      <HelpTip :text="HELP.ai_prompt_presets">
        <template #default="{ props: tip }">
          <h3
            v-bind="tip"
            class="text-subtitle-1"
          >
            名前付きプロンプト（{{ presets.length }} / {{ PRESET_LIMIT }} 件）
          </h3>
        </template>
      </HelpTip>
      <span
        data-test="preset-total"
        class="text-body-2"
        :class="overTotal ? 'text-error' : 'text-medium-emphasis'"
      >
        合計 {{ formatChars(totalChars) }} / {{ formatChars(PRESET_TOTAL_MAX_CHARS) }} 字
      </span>
      <v-spacer />
      <span
        v-if="full"
        data-test="preset-limit"
        class="text-caption text-medium-emphasis"
      >
        {{ PRESET_LIMIT }} 件まで
      </span>
      <span
        v-if="!createFits"
        data-test="preset-total-limit"
        class="text-caption text-medium-emphasis"
      >
        合計 {{ formatChars(PRESET_TOTAL_MAX_CHARS) }} 字まで
      </span>
      <v-btn
        data-test="preset-new"
        prepend-icon="mdi-content-save-plus-outline"
        variant="tonal"
        :disabled="!canCreate"
        @click="openCreate"
      >
        現在の文面を新規保存
      </v-btn>
    </div>
    <v-row dense>
      <v-col
        cols="12"
        md="6"
      >
        <v-card
          data-test="preset-default"
          variant="outlined"
          class="h-100 d-flex flex-column"
        >
          <v-card-text class="flex-grow-1">
            <div class="d-flex align-center flex-wrap ga-2 mb-1">
              <span class="text-subtitle-1 font-weight-medium">既定のプロンプト</span>
              <v-chip
                size="small"
                color="primary"
                variant="tonal"
              >
                既定
              </v-chip>
              <v-chip
                v-if="defaultActive"
                data-test="preset-active"
                size="small"
                color="success"
              >
                使用中
              </v-chip>
            </div>
            <div class="text-caption text-medium-emphasis mb-2">
              コードの既定です（消せません）
            </div>
            <div class="preset-body text-body-2">
              {{ promptExcerpt(defaultPrompt) || '—' }}
            </div>
          </v-card-text>
          <v-card-actions>
            <v-btn
              data-test="preset-apply"
              variant="text"
              :disabled="!editable"
              @click="apply(defaultPrompt)"
            >
              適用
            </v-btn>
          </v-card-actions>
        </v-card>
      </v-col>
      <v-col
        v-for="preset in presets"
        :key="preset.id"
        cols="12"
        md="6"
      >
        <v-card
          data-test="preset-card"
          variant="outlined"
          class="h-100 d-flex flex-column"
        >
          <v-card-text class="flex-grow-1">
            <div class="d-flex align-center flex-wrap ga-2 mb-1">
              <span
                data-test="preset-name"
                class="text-subtitle-1 font-weight-medium preset-name"
              >{{ preset.name }}</span>
              <v-chip
                v-if="isActive(preset.prompt)"
                data-test="preset-active"
                size="small"
                color="success"
              >
                使用中
              </v-chip>
            </div>
            <div class="text-caption text-medium-emphasis mb-2">
              更新 {{ updatedAt(preset.updated_at) }}
            </div>
            <div class="preset-body text-body-2">
              {{ promptExcerpt(preset.prompt) }}
            </div>
          </v-card-text>
          <v-card-actions class="flex-wrap">
            <v-btn
              data-test="preset-apply"
              variant="text"
              :disabled="!editable"
              @click="apply(preset.prompt)"
            >
              適用
            </v-btn>
            <v-btn
              data-test="preset-overwrite"
              variant="text"
              :disabled="!canOverwrite(preset)"
              @click="openConfirm('overwrite', preset)"
            >
              上書き保存
            </v-btn>
            <v-btn
              data-test="preset-rename"
              variant="text"
              :disabled="!editable"
              @click="openRename(preset)"
            >
              名前変更
            </v-btn>
            <v-btn
              data-test="preset-delete"
              variant="text"
              color="error"
              :disabled="!editable"
              @click="openConfirm('delete', preset)"
            >
              削除
            </v-btn>
            <span
              v-if="!overwriteFits(preset)"
              data-test="preset-overwrite-limit"
              class="text-caption text-medium-emphasis"
            >
              合計 {{ formatChars(PRESET_TOTAL_MAX_CHARS) }} 字まで
            </span>
          </v-card-actions>
        </v-card>
      </v-col>
    </v-row>
    <p class="text-caption text-medium-emphasis mt-2">
      カードの変更は設定の「保存」で反映されます
    </p>
    <v-dialog
      v-model="nameOpen"
      max-width="480"
    >
      <v-card data-test="preset-name-dialog">
        <v-card-title>{{ nameTarget ? '名前を変更' : '現在の文面を新規保存' }}</v-card-title>
        <v-card-text>
          <v-text-field
            v-model="nameValue"
            data-test="preset-name-input"
            label="名前"
            :counter="PRESET_NAME_MAX_CHARS"
            :counter-value="(value: string) => charCount(value.trim())"
            :error-messages="nameValue ? nameError : ''"
            variant="outlined"
            autofocus
            @keydown.enter.prevent="confirmName"
          />
          <p class="text-body-2 mt-2">
            カードの変更は設定の「保存」で反映されます。
          </p>
        </v-card-text>
        <v-card-actions>
          <v-spacer />
          <v-btn
            data-test="preset-name-cancel"
            @click="nameOpen = false"
          >
            キャンセル
          </v-btn>
          <v-btn
            data-test="preset-name-confirm"
            color="primary"
            variant="flat"
            :disabled="!!nameError"
            @click="confirmName"
          >
            {{ nameTarget ? '変更する' : '保存する' }}
          </v-btn>
        </v-card-actions>
      </v-card>
    </v-dialog>
    <v-dialog
      v-model="confirmOpen"
      max-width="480"
    >
      <v-card data-test="preset-confirm-dialog">
        <v-card-title>{{ confirmAction?.kind === 'delete' ? 'カードを削除' : 'カードを上書き保存' }}</v-card-title>
        <v-card-text>
          <p>{{ confirmMessage }}</p>
          <p class="text-body-2 mt-2">
            カードの変更は設定の「保存」で反映されます。
          </p>
        </v-card-text>
        <v-card-actions>
          <v-spacer />
          <v-btn
            data-test="preset-confirm-cancel"
            @click="confirmOpen = false"
          >
            キャンセル
          </v-btn>
          <v-btn
            data-test="preset-confirm"
            :color="confirmAction?.kind === 'delete' ? 'error' : 'primary'"
            variant="flat"
            @click="runConfirm"
          >
            {{ confirmAction?.kind === 'delete' ? '削除する' : '上書きする' }}
          </v-btn>
        </v-card-actions>
      </v-card>
    </v-dialog>
  </div>
</template>

<style scoped>
.preset-body { white-space: pre-line; overflow-wrap: anywhere; }
.preset-name { overflow-wrap: anywhere; }
</style>
