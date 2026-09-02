<template>
  <div class="text-center py-12">
    <v-icon
      icon="mdi-lock-outline"
      color="warning"
      size="64"
    />
    <h1 class="text-h5 mt-4 mb-2">
      アクセス権限がありません
    </h1>
    <p class="text-body-2 text-medium-emphasis mb-6">
      この画面を表示する権限がありません。利用が必要な場合は管理者へ申請してください。
    </p>
    <v-btn
      color="primary"
      variant="tonal"
      :to="{ name: 'home' }"
    >
      ホームへ戻る
    </v-btn>
  </div>
</template>
