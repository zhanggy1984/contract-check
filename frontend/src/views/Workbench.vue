<template>
  <div>
    <!-- 从历史记录打开详情时显示返回按钮，回列表页当前页 -->
    <div v-if="showBack" style="margin-bottom: 12px">
      <el-button size="small" @click="emit('back')">← 返回列表</el-button>
    </div>
    <!-- 上传（el-upload 走原生 XHR，不经 axios 拦截器，需显式带 Authorization） -->
    <el-upload
      drag
      action="/api/files/upload"
      name="file"
      :headers="uploadHeaders"
      :show-file-list="false"
      :on-success="onUploaded"
      :on-error="onUploadError"
      :disabled="!!task"
      style="margin-bottom: 16px"
    >
      <div style="padding: 24px">拖拽或点击上传合同（PDF / DOCX），上传后自动开始校验</div>
    </el-upload>

    <!-- 任务状态 -->
    <el-card v-if="task">
      <div style="display: flex; align-items: center; gap: 12px; margin-bottom: 8px">
        <span>任务 #{{ task.id }}</span>
        <el-tag :type="statusTagType(task.status)">{{ task.status }}</el-tag>
        <span v-if="task.extraction_status" class="dim">抽取质量：{{ task.extraction_status }}</span>
        <!-- 抽取中（PENDING/PARSING/EXTRACTING）可取消；抽取完成后任务不可取消，按钮变灰 -->
        <el-button v-if="['PENDING', 'PARSING', 'EXTRACTING'].includes(task.status)" size="small" @click="doCancel">取消任务</el-button>
        <el-tooltip v-else-if="['VALIDATING', 'WAITING_REVIEW', 'REVIEWING'].includes(task.status)" content="抽取已完成，任务不可取消" placement="top">
          <el-button size="small" disabled>取消任务</el-button>
        </el-tooltip>
      </div>
      <el-progress :percentage="task.progress || 0" style="margin-bottom: 8px" />
      <div v-if="task.message" style="color: #f56c6c; margin-bottom: 8px">{{ task.message }}</div>
      <div v-if="task.status === 'REVIEWING'" style="color: #e6a23c; margin-bottom: 8px">正在提交审核，请稍候…</div>
      <el-alert v-if="task.conflicts && task.conflicts.length" type="warning" :closable="false" show-icon
        title="跨段字段冲突（已标低置信，建议复核）" style="margin-bottom: 8px">
        <span>字段：{{ conflictLabel(task.conflicts) }}</span>
      </el-alert>
    </el-card>

    <!-- 审核视图：逐条确认/误报 -->
    <el-card v-if="task && task.status === 'WAITING_REVIEW' && violations.length" title="人工审核" style="margin-top: 16px">
      <el-table :data="violations" size="small" border>
        <el-table-column label="严重级别" width="90">
          <template #default="{ row }"><el-tag :type="severityTagType(row.severity)" size="small">{{ row.severity }}</el-tag></template>
        </el-table-column>
        <el-table-column label="置信度" width="90">
          <template #default="{ row }"><el-tag :type="confidenceTagType(row.confidence)" size="small">{{ confidenceLabel(row.confidence) }}</el-tag></template>
        </el-table-column>
        <el-table-column label="命中说明" prop="message" min-width="180" />
        <el-table-column label="段落" prop="segment_ref" width="80" />
        <el-table-column label="原文证据" min-width="180">
          <template #default="{ row }">
            <span v-if="row.evidence_text" class="dim">{{ row.evidence_text }}</span>
            <span v-else-if="row.confidence === 'LOW'" class="dim">（无原文证据，低置信）</span>
            <span v-else class="dim">—</span>
          </template>
        </el-table-column>
        <el-table-column label="审核" width="220">
          <template #default="{ row }">
            <el-radio-group v-model="reviewMap[row.id]" :disabled="submitting">
              <el-radio-button value="CONFIRMED">确认问题</el-radio-button>
              <el-radio-button value="FALSE_POSITIVE">误报</el-radio-button>
            </el-radio-group>
          </template>
        </el-table-column>
      </el-table>
      <div style="margin-top: 12px; display: flex; gap: 8px">
        <el-button type="primary" :loading="submitting" :disabled="!allReviewed" @click="submitReviews()">提交审核</el-button>
        <span v-if="!allReviewed" class="dim">请为全部异常选择「确认问题」或「误报」</span>
      </div>
    </el-card>
    <el-card v-if="task && task.status === 'WAITING_REVIEW' && !violations.length" style="margin-top: 16px">
      <span>无校验异常，可直接确认通过</span>
      <el-button type="primary" style="margin-left: 12px" :loading="submitting" @click="submitReviews([])">确认通过</el-button>
    </el-card>

    <!-- 结果视图 -->
    <el-card v-if="result && ['SUCCESS', 'FAILED', 'CANCELLED'].includes(task.status)" style="margin-top: 16px">
      <template #header>
        <div style="display: flex; align-items: center; justify-content: space-between">
          <span>校验结果</span>
          <div>
            <el-button size="small" type="primary" @click="downloadReport('pdf')">导出 PDF</el-button>
            <el-button size="small" type="success" @click="downloadReport('xlsx')">导出 Excel</el-button>
          </div>
        </div>
      </template>

      <h4>标准文本 JSON</h4>
      <pre class="json-block">{{ prettyJson(result.standard_json) }}</pre>

      <h4 style="margin-top: 16px">校验明细</h4>
      <el-table :data="result.rule_results" size="small" border>
        <el-table-column label="结果" width="100">
          <template #default="{ row }">
            <el-tag :type="row.result === 'PASS' ? 'success' : row.result === 'FAIL' ? 'danger' : 'info'" size="small">{{ row.result }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="规则" min-width="160" show-overflow-tooltip>
          <template #default="{ row }">{{ row.rule_name || ('规则 ' + row.rule_id) }}</template>
        </el-table-column>
        <el-table-column label="类型" prop="rule_type" width="130" />
        <el-table-column label="严重级别" width="90">
          <template #default="{ row }"><el-tag :type="severityTagType(row.severity)" size="small">{{ row.severity }}</el-tag></template>
        </el-table-column>
        <el-table-column label="置信度" width="90">
          <template #default="{ row }"><el-tag :type="confidenceTagType(row.confidence)" size="small">{{ confidenceLabel(row.confidence) }}</el-tag></template>
        </el-table-column>
        <el-table-column label="命中说明" prop="message" />
        <el-table-column label="证据" min-width="160">
          <template #default="{ row }"><span v-if="row.evidence_text" class="dim">{{ row.evidence_text }}</span></template>
        </el-table-column>
      </el-table>

      <h4 style="margin-top: 16px">异常列表（{{ result.violations.length }}）</h4>
      <el-table :data="result.violations" size="small" border>
        <el-table-column label="状态" width="130">
          <template #default="{ row }">
            <el-tag :type="row.status === 'CONFIRMED' ? 'success' : row.status === 'FALSE_POSITIVE' ? 'warning' : 'info'" size="small">{{ row.status }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="严重级别" width="90">
          <template #default="{ row }"><el-tag :type="severityTagType(row.severity)" size="small">{{ row.severity }}</el-tag></template>
        </el-table-column>
        <el-table-column label="置信度" width="90">
          <template #default="{ row }"><el-tag :type="confidenceTagType(row.confidence)" size="small">{{ confidenceLabel(row.confidence) }}</el-tag></template>
        </el-table-column>
        <el-table-column label="命中说明" prop="message" />
        <el-table-column label="字段" width="180">
          <template #default="{ row }">{{ row.property_iri?.split('#')[1] || '' }}</template>
        </el-table-column>
        <el-table-column label="确认人" prop="confirm_user" width="100" />
      </el-table>
    </el-card>
  </div>
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import {
  cancelTask, clearToken, downloadReportFile, getTask, getTaskResult, getToken, resumeTask, saveBlob,
} from '../api'

const props = defineProps({
  initialTaskId: { type: Number, default: null },
  showBack: { type: Boolean, default: false },  // 从历史记录打开时显示返回按钮
})
const emit = defineEmits(['clear-initial', 'back'])

const task = ref(null)
const result = ref(null)
const violations = ref([])
const reviewMap = ref({})
const submitting = ref(false)
let timer = null
let loadedResult = false

// el-upload 原生 XHR 不会走 axios 拦截器，这里显式注入 token
const uploadHeaders = computed(() => ({ Authorization: `Bearer ${getToken()}` }))

const statusTagType = (s) => ({
  SUCCESS: 'success', FAILED: 'danger', CANCELLED: 'info',
  WAITING_REVIEW: 'warning', REVIEWING: 'warning', VALIDATING: 'primary',
}[s] || 'primary')
const severityTagType = (s) => ({ HIGH: 'danger', MEDIUM: 'warning', LOW: 'info' }[s] || 'info')
const confidenceLabel = (c) => (c === 'LOW' ? '低置信' : '高置信')
const confidenceTagType = (c) => (c === 'LOW' ? 'warning' : 'info')
// 抽取字段中文映射：conflicts 数组里的 schema 字段名 → 可读中文
const FIELD_LABELS = {
  contractTitle: '合同标题', totalAmount: '合同总额', effectiveDate: '生效日期',
  contractNo: '合同编号', signedDate: '签订日期', signingPlace: '签订地点',
  currency: '币种', taxRate: '税率', invoiceType: '发票类型', contractType: '合同类型',
  depositAmount: '定金金额', depositType: '定金类型', depositRefundCondition: '定金退还条件',
}
function conflictLabel(conflicts) {
  return conflicts.map((c) => FIELD_LABELS[c] || c).join('、')
}
const allReviewed = computed(() =>
  violations.value.length > 0 && violations.value.every((v) => reviewMap.value[v.id]))

function onUploaded(res) {
  ElMessage.success('上传成功，任务已创建')
  reset()
  startPoll(res.task_id)
}

// el-upload 原生 XHR 失败不经 axios 响应拦截器：401（token 过期）手动清 token + 触发登出，
// 其余失败给通用提示——否则静态注入的旧 token 上传 401 会陷入"半死会话"无感知
function onUploadError(err) {
  if (err?.status === 401) {
    clearToken()
    window.dispatchEvent(new CustomEvent('auth:expired'))
    ElMessage.error('登录已失效，请重新登录')
    return
  }
  ElMessage.error('上传失败，请检查文件后重试')
}

function reset() {
  stopPoll()
  task.value = null
  result.value = null
  violations.value = []
  reviewMap.value = {}
  submitting.value = false
  loadedResult = false
}

function startPoll(id) {
  task.value = { id, status: 'PENDING', progress: 0 }
  timer = setInterval(async () => {
    const t = await getTask(id)
    task.value = { ...t }
    if (t.status === 'WAITING_REVIEW' && !violations.value.length && !loadedResult) {
      loadResult(id)
    }
    if (['SUCCESS', 'FAILED', 'CANCELLED'].includes(t.status)) {
      stopPoll()
      // 终态统一刷新最终 result：FAILED（确认的异常）需同步异常状态为 CONFIRMED，
      // 否则提交审核后仍显示提交前加载的 UNCONFIRMED 旧数据
      loadResult(id)
    }
  }, 1500)
}

async function loadResult(id) {
  loadedResult = true
  const r = await getTaskResult(id)
  result.value = r
  violations.value = r.violations || []
  reviewMap.value = {}
}

// 从历史记录跳转：加载已有任务
async function loadExisting(id) {
  reset()
  startPoll(id)
  const t = await getTask(id)
  task.value = { ...t }
  if (t.status === 'WAITING_REVIEW' || ['SUCCESS', 'FAILED', 'CANCELLED'].includes(t.status)) {
    loadResult(id)
  }
}

// 导出报告：<a href> 直链不带 Authorization header，改为带 token 的 axios blob 下载
async function downloadReport(format) {
  if (!task.value) return
  try {
    const res = await downloadReportFile(task.value.id, format)
    saveBlob(res, `report_${task.value.id}.${format === 'pdf' ? 'pdf' : 'xlsx'}`)
  } catch (e) {
    ElMessage.error(e?.response?.status === 401 ? '登录已失效，请重新登录' : '导出失败，请重试')
  }
}

onMounted(() => {
  if (props.initialTaskId) {
    loadExisting(props.initialTaskId)
    emit('clear-initial')
  }
})

async function submitReviews(reviews) {
  submitting.value = true
  const list = reviews || Object.entries(reviewMap.value).map(([id, action]) => ({
    violation_id: Number(id), action,
  }))
  try {
    await resumeTask(task.value.id, list)
    ElMessage.success('审核已提交')
    stopPoll()
    // resume 后 REVIEWING→SUCCESS 需要再轮询一次
    task.value.status = 'REVIEWING'
    startPoll(task.value.id)
  } catch (e) {
    ElMessage.error(e?.response?.data?.detail || '提交失败，请重试')
    submitting.value = false
  }
}

async function doCancel() {
  try {
    await cancelTask(task.value.id)
    ElMessage.warning('任务已取消')
    stopPoll()
    task.value.status = 'CANCELLED'
    loadResult(task.value.id)
  } catch (e) {
    ElMessage.error(e?.response?.data?.detail || '取消失败')
  }
}

function prettyJson(obj) {
  try { return obj ? JSON.stringify(obj, null, 2) : '（无）' } catch { return String(obj) }
}

function stopPoll() { clearInterval(timer); timer = null }
onBeforeUnmount(stopPoll)
</script>

<style scoped>
.dim { color: #909399; font-size: 12px; }
.json-block { background: #f5f7fa; padding: 12px; border-radius: 6px; max-height: 360px; overflow: auto; }
</style>
