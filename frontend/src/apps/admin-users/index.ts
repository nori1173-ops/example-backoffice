import { defineApp } from '../types'
import { routes } from './routes'

// admin 専用アプリ。サイドバーの表示もルートの遷移も requiredGroups が制御するが、
// 権限の実体はバックエンド（全 API が require_group('admin')）にある（設計 6.3）。
export default defineApp({
  id: 'admin-users',
  title: 'ユーザー管理',
  icon: 'mdi-account-cog-outline',
  description: 'サインイン済みユーザーの権限・有効/無効・パスワードリセット・削除',
  order: 90,
  requiredGroups: ['admin'],
  routes,
  nav: [{ title: 'ユーザー一覧', to: '/admin-users', icon: 'mdi-account-multiple-outline' }],
})
