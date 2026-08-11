<template>
  <div>
    <!-- 筛选栏 -->
    <div style="display: flex; gap: 8px; margin-bottom: 12px; flex-wrap: wrap">
      <el-select v-model="filter.rule_type" placeholder="类型" clearable style="width: 150px">
        <el-option v-for="t in ['DETERMINISTIC', 'SEMANTIC']" :key="t" :label="t" :value="t" />
      </el-select>
      <el-select v-model="filter.source" placeholder="来源" clearable style="width: 170px">
        <el-option v-for="s in ['ONTOLOGY_GENERATED', 'MANUAL']" :key="s" :label="s" :value="s" />
      </el-select>
      <el-select v-model="filter.enabled" placeholder="状态" clearable style="width: 100px">
        <el-option label="启用" :value="true" />
        <el-option label="失效" :value="false" />
      </el-select>
      <el-button type="primary" @click="load(1)">查询</el-button>
      <el-button type="success" @click="openCreate">新建规则</el-button>
    </div>

    <el-table :data="items" border size="small">
      <el-table-column prop="id" label="ID" width="60" />
      <el-table-column prop="rule_name" label="规则名称" min-width="140" show-overflow-tooltip />
      <el-table-column prop="rule_type" label="类型" width="120" />
      <el-table-column prop="severity" label="级别" width="80" />
      <el-table-column prop="source" label="来源" width="150" />
      <el-table-column prop="aggregation" label="聚合" width="70" />
      <el-table-column label="状态" width="80">
        <template #default="{ row }">
          <el-tag :type="row.enabled ? 'success' : 'info'" size="small">{{ row.enabled ? '启用' : '失效' }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column label="操作" width="200" fixed="right">
        <template #default="{ row }">
          <el-button size="small" link type="primary" @click="openEdit(row)">编辑</el-button>
          <el-button size="small" link type="warning" @click="openDryRun(row)">试跑</el-button>
          <!-- 人工规则支持启停（软删）与彻底删除；本体规则只读，仅可试跑 -->
          <el-button v-if="row.source === 'MANUAL' && row.enabled" size="small" link type="warning" @click="disable(row)">失效</el-button>
          <el-button v-else-if="row.source === 'MANUAL'" size="small" link type="success" @click="enable(row)">启用</el-button>
          <el-tooltip v-else content="本体自动生成的规则不可失效" placement="top">
            <el-button size="small" link type="info" disabled>失效</el-button>
          </el-tooltip>
          <el-button v-if="row.source === 'MANUAL'" size="small" link type="danger" @click="remove(row)">删除</el-button>
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

    <!-- 编辑 / 新建抽屉 -->
    <el-drawer v-model="drawer" :title="editing ? '编辑规则' : '新建规则'" size="560px">
      <el-form label-width="90px">
        <el-form-item label="规则名称">
          <el-input v-model="form.rule_name" />
        </el-form-item>
        <el-form-item label="严重级别">
          <el-radio-group v-model="form.severity">
            <el-radio-button value="HIGH">高</el-radio-button>
            <el-radio-button value="MEDIUM">中</el-radio-button>
            <el-radio-button value="LOW">低</el-radio-button>
          </el-radio-group>
        </el-form-item>
        <el-form-item :label="form.rule_type === 'SEMANTIC' ? 'Prompt' : 'SPARQL'">
          <el-input
            v-model="form.expression"
            type="textarea"
            :rows="10"
            :placeholder="form.rule_type === 'SEMANTIC' ? '语义校验 prompt，要求返回 pass/reason/evidence/applicable' : 'SPARQL 反例查询'"
          />
        </el-form-item>
        <el-form-item label="聚合方式" v-if="form.rule_type === 'SEMANTIC'">
          <el-radio-group v-model="form.aggregation">
            <el-radio-button value="any">any（任一命中即报）</el-radio-button>
            <el-radio-button value="all">all（全部段缺失才报）</el-radio-button>
          </el-radio-group>
        </el-form-item>
        <el-form-item label="描述">
          <el-input v-model="form.description" type="textarea" :rows="2" />
        </el-form-item>

        <!-- 示例与讲解：降低新规则上手门槛。注意内容必须直接放在 collapse-item 内，
             不能套 <template>——Vue3 里裸 <template> 会渲染成 display:none 的真实节点，内容将永远不可见 -->
        <el-collapse v-model="activeGuide" style="margin-top: 4px">
          <el-collapse-item title="示例与说明" name="guide">
            <p class="dim">语义规则用 LLM 判断「需要理解的违背」，expression 写判断指令。样例（违约条款检测）：</p>
            <pre class="example-block">{{ SEMANTIC_EXAMPLE }}</pre>
            <p class="dim">· prompt 须让 LLM 逐条返回 JSON 四字段：pass（是否违规）/ reason / evidence（原文证据，必须是原文子串）/ applicable（规则是否适用，false 计入 SKIPPED）</p>
            <p class="dim">· 聚合方式：<b>any</b>=任一适用段命中即报；<b>all</b>=按整个合同整体判断（某段缺失不算违规）</p>
          </el-collapse-item>
        </el-collapse>
      </el-form>
      <template #footer>
        <el-button @click="drawer = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="save">保存</el-button>
      </template>
    </el-drawer>

    <!-- 试跑抽屉 -->
    <el-drawer v-model="runDrawer" :title="`规则试跑${runRule ? '：' + runRule.rule_name : ''}`" size="420px">
      <p class="dim">基于历史任务数据试跑，不落库。选择已校验过的任务。</p>
      <el-select v-model="runTaskId" placeholder="选择任务" filterable style="width: 100%">
        <el-option
          v-for="t in tasks"
          :key="t.id"
          :label="`#${t.id} ${t.file_name}（${t.status}）`"
          :value="t.id"
        />
      </el-select>
      <el-button type="primary" style="margin-top: 12px" :loading="running" :disabled="!runTaskId" @click="run">试跑</el-button>
      <div v-if="runResult" style="margin-top: 16px">
        <el-tag :type="runResult.result === 'PASS' ? 'success' : runResult.result === 'FAIL' ? 'danger' : 'info'" size="small">
          {{ runResult.result }}
        </el-tag>
        <el-tag v-if="runResult.confidence" size="small" style="margin-left: 6px">{{ runResult.confidence }}</el-tag>
        <el-tag size="small" style="margin-left: 6px">token 成本 {{ runResult.token_cost }}</el-tag>
        <p v-if="runResult.message" style="margin-top: 10px" class="dim">{{ runResult.message }}</p>
        <p v-if="runResult.subjects && runResult.subjects.length" style="margin-top: 10px" class="dim">
          命中主体：{{ runResult.subjects.join('、') }}
        </p>
      </div>
    </el-drawer>
  </div>
</template>

<script setup>
import { onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { createRule, deleteRule, dryRunRule, listRules, listTasks, updateRule } from '../api'

const filter = reactive({ rule_type: null, source: null, enabled: null, page: 1, size: 20 })
const items = ref([])
const total = ref(0)

const drawer = ref(false)
const editing = ref(false)
const saving = ref(false)
const form = reactive({})

const runDrawer = ref(false)
const runRule = ref(null)
const runTaskId = ref(null)
const tasks = ref([])
const running = ref(false)
const runResult = ref(null)

const activeGuide = ref(['guide'])  // 默认展开示例说明，降低新规则上手门槛
// 示例素材取自 rules/manual/ 现有语义规则（与 seed 一致），新增规则时供参考
const SEMANTIC_EXAMPLE = `检查本合同是否具备明确的违约责任条款。判定依据是整个合同文本：只要原文明确了违约金数额、违约金比例（如"合同总价的10%"）、赔偿范围、责任承担方式中的任一具体内容，即视为具备明确违约责任条款，判定通过（pass=true）；只有当整个合同完全没有违约/赔偿/责任承担相关约定，或仅有"任何一方违约应赔偿损失"这类无任何具体化的泛泛表述时，才判定违约（pass=false）。`

function load(page) {
  filter.page = page
  listRules({
    page: filter.page, size: filter.size,
    rule_type: filter.rule_type || undefined,
    source: filter.source || undefined,
    enabled: filter.enabled ?? undefined,
  }).then((r) => {
    items.value = r.items
    total.value = r.total
  })
}

function openCreate() {
  editing.value = false
  Object.assign(form, {
    rule_name: '', rule_type: 'SEMANTIC', severity: 'MEDIUM',
    expression: '', aggregation: 'any', description: '',
  })
  drawer.value = true
}

function openEdit(row) {
  editing.value = true
  Object.assign(form, { ...row })
  drawer.value = true
}

function save() {
  if (!form.rule_name || !form.expression) {
    ElMessage.warning('规则名称和表达式不能为空')
    return
  }
  saving.value = true
  const body = {
    name: form.rule_name,
    expression: form.expression,
    severity: form.severity,
    description: form.description || undefined,
    aggregation: form.rule_type === 'SEMANTIC' ? form.aggregation : undefined,
  }
  const done = () => {
    saving.value = false
    drawer.value = false
    ElMessage.success('保存成功')
    load(filter.page)  // R2：保留当前页，不跳回第一页
  }
  if (editing.value) {
    updateRule(form.id, body).then(done).catch((e) => {
      saving.value = false
      ElMessage.error(e.response?.data?.detail || '保存失败')
    })
  } else {
    createRule({ type: form.rule_type, ...body }).then(done).catch((e) => {
      saving.value = false
      ElMessage.error(e.response?.data?.detail || '创建失败')
    })
  }
}

function disable(row) {
  ElMessageBox.confirm(`确定失效规则「${row.rule_name}」？失效后不再参与校验。`, '提示', { type: 'warning' })
    .then(() => updateRule(row.id, { enabled: false }))
    .then(() => {
      ElMessage.success('已失效')
      load(filter.page)
    })
    .catch((e) => {
      if (e !== 'cancel') {
        ElMessage.error(e?.response?.data?.detail || '失效失败')
      }
    })
}

function remove(row) {
  ElMessageBox.confirm(`确定彻底删除规则「${row.rule_name}」？删除后不可恢复。`, '删除确认', { type: 'warning' })
    .then(() => deleteRule(row.id))
    .then(() => {
      ElMessage.success('已删除')
      load(filter.page)
    })
    .catch((e) => {
      if (e !== 'cancel') {
        ElMessage.error(e?.response?.data?.detail || '删除失败')
      }
    })
}

function enable(row) {
  updateRule(row.id, { enabled: true })
    .then(() => {
      ElMessage.success('已启用')
      load(filter.page)
    })
    .catch((e) => ElMessage.error(e.response?.data?.detail || '启用失败'))
}

function openDryRun(row) {
  runRule.value = row
  runResult.value = null
  runTaskId.value = null
  // R3：只列出有校验数据的任务（FAILED/CANCELLED 无 RDF/segments，试跑无意义）
  listTasks({ page: 1, size: 50 }).then((r) => {
    tasks.value = r.items.filter((t) => ['SUCCESS', 'WAITING_REVIEW'].includes(t.status))
  })
  runDrawer.value = true
}

function run() {
  running.value = true
  dryRunRule(runRule.value.id, runTaskId.value)
    .then((r) => { runResult.value = r })
    .catch((e) => ElMessage.error(e.response?.data?.detail || '试跑失败'))
    .finally(() => { running.value = false })
}

onMounted(() => load(1))
</script>

<style scoped>
.dim { color: #909399; font-size: 12px; line-height: 1.7; }
.example-block {
  background: #f5f7fa; border: 1px solid #e4e7ed; border-radius: 4px;
  padding: 8px 12px; font-size: 12px; white-space: pre-wrap;
  margin: 6px 0; max-height: 220px; overflow: auto;
}
</style>
