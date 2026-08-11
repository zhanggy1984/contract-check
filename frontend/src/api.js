import axios from 'axios'

export const getTask = (id) => axios.get(`/api/tasks/${id}`).then((r) => r.data)
export const getTaskResult = (id) => axios.get(`/api/tasks/${id}/result`).then((r) => r.data)
export const listTasks = (params) => axios.get('/api/tasks', { params }).then((r) => r.data)
export const resumeTask = (id, reviews) =>
  axios.post(`/api/tasks/${id}/resume`, { reviews }).then((r) => r.data)
export const cancelTask = (id) => axios.post(`/api/tasks/${id}/cancel`).then((r) => r.data)
export const deleteTask = (id) => axios.delete(`/api/tasks/${id}`).then((r) => r.data)
export const getViolations = (params) => axios.get('/api/violations', { params }).then((r) => r.data)
export const listRules = (params) => axios.get('/api/rules', { params }).then((r) => r.data)
export const createRule = (body) => axios.post('/api/rules', body).then((r) => r.data)
export const updateRule = (id, body) => axios.put(`/api/rules/${id}`, body).then((r) => r.data)
export const deleteRule = (id) => axios.delete(`/api/rules/${id}`).then((r) => r.data)
export const dryRunRule = (id, taskId) =>
  axios.post(`/api/rules/${id}/dry-run`, { task_id: taskId }).then((r) => r.data)
