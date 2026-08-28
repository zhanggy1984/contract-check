import axios from 'axios'

// 单用户 JWT 鉴权：token 存 localStorage；请求拦截器统一加 Authorization；
// 401 响应清除 token 并广播 auth:expired（App.vue 监听后回到登录页）
const TOKEN_KEY = 'cc_token'

export const getToken = () => localStorage.getItem(TOKEN_KEY)
export const setToken = (t) => localStorage.setItem(TOKEN_KEY, t)
export const clearToken = () => localStorage.removeItem(TOKEN_KEY)

const http = axios.create({ baseURL: '/api' })

http.interceptors.request.use((config) => {
  const token = getToken()
  if (token) config.headers.Authorization = `Bearer ${token}`
  return config
})

http.interceptors.response.use(
  (r) => r,
  (err) => {
    if (err.response?.status === 401) {
      clearToken()
      window.dispatchEvent(new CustomEvent('auth:expired'))
    }
    return Promise.reject(err)
  },
)

// 登录与健康检查（豁免鉴权）
export const login = (username, password) =>
  http.post('/auth/login', { username, password }).then((r) => r.data)
export const getHealth = () => http.get('/health').then((r) => r.data)

export const getTask = (id) => http.get(`/tasks/${id}`).then((r) => r.data)
export const getTaskResult = (id) => http.get(`/tasks/${id}/result`).then((r) => r.data)
export const listTasks = (params) => http.get('/tasks', { params }).then((r) => r.data)
export const resumeTask = (id, reviews) =>
  http.post(`/tasks/${id}/resume`, { reviews }).then((r) => r.data)
export const cancelTask = (id) => http.post(`/tasks/${id}/cancel`).then((r) => r.data)
export const deleteTask = (id) => http.delete(`/tasks/${id}`).then((r) => r.data)
export const getViolations = (params) => http.get('/violations', { params }).then((r) => r.data)
export const listRules = (params) => http.get('/rules', { params }).then((r) => r.data)
export const createRule = (body) => http.post('/rules', body).then((r) => r.data)
export const updateRule = (id, body) => http.put(`/rules/${id}`, body).then((r) => r.data)
export const deleteRule = (id) => http.delete(`/rules/${id}`).then((r) => r.data)
export const dryRunRule = (id, taskId) =>
  http.post(`/rules/${id}/dry-run`, { task_id: taskId }).then((r) => r.data)

// 报告导出：axios blob 下载（<a href> 直链不带 Authorization header，改为带 token 的请求）
export const downloadReportFile = (id, format) =>
  http.get(`/tasks/${id}/report`, { params: { format }, responseType: 'blob' })

// 触发浏览器下载 blob；文件名从 Content-Disposition 的 filename*=UTF-8'' 解析，兜底用默认名
export function saveBlob(res, fallbackName) {
  const blob = new Blob([res.data], { type: res.headers['content-type'] || 'application/octet-stream' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  const cd = res.headers['content-disposition'] || ''
  const m = cd.match(/filename\*?=(?:UTF-8'')?["']?([^"';]+)/i)
  a.download = m ? decodeURIComponent(m[1]) : fallbackName
  a.click()
  URL.revokeObjectURL(url)
}
