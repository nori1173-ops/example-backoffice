import type { RouteRecordRaw } from 'vue-router'

// アプリ id に使えないパス（設計 9.1）
export const RESERVED_PATHS: readonly string[] = [
  'login',
  'signup',
  'mfa',
  'forbidden',
  'api',
  'assets',
]

export interface AppNavItem {
  title: string
  to: string // '/saas-a-billing/settings' のような絶対パス
  icon?: string // 'mdi-cog'
  requiredGroups?: string[] // 未指定ならアプリの requiredGroups を継承
}

export interface AppManifest {
  id: string // kebab-case。SPA ルート /{id}、API /api/{id}、Cognito 判定に使う
  title: string // サイドバー表示名
  icon: string // mdi アイコン名
  description?: string // ホーム画面のカード説明
  order?: number // サイドバー並び順（小さいほど上、既定 50）
  requiredGroups?: string[] // 空なら全ログインユーザー
  routes: RouteRecordRaw[] // /{id} 配下の子ルート。path は相対、component は () => import()
  nav: AppNavItem[] // アプリ内サブナビ（app-bar のタブに描画）
}

export function defineApp(manifest: AppManifest): AppManifest {
  return manifest
}
