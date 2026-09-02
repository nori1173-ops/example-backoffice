<script setup lang="ts">
import { computed } from 'vue'
import { SITE_ENV_COLORS, SITE_ENV_LABELS, SITE_TITLE, siteEnv } from '@/config/site'
import { useUiStore } from '@/stores/ui'

const ui = useUiStore()

// サインイン前に検証サイトと本番サイトを取り違えないようにする（UI-12 / R7）
const site = computed(() => siteEnv())
const siteLabel = computed(() => SITE_ENV_LABELS[site.value])
const siteColor = computed(() => SITE_ENV_COLORS[site.value])
</script>

<template>
  <v-app>
    <v-main class="bg-grey-lighten-4">
      <v-container class="d-flex align-center justify-center fill-height">
        <v-card
          class="pa-2"
          max-width="440"
          width="100%"
          elevation="3"
        >
          <v-card-item class="text-center pt-6 pb-2">
            <v-chip
              data-test="site-env"
              :color="siteColor"
              variant="flat"
              size="small"
              label
              class="mb-2"
            >
              {{ siteLabel }}
            </v-chip>
            <div class="text-h6">
              example-backoffice
            </div>
            <div class="text-caption text-medium-emphasis">
              {{ SITE_TITLE }}
            </div>
          </v-card-item>
          <v-card-text class="pa-6 pt-2">
            <slot />
          </v-card-text>
        </v-card>
      </v-container>
    </v-main>

    <v-snackbar
      v-model="ui.snackbar.visible"
      :color="ui.snackbar.color"
      location="bottom right"
    >
      {{ ui.snackbar.text }}
    </v-snackbar>
  </v-app>
</template>
