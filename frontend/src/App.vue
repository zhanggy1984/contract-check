<template>
  <el-container class="layout">
    <el-aside width="180px" class="aside">
      <div class="brand">合同校验系统</div>
      <el-menu :default-active="active" @select="onSelect">
        <el-menu-item index="workbench">工作台</el-menu-item>
        <el-menu-item index="history">历史记录</el-menu-item>
        <el-menu-item index="rules">规则管理</el-menu-item>
      </el-menu>
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
import { ref } from 'vue'
import Workbench from './views/Workbench.vue'
import History from './views/History.vue'
import Rules from './views/Rules.vue'

const active = ref('workbench')
const initialTaskId = ref(null)
const workbenchKey = ref(0)
const showBack = ref(false)   // 从历史记录打开详情时显示「返回列表」

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
.layout { min-height: 100vh; }
.aside { background: #fff; border-right: 1px solid #ebeef5; }
.brand { font-size: 16px; font-weight: 600; padding: 16px 12px; color: #409eff; }
</style>
