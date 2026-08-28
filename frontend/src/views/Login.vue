<template>
  <div class="login-wrap">
    <el-card class="login-card">
      <template #header>
        <div class="login-title">合同校验系统</div>
      </template>
      <el-form label-position="top" @submit.prevent="doLogin">
        <el-form-item label="用户名">
          <el-input v-model="username" placeholder="请输入用户名" @keyup.enter="doLogin" />
        </el-form-item>
        <el-form-item label="密码">
          <el-input v-model="password" type="password" show-password placeholder="请输入密码" @keyup.enter="doLogin" />
        </el-form-item>
        <el-button type="primary" style="width: 100%" :loading="loading" @click="doLogin">登 录</el-button>
      </el-form>
    </el-card>
  </div>
</template>

<script setup>
import { ref } from 'vue'
import { ElMessage } from 'element-plus'
import { login, setToken } from '../api'

const emit = defineEmits(['success'])
const username = ref('')
const password = ref('')
const loading = ref(false)

async function doLogin() {
  if (!username.value || !password.value) {
    ElMessage.warning('请输入用户名和密码')
    return
  }
  loading.value = true
  try {
    const res = await login(username.value, password.value)
    setToken(res.token)
    ElMessage.success('登录成功')
    emit('success')
  } catch (e) {
    const status = e?.response?.status
    if (status === 503) {
      ElMessage.error('服务端未配置登录口令（AUTH_PASSWORD），请联系管理员')
    } else {
      ElMessage.error('用户名或密码错误')
    }
  } finally {
    loading.value = false
  }
}
</script>

<style scoped>
.login-wrap {
  min-height: 100vh;
  display: flex;
  align-items: center;
  justify-content: center;
  background: #f5f7fa;
}
.login-card { width: 360px; }
.login-title { text-align: center; font-size: 18px; font-weight: 600; color: #409eff; }
</style>
