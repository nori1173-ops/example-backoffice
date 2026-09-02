# example-backoffice フロントエンド調査レポート（R2: Vue 3 / Vuetify / Amplify v6）

調査日: 2026-08-27
対象: 社内向け保守管理サイト `example-backoffice`（CloudFront + S3 の単一 SPA、サイドバーでアプリ切替、第一弾 = SaaS_A 課金レポート）
出典は末尾「9. 出典 URL 一覧」に番号で対応。

---

## 0. 結論（先に要点）

| 論点 | 推奨 | 根拠 |
|---|---|---|
| アプリ構成 | **単一 SPA + `src/apps/<app>/index.ts` マニフェスト + `import.meta.glob` 自動登録**。Module Federation は採用しない | 社内少人数・1チーム・単一デプロイ。MF は「複数チームの独立デプロイ」が課題のときの道具 [22][23] |
| UI シェル | `v-app` > `v-navigation-drawer`（アプリ一覧、rail 対応）+ `v-app-bar` + `v-main` > `<router-view>`。アプリ内サブナビはマニフェストの `nav` を app-bar 下のタブ or ドロワー第2階層で描画 | Vuetify 公式レイアウトシステム [8] |
| バージョン | **ref-project-a と同一系統に揃える**: Vue 3.5 / Vuetify 4.1 / Vue Router 5 / Pinia 4 / Vite 8 / Vitest 4 / TypeScript 6.0（7 は vue-tsc 未対応）/ aws-amplify 6.20 / Node 24 | 4章の表参照。example-portal 配下（Vuetify 3.5 / Vite 5 / Vitest 1）は 1 世代古い |
| 認証 | Amplify v6 の自前 UI（ref-project-a の `stores/auth.ts` を流用し、`signIn` の `nextStep` 分岐を追加）。PreSignUp Lambda で `@example.co.jp` 制限 | 3章 |
| MFA 後付け | **可能**。`MfaConfiguration` は作成後に `OFF → OPTIONAL → ON` へ変更できる（`SetUserPoolMfaConfig` / コンソール / 現行 CloudFormation）。SMS 未設定のまま **TOTP のみ必須にできる**。ただし「サインイン識別子・必須属性・大文字小文字区別・アプリクライアントシークレット」は作成後に変更不可なので最初に確定すること。SMS は一度有効化すると無効化できないので触らない | 3.4 節 [12][13][14][15] |
| Managed Login | 当面は不採用（自前 UI）。MFA セットアップ画面を書きたくない場合の代替として温存 | 3.6 節 [17] |

---

## 1. プラガブルなアプリ構成

### 1.1 方式比較（単一 SPA マニフェスト vs マイクロフロントエンド）

| 観点 | 単一 SPA + マニフェスト（推奨） | Module Federation（Vite 8/Rolldown でネイティブ対応 [21]） |
|---|---|---|
| ビルド/デプロイ | 1 ビルド・1 バケット・1 CloudFront | アプリごとに独立ビルド・独立配置、ホスト側でランタイム解決 |
| 依存の整合 | `package.json` 1 本。Vuetify/Vue のバージョン差異が起きない | 共有依存（vue/vuetify/pinia）のバージョン整合をランタイムで管理 |
| 型安全・テスト | マニフェストを TS 型で強制、vitest でレジストリ検証が容易 | 境界が実行時のため型は手動共有、E2E が難しい |
| チーム規模 | 1〜3 チームなら「lazy route + フォルダ境界」で十分 [22] | 4 チーム以上・ドメイン境界が明確なとき [22][23] |
| 後方互換 | マニフェスト境界を保てば後から MF に切り出せる | 最初から MF は「organizational agility と引き換えに複雑さ」[23] |

社内少人数・保守担当が数名という前提では単一 SPA が明確に優位。「後から別の社員がアプリを追加しやすい」は **フォルダを 1 つ足せば終わる** 設計で満たす。

### 1.2 推奨ディレクトリツリー

```
example-backoffice/
├─ frontend/
│  ├─ index.html                    <html lang="ja">、<title>
│  ├─ package.json
│  ├─ vite.config.ts                vue() + vuetify() + server.proxy['/api']
│  ├─ vitest.config.ts              jsdom / setupFiles / server.deps.inline ['vuetify']
│  ├─ playwright.config.ts          baseURL / storageState / projects
│  ├─ e2e/
│  │  ├─ auth.setup.ts              1 回ログインして storageState 保存
│  │  └─ saas-a-billing.spec.ts
│  └─ src/
│     ├─ main.ts                    Amplify.configure → pinia → vuetify → router
│     ├─ App.vue                    <router-view/> のみ
│     ├─ env.d.ts                   RouteMeta 拡張・import.meta.env 型
│     ├─ config/amplify.ts
│     ├─ plugins/vuetify.ts         ja ロケール・テーマ・defaults
│     ├─ styles/settings.scss       $body-font-family 等（中華フォント回避）
│     ├─ router/
│     │  ├─ index.ts                コア route + apps レジストリを結合
│     │  └─ guards.ts               requiresAuth / requiredGroups
│     ├─ stores/auth.ts             ref-project-a 流用 + nextStep 分岐
│     ├─ services/http.ts           createAppApi(appId) ファクトリ
│     ├─ layouts/
│     │  ├─ ShellLayout.vue         v-app / v-navigation-drawer / v-app-bar / v-main
│     │  └─ AuthLayout.vue          ログイン・サインアップ・MFA 用
│     ├─ components/                共通部品（PageHeader, ConfirmDialog, JsonViewer…）
│     ├─ views/                     LoginView / SignupView / MfaSetupView / HomeView / Forbidden / NotFound
│     └─ apps/
│        ├─ types.ts                AppManifest 型
│        ├─ registry.ts             import.meta.glob('./*/index.ts', { eager: true })
│        ├─ registry.test.ts        id 重複・path 接頭辞・遅延ロード検証
│        ├─ _template/              新アプリのコピー元（先頭 _ はレジストリで除外）
│        └─ saas-a-billing/
│           ├─ index.ts             マニフェスト（表示名・アイコン・routes・nav・必要グループ）
│           ├─ routes.ts            RouteRecordRaw[]（component は () => import()）
│           ├─ api.ts               createAppApi('saas-a-billing')
│           ├─ stores/settings.ts   defineStore('saas-a-billing/settings')
│           ├─ views/               SettingsView / RunView / ReportListView / ReportDetailView
│           ├─ components/
│           └─ __tests__/
```

### 1.3 マニフェストの型定義例

```ts
// src/apps/types.ts
import type { RouteRecordRaw } from 'vue-router'

export interface AppNavItem {
  title: string
  to: string                 // '/apps/saas-a-billing/settings' のような絶対パス or route name
  icon?: string              // 'mdi-cog'
  requiredGroups?: string[]  // 未指定ならアプリの requiredGroups を継承
}

export interface AppManifest {
  id: string                 // URL 接頭辞 /apps/<id> と API 接頭辞 /api/<id> に使う（kebab-case）
  title: string              // サイドバー表示名
  icon: string               // mdi アイコン名
  description?: string       // ホーム画面のカード説明
  order?: number             // サイドバー並び順（小さいほど上、既定 50）
  requiredGroups?: string[]  // Cognito グループ（ID トークン cognito:groups）。空なら全ログインユーザー
  routes: RouteRecordRaw[]   // /apps/<id> 配下の子ルート。path は相対（'' | 'settings' | 'reports/:id'）
  nav: AppNavItem[]          // アプリ内サブナビ
}

export function defineApp(manifest: AppManifest): AppManifest {
  return manifest            // 型推論用ヘルパ（defineConfig と同じ発想）
}
```

```ts
// src/apps/saas-a-billing/index.ts
import { defineApp } from '../types'
import { routes } from './routes'

export default defineApp({
  id: 'saas-a-billing',
  title: 'SaaS_A 課金レポート',
  icon: 'mdi-file-chart-outline',
  description: '月額ライセンス料金の集計・配布',
  order: 10,
  requiredGroups: ['saas-a-billing'],
  routes,
  nav: [
    { title: 'レポート一覧', to: '/apps/saas-a-billing', icon: 'mdi-view-list' },
    { title: '手動実行',     to: '/apps/saas-a-billing/run', icon: 'mdi-play' },
    { title: '設定',         to: '/apps/saas-a-billing/settings', icon: 'mdi-cog' },
  ],
})
```

```ts
// src/apps/saas-a-billing/routes.ts  — マニフェストからビューを静的 import しない（遅延ロードのため）
import type { RouteRecordRaw } from 'vue-router'
export const routes: RouteRecordRaw[] = [
  { path: '',             name: 'saas-a-billing.reports', component: () => import('./views/ReportListView.vue'),  meta: { title: 'レポート一覧' } },
  { path: 'reports/:id',  name: 'saas-a-billing.report',  component: () => import('./views/ReportDetailView.vue'), props: true },
  { path: 'run',          name: 'saas-a-billing.run',     component: () => import('./views/RunView.vue') },
  { path: 'settings',     name: 'saas-a-billing.settings',component: () => import('./views/SettingsView.vue') },
]
```

### 1.4 レジストリ（`import.meta.glob` 自動登録）

Vite の `import.meta.glob` は **パターンをリテラルで書く必要があり、変数は使えない** [4]。`eager: true` にすると静的 import に変換される [4]。マニフェスト（軽量）は eager、ビュー（重い）はルート単位の `() => import()` で遅延ロード、という二段構えにする。

```ts
// src/apps/registry.ts
import type { AppManifest } from './types'

const modules = import.meta.glob<{ default: AppManifest }>(
  ['./*/index.ts', '!./_*/index.ts'],   // _template は除外（負のパターン [4]）
  { eager: true },
)

export const apps: AppManifest[] = Object.values(modules)
  .map((m) => m.default)
  .sort((a, b) => (a.order ?? 50) - (b.order ?? 50) || a.title.localeCompare(b.title, 'ja'))

export function findApp(id: string) {
  return apps.find((a) => a.id === id)
}
```

```ts
// src/router/index.ts
import { createRouter, createWebHistory, type RouteRecordRaw } from 'vue-router'
import { apps } from '@/apps/registry'
import { installGuards } from './guards'

const appRoutes: RouteRecordRaw[] = apps.map((app) => ({
  path: `/apps/${app.id}`,
  meta: { appId: app.id, requiredGroups: app.requiredGroups },
  children: app.routes,                // 子は相対 path。meta は to.matched で親から集約
}))

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/login',  name: 'login',  component: () => import('@/views/LoginView.vue'),  meta: { requiresAuth: false, layout: 'auth' } },
    { path: '/signup', name: 'signup', component: () => import('@/views/SignupView.vue'), meta: { requiresAuth: false, layout: 'auth' } },
    { path: '/mfa/setup', name: 'mfa-setup', component: () => import('@/views/MfaSetupView.vue') },
    {
      path: '/',
      component: () => import('@/layouts/ShellLayout.vue'),   // サイドバー付きシェル
      children: [
        { path: '', name: 'home', component: () => import('@/views/HomeView.vue') },
        ...appRoutes,
        { path: 'forbidden', name: 'forbidden', component: () => import('@/views/ForbiddenView.vue') },
      ],
    },
    { path: '/:pathMatch(.*)*', name: 'not-found', component: () => import('@/views/NotFoundView.vue') },
  ],
})
installGuards(router)
export default router
```

```ts
// src/env.d.ts — RouteMeta の型拡張（Vue Router 公式の宣言マージ [3]）
import 'vue-router'
declare module 'vue-router' {
  interface RouteMeta {
    requiresAuth?: boolean       // 既定 true。false を明示したルートだけ公開
    requiredGroups?: string[]
    appId?: string
    title?: string
    layout?: 'shell' | 'auth'
  }
}
```

補足: Vue Router 5 は `unplugin-vue-router` をコアに取り込んだ「ファイルベースルーティング」も提供する（`vue-router/vite`）[2]。ただし本件は「アプリ = フォルダ」の粒度で登録したいのでマニフェスト方式のほうが素直。従来の `createRouter` + routes 配列 API に破壊的変更はない [2]。

### 1.5 ルーターガード（認証 + グループ）

```ts
// src/router/guards.ts
import type { Router } from 'vue-router'
import { useAuthStore } from '@/stores/auth'

export function installGuards(router: Router) {
  router.beforeEach(async (to) => {
    const auth = useAuthStore()
    if (!auth.initialized) await auth.checkAuth()          // 初回のみ getCurrentUser + fetchAuthSession

    const requiresAuth = to.matched.every((r) => r.meta.requiresAuth !== false)
    if (requiresAuth && !auth.isAuthenticated) {
      return { name: 'login', query: { redirect: to.fullPath } }
    }
    if (!requiresAuth && auth.isAuthenticated && (to.name === 'login' || to.name === 'signup')) {
      return { name: 'home' }
    }
    const need = to.matched.flatMap((r) => r.meta.requiredGroups ?? [])
    if (need.length && !need.some((g) => auth.groups.includes(g))) {
      return { name: 'forbidden' }
    }
  })
  router.afterEach((to) => {
    document.title = [to.meta.title, 'example-backoffice'].filter(Boolean).join(' | ')
  })
}
```

グループは ID トークンの `cognito:groups` クレームから取る（`fetchAuthSession()` → `tokens.idToken.payload['cognito:groups']`）。サイドバーも同じ判定で「見えるアプリ」を絞る（表示制御は UX、実際の権限はバックエンド側で再検証）。

### 1.6 Pinia ストアのアプリ分離

- ストアはアプリフォルダ内に置き、**ID に接頭辞**を付ける: `defineStore('saas-a-billing/settings', …)`。Pinia は初回 `useXxxStore()` で自動登録されるため、レジストリへの登録処理は不要。
- 横断で使うのは `stores/auth.ts` と `stores/ui.ts`（ドロワー開閉・スナックバー）だけに限定し、アプリ間でストアを直接参照しない。
- Pinia 4（2026-07）は ESM 専用化と `@vue/devtools-api` の同梱化以外に API 変更なし [20]。ref-project-a は 3.0.4 だが、Vite 8 は ESM 前提なので 4 で問題ない（`@vue/devtools-api` を devDependencies に追加）。

### 1.7 アプリ別 API クライアント（axios + Amplify トークン付与）

```ts
// src/services/http.ts
import axios, { type AxiosInstance } from 'axios'
import { fetchAuthSession, signOut } from 'aws-amplify/auth'
import router from '@/router'

export function createAppApi(appId: string): AxiosInstance {
  const api = axios.create({ baseURL: `/api/${appId}`, timeout: 30_000 })

  api.interceptors.request.use(async (config) => {
    // fetchAuthSession は期限切れなら refreshToken で自動更新する [16]
    const session = await fetchAuthSession()
    const token = session.tokens?.idToken?.toString()
    if (token) config.headers.Authorization = `Bearer ${token}`
    return config
  })

  api.interceptors.response.use(
    (res) => res,
    async (err) => {
      if (err.response?.status === 401) {
        await signOut().catch(() => {})
        router.push({ name: 'login', query: { redirect: router.currentRoute.value.fullPath } })
      }
      return Promise.reject(err)
    },
  )
  return api
}
```

```ts
// src/apps/saas-a-billing/api.ts
import { createAppApi } from '@/services/http'
export const api = createAppApi('saas-a-billing')
export const billingApi = {
  listReports: () => api.get<ReportSummary[]>('/reports'),
  getReport: (id: string) => api.get<ReportDetail>(`/reports/${id}`),
  run: (body: RunRequest) => api.post<RunResult>('/runs', body),
  getSettings: () => api.get<Settings>('/settings'),
  updateSettings: (body: Settings) => api.put<Settings>('/settings', body),
}
```

- 同一オリジン `/api/<app>/...` なので CORS 不要。ローカル開発は `vite.config.ts` の `server.proxy['/api']` で SAM local または検証環境の CloudFront に転送する [5]。
- ref-project-a は ID トークンを Bearer 送信している（REST API の Cognito オーソライザー互換）。本件も同じにする。
- **ref-project-a の `services/api.ts` にある `X-Admin-Auth: btoa('admin:changeme')`（フロントにハードコードされた認証情報）は持ち込まない**。管理者判定は Cognito グループで行う。

---

## 2. Vuetify の管理画面シェル

### 2.1 レイアウト定番

Vuetify のレイアウトシステムは「外側から内側へ」各コンポーネントが上下左右の領域を予約し、`v-main` が残り領域を使う仕組み [8]。`v-app` はルート（min-height 100dvh）。

```vue
<!-- src/layouts/ShellLayout.vue -->
<template>
  <v-app>
    <v-navigation-drawer
      v-model="drawer"
      :rail="rail && !mobile"
      :temporary="mobile"
      :permanent="!mobile"
      expand-on-hover
      width="260"
    >
      <v-list density="compact" nav>
        <v-list-item prepend-icon="mdi-home" title="ホーム" :to="{ name: 'home' }" exact />
        <v-divider class="my-1" />
        <v-list-item
          v-for="app in visibleApps" :key="app.id"
          :prepend-icon="app.icon" :title="app.title" :to="`/apps/${app.id}`"
        />
      </v-list>
      <template #append>
        <v-list density="compact" nav>
          <v-list-item prepend-icon="mdi-shield-key" title="セキュリティ" :to="{ name: 'mfa-setup' }" />
          <v-list-item prepend-icon="mdi-logout" title="ログアウト" @click="logout" />
        </v-list>
      </template>
    </v-navigation-drawer>

    <v-app-bar density="comfortable" flat border="b">
      <v-app-bar-nav-icon @click="mobile ? (drawer = !drawer) : (rail = !rail)" />
      <v-app-bar-title>{{ currentApp?.title ?? 'example-backoffice' }}</v-app-bar-title>
      <template #append>
        <span class="text-body-2 mr-2 d-none d-sm-inline">{{ auth.email }}</span>
        <v-avatar color="primary" size="32"><span class="text-caption">{{ initials }}</span></v-avatar>
      </template>
      <!-- アプリ内サブナビ: マニフェストの nav をタブで描画 -->
      <template v-if="currentApp?.nav.length" #extension>
        <v-tabs :model-value="route.path" density="compact">
          <v-tab v-for="n in currentApp.nav" :key="n.to" :value="n.to" :to="n.to" :prepend-icon="n.icon">{{ n.title }}</v-tab>
        </v-tabs>
      </template>
    </v-app-bar>

    <v-main>
      <v-container fluid class="pa-4 pa-md-6">
        <router-view v-slot="{ Component }">
          <keep-alive :max="5"><component :is="Component" /></keep-alive>
        </router-view>
      </v-container>
    </v-main>

    <AppSnackbar />
  </v-app>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue'
import { useRoute } from 'vue-router'
import { useDisplay } from 'vuetify'
import { apps } from '@/apps/registry'
import { useAuthStore } from '@/stores/auth'

const route = useRoute()
const auth = useAuthStore()
const { mobile } = useDisplay()         // mobile-breakpoint 既定 lg 未満。必要なら createVuetify({ display: { mobileBreakpoint: 'md' } })
const drawer = ref(!mobile.value)
const rail = ref(false)
const visibleApps = computed(() => apps.filter((a) => !a.requiredGroups?.length || a.requiredGroups.some((g) => auth.groups.includes(g))))
const currentApp = computed(() => apps.find((a) => a.id === route.meta.appId))
</script>
```

- **アプリ内サブナビの持ち方**は 2 択。(a) 上記のように app-bar の `#extension` に `v-tabs`（画面幅を食わない、3〜6 項目向き）。(b) ドロワーを `v-list-group` で 2 階層にしてアプリ配下に nav を展開（項目が多い・深いとき）。第一弾は 3 項目なので (a) を推奨。両方ともデータ源はマニフェストの `nav` なので後から切替可能。
- レスポンシブ: `useDisplay().mobile` で `temporary`（オーバーレイ）と `permanent + rail`（常設・縮小）を切替。`expand-on-hover` はマウス環境で便利。
- `keep-alive` はレポート一覧など「戻ったときに状態を保ちたい」画面向け。フォーム系は `max` を小さく保つ。

### 2.2 Vuetify 4 を使う場合の注意（ref-project-a は既に 4.0.5）

`v-app` / `v-navigation-drawer` / `v-app-bar` / `v-main` 自体にアップグレードガイド上の破壊的変更はない [9]。影響するのは主にスタイル層:

| 変更点（3 → 4）[9][10] | 管理画面での対処 |
|---|---|
| 既定テーマが `light` → `system`（OS 設定追従） | 業務画面なら `createVuetify({ theme: { defaultTheme: 'light' } })` を明示（ref-project-a 同様） |
| CSS リセット縮小（`* { margin:0; padding:0 }` 撤廃、`button/input/select` はブラウザ既定） | 素の `<button>` を使わず Vuetify コンポーネントで統一。必要なら公式の復元スニペット |
| タイポグラフィが MD3（`text-h1`→`text-display-large` 等、ボタンの大文字化廃止） | 見出しクラスは新名称で書く。`vuetify-codemods` で機械変換可 |
| elevation 0〜24 → 0〜5、breakpoint 縮小（md 960→840, lg 1280→1145） | `useDisplay().mobile` の閾値が変わるので実機で確認 |
| グリッドが negative margin → CSS `gap`、`v-row` の `dense/align/justify` はユーティリティクラスへ | フォーム配置は `d-flex` + `ga-*` / `v-col` で組む |
| CSS カスケードレイヤーを常用（`vuetify-core` … `vuetify-final`） | **アプリ側の無レイヤー CSS がそのまま勝つ**ので `!important` が不要になる（フォント指定で有利） |
| `defaults` のマージで `undefined` をスキップ | 既定値を打ち消すときは `null` |

Vuetify 4 の peerDependency は `vue ^3.5.0`（npm 実測）[10]。Vuetify MCP（`claude mcp add --transport http vuetify-mcp https://mcp.vuetifyjs.com/mcp`）で `get_v4_breaking_changes` が引ける [9]。

### 2.3 日本語フォント（中華フォント回避）

1. `index.html` を `<html lang="ja">` にする（CJK 統合漢字のフォールバック先が中国語フォントになるのを防ぐ）。
2. Vuetify の本文フォントは Sass 変数で差し替える。`vite-plugin-vuetify` の `styles.configFile` で設定ファイルを指定:

```scss
// src/styles/settings.scss
@use 'vuetify/settings' with (
  $body-font-family: ('Yu Gothic UI', 'Meiryo', 'Hiragino Kaku Gothic ProN', 'Noto Sans JP', 'Segoe UI', sans-serif),
  $heading-font-family: ('Yu Gothic UI', 'Meiryo', 'Hiragino Kaku Gothic ProN', 'Noto Sans JP', sans-serif)
);
```

```ts
// vite.config.ts
import vuetify from 'vite-plugin-vuetify'
export default defineConfig({
  plugins: [vue(), vuetify({ autoImport: true, styles: { configFile: 'src/styles/settings.scss' } })],
  server: { proxy: { '/api': { target: 'http://127.0.0.1:3000', changeOrigin: true } } },
})
```

3. 変数名は Vuetify 4 の Sass variables ページで最終確認する（4 でタイポグラフィ体系が MD3 化されているため）[9][11]。Sass を使わない場合は、v4 のレイヤー構造により通常の CSS で上書きできる:

```css
/* src/styles/fonts.css — 無レイヤー CSS は vuetify-* レイヤーより優先される */
.v-application, .v-application * { font-family: 'Yu Gothic UI', Meiryo, 'Hiragino Kaku Gothic ProN', 'Noto Sans JP', sans-serif; }
```

4. Noto Sans JP を同梱したい場合は `@fontsource/noto-sans-jp`（npm）で自己ホストし、外部 CDN に依存しない（社内 IP 制限下でも安定）。**SimSun / SimHei / Microsoft YaHei / PingFang SC / Noto Sans SC をスタックに入れない**。
5. `createVuetify({ locale: { locale: 'ja', messages: { ja } } })`（ref-project-a と同じ）で v-data-table / v-pagination 等のラベルを日本語化。

---

## 3. Amplify v6（`aws-amplify/auth`）の認証フロー

### 3.1 設定（既存 Cognito リソースを使う）

SAM で作ったユーザープールを Amplify Gen1 相当の手動設定で使う。`Amplify.configure({ Auth: { Cognito: { userPoolId, userPoolClientId, loginWith: { email: true }, signUpVerificationMethod: 'code', userAttributes: { email: { required: true } } } } })` [18]。ref-project-a の `config/amplify.ts` と同型。

**ユーザープール作成時に確定させる項目（後から変更不可 [12]）**
- サインイン識別子: `UsernameAttributes: ["email"]`（メールアドレスをユーザー名にする。エイリアス方式との選択は作成時のみ）
- 大文字小文字を区別しない（`UsernameConfiguration.CaseSensitive: false`）
- 必須属性: `email` のみ
- アプリクライアント: SPA 用は **クライアントシークレットなし**（後から変更不可）
- カスタム属性は削除不可なので、権限は属性ではなく **グループ**（`cognito:groups`）で表現する

### 3.2 signUp / confirmSignUp / signIn と PreSignUp ドメイン制限

- SPA: `signUp({ username: email, password, options: { userAttributes: { email } } })` → `confirmSignUp({ username, confirmationCode })` → `signIn({ username, password })`。ref-project-a `stores/auth.ts` の `register / confirmRegistration / login` をそのまま流用できる。
- **ドメイン制限は PreSignUp Lambda トリガーで実施**（SPA 側の入力チェックは UX のみ）。トリガーは `SignUp` と `AdminCreateUser` の直前に呼ばれ、例外を投げると登録が拒否される [19]。`autoConfirmUser` は使わず、メール検証コードで確認させる（メール実在性の担保）。

```python
# lambda/pre_signup/app.py
ALLOWED = {"example.co.jp"}

def handler(event, context):
    email = event["request"]["userAttributes"].get("email", "").lower()
    domain = email.rsplit("@", 1)[-1] if "@" in email else ""
    if domain not in ALLOWED:
        raise Exception("社内メールアドレス（@example.co.jp）のみ登録できます")
    return event
```

- 追加の防御: 当面は CloudFront の WAF で社内 IP 制限。Cognito のエンドポイント（`cognito-idp.*.amazonaws.com`）は CloudFront 経由ではないため、ユーザープールにも WAF web ACL を関連付けられる（[13] の関連トピック）。IP 制限を外す日に MFA 必須へ切り替える、という運用順序が成り立つ。

### 3.3 ルーターガードとトークンリフレッシュ

- ガードは 1.5 節。`checkAuth()` は `getCurrentUser()` + `fetchAuthSession()` でメール・グループを取得し `initialized = true` にする。
- `fetchAuthSession()` は「トークン期限切れかつ refreshToken 有効」なら自動更新、`{ forceRefresh: true }` で強制更新 [16]。axios インターセプタで毎回呼ぶだけでよい。
- リフレッシュ失敗は `Hub.listen('auth', …)` の `tokenRefresh_failure` で拾い、ストアをクリアしてログインへ [24]。
- トークン保存先は既定 localStorage。「タブを閉じたらログアウト」にしたければ `cognitoUserPoolsTokenProvider.setKeyValueStorage(sessionStorage)` [25]。
- アプリクライアントの refresh token 有効期間は社内保守サイトなら 1〜8 時間程度に短縮（既定 30 日）。

### 3.4 MFA を後から「OFF → OPTIONAL → REQUIRED」にする際の制約

**結論: 可能。ただし順序と副作用に注意。**

| 項目 | 事実 | 出典 |
|---|---|---|
| MFA 設定の変更可否 | `MfaConfiguration` は `CreateUserPool` / `UpdateUserPool` / `SetUserPoolMfaConfig` で設定でき、**作成後に OFF / OPTIONAL / ON を切り替えられる**。コンソールも「Sign-in > Multi-factor authentication > Edit」で変更可 | [13][14] |
| CloudFormation / SAM | 過去（2022 年以前）は「既存プールで MFA を required に更新できない」エラーがあり、カスタムリソースで `SetUserPoolMfaConfig` を呼ぶ回避策が必要だった。CloudFormation 側の対応は 2022-10 に完了、CDK 側 Issue も 2024-06 に「動作する」としてクローズ。**現行は template の `MfaConfiguration: ON` への更新で切替可能**（初回は検証用プールで update を試すこと） | [26][27] |
| SMS なしで TOTP のみ必須 | `SoftwareTokenMfaConfiguration.Enabled: true` + `MfaConfiguration: ON` で **TOTP 単独の必須化が可能**。「SMS 構成が有効だと SMS が自動的に MFA 方式として使えるようになる」ため、SMS は設定しない | [13][14] |
| SMS の罠 | **SMS を一度有効化すると無効化できない**（トグルのオン/オフはできるが構成自体は残る）。TOTP のみで行くなら最初から SMS 構成を作らない | [12] |
| メール MFA | Essentials プラン以上が必要。TOTP / SMS は Lite でも可。新規プールの既定は Essentials | [15] |
| 既存ユーザーへの影響（ON 化時） | TOTP 未登録ユーザーは次回サインインで `MFA_SETUP` チャレンジ（Amplify では `CONTINUE_SIGN_IN_WITH_TOTP_SETUP`）を受け、**登録を完了するまでサインインできない**。猶予期間や個人単位の免除は無い | [13][14] |
| ON 中のユーザー側操作 | 「ON のときユーザーは MFA 方式を有効/無効にできず、優先方式の設定のみ」。`AdminSetUserMFAPreference` で無効化もできない | [13] |
| OPTIONAL 中の挙動 | Managed Login も API も「登録を促さない」。任意登録させるには **自前で TOTP 登録画面**（`setUpTOTP` → `verifyTOTPSetup` → `updateMFAPreference({ totp: 'PREFERRED' })`）を用意する必要がある | [13][14][28] |
| 端末紛失時 | 管理者が `AdminDeleteSoftwareToken` で解除 → 次回サインインで再度 `MFA_SETUP`。ヘルプデスク手順として用意 | [14] |
| TOTP 仕様 | HMAC-SHA1 の認証アプリのみ（Google/Microsoft Authenticator 可）。5 回連続失敗で指数ロックアウト | [13][14] |
| 作成後に変更不可なもの | サインイン識別子・大文字小文字区別・必須属性・カスタム属性の削除・アプリクライアントシークレット・プール ID。**MFA は含まれない** | [12] |

**推奨ロールアウト**
1. 作成時: `MfaConfiguration: OFF`、`SoftwareTokenMfaConfiguration.Enabled: true`（TOTP の「方式」だけ先に有効化。ユーザーが任意登録できる状態）。
2. SPA に「セキュリティ設定」画面（QR 表示 → コード検証 → PREFERRED 化）を初期リリースから同梱。IP 制限中に全員へ任意登録を促す。
3. `OPTIONAL` に切替（登録済みユーザーは即 TOTP 要求、未登録者はそのまま）。
4. 未登録者ゼロを `cognito-idp admin-get-user`（`UserMFASettingList`）で確認 → `ON` に切替 → WAF の IP 制限を外す。

### 3.5 SPA 側で必要になるコード変更（`confirmSignIn`）

ref-project-a の `login()` は `result.isSignedIn` が false だと例外にしている。ここを `nextStep.signInStep` の分岐に置き換える [1]。

```ts
// src/stores/auth.ts（抜粋）
import { signIn, confirmSignIn, type SignInOutput } from 'aws-amplify/auth'

type Pending =
  | { kind: 'totp-setup'; setupUri: string; sharedSecret: string }
  | { kind: 'totp-code' }
  | { kind: 'new-password' }
  | { kind: 'confirm-signup'; username: string }
  | null

const pending = ref<Pending>(null)

function route(result: SignInOutput, username: string) {
  const step = result.nextStep
  switch (step.signInStep) {
    case 'DONE':
      pending.value = null
      return
    case 'CONTINUE_SIGN_IN_WITH_TOTP_SETUP': {        // プールが ON で未登録 → 初回登録
      const uri = step.totpSetupDetails.getSetupUri('example-backoffice', username).toString()
      pending.value = { kind: 'totp-setup', setupUri: uri, sharedSecret: step.totpSetupDetails.sharedSecret }
      return
    }
    case 'CONFIRM_SIGN_IN_WITH_TOTP_CODE':            // 登録済み → 6 桁コード
      pending.value = { kind: 'totp-code' }
      return
    case 'CONFIRM_SIGN_IN_WITH_NEW_PASSWORD_REQUIRED': // 管理者作成ユーザーの初回
      pending.value = { kind: 'new-password' }
      return
    case 'CONFIRM_SIGN_UP':                           // メール未確認
      pending.value = { kind: 'confirm-signup', username }
      return
    case 'CONTINUE_SIGN_IN_WITH_MFA_SELECTION':       // TOTP 単独運用なら来ないが、来たら TOTP を選ぶ
      return confirmSignIn({ challengeResponse: 'TOTP' }).then((r) => route(r, username))
    default:
      throw new Error(`未対応のサインインステップ: ${step.signInStep}`)
  }
}

async function login(username: string, password: string) {
  const result = await signIn({ username, password })
  await route(result, username)
}

async function submitChallenge(code: string, username: string) {   // TOTP 登録コード / TOTP コード / 新パスワード 共通
  const result = await confirmSignIn({ challengeResponse: code })
  await route(result, username)
}
```

- `signIn` → `confirmSignIn` は **同一ページセッション内のメモリ状態**で繋がる。リロードすると `confirmSignIn` が失敗するので、MFA 入力画面はログイン画面内のステップ表示（`v-window`）にし、別ルートへ遷移しない [29]。
- `CONTINUE_SIGN_IN_WITH_TOTP_SETUP` では `getSetupUri()` の `otpauth://` URI を QR 化して表示（`qrcode` パッケージ等）し、`sharedSecret` を手入力用に併記。ユーザーが認証アプリのコードを入れたら `confirmSignIn({ challengeResponse: code })` で登録完了 [1]。
- サインイン後の任意登録（OPTIONAL 期間用）は `setUpTOTP()` → `verifyTOTPSetup({ code })` → `updateMFAPreference({ totp: 'PREFERRED' })` [28]。上と UI を共有できる（QR + コード入力の 1 コンポーネント）。
- ログイン画面の状態遷移は「パスワード → (TOTP 登録 | TOTP コード | 新パスワード | メール確認) → 完了」の有限状態機械として `pending` で表現し、vitest で `aws-amplify/auth` を `vi.mock` して各分岐をテストする。

### 3.6 Amplify Hosted UI / Managed Login との比較

| 観点 | 自前 UI（Amplify v6 API）— 推奨 | Managed Login（`signInWithRedirect` + OAuth） |
|---|---|---|
| 実装量 | ログイン・サインアップ・確認コード・MFA 登録/入力・パスワード再設定を自作（ref-project-a 流用で半分は済む） | サインイン/サインアップ/パスワード再設定/**MFA 登録（TOTP QR）**/パスキーは Cognito 側が提供 [17] |
| 見た目・言語 | Vuetify で統一、日本語自在 | 別ドメインへリダイレクト。日本語化は Essentials 以上かつ Managed Login ブランディング必須（`lang=ja`）[17] |
| 社内 IP 制限 | CloudFront WAF + ユーザープール WAF | Cognito ドメインへの WAF 関連付けで対応 |
| MFA 必須化時の SPA 変更 | `confirmSignIn` 分岐の追加（3.5 節） | ほぼ不要（Managed Login が `MFA_SETUP` を処理） |
| できないこと | — | ユーザー自身の MFA 優先方式変更・属性変更などプロフィール管理は自前実装が必要。1 時間のセッション Cookie がありログアウト挙動に癖 [17] |
| PreSignUp ドメイン制限 | 有効 | 有効（同じトリガー） |

社内少人数・Vuetify 統一・ref-project-a 資産流用という条件では自前 UI が妥当。ただし「MFA 登録 UI を作る工数を省きたい」なら Managed Login への切替はアプリクライアント設定（ドメイン・コールバック URL）+ `signInWithRedirect()` 呼び出しで済み、ルーターガードや API クライアントはそのまま使える。

---

## 4. 既存リポとの整合（バージョン提案）

### 4.1 実測（2026-08-27）

| パッケージ | ref-project-a（node_modules 実測） | example-portal（sso-mock / ui-*） | npm latest | **新規プロジェクト推奨** |
|---|---|---|---|---|
| vue | 3.5.32 | ^3.4 / ^3.5 | 3.5.42 | `^3.5.40` |
| vuetify | 4.0.5 | ^3.5 | 4.1.12（peer vue ^3.5） | `^4.1` |
| vue-router | 5.0.4 | ^4.4 | 5.2.0 | `^5.2` |
| pinia | 3.0.4 | ^2.1 | 4.0.3 | `^4.0`（+ `@vue/devtools-api`） |
| aws-amplify | 6.16.3 | ^6.14 | 6.20.0 | `^6.20` |
| axios | ^1.7 | ^1.7 | 1.20.0 | `^1.20` |
| @mdi/font | 7.4.47 | 7.4.47 / @mdi/js | 7.4.47 | `^7.4`（`@mdi/js` + `aliases` にすると CSS 全量読込を回避できるが、まずは font で可） |
| vite | 8.0.4 | ^5 | 8.2.2 | `^8.2` |
| @vitejs/plugin-vue | ^6.0.5 | ^5 | 6.0.8 | `^6.0` |
| vite-plugin-vuetify | – | – | 2.1.3 | `^2.1`（autoImport + Sass 設定） |
| vitest | 4.1.4 | ^1.6 | 4.1.11 | `^4.1` |
| @vue/test-utils | ^2.4.6 | ^2.4 | 2.4.11 | `^2.4` |
| jsdom | ^29 | ^24 | – | `^29` |
| typescript | ~6.0.2 | ^5.5 | 7.0.2 | **`~6.0`**（7.x は vue-tsc が未対応。TS 7.1 で対応見込み [30]） |
| vue-tsc | ^3.2.6 | ^2 | 3.3.11 | `^3.3` |
| @playwright/test | – | ^1.60 | 1.62.1 | `^1.62` |
| Node.js | – | – | – | 24 LTS（この WSL は v24.13.0） |

- ref-project-a が既に Vuetify 4 / Vue Router 5 / Vite 8 / Vitest 4 に乗っているので、**ref-project-a 系統に揃える**のが最小コスト（example-portal 側は Vite 5 世代で 1 段古い）。
- Vite 8 は Rolldown 単一バンドラ化（2026-03）。プラグイン API は互換 [21]。
- Vue Router 5 は「破壊的変更なし」の移行リリース [2]。
- TypeScript は 7 に上げない（`vue-tsc -b` が動かない）[30]。

### 4.2 参考実装（ref-project-a）から流用するもの・しないもの

| ファイル | 要点 | 扱い |
|---|---|---|
| `src/router/index.ts` | `meta.requiresAuth` + `beforeEach` で `checkAuth()`。全ルート遅延 import | 流用。ガードを `guards.ts` に分離し、`requiredGroups` を追加 |
| `src/stores/auth.ts` | Pinia setup ストア。`signIn/signUp/confirmSignUp/signOut/getCurrentUser/fetchAuthSession`。`isSignedIn` 以外は例外 | 流用。3.5 節の `nextStep` 分岐と `groups` を追加 |
| `src/services/api.ts` | axios 単一インスタンス + `fetchAuthSession` で ID トークン付与 | ファクトリ化（`createAppApi`）。`X-Admin-Auth` のハードコードは捨てる |
| `src/components/AppLayout.vue` | `v-app` > `v-app-bar` > `v-main` > slot。ドロワー無し | ドロワー付き `ShellLayout.vue` に置換。ダイアログの書き方は流用 |
| `src/plugins/vuetify.ts` | `ja` ロケール・ライトテーマ・`defaults`（VTextField outlined 等） | ほぼそのまま流用 |
| `vitest.config.ts` | jsdom / globals / `src/**/*.{test,spec}.ts` | 流用。`server.deps.inline: ['vuetify']` と setupFiles を追加 |
| `index.html` | `lang="en"` | **`lang="ja"` に変更** |

---

## 5. テスト構成

### 5.1 vitest（ユニット）

```ts
// vitest.config.ts
import { defineConfig } from 'vitest/config'
import vue from '@vitejs/plugin-vue'
import vuetify from 'vite-plugin-vuetify'

export default defineConfig({
  plugins: [vue(), vuetify({ autoImport: true })],
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: ['src/test/setup.ts'],
    include: ['src/**/*.{test,spec}.ts'],
    server: { deps: { inline: ['vuetify'] } },      // Vuetify 公式推奨 [11]
    coverage: { provider: 'v8', include: ['src/**'], exclude: ['src/**/*.d.ts', 'src/main.ts'] },
  },
})
```

```ts
// src/test/setup.ts
import ResizeObserver from 'resize-observer-polyfill'
globalThis.ResizeObserver = ResizeObserver               // Vuetify のレイアウト計測用 [11]
window.matchMedia ??= (q) => ({ matches: false, media: q, addEventListener() {}, removeEventListener() {}, addListener() {}, removeListener() {}, onchange: null, dispatchEvent: () => false })
```

```ts
// src/test/mount.ts — Vuetify + Pinia + Router を束ねたヘルパ
import { mount, type MountingOptions } from '@vue/test-utils'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { createTestingPinia } from '@pinia/testing'
export function mountWithShell(comp: any, opts: MountingOptions<any> = {}) {
  const vuetify = createVuetify({ components, directives })
  return mount(comp, { ...opts, global: { plugins: [vuetify, createTestingPinia({ stubActions: false })], ...opts.global } })
}
```

テストの重点（TDD で先に書く）
- `apps/registry.test.ts`: id の重複なし・kebab-case・`routes[].path` が `/` で始まらない・`component` が関数（遅延ロード）・`_template` が含まれない。
- `stores/auth.test.ts`: `vi.mock('aws-amplify/auth')` で `signIn` の各 `nextStep` を返し、`pending` の遷移を検証。
- `router/guards.test.ts`: `createRouter({ history: createMemoryHistory() })` で未認証リダイレクト・グループ不足の `forbidden`。
- `services/http.test.ts`: リクエストヘッダに Bearer が付く・401 で `signOut` が呼ばれる（axios adapter をモック）。
- アプリ側: `RunView` のフォームバリデーション、`ReportDetailView` の色分け（通信なし/除外/要確認）は純粋関数に切り出して単体テスト。

### 5.2 Playwright（E2E）

```ts
// playwright.config.ts
import { defineConfig, devices } from '@playwright/test'
export default defineConfig({
  testDir: 'e2e',
  baseURL: process.env.E2E_BASE_URL ?? 'http://localhost:5173',
  use: { locale: 'ja-JP', timezoneId: 'Asia/Tokyo', trace: 'retain-on-failure', screenshot: 'only-on-failure' },
  projects: [
    { name: 'setup', testMatch: /auth\.setup\.ts/ },
    { name: 'chromium', use: { ...devices['Desktop Chrome'], storageState: 'e2e/.auth/user.json' }, dependencies: ['setup'] },
  ],
  webServer: { command: 'npm run dev', url: 'http://localhost:5173', reuseExistingServer: true },
})
```

- 認証は `auth.setup.ts` で 1 回だけ実ログインし `storageState` を保存（Amplify は localStorage 保存なので `storageState` で引き継げる）[31]。テスト用ユーザーは **検証用ユーザープール（MFA OFF）** に置く。MFA ON のプールで E2E するなら `otplib` で TOTP を生成して入力する。
- Cognito の実ログインを避けたい場合は `page.route('**/cognito-idp.*', …)` で応答をモックする手もあるが、トークン検証が絡むためまずは検証プール方式を推奨。

**Vuetify 操作の注意点** [7]
- `v-select` / `v-autocomplete` はメニューが `body` 直下にテレポートされる。選択肢は `page.getByRole('option', { name: '3年' })` で取るのが最も安定。
- 入力欄の直クリックは `data-no-activator` の div に遮られ「element is not stable」になることがある。`getByLabel('保存期間')` を `click({ force: true })` するか、末尾アイコン（`.v-field__append-inner`）をクリックして開く。
- クラスに頼る場合は `.v-overlay--active .v-list-item`（v2 の `.menuable__content__active` は不可）。
- 主要な操作対象には `data-testid` を付け、`getByTestId` を第一選択にする（Vuetify のクラス名変更に強い）。
- `v-data-table` は `getByRole('row').filter({ hasText: '検証テナント' })` で行を絞り、`getByRole('cell')` で値を確認。
- `v-dialog` は `getByRole('dialog')` の中で操作。トランジション待ちは `expect(locator).toBeVisible()` に任せる。
- 実行環境の指示「ブラウザ操作は Playwright MCP を使う」はローカル確認用。CI では通常の `@playwright/test`。

---

## 6. 先行事例（OSS）

| リポジトリ | ★ | 参考になる点 |
|---|---|---|
| [pure-admin/vue-pure-admin](https://github.com/pure-admin/vue-pure-admin) | 20,572 | `src/router/index.ts` で `import.meta.glob(["./modules/**/*.ts", "!./modules/**/remaining.ts"], { eager: true })` → `Object.keys(modules).forEach(k => routes.push(modules[k].default))` の **モジュール別ルート自動集約**。UI は Element Plus だがルーター設計はそのまま使える |
| [posva/unplugin-vue-router](https://github.com/posva/unplugin-vue-router) | 2,240 | 型付きファイルベースルーティング。Vue Router 5 に統合済み（`vue-router/vite`）。マニフェスト方式で足りなければここへ寄せる |
| [hannoeru/vite-plugin-pages](https://github.com/hannoeru/vite-plugin-pages) | 2,078 | `import.meta.glob` ベースのファイルシステムルート生成の古典 |
| [yangjiakai/lux-ui](https://github.com/yangjiakai/lux-ui) | 682 | Vue 3 + Vuetify 3 + Pinia + vue-i18n の管理画面。ドロワー/app-bar/テーマ切替の実装例 |
| [themeselection/materio-vuetify-vuejs-admin-template-free](https://github.com/themeselection/materio-vuetify-vuejs-admin-template-free) | 964 | Vuetify 製の無料管理テンプレート。ナビ設定をデータ（配列）で持つ流儀 |
| [stijnvandepol/Accuro-management `frontend/src/modules/loader.ts`](https://github.com/stijnvandepol/Accuro-management/blob/5f2b989cdf605ae7474e7534f1bf683d042f600b/frontend/src/modules/loader.ts) | 小規模 | `import.meta.glob<{ default: ModuleManifest }>('./*/index.ts', { eager: true })` で **name/routes/icon/label/menuRoles/menuOrder** を持つマニフェストを集約し、メニューを `roles` でフィルタ。本件の設計にもっとも近い実装 |

---

## 7. フロントにアプリを追加する手順（10 ステップ以内）

1. `src/apps/_template/` を `src/apps/<app-id>/` にコピーする（`<app-id>` は kebab-case。URL `/apps/<app-id>`、API `/api/<app-id>` になる）。
2. `index.ts` のマニフェストを書き換える: `id` / `title` / `icon`（mdi） / `order` / `requiredGroups` / `nav`。
3. `routes.ts` に画面を追加する。`path` は相対、`component` は必ず `() => import('./views/Xxx.vue')`。
4. `api.ts` で `createAppApi('<app-id>')` を作り、エンドポイント関数をまとめる（型は `types.ts` に置く）。
5. 必要なら `stores/*.ts` を `defineStore('<app-id>/<name>', …)` で作る（他アプリのストアは参照しない）。
6. `views/` と `components/` を実装する。共通部品は `src/components/` のものを優先して使う。
7. `__tests__/` にユニットテストを書く（TDD: 先に失敗するテスト → 実装）。`npm test` で `registry.test.ts` を含めて緑にする。
8. Cognito にグループ `<app-id>`（`requiredGroups` に書いた名前）を作り、利用者を所属させる。
9. `e2e/<app-id>.spec.ts` に主要導線の E2E を 1 本書く（ログイン → サイドバー → 一覧表示）。
10. `npm run build`（`vue-tsc -b && vite build`）が通ることを確認して PR。サイドバー・ルーター・権限は自動反映されるので他ファイルの修正は不要。

---

## 8. 補足: 第一弾（saas-a-billing）画面の骨子

| 画面 | 主要コンポーネント | メモ |
|---|---|---|
| レポート一覧 `''` | `v-data-table-server`（対象月・実行日時・テナント数・地点数・通信なし・除外・合計・配布状況・Excel DL） | 行クリックで詳細へ。DL は署名付き URL |
| レポート詳細 `reports/:id` | `v-expansion-panels`（テナント → 現場） + `v-data-table`（地点）。色は `通信なし=amber-lighten-4 / 除外=blue-lighten-4 / 要確認=text-error` | Excel と同じ配色をテーマの `colors` に登録して共有 |
| 手動実行 `run` | 対象月 `v-date-input`（月選択）+ 「配布する/しない」`v-radio-group` + 確認 `v-dialog` + 進捗 `v-progress-linear` | 実行後はジョブ ID をポーリング |
| 設定 `settings` | 締め日 `v-select`(1–28) / 実行時刻 / 宛先 `v-combobox multiple chips` / 単価表（適用開始日付き履歴） `v-data-table` 編集 / 除外キーワード / カメラ扱いフォーマット / 接続先 | 保存時に更新者（メール）と日時を表示 |

---

## 9. 出典 URL 一覧

1. Amplify: Multi-step sign-in（`nextStep` / `CONTINUE_SIGN_IN_WITH_TOTP_SETUP` / `CONFIRM_SIGN_IN_WITH_TOTP_CODE`） — https://docs.amplify.aws/javascript/frontend/auth/multi-step-sign-in/
2. Vue Router: Migrating to Vue Router 5（破壊的変更なし・unplugin-vue-router 統合） — https://router.vuejs.org/guide/migration/v4-to-v5
3. Vue Router: Route Meta Fields（`RouteMeta` 宣言マージ） — https://router.vuejs.org/guide/advanced/meta.html
4. Vite: Glob Import（`import.meta.glob`、eager、負のパターン、リテラル制約） — https://vite.dev/guide/features#glob-import
5. Vite: server.proxy — https://vite.dev/config/server-options#server-proxy
6. Vue Router: Lazy Loading Routes — https://router.vuejs.org/guide/advanced/lazy-loading.html
7. Vuetify Discussion #18742: VSelect and Playwright — https://github.com/vuetifyjs/vuetify/discussions/18742
8. Vuetify: Application layout（レイアウトシステム） — https://vuetifyjs.com/en/features/application-layout/
9. Vuetify: Upgrade guide（v3 → v4 破壊的変更・revert スニペット・MCP） — https://vuetifyjs.com/en/getting-started/upgrade-guide/ （Markdown 原本: https://github.com/vuetifyjs/vuetify/blob/master/packages/docs/src/pages/en/getting-started/upgrade-guide.md）
10. Vuetify v4.0.0 Release — https://github.com/vuetifyjs/vuetify/releases/tag/v4.0.0 （npm: vuetify 4.1.12、peer `vue ^3.5.0`）
11. Vuetify: Unit testing（vitest + test-utils、ResizeObserver、`server.deps.inline`） — https://vuetifyjs.com/en/getting-started/unit-testing/
12. Cognito: Updating user pool and app client configuration（作成後に変更できない設定、SMS 無効化不可） — https://docs.aws.amazon.com/cognito/latest/developerguide/cognito-user-pool-updating.html
13. Cognito: Adding MFA to a user pool（`MfaConfiguration` は Create/Update/SetUserPoolMfaConfig、ON 時の挙動、MFA_SETUP、ロックアウト） — https://docs.aws.amazon.com/cognito/latest/developerguide/user-pool-settings-mfa.html
14. Cognito: TOTP software token MFA（AssociateSoftwareToken / VerifySoftwareToken、AdminDeleteSoftwareToken、HMAC-SHA1） — https://docs.aws.amazon.com/cognito/latest/developerguide/user-pool-settings-mfa-totp.html
15. Cognito: User pool feature plans（Lite/Essentials/Plus、TOTP は Lite 可、メール MFA・Managed Login ブランディングは Essentials 以上） — https://docs.aws.amazon.com/cognito/latest/developerguide/cognito-sign-in-feature-plans.html
16. Amplify: Manage user sessions（`fetchAuthSession` 自動更新・`forceRefresh`） — https://docs.amplify.aws/javascript/build-a-backend/auth/connect-your-frontend/manage-user-sessions/
17. Cognito: User pool managed login（機能範囲、日本語化条件、1 時間 Cookie、プロフィール管理は非対応） — https://docs.aws.amazon.com/cognito/latest/developerguide/cognito-user-pools-managed-login.html
18. Amplify: Use existing Cognito resources（`Amplify.configure({ Auth: { Cognito: … } })`） — https://docs.amplify.aws/javascript/build-a-backend/auth/use-existing-cognito-resources/
19. Cognito: Pre sign-up Lambda trigger（イベント構造、拒否方法、autoConfirmUser） — https://docs.aws.amazon.com/cognito/latest/developerguide/user-pool-lambda-pre-sign-up.html
20. Pinia v4.0.0 Release（ESM only、`@vue/devtools-api` 同梱） — https://github.com/vuejs/pinia/releases/tag/v4.0.0
21. Vite 8.0 is out!（Rolldown 統合、Module Federation 対応） — https://vite.dev/blog/announcing-vite8
22. Micro-Frontends in 2026: When They Work（チーム規模の目安） — https://blog.codercops.com/blog/micro-frontends-module-federation-architecture-2026
23. Monoliths, Microfrontends, Monorepos, and the Real Tradeoffs — https://stevekinney.com/courses/enterprise-ui/monoliths-microfrontends-and-monorepos
24. Amplify: Listen to auth events（Hub、`tokenRefresh_failure`） — https://docs.amplify.aws/javascript/build-a-backend/auth/connect-your-frontend/listen-to-auth-events/
25. Amplify: Tokens and credentials（トークン保存先の変更 `cognitoUserPoolsTokenProvider.setKeyValueStorage`） — https://docs.amplify.aws/javascript/build-a-backend/auth/concepts/tokens-and-credentials/
26. aws-cdk Issue #21284: enable mfa on existing user pool（2024-06 クローズ、CloudFormation 対応済み） — https://github.com/aws/aws-cdk/issues/21284
27. cloudformation-coverage-roadmap Issue #1176: Cognito Userpool set-user-pool-mfa-config（2022-10 クローズ） — https://github.com/aws-cloudformation/cloudformation-coverage-roadmap/issues/1176
28. Amplify (Gen1 docs): Manage MFA settings（`setUpTOTP` / `verifyTOTPSetup` / `updateMFAPreference`） — https://docs.amplify.aws/gen1/javascript/build-a-backend/auth/manage-mfa/
29. amplify-js Issue #13175: signIn と confirmSignIn は分離して呼べない（メモリ内状態） — https://github.com/aws-amplify/amplify-js/issues/13175
30. vuejs/language-tools Discussion #6121: vue-tsc support for TypeScript 7 — https://github.com/vuejs/language-tools/discussions/6121
31. Playwright: Authentication（storageState） — https://playwright.dev/docs/auth
32. Cognito API: SetUserPoolMfaConfig（`OFF | ON | OPTIONAL`、`SoftwareTokenMfaConfiguration`） — https://docs.aws.amazon.com/cognito-user-identity-pools/latest/APIReference/API_SetUserPoolMfaConfig.html
33. Cognito: Working with user attributes（必須属性・サインイン識別子は作成後変更不可） — https://docs.aws.amazon.com/cognito/latest/developerguide/user-pool-settings-attributes.html
34. vue-pure-admin `src/router/index.ts`（`import.meta.glob` によるルートモジュール集約） — https://github.com/pure-admin/vue-pure-admin/blob/main/src/router/index.ts

参照した社内リポジトリ（ローカル）
- `/mnt/c/Users/devA/git/ref-project-a/frontend/package.json`、`src/router/index.ts`、`src/stores/auth.ts`、`src/services/api.ts`、`src/components/AppLayout.vue`、`src/plugins/vuetify.ts`、`vitest.config.ts`
- `/mnt/c/Users/devA/git/example-portal/apps/sso-mock/web/package.json`、`apps/ui-lte-m/spa/package.json`、`apps/ui-point-setup/spa/package.json`
- `/mnt/c/Users/devA/git/example-backoffice/Documents/apps/saas-a-billing/DESIGN.md`（第一弾アプリの要件）
