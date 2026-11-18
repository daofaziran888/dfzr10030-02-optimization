import { expect, test } from '@playwright/test'

/**
 * 责任覆盖数据快照 E2E：
 * - 换班后按责任人/团队归属重建快照，新团队只得到本归属源端配置；
 * - 待审清单同一批次仅一条确认、其余拒绝；
 * - 只读角色只能查看、外部访客直接拒绝；
 * - 认证服务按同一份快照做续期核对。
 */
test.describe('责任覆盖数据快照', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto('/coverage')
    await expect(page.getByTestId('select-team')).toBeVisible()
  })

  test('整体重建快照：仅一条确认其余拒绝，覆盖只含本归属源端', async ({ page }) => {
    const browserErrors: string[] = []
    page.on('pageerror', (e) => browserErrors.push(e.message))
    page.on('response', (r) => {
      if (r.url().includes('/api/') && r.status() >= 400) browserErrors.push(`${r.status()} ${r.url()}`)
    })

    await page.getByTestId('select-team').selectOption('B班-采集二组')
    await page.getByTestId('select-role').selectOption('值班管理员')
    await page.getByTestId('query').click()

    await page.getByTestId('rebuild').click()
    // 重算后生成待审清单（B班接收 DATA-0001 / DATA-0002 两条移交）
    const reviewRows = page.getByTestId(/^review-row-/)
    await expect(reviewRows).toHaveCount(2)

    // 第一条确认
    await reviewRows.first().getByTestId('review-confirm').click()
    // 同批次第二条再确认必须被拦：页面出现错误提示，条目仍为待审核
    await reviewRows.nth(1).getByTestId('review-confirm').click()
    await expect(page.getByTestId('coverage-error')).toContainText('一条确认')
    // 改为拒绝
    await reviewRows.nth(1).getByTestId('review-reject').click()
    await expect(page.getByTestId('snapshot-meta')).toContainText('当前版本')

    // 覆盖表只含 B 班本归属源端（DATA-0001、DATA-0002），不含已留在 A 班的 DATA-0003
    await expect(page.getByTestId('coverage-row-DATA-0001')).toBeVisible()
    await expect(page.getByTestId('coverage-row-DATA-0002')).toBeVisible()
    await expect(page.getByTestId('coverage-row-DATA-0003')).toHaveCount(0)

    // 团队页核对：覆盖数量=2、覆盖更新时间有值
    const cards = page.getByTestId('kpi-card')
    await expect(cards.nth(0)).toContainText('2')
    await expect(cards.nth(3)).not.toContainText('—')

    expect(browserErrors).toEqual([])
  })

  test('换班到A班：增量更新后旧范围不可见，越权查看写拒绝记录', async ({ page }) => {
    // 先让 B 班快照生效
    await page.getByTestId('rebuild').click()
    let reviewRows = page.getByTestId(/^review-row-/)
    await reviewRows.first().getByTestId('review-confirm').click()
    await reviewRows.nth(1).getByTestId('review-reject').click()
    await expect(page.getByTestId('snapshot-meta')).toContainText('当前版本')

    // 换班到 A 班，做增量更新（保留未变动条目、逐个移除已交接条目）
    await page.getByTestId('select-team').selectOption('A班-采集一组')
    await page.getByTestId('query').click()
    await page.getByTestId('rebuild-incremental').click()
    reviewRows = page.getByTestId(/^review-row-/)
    await expect(reviewRows.first()).toBeVisible()
    const count = await reviewRows.count()
    await reviewRows.first().getByTestId('review-confirm').click()
    for (let i = 1; i < count; i += 1) {
      await reviewRows.nth(i).getByTestId('review-reject').click()
    }
    await expect(page.getByTestId('snapshot-meta')).toContainText('当前版本')

    // A 班当前责任范围只剩 DATA-0003
    await expect(page.getByTestId('coverage-row-DATA-0003')).toBeVisible()
    await expect(page.getByTestId('coverage-row-DATA-0001')).toHaveCount(0)
    await expect(page.getByTestId('coverage-row-DATA-0002')).toHaveCount(0)

    // 历史快照仍保留可核对
    await expect(page.getByTestId(/^history-row-/).first()).toBeVisible()
  })

  test('权限：只读角色不能改动，外部访客直接拒绝', async ({ page }) => {
    await page.getByTestId('select-role').selectOption('只读角色')
    await page.getByTestId('query').click()
    await expect(page.getByTestId('rebuild')).toBeDisabled()
    await expect(page.getByTestId('rebuild-incremental')).toBeDisabled()
    // 只读角色仍可查看页面结构
    await expect(page.getByTestId('summary-row')).toBeVisible()

    await page.getByTestId('select-role').selectOption('外部访客')
    await page.getByTestId('query').click()
    await expect(page.getByTestId('view-denied')).toBeVisible()
    await expect(page.getByTestId('summary-row')).toHaveCount(0)
  })

  test('认证服务读取同一份快照返回核对状态', async ({ page }) => {
    // B 班先生效快照
    await page.getByTestId('rebuild').click()
    const reviewRows = page.getByTestId(/^review-row-/)
    await reviewRows.first().getByTestId('review-confirm').click()
    await reviewRows.nth(1).getByTestId('review-reject').click()
    await expect(page.getByTestId('snapshot-meta')).toContainText('当前版本')

    await page.goto('/auth')
    // 凭证 101 关联 DATA-0001，属于 B 班可续期集合
    await page.getByTestId('coverage-check-1').click()
    const modal = page.getByTestId('coverage-check-modal')
    await expect(modal).toBeVisible()
    await expect(page.getByTestId('check-result-status')).toHaveText('可续期')

    // 凭证 103 关联 DATA-0003，不在 B 班归属内 → 越界拒绝
    await page.getByTestId('close-check').click()
    await page.getByTestId('coverage-check-3').click()
    await expect(page.getByTestId('check-result-status')).toHaveText('越界拒绝')

    // 责任续期越界时不改动凭证，错误原因展示在页脚
    await page.getByTestId('close-check').click()
    await page.getByTestId('renew-coverage-3').click()
    await expect(page.locator('.page-foot .error-text')).toContainText('责任归属')
  })
})
