/// <reference types="vite/client" />

declare module '*.vue' {
  import type { DefineComponent } from 'vue'
  const component: DefineComponent<Record<string, never>, Record<string, never>, unknown>
  export default component
}

import 'vue-router'

declare module 'vue-router' {
  interface RouteMeta {
    requiresAuth?: boolean
    requiredGroups?: string[]
    appId?: string
    title?: string
    layout?: 'shell' | 'auth'
  }
}

declare global {
  interface ImportMetaEnv {
    readonly VITE_COGNITO_USER_POOL_ID: string
    readonly VITE_COGNITO_CLIENT_ID: string
    /** サイトの配置環境。未設定は検証サイト扱い（設計 R7） */
    readonly VITE_SITE_ENV?: 'dev' | 'prod'
  }
}
