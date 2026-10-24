<script setup lang="ts">
import { computed, ref, useId, watch } from 'vue'

/** 候補 1 件。`value` は保存する値、`label` は画面に出す名前（ID は出さない）。 */
export interface SuggestItem {
  value: string
  label: string
  hint?: string
  keywords?: string[]
}

const MAX_ITEMS = 20
const NO_MATCH = '該当する候補がありません'
const LOADING = '候補を読み込んでいます…'
const NAVIGATION_KEYS = ['ArrowDown', 'ArrowUp', 'Enter', 'Escape']

const props = withDefaults(
  defineProps<{
    /** 候補を開く文字（`#` / `@`）。打つまで候補は開かない */
    trigger: string
    items: SuggestItem[]
    modelValue: string[]
    /** false なら 1 件で置き換える */
    multiple?: boolean
    label?: string
    hint?: string
    placeholder?: string
    disabled?: boolean
    loading?: boolean
    errorMessages?: string[]
    /** `items` に無い値のチップ表示。省略すると値をそのまま出す */
    fallbackLabel?: (value: string) => string
  }>(),
  {
    multiple: false,
    label: '',
    hint: '',
    placeholder: '',
    disabled: false,
    loading: false,
    errorMessages: () => [],
    fallbackLabel: undefined,
  },
)

const emit = defineEmits<{ 'update:modelValue': [string[]] }>()

const query = ref('')
const activeIndex = ref(0)
const fieldId = useId()
const listId = `${fieldId}-list`

/** 最後の語がトリガーで始まるときの、それ以降の絞り込み文字列。そうでなければ null。 */
const term = computed(() => {
  const lastWord = query.value.split(/\s/).at(-1) ?? ''
  return lastWord.startsWith(props.trigger) ? lastWord.slice(props.trigger.length) : null
})

const matches = computed(() => {
  if (term.value === null) {
    return []
  }
  const needle = term.value.toLowerCase()
  return props.items.filter((item) => haystack(item).includes(needle)).slice(0, MAX_ITEMS)
})

const open = computed({
  get: () => term.value !== null && !props.disabled,
  set: (value: boolean) => {
    if (!value) {
      close()
    }
  },
})

// 保存済みの値は重複しうる（例 `<!here> <!here>`）ため、キーも削除も添字で扱う
const chips = computed(() =>
  props.modelValue.map((value, index) => ({ key: `${index}:${value}`, index, label: labelOf(value) })),
)

const emptyLabel = computed(() => (props.loading ? LOADING : NO_MATCH))

const activeDescendant = computed(() =>
  open.value && matches.value.length ? optionId(activeIndex.value) : undefined,
)

function optionId(index: number): string {
  return `${fieldId}-option-${index}`
}

function haystack(item: SuggestItem): string {
  return [item.label, item.hint ?? '', ...(item.keywords ?? [])].join(' ').toLowerCase()
}

function labelOf(value: string): string {
  const item = props.items.find((candidate) => candidate.value === value)
  if (item) {
    return item.label
  }
  return props.fallbackLabel ? props.fallbackLabel(value) : value
}

function close(): void {
  query.value = ''
  activeIndex.value = 0
}

function select(item: SuggestItem): void {
  const next = props.multiple
    ? props.modelValue.includes(item.value)
      ? props.modelValue
      : [...props.modelValue, item.value]
    : [item.value]
  close()
  emit('update:modelValue', next)
}

function removeAt(index: number): void {
  emit(
    'update:modelValue',
    props.modelValue.filter((_, current) => current !== index),
  )
}

function move(step: number): void {
  const count = matches.value.length
  if (count) {
    activeIndex.value = (activeIndex.value + step + count) % count
  }
}

/**
 * 候補が開いている間の移動・決定は自前で処理する。v-menu の既定は活性化要素の
 * ↑↓ でメニュー内へフォーカスを移すため、伝播を止めて入力欄から離れないようにする。
 */
function onKeydown(event: KeyboardEvent): void {
  if (event.key === 'Backspace' && !query.value && props.modelValue.length) {
    event.preventDefault()
    emit('update:modelValue', props.modelValue.slice(0, -1))
    return
  }
  if (!open.value || !NAVIGATION_KEYS.includes(event.key)) {
    return
  }
  event.preventDefault()
  event.stopPropagation()
  if (event.key === 'ArrowDown') {
    move(1)
  } else if (event.key === 'ArrowUp') {
    move(-1)
  } else if (event.key === 'Escape') {
    close()
  } else {
    const item = matches.value[activeIndex.value]
    if (item) {
      select(item)
    }
  }
}

watch(term, () => {
  activeIndex.value = 0
})
</script>

<template>
  <div>
    <v-text-field
      v-model="query"
      :label="label"
      :hint="hint"
      :persistent-hint="!!hint"
      :placeholder="placeholder"
      :disabled="disabled"
      :loading="loading"
      :error-messages="errorMessages"
      autocomplete="off"
      density="comfortable"
      variant="outlined"
      role="combobox"
      aria-autocomplete="list"
      :aria-expanded="String(open)"
      :aria-controls="open ? listId : undefined"
      :aria-activedescendant="activeDescendant"
      @keydown="onKeydown"
    />

    <div
      v-if="chips.length"
      data-test="suggest-chips"
      class="d-flex flex-wrap ga-1 mt-1"
    >
      <v-chip
        v-for="chip in chips"
        :key="chip.key"
        size="small"
        :closable="!disabled"
        @click:close="removeAt(chip.index)"
      >
        {{ chip.label }}
      </v-chip>
    </div>

    <v-menu
      v-model="open"
      activator="parent"
      :open-on-click="false"
      :close-on-content-click="false"
      location="bottom start"
      max-height="320"
    >
      <v-list
        :id="listId"
        data-test="suggest-list"
        role="listbox"
        density="compact"
      >
        <v-list-item
          v-if="!matches.length"
          data-test="suggest-empty"
          :title="emptyLabel"
          disabled
        />
        <v-list-item
          v-for="(item, index) in matches"
          :id="optionId(index)"
          :key="item.value"
          data-test="suggest-option"
          role="option"
          :active="index === activeIndex"
          :aria-selected="String(index === activeIndex)"
          @click="select(item)"
        >
          <v-list-item-title data-test="suggest-label">
            {{ item.label }}
          </v-list-item-title>
          <v-list-item-subtitle v-if="item.hint">
            {{ item.hint }}
          </v-list-item-subtitle>
        </v-list-item>
      </v-list>
    </v-menu>
  </div>
</template>
