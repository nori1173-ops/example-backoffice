import type { RouteRecordRaw } from 'vue-router'

export const routes: RouteRecordRaw[] = [
  { path: '', name: 'daily-security-report.days', component: () => import('./views/DayListView.vue'),
    meta: { title: 'レポート一覧' } },
  { path: 'days/:date', name: 'daily-security-report.day', component: () => import('./views/DayDetailView.vue'),
    meta: { title: 'レポートの詳細' } },
  { path: 'settings', name: 'daily-security-report.settings', component: () => import('./views/SettingsView.vue'),
    meta: { title: '設定', requiredGroups: ['admin'] } },
]
