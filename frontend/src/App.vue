<template>
  <!-- 鉴权门控：后端要求登录（health.auth_required）且本机无有效 token 时展示登录页 -->
  <Login v-if="authed === false" @success="authed = true" />
  <!-- authed 未决（health 未返回）先占位，避免主界面一闪而过 -->
  <div v-else-if="authed === null" class="boot">加载中…</div>
  <el-container v-else class="layout">
    <el-aside width="180px" class="aside">
      <div class="brand">合同校验系统</div>
      <el-menu :default-active="active" @select="onSelect">
        <el-menu-item index="workbench">工作台</el-menu-item>
        <el-menu-item index="history">历史记录</el-menu-item>
        <el-menu-item index="rules">规则管理</el-menu-item>
      </el-menu>
      <div class="logout-bar"><el-button size="small" link @click="doLogout">退出登录</el-button></div>
    </el-aside>
    <el-main>
      <Workbench v-if="active === 'workbench'" :key="workbenchKey" :initial-task-id="initialTaskId" :show-back="showBack" @clear-initial="initialTaskId = null" @back="goBack" />
      <!-- KeepAlive 常挂载（缓存 History 状态：从详情返回保留当前页与筛选）；不能用 v-else-if，否则条件变 false 时整个 KeepAlive 卸载丢缓存 -->
      <KeepAlive>
        <History v-if="active === 'history'" @open="openTask" />
      </KeepAlive>
      <Rules v-if="active === 'rules'" />
    </el-main>
  </el-container>
</template>

<script setup>
import { onMounted, ref } from 'vue'
import Workbench from './views/Workbench.vue'
import History from './views/History.vue'
import Rules from './views/Rules.vue'
import Login from './views/Login.vue'
import { clearToken, getHealth, getToken } from './api'

const authed = ref(null)   // null=未知（health 返回前）；false=需登录；true=放行
const active = ref('workbench')
const initialTaskId = ref(null)
const workbenchKey = ref(0)
const showBack = ref(false)   // 从历史记录打开详情时显示「返回列表」

onMounted(async () => {
  try {
    const health = await getHealth()
    authed.value = !health.auth_required || !!getToken()
  } catch {
    authed.value = true   // 后端不可达时先放行主界面，由各请求自行报错
  }
})

// 任一受保护请求返回 401（token 失效）→ 全局回登录页
window.addEventListener('auth:expired', () => { authed.value = false })

function doLogout() {
  clearToken()
  authed.value = false
}

function onSelect(idx) { active.value = idx }

function openTask(id) {
  initialTaskId.value = id
  workbenchKey.value += 1     // 强制重建 Workbench 以加载该任务
  showBack.value = true       // 来自历史记录，可返回列表
  active.value = 'workbench'
}

function goBack() {
  active.value = 'history'
  showBack.value = false
}
</script>

<style scoped>
.boot { min-height: 100vh; display: flex; align-items: center; justify-content: center; color: #909399; }
.layout { min-height: 100vh; }
.aside { background: #fff; border-right: 1px solid #ebeef5; display: flex; flex-direction: column; }
.brand { font-size: 16px; font-weight: 600; padding: 16px 12px; color: #409eff; }
.logout-bar { margin-top: auto; padding: 12px; text-align: center; border-top: 1px solid #ebeef5; }
</style>
