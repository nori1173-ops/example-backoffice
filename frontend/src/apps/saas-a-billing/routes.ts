import type { RouteRecordRaw } from 'vue-router'

export const routes: RouteRecordRaw[] = [
  {
    path: '',
    name: 'saas-a-billing.runs',
    component: () => import('./views/RunListView.vue'),
    meta: { title: '実行履歴' },
  },
  {
    path: 'runs/:targetEnv/:month',
    name: 'saas-a-billing.run-detail',
    component: () => import('./views/RunDetailView.vue'),
    meta: { title: '月次内訳' },
  },
  {
    path: 'settings',
    name: 'saas-a-billing.settings',
    component: () => import('./views/SettingsView.vue'),
    meta: { title: '設定', requiredGroups: ['admin'] },
  },
]
