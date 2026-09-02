<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import type { Band, PriceTable, PriceVersion } from '../types'

interface BandForm {
  max_items: string
  price_3y: string
  price_1y: string
}

const props = defineProps<{ versions: PriceVersion[]; editable: boolean }>()
const emit = defineEmits<{ add: [PriceTable] }>()

const DATE_PATTERN = /^\d{4}-\d{2}-\d{2}$/
const INTEGER_PATTERN = /^\d+$/
// 既定値は support-tracker #2001（設計 2.3）。通常はバックエンドが自動シードするため画面には既定版が届く
const DEFAULT_BANDS: Band[] = [
  { max_items: 500, price_3y: 650, price_1y: 450 },
  { max_items: 800, price_3y: 1000, price_1y: 700 },
  { max_items: 1600, price_3y: 1200, price_1y: 900 },
  { max_items: 1700, price_3y: 1350, price_1y: 1000 },
]
const DEFAULT_CAMERA = { price_3y: 1800, price_1y: 1400 }

const formOpen = ref(false)
const effectiveFrom = ref('')
const bands = ref<BandForm[]>([])
const cameraPrice3y = ref('')
const cameraPrice1y = ref('')
const note = ref('')

const sorted = computed(() =>
  [...props.versions].sort((a, b) => b.effective_from.localeCompare(a.effective_from)),
)

/** 今日時点で有効な版。全部が適用予定（未来日付）なら最新をそのまま見せる。 */
const current = computed(
  () => sorted.value.find((version) => version.effective_from <= today()) ?? sorted.value[0] ?? null,
)

const scheduled = computed(() => sorted.value.filter((version) => version.effective_from > today()))

function pad(value: number): string {
  return String(value).padStart(2, '0')
}

function today(): string {
  const now = new Date()
  return `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}`
}

function yen(value: number): string {
  return `${value.toLocaleString('ja-JP')} 円`
}

function bandLabel(versionBands: Band[], index: number): string {
  const max = versionBands[index].max_items.toLocaleString('ja-JP')
  if (index === 0) {
    return `${max} 項目まで`
  }
  const min = (versionBands[index - 1].max_items + 1).toLocaleString('ja-JP')
  return `${min}〜${max} 項目`
}

function toBandForm(band: Band): BandForm {
  return {
    max_items: String(band.max_items),
    price_3y: String(band.price_3y),
    price_1y: String(band.price_1y),
  }
}

function seed(): void {
  const latest = sorted.value[0]
  effectiveFrom.value = today()
  bands.value = (latest?.bands ?? DEFAULT_BANDS).map(toBandForm)
  cameraPrice3y.value = String(latest?.camera.price_3y ?? DEFAULT_CAMERA.price_3y)
  cameraPrice1y.value = String(latest?.camera.price_1y ?? DEFAULT_CAMERA.price_1y)
  note.value = ''
}

function openForm(): void {
  seed()
  formOpen.value = true
}

const amounts = computed(() => [
  ...bands.value.flatMap((band) => [band.price_3y, band.price_1y]),
  cameraPrice3y.value,
  cameraPrice1y.value,
])

/** 入力の妥当性は UX 目的で、実体の検証はバックエンド（設計 8 章の `POST /prices`）。 */
const error = computed(() => {
  if (!DATE_PATTERN.test(effectiveFrom.value)) {
    return '適用開始日は YYYY-MM-DD 形式で入力してください'
  }
  if (bands.value.some((band) => !INTEGER_PATTERN.test(band.max_items))) {
    return '項目数帯は 0 以上の整数で入力してください'
  }
  const maxItems = bands.value.map((band) => Number(band.max_items))
  if (maxItems.some((value, index) => index > 0 && value <= maxItems[index - 1])) {
    return '項目数帯は昇順で、重複なく入力してください'
  }
  if (amounts.value.some((value) => !INTEGER_PATTERN.test(value))) {
    return '単価は 0 以上の整数で入力してください'
  }
  return null
})

function submit(): void {
  emit('add', {
    effective_from: effectiveFrom.value,
    bands: bands.value.map((band) => ({
      max_items: Number(band.max_items),
      price_3y: Number(band.price_3y),
      price_1y: Number(band.price_1y),
    })),
    camera: { price_3y: Number(cameraPrice3y.value), price_1y: Number(cameraPrice1y.value) },
    note: note.value,
  })
}

watch(() => props.versions, seed, { immediate: true })
</script>

<template>
  <v-card
    variant="flat"
    border
  >
    <v-card-item>
      <v-card-title class="text-subtitle-1">
        単価表
      </v-card-title>
      <v-card-subtitle>
        地点ごとの月額単価です。各月の集計には、その月の締め日時点で有効な単価が使われます。
      </v-card-subtitle>
    </v-card-item>

    <v-card-text
      v-if="current"
      data-test="price-current"
    >
      <div class="text-subtitle-2 mb-2">
        現在の単価（適用開始日 {{ current.effective_from }}）
      </div>
      <v-table density="comfortable">
        <thead>
          <tr>
            <th>地点の項目数</th>
            <th class="text-right">
              3年保存
            </th>
            <th class="text-right">
              1年保存
            </th>
          </tr>
        </thead>
        <tbody>
          <tr
            v-for="(band, index) in current.bands"
            :key="band.max_items"
          >
            <td>{{ bandLabel(current.bands, index) }}</td>
            <td class="text-right">
              {{ yen(band.price_3y) }}
            </td>
            <td class="text-right">
              {{ yen(band.price_1y) }}
            </td>
          </tr>
          <tr>
            <td>カメラ地点</td>
            <td class="text-right">
              {{ yen(current.camera.price_3y) }}
            </td>
            <td class="text-right">
              {{ yen(current.camera.price_1y) }}
            </td>
          </tr>
        </tbody>
      </v-table>
      <div
        v-if="current.note"
        class="text-caption text-medium-emphasis mt-1"
      >
        備考: {{ current.note }}
      </div>
      <v-alert
        v-for="version in scheduled"
        :key="version.effective_from"
        data-test="price-scheduled"
        type="info"
        variant="tonal"
        density="compact"
        class="mt-2"
      >
        {{ version.effective_from }} から新しい単価が適用される予定です。
      </v-alert>
    </v-card-text>
    <v-card-text
      v-else
      class="text-medium-emphasis"
    >
      単価表を読み込んでいます。表示されない場合は再読み込みしてください。
    </v-card-text>

    <template v-if="editable">
      <v-divider />
      <v-card-actions v-if="!formOpen">
        <v-btn
          data-test="price-revise-open"
          color="primary"
          variant="text"
          @click="openForm"
        >
          単価を改定する
        </v-btn>
        <span class="text-caption text-medium-emphasis">
          単価が変わったときに、新しい単価と適用開始日を登録します
        </span>
      </v-card-actions>
      <template v-else>
        <v-card-text>
          <div class="text-subtitle-2 mb-1">
            単価の改定を登録
          </div>
          <p class="text-caption text-medium-emphasis mb-3">
            現在の単価をコピーした状態です。変わる金額だけ書き換えてください。
            適用開始日以降の締め日の集計から新しい単価が使われ、過去の集計は変わりません。
          </p>
          <v-row dense>
            <v-col
              cols="12"
              sm="4"
            >
              <v-text-field
                v-model="effectiveFrom"
                data-test="price-effective-from"
                label="適用開始日"
                placeholder="YYYY-MM-DD"
                density="comfortable"
                variant="outlined"
                hide-details
              />
            </v-col>
            <v-col
              cols="12"
              sm="8"
            >
              <v-text-field
                v-model="note"
                data-test="price-note"
                label="備考（改定の根拠など）"
                density="comfortable"
                variant="outlined"
                hide-details
              />
            </v-col>
          </v-row>

          <v-row
            v-for="(band, index) in bands"
            :key="index"
            dense
            class="mt-1"
          >
            <v-col
              cols="4"
              sm="4"
            >
              <v-text-field
                v-model="band.max_items"
                :data-test="`band-max-items-${index}`"
                label="項目数（〜この値まで）"
                density="comfortable"
                variant="outlined"
                hide-details
              />
            </v-col>
            <v-col
              cols="4"
              sm="4"
            >
              <v-text-field
                v-model="band.price_3y"
                :data-test="`band-price-3y-${index}`"
                label="3年保存（円）"
                density="comfortable"
                variant="outlined"
                hide-details
              />
            </v-col>
            <v-col
              cols="4"
              sm="4"
            >
              <v-text-field
                v-model="band.price_1y"
                :data-test="`band-price-1y-${index}`"
                label="1年保存（円）"
                density="comfortable"
                variant="outlined"
                hide-details
              />
            </v-col>
          </v-row>

          <v-row
            dense
            class="mt-1"
          >
            <v-col
              cols="6"
              sm="4"
            >
              <v-text-field
                v-model="cameraPrice3y"
                data-test="camera-price-3y"
                label="カメラ地点 3年保存（円）"
                density="comfortable"
                variant="outlined"
                hide-details
              />
            </v-col>
            <v-col
              cols="6"
              sm="4"
            >
              <v-text-field
                v-model="cameraPrice1y"
                data-test="camera-price-1y"
                label="カメラ地点 1年保存（円）"
                density="comfortable"
                variant="outlined"
                hide-details
              />
            </v-col>
          </v-row>

          <v-alert
            v-if="error"
            data-test="price-error"
            type="error"
            variant="tonal"
            density="compact"
            class="mt-4"
          >
            {{ error }}
          </v-alert>
        </v-card-text>
        <v-card-actions>
          <v-spacer />
          <v-btn
            data-test="price-revise-cancel"
            variant="text"
            @click="formOpen = false"
          >
            キャンセル
          </v-btn>
          <v-btn
            data-test="price-add"
            color="primary"
            variant="flat"
            :disabled="Boolean(error)"
            @click="submit"
          >
            改定を登録
          </v-btn>
        </v-card-actions>
      </template>
    </template>

    <v-divider />
    <v-expansion-panels variant="accordion">
      <v-expansion-panel
        eager
        elevation="0"
      >
        <v-expansion-panel-title class="text-body-2">
          改定履歴（{{ sorted.length }} 件）
        </v-expansion-panel-title>
        <v-expansion-panel-text>
          <v-table density="comfortable">
            <thead>
              <tr>
                <th>適用開始日</th>
                <th>項目数帯 / 3年保存 / 1年保存</th>
                <th>カメラ</th>
                <th>備考</th>
                <th>登録者</th>
              </tr>
            </thead>
            <tbody>
              <tr v-if="!sorted.length">
                <td
                  colspan="5"
                  class="text-center text-medium-emphasis py-6"
                >
                  単価表の版がありません。
                </td>
              </tr>
              <tr
                v-for="version in sorted"
                :key="version.effective_from"
                data-test="price-version-row"
              >
                <td class="text-no-wrap">
                  {{ version.effective_from }}
                </td>
                <td>
                  <div
                    v-for="band in version.bands"
                    :key="band.max_items"
                    class="text-body-2"
                  >
                    〜{{ band.max_items.toLocaleString('ja-JP') }}: {{ band.price_3y }} / {{ band.price_1y }}
                  </div>
                </td>
                <td class="text-no-wrap">
                  {{ version.camera.price_3y }} / {{ version.camera.price_1y }}
                </td>
                <td>{{ version.note }}</td>
                <td>{{ version.created_by_email }}</td>
              </tr>
            </tbody>
          </v-table>
        </v-expansion-panel-text>
      </v-expansion-panel>
    </v-expansion-panels>
  </v-card>
</template>
