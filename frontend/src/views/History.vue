<template>
  <div>
    <div style="display: flex; gap: 8px; margin-bottom: 12px">
      <el-select v-model="filter.status" placeholder="状态" clearable style="width: 160px">
        <el-option v-for="s in ['PENDING','PARSING','EXTRACTING','VALIDATING','WAITING_REVIEW','REVIEWING','SUCCESS','FAILED','CANCELLED']" :key="s" :label="s" :value="s" />
      </el-select>
      <el-input v-model="filter.file_name" placeholder="文件名" clearable style="width: 220px" @keyup.enter="load(1)" />
      <el-button type="primary" @click="load(1)">查询</el-button>
    </div>

    <el-table :data="items" border size="small" @row-click="openDetail">
      <el-table-column prop="id" label="任务ID" width="80" />
      <el-table-column prop="file_name" label="文件名" min-width="180" />
      <el-table-column label="最终结果" width="120">
        <template #default="{ row }"><el-tag :type="finalTagType(row.status)" size="small">{{ finalLabel(row.status) }}</el-tag></template>
      </el-table-column>
      <el-table-column prop="extraction_status" label="抽取" width="110" />
      <el-table-column prop="create_time" label="创建时间" width="180" />
      <el-table-column label="操作" width="210" fixed="right">
        <template #default="{ row }">
          <el-button v-if="['SUCCESS','FAILED','CANCELLED'].includes(row.status)" size="small" link type="primary" @click.stop="downloadReport(row.id, 'pdf')">PDF</el-button>
          <el-button v-if="['SUCCESS','FAILED','CANCELLED'].includes(row.status)" size="small" link type="success" @click.stop="downloadReport(row.id, 'xlsx')">Excel</el-button>
          <el-button size="small" link type="danger" @click.stop="doDelete(row)">删除</el-button>
        </template>
      </el-table-column>
    </el-table>

    <el-pagination
      style="margin-top: 12px"
      layout="total, prev, pager, next"
      :total="total"
      :page-size="filter.size"
      v-model:current-page="filter.page"
      @current-change="load"
    />
    <div class="dim">点击行查看任务详情</div>
  </div>
</template>

<script setup>
import { onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { deleteTask, downloadReportFile, listTasks, saveBlob } from '../api'

const emit = defineEmits(['open'])
const items = ref([])
const total = ref(0)
const filter = reactive({ status: null, file_name: '', page: 1, size: 10 })

// 最终结果（业务语义）：SUCCESS→验证通过 / FAILED→验证失败 / WAITING_REVIEW+REVIEWING→等待人工审核 / CANCELLED→已取消 / 其余→处理中
const finalLabel = (s) => ({
  SUCCESS: '验证通过', FAILED: '验证失败',
  WAITING_REVIEW: '等待人工审核', REVIEWING: '等待人工审核',
  CANCELLED: '已取消',
}[s] || '处理中')
const finalTagType = (s) => ({
  SUCCESS: 'success', FAILED: 'danger',
  WAITING_REVIEW: 'warning', REVIEWING: 'warning',
  CANCELLED: 'info',
}[s] || 'primary')

async function load(page) {
  filter.page = page
  const r = await listTasks({
    page: filter.page, size: filter.size,
    status: filter.status || undefined,
    file_name: filter.file_name || undefined,
  })
  items.value = r.items
  total.value = r.total
}

function openDetail(row) { emit('open', row.id) }

// 导出报告：<a href> 直链不带 Authorization header，改为带 token 的 axios blob 下载
async function downloadReport(taskId, format) {
  try {
    const res = await downloadReportFile(taskId, format)
    saveBlob(res, `report_${taskId}.${format === 'pdf' ? 'pdf' : 'xlsx'}`)
  } catch (e) {
    ElMessage.error(e?.response?.status === 401 ? '登录已失效，请重新登录' : '导出失败，请重试')
  }
}

function doDelete(row) {
  ElMessageBox.confirm(
    `确定删除任务 #${row.id}「${row.file_name}」？关联校验明细、异常及独占文件将一并删除，且不可恢复。`,
    '删除确认', { type: 'warning' },
  )
    .then(() => deleteTask(row.id))
    .then(() => {
      ElMessage.success('已删除')
      load(filter.page)
    })
    .catch(() => {})
}

onMounted(() => load(1))
</script>

<style scoped>
.dim { color: #909399; font-size: 12px; margin-top: 8px; }
</style>
