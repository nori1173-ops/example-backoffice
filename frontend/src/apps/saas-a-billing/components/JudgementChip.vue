<script lang="ts">
import type { Judgement } from '../types'

/** 「消えた地点」は `judgement` を持たないが、同じ体裁で並べるため列挙に加える（設計 6.5）。 */
export type ChipJudgement = Judgement | 'missing'

/** excel.py の COLOR_* と同値（設計 6.6）。課金だけ塗らない。 */
export const JUDGEMENT_FILLS: Record<ChipJudgement, string> = {
  billable: 'transparent',
  no_data: '#FDF3DC',
  excluded: '#DFE9F8',
  failed: '#E0E0E0',
  missing: '#FCE4EC',
}

export const MISSING_TEXT_COLOR = '#616161'
export const REVIEW_TEXT_COLOR = '#C00000'

export const JUDGEMENT_LABELS: Record<ChipJudgement, string> = {
  billable: '課金',
  no_data: '通信なし',
  excluded: '除外',
  failed: '取得失敗',
  missing: '消えた地点',
}

export const REVIEW_REASONS: Record<string, string> = {
  OVER_MAX_ITEMS: '項目数が上限帯を超過・上限帯の仮単価（OVER_MAX_ITEMS）',
  NO_ITEMS: '項目数が 0・0 円（NO_ITEMS）',
  RETENTION_UNKNOWN: '保存期間が不明・3年扱いの仮単価（RETENTION_UNKNOWN）',
}
</script>

<script setup lang="ts">
import { computed } from 'vue'
import HelpTip from '@/components/HelpTip.vue'
import { HELP } from '../help'

const props = withDefaults(defineProps<{ judgement: ChipJudgement; flags?: string[] }>(), {
  flags: () => [],
})

const label = computed(() => JUDGEMENT_LABELS[props.judgement])

const chipStyle = computed(() => ({
  '--chip-fill': JUDGEMENT_FILLS[props.judgement],
  '--chip-text': props.judgement === 'missing' ? MISSING_TEXT_COLOR : 'inherit',
}))

const reviewStyle = { '--chip-review': REVIEW_TEXT_COLOR }

// フラグは判定を問わず立ちうるため、除外・通信なしの行にも理由が付く（設計 6.5）。
const reasons = computed(() =>
  props.flags.map((flag) => REVIEW_REASONS[flag] ?? flag).join('、'),
)
</script>

<template>
  <span class="judgement-chip">
    <HelpTip :text="HELP.judgement[judgement]">
      <template #default="{ props: tip }">
        <span
          v-bind="tip"
          class="judgement-chip__label"
          :style="chipStyle"
        >{{ label }}</span>
      </template>
    </HelpTip>
    <HelpTip
      v-if="flags.length"
      :text="reasons"
    >
      <template #default="{ props: tip }">
        <span
          v-bind="tip"
          class="judgement-chip__review"
          :style="reviewStyle"
        >要確認: {{ reasons }}</span>
      </template>
    </HelpTip>
  </span>
</template>

<style scoped>
.judgement-chip {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
}

.judgement-chip__label {
  background-color: var(--chip-fill);
  color: var(--chip-text);
  border: 1px solid rgba(0, 0, 0, 0.12);
  border-radius: 4px;
  padding: 0 6px;
  font-size: 0.75rem;
  line-height: 1.6;
  white-space: nowrap;
}

.judgement-chip__review {
  color: var(--chip-review);
  font-size: 0.75rem;
  font-weight: 600;
}
</style>
