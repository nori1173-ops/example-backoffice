<script setup lang="ts">
import { useUiStore } from '@/stores/ui'

const ui = useUiStore()
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
            <div class="text-h6">
              example-backoffice
            </div>
            <div class="text-caption text-medium-emphasis">
              社内保守管理サイト
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
