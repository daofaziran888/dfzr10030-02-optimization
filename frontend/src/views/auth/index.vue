<template>
  <section class="page" data-module="auth">
    <header class="page-head">
      <div>
        <h2>凭证管理管理</h2>
        <p class="page-desc">维护认证凭证，围绕凭证编号、凭证类型、关联数据源、凭证内容做登记、筛选与状态流转。</p>
      </div>
      <div class="page-actions">
        <button class="btn primary" data-testid="open-create" type="button" @click="openCreate">登记认证凭证</button>
        <button class="btn" data-testid="export" type="button" @click="exportRows">导出凭证管理清单</button>
      </div>
    </header>

    <div class="stat-row">
      <article v-for="item in stats" :key="item.label" class="stat-card">
        <span class="stat-label">{{ item.label }}</span>
        <strong class="stat-value">{{ item.value }}</strong>
      </article>
    </div>

    <form class="filter-bar" @submit.prevent="reload">
      <label v-for="field in filterFields" :key="field" class="filter-item">
        <span>{{ field }}</span>
        <input v-model="filters[field]" :data-testid="`filter-${field}`" :placeholder="`按${field}检索`" />
      </label>
      <button class="btn" data-testid="query" type="submit">查询</button>
      <button class="btn ghost" data-testid="reset" type="button" @click="resetFilters">重置条件</button>
    </form>

    <table class="data-table">
      <thead>
        <tr>
          <th v-for="column in columns" :key="column">{{ column }}</th>
          <th>可执行动作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in rows" :key="String(row.id)" :data-testid="`row-${row.id}`" :data-status="String(row.status)">
          <td v-for="column in columns" :key="column">{{ row[column] ?? '—' }}</td>
          <td class="row-actions">
            <button
              v-for="action in actions"
              :key="action"
              class="link"
              type="button"
              :data-testid="`action-${row.id}-${action}`"
              @click="runAction(action, row)"
            >
              {{ action }}
            </button>
            <button
              class="link"
              type="button"
              :data-testid="`coverage-check-${row.id}`"
              @click="runCoverageCheck(row)"
            >
              快照核对
            </button>
            <button
              class="link"
              type="button"
              :data-testid="`renew-coverage-${row.id}`"
              @click="runCoverageRenew(row)"
            >
              责任续期
            </button>
          </td>
        </tr>
        <tr v-if="!rows.length">
          <td :colspan="columns.length + 1" class="empty-state">暂无凭证管理数据，可先登记认证凭证</td>
        </tr>
      </tbody>
    </table>

    <div v-if="showCreate" class="modal-backdrop" data-testid="create-modal">
      <form class="modal" @submit.prevent="submitCreate">
        <h3>登记认证凭证</h3>
        <label v-for="field in columns" :key="field" class="create-field">
          <span>{{ field }}</span>
          <input v-model="createValues[field]" :data-testid="`create-${field}`" required />
        </label>
        <div class="page-actions">
          <button class="btn" type="button" @click="showCreate = false">取消</button>
          <button class="btn primary" data-testid="submit-create" type="submit" :disabled="creating">
            {{ creating ? '提交中…' : '确认登记' }}
          </button>
        </div>
      </form>
    </div>

    <div v-if="checkResult" class="modal-backdrop" data-testid="coverage-check-modal">
      <div class="modal">
        <h3>责任覆盖快照核对状态</h3>
        <dl class="dag-detail">
          <dt>核对状态</dt>
          <dd data-testid="check-result-status">{{ checkResult.核对状态 }}</dd>
          <dt>凭证编号</dt>
          <dd>{{ checkResult.凭证编号 ?? '—' }}</dd>
          <dt>数据源编号</dt>
          <dd>{{ checkResult.数据源编号 ?? '—' }}</dd>
          <dt>快照编号</dt>
          <dd>{{ checkResult.快照编号 ?? '—' }}</dd>
          <dt>快照版本</dt>
          <dd>{{ checkResult.快照版本 ?? '—' }}</dd>
          <dt>快照更新时间</dt>
          <dd>{{ checkResult.快照更新时间 ?? '—' }}</dd>
          <dt>说明</dt>
          <dd>{{ checkResult.message ?? '—' }}</dd>
        </dl>
        <div class="page-actions">
          <button class="btn primary" type="button" data-testid="close-check" @click="checkResult = null">关闭</button>
        </div>
      </div>
    </div>

    <footer class="page-foot">
      <span>共 {{ total }} 条凭证管理记录 · 当前团队 {{ store.team }} · 角色 {{ store.role }}</span>
      <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
    </footer>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

import { request } from '@/api/client'
import { useSessionStore } from '@/stores/session'

type Row = Record<string, string | number | null>
type CoverageCheckResult = {
  ok: boolean
  message: string
  核对状态: string
  凭证编号?: string
  数据源编号?: string
  快照编号?: string
  快照版本?: number
  快照更新时间?: string
  可续期集合?: string[]
}

const store = useSessionStore()
const ENDPOINT = '/api/auth'
const columns = ["凭证编号", "凭证类型", "关联数据源", "凭证内容", "有效期至", "创建时间", "最后使用", "凭证状态"]
const actions = ["续期凭证", "吊销凭证"]
const statuses = ["有效", "即将过期", "已过期", "已吊销"]
const metricNames = ["有效凭证", "即将过期凭证", "已过期凭证"]

const rows = ref<Row[]>([])
const total = ref(0)
const errorMessage = ref('')
const filters = ref<Record<string, string>>({})
const filterFields = columns.slice(0, 3)
const showCreate = ref(false)
const creating = ref(false)
const createValues = ref<Record<string, string>>({})
const checkResult = ref<CoverageCheckResult | null>(null)
const stats = computed(() => [
  { label: metricNames[0], value: total.value },
  { label: metricNames[1], value: rows.value.filter((row) => row.status === statuses[1]).length },
  { label: metricNames[2], value: rows.value.filter((row) => row.status === statuses[statuses.length - 1]).length },
])

function contextQuery(): string {
  return new URLSearchParams({ team: store.team, role: store.role }).toString()
}

function resetFilters() {
  filters.value = {}
  void reload()
}

async function exportRows() {
  errorMessage.value = ''
  const query = new URLSearchParams(filters.value).toString()
  try {
    const response = await request(`${ENDPOINT}/export?${query}`)
    if (!response.ok) throw new Error('导出失败，请稍后重试')
    const url = URL.createObjectURL(await response.blob())
    const link = document.createElement('a')
    link.href = url
    link.download = 'auth.csv'
    link.click()
    URL.revokeObjectURL(url)
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '导出失败'
  }
}

function openCreate() {
  createValues.value = Object.fromEntries(columns.map((field) => [field, '']))
  errorMessage.value = ''
  showCreate.value = true
}

async function submitCreate() {
  creating.value = true
  errorMessage.value = ''
  try {
    const response = await request(ENDPOINT, {
      method: 'POST', body: JSON.stringify(createValues.value),
    })
    const payload = await response.json()
    if (!response.ok || !payload.ok) throw new Error(payload.message || '登记失败')
    showCreate.value = false
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '登记失败'
  } finally {
    creating.value = false
  }
}

async function runAction(action: string, row: Row) {
  errorMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/${row.id}/actions`, {
      method: 'POST',
      body: JSON.stringify({ action }),
    })
    if (!response.ok) {
      throw new Error('凭证管理动作未生效，请稍后重试')
    }
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '凭证管理操作失败'
  }
}

async function reload() {
  errorMessage.value = ''
  const query = new URLSearchParams(filters.value as Record<string, string>).toString()
  try {
    const response = await request(`${ENDPOINT}?${query}`)
    if (!response.ok) {
      throw new Error('认证凭证列表读取失败')
    }
    const payload = await response.json()
    rows.value = payload.items ?? []
    total.value = payload.total ?? rows.value.length
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '凭证管理列表读取失败'
  }
}

async function runCoverageCheck(row: Row) {
  errorMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/${row.id}/coverage-check?${contextQuery()}`)
    checkResult.value = await response.json()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '责任覆盖核对失败'
  }
}

async function runCoverageRenew(row: Row) {
  errorMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/${row.id}/renew?${contextQuery()}`, {
      method: 'POST',
      body: JSON.stringify({}),
    })
    const payload = await response.json()
    if (!response.ok || !payload.ok) {
      // 越出归属：后端只写拒绝记录，原凭证不改动；把拒绝原因直接展示给值班团队。
      errorMessage.value = payload.message || '责任续期被拒绝'
      return
    }
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '责任续期请求失败'
  }
}

onMounted(reload)
</script>
