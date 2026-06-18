import { useState } from 'react'
import { CalendarDays, TrendingUp, FileText, Wallet, Activity } from 'lucide-react'
import {
  useFinancialMetrics,
  useFinancialIncome,
  useFinancialBalanceSheet,
  useFinancialCashFlow,
} from '@/lib/useFinancials'
import { fmtPrice, fmtBigNum, fmtDate } from '@/lib/format'
import { Skeleton } from '@/components/data/Skeleton'

interface Props {
  symbol: string
  name: string
}

type TabKey = 'metrics' | 'income' | 'balance_sheet' | 'cash_flow'

const TABS: { key: TabKey; label: string; icon: typeof TrendingUp }[] = [
  { key: 'metrics', label: '核心指标', icon: TrendingUp },
  { key: 'income', label: '利润表', icon: FileText },
  { key: 'balance_sheet', label: '资产负债表', icon: Wallet },
  { key: 'cash_flow', label: '现金流量表', icon: Activity },
]

// 字段定义:键 → (中文名, 格式化类型)
// pct=百分点(存的是 12.3 表示 12.3%); amount=金额(元,转亿/万亿); perShare=每股; num=普通数值(保留2位)
type FmtType = 'pct' | 'amount' | 'perShare' | 'num'
type FieldDef = { label: string; fmt: FmtType; group?: string }

const FIELD_DEFS: Record<TabKey, FieldDef[]> = {
  metrics: [
    { label: '基本每股收益 EPS', fmt: 'perShare', key: 'eps_basic' } as any,
    { label: '稀释每股收益 EPS', fmt: 'perShare', key: 'eps_diluted' } as any,
    { label: '每股净资产 BPS', fmt: 'perShare', key: 'bps' } as any,
    { label: '每股经营现金流', fmt: 'perShare', key: 'ocfps' } as any,
    { label: '净资产收益率 ROE', fmt: 'pct', key: 'roe' } as any,
    { label: '稀释 ROE', fmt: 'pct', key: 'roe_diluted' } as any,
    { label: '总资产收益率 ROA', fmt: 'pct', key: 'roa' } as any,
    { label: '销售毛利率', fmt: 'pct', key: 'gross_margin' } as any,
    { label: '销售净利率', fmt: 'pct', key: 'net_margin' } as any,
    { label: '资产负债率', fmt: 'pct', key: 'debt_to_asset_ratio' } as any,
    { label: '营业收入同比增长', fmt: 'pct', key: 'revenue_yoy' } as any,
    { label: '净利润同比增长', fmt: 'pct', key: 'net_income_yoy' } as any,
    { label: '经营现金/营收', fmt: 'pct', key: 'operating_cash_to_revenue' } as any,
    { label: '存货周转率', fmt: 'num', key: 'inventory_turnover' } as any,
  ],
  income: [
    { label: '营业收入', fmt: 'amount', key: 'revenue' } as any,
    { label: '营业成本', fmt: 'amount', key: 'operating_cost' } as any,
    { label: '营业利润', fmt: 'amount', key: 'operating_profit' } as any,
    { label: '销售费用', fmt: 'amount', key: 'selling_expense' } as any,
    { label: '管理费用', fmt: 'amount', key: 'admin_expense' } as any,
    { label: '研发费用', fmt: 'amount', key: 'rd_expense' } as any,
    { label: '财务费用', fmt: 'amount', key: 'financial_expense' } as any,
    { label: '营业外收入', fmt: 'amount', key: 'non_operating_income' } as any,
    { label: '营业外支出', fmt: 'amount', key: 'non_operating_expense' } as any,
    { label: '利润总额', fmt: 'amount', key: 'total_profit' } as any,
    { label: '所得税', fmt: 'amount', key: 'income_tax' } as any,
    { label: '净利润', fmt: 'amount', key: 'net_income' } as any,
    { label: '归母净利润', fmt: 'amount', key: 'net_income_attributable' } as any,
    { label: '扣非净利润', fmt: 'amount', key: 'net_income_deducted' } as any,
    { label: '基本每股收益', fmt: 'perShare', key: 'basic_eps' } as any,
    { label: '稀释每股收益', fmt: 'perShare', key: 'diluted_eps' } as any,
  ],
  balance_sheet: [
    { label: '资产总计', fmt: 'amount', key: 'total_assets' } as any,
    { label: '流动资产合计', fmt: 'amount', key: 'total_current_assets' } as any,
    { label: '非流动资产合计', fmt: 'amount', key: 'total_non_current_assets' } as any,
    { label: '货币资金', fmt: 'amount', key: 'cash_and_equivalents' } as any,
    { label: '应收账款', fmt: 'amount', key: 'accounts_receivable' } as any,
    { label: '存货', fmt: 'amount', key: 'inventory' } as any,
    { label: '固定资产', fmt: 'amount', key: 'fixed_assets' } as any,
    { label: '无形资产', fmt: 'amount', key: 'intangible_assets' } as any,
    { label: '商誉', fmt: 'amount', key: 'goodwill' } as any,
    { label: '负债合计', fmt: 'amount', key: 'total_liabilities' } as any,
    { label: '流动负债合计', fmt: 'amount', key: 'total_current_liabilities' } as any,
    { label: '非流动负债合计', fmt: 'amount', key: 'total_non_current_liabilities' } as any,
    { label: '短期借款', fmt: 'amount', key: 'short_term_borrowing' } as any,
    { label: '长期借款', fmt: 'amount', key: 'long_term_borrowing' } as any,
    { label: '应付账款', fmt: 'amount', key: 'accounts_payable' } as any,
    { label: '所有者权益合计', fmt: 'amount', key: 'total_equity' } as any,
    { label: '归母所有者权益', fmt: 'amount', key: 'equity_attributable' } as any,
    { label: '未分配利润', fmt: 'amount', key: 'retained_earnings' } as any,
    { label: '少数股东权益', fmt: 'amount', key: 'minority_interest' } as any,
  ],
  cash_flow: [
    { label: '经营活动现金流净额', fmt: 'amount', key: 'net_operating_cash_flow' } as any,
    { label: '投资活动现金流净额', fmt: 'amount', key: 'net_investing_cash_flow' } as any,
    { label: '筹资活动现金流净额', fmt: 'amount', key: 'net_financing_cash_flow' } as any,
    { label: '固定资产/无形资产投资', fmt: 'amount', key: 'capex' } as any,
    { label: '现金及等价物净增加额', fmt: 'amount', key: 'net_cash_change' } as any,
  ],
}

function formatValue(v: number | null | undefined, fmt: FmtType): string {
  if (v == null || Number.isNaN(v)) return '—'
  switch (fmt) {
    case 'pct':
      // 存储的是百分点(12.3 表示 12.3%),直接保留2位 + %
      return `${v.toFixed(2)}%`
    case 'amount':
      // 金额(元)→ 亿/万亿;保留负号
      return fmtBigNum(v)
    case 'perShare':
      return fmtPrice(v, 2)
    case 'num':
    default:
      return v.toFixed(2)
  }
}

export function StockFinancialDetail({ symbol, name }: Props) {
  const [tab, setTab] = useState<TabKey>('metrics')

  const metrics = useFinancialMetrics(symbol)
  const income = useFinancialIncome(symbol)
  const balance = useFinancialBalanceSheet(symbol)
  const cashFlow = useFinancialCashFlow(symbol)

  const queryMap = {
    metrics: metrics,
    income: income,
    balance_sheet: balance,
    cash_flow: cashFlow,
  } as const

  const current = queryMap[tab]
  // 按 period_end 降序(最新在前);同步默认 latest_only,通常只有1期
  const rows = (current.data?.data ?? []).slice().sort((a, b) =>
    (b.period_end ?? '').localeCompare(a.period_end ?? '')
  )
  const fieldDefs = FIELD_DEFS[tab]

  // 头部报告期信息取最新一期(优先用当前 tab,兜底用 metrics)
  const latestPeriod = rows[0]?.period_end ?? metrics.data?.data?.[0]?.period_end ?? null
  const latestAnnounce = rows[0]?.announce_date ?? metrics.data?.data?.[0]?.announce_date ?? null

  return (
    <div className="rounded-card border border-border bg-surface overflow-hidden">
      {/* 头部:标的 + 报告期 */}
      <div className="px-5 py-4 border-b border-border flex items-center gap-3 flex-wrap">
        <div className="flex items-baseline gap-2 min-w-0">
          <span className="text-lg font-semibold text-foreground">{name}</span>
          <span className="text-xs font-mono text-muted">{symbol}</span>
        </div>
        {latestPeriod && (
          <div className="flex items-center gap-1.5 text-xs text-secondary ml-auto">
            <CalendarDays className="h-3.5 w-3.5" />
            <span>报告期 <span className="font-mono">{latestPeriod}</span></span>
            {latestAnnounce && (
              <span className="text-muted">· 披露 {fmtDate(latestAnnounce)}</span>
            )}
          </div>
        )}
      </div>

      {/* 标签页 */}
      <div className="flex items-center gap-1 px-3 pt-2 border-b border-border/60">
        {TABS.map(t => {
          const Icon = t.icon
          const isActive = tab === t.key
          return (
            <button
              key={t.key}
              onClick={() => setTab(t.key)}
              className={`inline-flex items-center gap-1.5 px-3 py-2 text-xs font-medium border-b-2 -mb-px transition-colors ${
                isActive
                  ? 'border-accent text-accent'
                  : 'border-transparent text-muted hover:text-secondary'
              }`}
            >
              <Icon className="h-3.5 w-3.5" />
              {t.label}
            </button>
          )
        })}
      </div>

      {/* 表格内容 */}
      <div className="p-4">
        {current.isLoading ? (
          <div className="space-y-2">
            {Array.from({ length: 6 }).map((_, i) => (
              <div key={i} className="flex justify-between">
                <Skeleton w="w-32" h="h-4" />
                <Skeleton w="w-20" h="h-4" />
              </div>
            ))}
          </div>
        ) : rows.length === 0 ? (
          <div className="py-10 text-center text-xs text-muted">
            暂无{TABS.find(t => t.key === tab)?.label}数据 — 可点击顶部「全部同步」拉取
          </div>
        ) : (
          <div className="space-y-5">
            {/* 多期时为每期渲染一组;单期时只有一组 */}
            {rows.map((row, ri) => (
              <div key={row.period_end ?? ri}>
                {rows.length > 1 && (
                  <div className="text-[11px] text-muted mb-2 flex items-center gap-1.5">
                    <CalendarDays className="h-3 w-3" />
                    报告期 <span className="font-mono text-secondary">{row.period_end}</span>
                  </div>
                )}
                <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-x-8 gap-y-0">
                  {fieldDefs.map((def: any) => {
                    const val = row[def.key]
                    return (
                      <div
                        key={def.key}
                        className="flex items-baseline justify-between gap-3 py-2 border-b border-border/40"
                      >
                        <span className="text-xs text-secondary shrink-0">{def.label}</span>
                        <span className="text-sm font-mono tabular-nums text-foreground text-right">
                          {formatValue(val, def.fmt)}
                        </span>
                      </div>
                    )
                  })}
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}
