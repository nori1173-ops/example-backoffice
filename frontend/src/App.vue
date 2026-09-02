<script setup lang="ts">
import { computed } from 'vue'
import { RouterView, useRoute } from 'vue-router'
import AuthLayout from '@/layouts/AuthLayout.vue'

const route = useRoute()

// shell レイアウトはルート階層（'/' の親）が担うため、ここでは auth だけを被せる
const isAuthLayout = computed(() => route.meta.layout === 'auth')
</script>

<template>
  <AuthLayout v-if="isAuthLayout">
    <RouterView />
  </AuthLayout>
  <RouterView v-else />
</template>
