import type { RouteRecordRaw } from 'vue-router'

export const routes: RouteRecordRaw[] = [
  {
    path: '',
    name: 'admin-users.users',
    component: () => import('./views/UsersView.vue'),
    meta: { title: 'ユーザー管理' },
  },
]
