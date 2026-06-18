import { useState } from 'react'
import { RefreshCw, Lock, Loader2, X, Search } from 'lucide-react'
import { PageHeader } from '@/components/PageHeader'
import { EmptyState } from '@/components/EmptyState'
import { useCapabilities } from '@/lib/useSharedQueries'
import { useFinancialStatus, useFinancialSync } from '@/lib/useFinancials'
import { StockFinancialSearch } from '@/components/financials/StockFinancialSearch'
import { StockFinancialDetail } from '@/components/financials/StockFinancialDetail'

const TABLE_LABELS: Record<string, string> = {
  metrics: '核心指标',
  income: '利润表',
  balance_sheet: '资产负债表',
  cash_flow: '现金流量表',
}

export function Financials() {
  const { data: caps } = useCapabilities()
  const hasFinancial = caps?.capabilities?.['financial'] != null
  const { data: status, isLoading } = useFinancialStatus()
  const syncMut = useFinancialSync()
  // 用服务端 syncing 真值驱动 UI(而非本地状态),刷新后仍正确,且天然防重复点击
  const syncing = status?.syncing ?? false
  // 选中的个股(模糊搜索结果);null 时显示搜索引导
  const [selected, setSelected] = useState<{ symbol: string; name: string } | null>(null)

  if (!hasFinancial) {
    return (
      <>
        <PageHeader title="财务" subtitle="利润表 / 资负表 / 现金流 / 关键指标" />
        <EmptyState
          icon={Lock}
          title="需要 Expert 套餐"
          hint="财务数据接口仅 Expert 套餐可用。升级后此页自动显示财务数据面板。"
        />
      </>
    )
  }

  const handleSync = (table: string) => {
    // 防重复点击:syncing 中不再触发(后端 run_now 也有 is_syncing 兜底)
    if (syncing) return
    syncMut.mutate(table)
  }

  const tables = status?.tables ?? {}
  const available = status?.available ?? false
  // 进度:已同步(rows>0)的表数 / 总表数,供同步中提示
  const syncedCount = Object.values(tables).filter(t => (t?.rows ?? 0) > 0).length

  return (
    <>
      <PageHeader
        title="财务"
        subtitle="利润表 / 资负表 / 现金流 / 关键指标 · Expert"
        right={
          <div className="flex gap-2">
            <button
              className="px-3 py-1.5 text-xs bg-card border border-border rounded-md hover:bg-accent transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
              onClick={() => handleSync('all')}
              disabled={syncing}
              title={syncing ? '正在同步，请稍候…' : '同步全部财务表'}
            >
              {syncing
                ? <Loader2 className="inline w-3 h-3 mr-1 animate-spin" />
                : <RefreshCw className="inline w-3 h-3 mr-1" />}
              {syncing ? '同步中…' : '全部同步'}
            </button>
          </div>
        }
      />

      {syncing && (
        <div className="px-5 -mt-2 pb-1 text-xs text-accent/80 flex items-center gap-1.5">
          <Loader2 className="w-3 h-3 animate-spin" />
          正在从 TickFlow 拉取财务数据，已同步 {syncedCount}/4 张表…
        </div>
      )}

      {/* 同步状态卡片 —— 始终显示,反映本地财务数据概况 */}
      {!isLoading && available && (
        <div className="px-5 pt-3">
          <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
            {Object.entries(TABLE_LABELS).map(([key, label]) => {
              const info = tables[key]
              return (
                <div key={key} className="bg-card border border-border rounded-lg p-3">
                  <div className="flex items-center justify-between">
                    <span className="text-sm font-medium">{label}</span>
                    <button
                      className="text-muted hover:text-foreground transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
                      onClick={() => handleSync(key)}
                      disabled={syncing}
                      title={syncing ? '正在同步…' : `同步${label}`}
                    >
                      {syncing
                        ? <Loader2 className="w-3.5 h-3.5 animate-spin" />
                        : <RefreshCw className="w-3.5 h-3.5" />}
                    </button>
                  </div>
                  <div className="mt-2 text-2xl font-semibold tabular-nums">
                    {info?.rows ?? 0}
                    <span className="text-xs text-muted ml-1">行</span>
                  </div>
                  <div className="text-xs text-muted mt-0.5">
                    {info?.symbols ?? 0} 只标的
                  </div>
                </div>
              )
            })}
          </div>
          {status?.last_sync && Object.keys(status.last_sync).length > 0 && (
            <div className="text-xs text-muted mt-3">
              最后同步: {Object.entries(status.last_sync).map(([k, v]) =>
                `${TABLE_LABELS[k] || k}: ${new Date(v).toLocaleString()}`
              ).join(' / ')}
            </div>
          )}
        </div>
      )}

      {isLoading ? (
        <div className="p-5 text-sm text-muted">加载中…</div>
      ) : !available ? (
        <div className="p-5 text-sm text-muted">暂无数据，点击"全部同步"从 TickFlow 拉取财务数据</div>
      ) : (
        <>
          {/* 个股搜索区 */}
          <div className="px-5 pt-6 pb-2">
            {selected ? (
              // 已选股:紧凑搜索条 + 清除按钮(便于换股)
              <div className="flex items-center gap-3">
                <div className="flex-1 max-w-xl">
                  <StockFinancialSearch onSelect={(symbol, name) => setSelected({ symbol, name })} />
                </div>
                <button
                  onClick={() => setSelected(null)}
                  className="inline-flex items-center gap-1 px-2.5 py-1.5 text-xs text-secondary hover:text-foreground rounded-btn border border-border hover:bg-elevated transition-colors shrink-0"
                  title="清除选择"
                >
                  <X className="h-3.5 w-3.5" />
                  清除
                </button>
              </div>
            ) : (
              // 未选股:醒目居中引导
              <div className="flex flex-col items-center gap-3 py-6">
                <div className="flex items-center gap-2 text-sm text-secondary">
                  <Search className="h-4 w-4" />
                  <span>搜索个股查看详细财务数据</span>
                </div>
                <StockFinancialSearch onSelect={(symbol, name) => setSelected({ symbol, name })} />
                <div className="text-[11px] text-muted">支持股票代码或名称模糊匹配，如 600000 / 浦发</div>
              </div>
            )}
          </div>

          {/* 个股详情 / 空引导 */}
          <div className="px-5 pb-8">
            {selected ? (
              <StockFinancialDetail symbol={selected.symbol} name={selected.name} />
            ) : (
              <EmptyState
                icon={Search}
                title="未选择股票"
                hint="在上方搜索框输入股票代码或名称，选择后即可查看该股的核心指标、利润表、资产负债表与现金流量表。"
              />
            )}
          </div>
        </>
      )}
    </>
  )
}
