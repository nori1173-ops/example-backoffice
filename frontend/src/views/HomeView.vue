<script setup lang="ts">
import { computed } from 'vue'
import { apps } from '@/apps/registry'
import { useAuthStore } from '@/stores/auth'

const auth = useAuthStore()

const visibleApps = computed(() =>
  apps.filter(
    (app) =>
      !app.requiredGroups?.length ||
      app.requiredGroups.some((group) => auth.groups.includes(group)),
  ),
)
</script>

<template>
  <div>
    <h1 class="text-h5 mb-1">
      ホーム
    </h1>
    <p class="text-body-2 text-medium-emphasis mb-6">
      利用できるアプリの一覧です。
    </p>

    <v-alert
      v-if="!visibleApps.length"
      type="info"
      variant="tonal"
    >
      利用できるアプリがありません。権限が必要な場合は管理者へお問い合わせください。
    </v-alert>

    <v-row v-else>
      <v-col
        v-for="app in visibleApps"
        :key="app.id"
        cols="12"
        sm="6"
        md="4"
      >
        <v-card
          :to="`/${app.id}`"
          height="100%"
          border
          flat
        >
          <v-card-item>
            <template #prepend>
              <v-icon
                :icon="app.icon"
                color="primary"
                size="32"
              />
            </template>
            <v-card-title>{{ app.title }}</v-card-title>
          </v-card-item>
          <v-card-text
            v-if="app.description"
            class="text-body-2 text-medium-emphasis"
          >
            {{ app.description }}
          </v-card-text>
        </v-card>
      </v-col>
    </v-row>
  </div>
</template>
