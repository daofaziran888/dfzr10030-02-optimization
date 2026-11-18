/** 责任覆盖快照接口封装：统一附带当前值班团队与角色上下文。 */
import { request } from '@/api/client'

export type CoverageEntry = {
  [key: string]: string | number
  id: number
  快照编号: string
  数据源编号: string
  责任人: string
  归属团队: string
  共享范围: string
  来源记录版本: number
  覆盖状态: string
}

export type CoverageSnapshot = {
  id: number
  快照编号: string
  归属团队: string
  边界模式: string
  快照版本: number
  生成时间: string
  覆盖数量: number
  状态: string
  更新时间: string
  备注?: string
  审核备注?: string
}

export type ReviewItem = {
  [key: string]: string | number | undefined
  id: number
  待审编号: string
  快照编号: string
  归属团队: string
  数据源编号: string
  责任人: string
  变更类型: string
  来源记录版本: number
  审核状态: string
  审核意见: string
  提交时间: string
  审核时间?: string
}

export type DenialItem = {
  [key: string]: string | number
  id: number
  拒绝编号: string
  请求方团队: string
  请求方角色: string
  数据源编号: string
  请求类型: string
  拒绝原因: string
  记录时间: string
}

export type TeamSummary = {
  team: string
  role: string
  只读: boolean
  current_snapshot: CoverageSnapshot | null
  latest_snapshot: CoverageSnapshot | null
  coverage_count: number
  denial_count: number
  pending_review_count: number
  coverage_updated_at: string | null
}

type ActionEnvelope<T> = { ok: boolean; message: string; entry: T | null }

function contextQuery(team: string, role: string): string {
  return new URLSearchParams({ team, role }).toString()
}

async function readJson<T>(response: Response): Promise<T> {
  return (await response.json()) as T
}

/** 团队页核对：覆盖数量、越权拒绝记录、覆盖更新时间。 */
export async function fetchSummary(team: string, role: string): Promise<ActionEnvelope<TeamSummary>> {
  const response = await request(`/api/coverage/summary?${contextQuery(team, role)}`)
  return readJson(response)
}

/** 读取本团队责任覆盖数据快照（活动视图只含当前版本）。 */
export async function fetchCoverage(
  team: string,
  role: string,
): Promise<ActionEnvelope<{
  snapshot: CoverageSnapshot | null
  latest_snapshot?: CoverageSnapshot | null
  items: CoverageEntry[]
  shared_items: CoverageEntry[]
  denied: boolean
}>> {
  const response = await request(`/api/coverage/snapshots?${contextQuery(team, role)}`)
  return readJson(response)
}

/** 单条覆盖核对：越权查看返回 ok=false 且后端已写拒绝记录。 */
export async function fetchCoverageEntry(
  team: string,
  role: string,
  datasourceId: string,
): Promise<ActionEnvelope<CoverageEntry & { denied?: boolean }>> {
  const response = await request(
    `/api/coverage/entries/${encodeURIComponent(datasourceId)}?${contextQuery(team, role)}`,
  )
  return readJson(response)
}

/** 重算责任覆盖快照：rebuild 整体重建 / incremental 增量更新，并生成待审清单。 */
export async function rebuildSnapshot(
  team: string,
  role: string,
  mode: 'rebuild' | 'incremental',
): Promise<ActionEnvelope<{
  snapshot: CoverageSnapshot
  changes: { 数据源编号: string; 变更类型: string; 责任人: string; 来源记录版本: number }[]
  entries: CoverageEntry[]
}>> {
  const response = await request(`/api/coverage/snapshots/rebuild?${contextQuery(team, role)}`, {
    method: 'POST',
    body: JSON.stringify({ mode }),
  })
  return readJson(response)
}

export async function fetchReviews(
  team: string,
  role: string,
  status?: string,
): Promise<{ items: ReviewItem[]; total: number }> {
  const query = new URLSearchParams({ team, role })
  if (status) query.set('status', status)
  const response = await request(`/api/coverage/reviews?${query.toString()}`)
  return readJson(response)
}

/** 审核结论：同一快照批次仅一条确认，其余拒绝。 */
export async function decideReview(
  team: string,
  role: string,
  reviewId: number,
  decision: '确认' | '拒绝',
  remark?: string,
): Promise<ActionEnvelope<ReviewItem & { denied?: boolean }>> {
  const response = await request(
    `/api/coverage/reviews/${reviewId}/decision?${contextQuery(team, role)}`,
    { method: 'POST', body: JSON.stringify({ decision, remark }) },
  )
  return readJson(response)
}

export async function fetchDenials(
  team: string,
  role: string,
): Promise<{ items: DenialItem[]; total: number }> {
  const response = await request(`/api/coverage/denials?${contextQuery(team, role)}`)
  return readJson(response)
}

export async function fetchOwnership(
  team: string,
  role: string,
  filterTeam?: string,
): Promise<{ items: Record<string, string | number | boolean>[]; total: number }> {
  const query = new URLSearchParams({ team, role })
  if (filterTeam) query.set('filter_team', filterTeam)
  const response = await request(`/api/coverage/ownership?${query.toString()}`)
  return readJson(response)
}

export async function fetchHistorySnapshots(
  team: string,
  role: string,
): Promise<{ items: CoverageSnapshot[]; total: number }> {
  const response = await request(`/api/coverage/snapshots/history?${contextQuery(team, role)}`)
  return readJson(response)
}
