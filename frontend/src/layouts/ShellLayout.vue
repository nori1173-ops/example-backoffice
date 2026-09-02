<script setup lang="ts">
import { computed, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useDisplay } from 'vuetify'
import { apps } from '@/apps/registry'
import { SITE_ENV_COLORS, SITE_ENV_LABELS, siteEnv } from '@/config/site'
import { useAuthStore } from '@/stores/auth'
import { useUiStore } from '@/stores/ui'

const route = useRoute()
const router = useRouter()
const auth = useAuthStore()
const ui = useUiStore()
const { mdAndUp } = useDisplay()

// 狭い画面ではドロワーが一時表示で画面を覆うため、閉じた状態から始める（UI-02）
watch(mdAndUp, (wide) => { ui.drawer = wide }, { immediate: true })

function closeOnNarrow(): void {
  if (!mdAndUp.value) {
    ui.drawer = false
  }
}

function allowed(requiredGroups?: string[]): boolean {
  return !requiredGroups?.length || requiredGroups.some((group) => auth.groups.includes(group))
}

const visibleApps = computed(() => apps.filter((app) => allowed(app.requiredGroups)))

// dev サイトと本番サイトの取り違えを防ぐため常時表示する（UI-12）
const site = computed(() => siteEnv())
const siteLabel = computed(() => SITE_ENV_LABELS[site.value])
const siteColor = computed(() => SITE_ENV_COLORS[site.value])

const currentApp = computed(() => apps.find((app) => app.id === route.meta.appId))

const currentNav = computed(() =>
  (currentApp.value?.nav ?? []).filter((item) =>
    allowed(item.requiredGroups ?? currentApp.value?.requiredGroups),
  ),
)

function toggleNav(): void {
  if (mdAndUp.value) {
    ui.toggleRail()
  } else {
    ui.toggleDrawer()
  }
}

async function logout(): Promise<void> {
  try {
    await auth.logout()
  } catch {
    ui.notify('ログアウトに失敗しましたが、この端末からはサインアウトしました', 'warning')
  }
  await router.push({ name: 'login' })
}
</script>

<template>
  <v-app>
    <v-navigation-drawer
      v-model="ui.drawer"
      :rail="ui.rail && mdAndUp"
      :temporary="!mdAndUp"
      :permanent="mdAndUp"
      expand-on-hover
      width="260"
    >
      <v-list
        density="compact"
        nav
      >
        <v-list-item
          prepend-icon="mdi-home-outline"
          title="ホーム"
          :to="{ name: 'home' }"
          exact
          @click="closeOnNarrow"
        />
        <v-divider class="my-1" />
        <v-list-item
          v-for="app in visibleApps"
          :key="app.id"
          :prepend-icon="app.icon"
          :title="app.title"
          :to="`/${app.id}`"
          @click="closeOnNarrow"
        />
      </v-list>

      <template #append>
        <v-list
          density="compact"
          nav
        >
          <v-list-item
            prepend-icon="mdi-shield-key-outline"
            title="セキュリティ"
            :to="{ name: 'mfa' }"
            @click="closeOnNarrow"
          />
          <v-list-item
            prepend-icon="mdi-logout"
            title="ログアウト"
            @click="logout"
          />
        </v-list>
      </template>
    </v-navigation-drawer>

    <v-app-bar
      density="comfortable"
      flat
      border="b"
    >
      <v-app-bar-nav-icon @click="toggleNav" />
      <v-chip
        data-test="site-env"
        :color="siteColor"
        variant="flat"
        size="small"
        label
        class="me-3 flex-0-0"
      >
        {{ siteLabel }}
      </v-chip>
      <v-app-bar-title>{{ currentApp?.title ?? 'example-backoffice' }}</v-app-bar-title>
      <template #append>
        <span class="text-body-2 d-none d-sm-inline">{{ auth.email }}</span>
      </template>
      <template
        v-if="currentNav.length"
        #extension
      >
        <v-tabs
          :model-value="route.path"
          density="compact"
        >
          <v-tab
            v-for="item in currentNav"
            :key="item.to"
            :value="item.to"
            :to="item.to"
            :prepend-icon="item.icon"
          >
            {{ item.title }}
          </v-tab>
        </v-tabs>
      </template>
    </v-app-bar>

    <v-main>
      <v-container
        fluid
        class="pa-4 pa-md-6"
      >
        <router-view v-slot="{ Component }">
          <keep-alive :max="5">
            <component :is="Component" />
          </keep-alive>
        </router-view>
      </v-container>
    </v-main>

    <v-snackbar
      v-model="ui.snackbar.visible"
      :color="ui.snackbar.color"
      :timeout="ui.snackbar.color === 'error' ? -1 : 5000"
      location="bottom right"
    >
      {{ ui.snackbar.text }}
      <template #actions>
        <v-btn
          data-test="snackbar-close"
          variant="text"
          @click="ui.snackbar.visible = false"
        >
          閉じる
        </v-btn>
      </template>
    </v-snackbar>
  </v-app>
</template>
