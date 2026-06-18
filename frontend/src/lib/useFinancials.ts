import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { api } from '@/lib/api'

export const FINANCIAL_QK = {
  status: ['financials', 'status'],
  metrics: (symbol?: string) => ['financials', 'metrics', symbol],
  income: (symbol?: string) => ['financials', 'income', symbol],
  balanceSheet: (symbol?: string) => ['financials', 'balance-sheet', symbol],
  cashFlow: (symbol?: string) => ['financials', 'cash-flow', symbol],
}

export function useFinancialStatus() {
  return useQuery({
    queryKey: FINANCIAL_QK.status,
    queryFn: () => api.financialStatus(),
    staleTime: 60_000,
    // 同步进行中时每 3s 轮询,及时反映表数变化与同步完成;空闲时不轮询。
    refetchInterval: (query) => (query.state.data?.syncing ? 3_000 : false),
  })
}

export function useFinancialMetrics(symbol?: string) {
  return useQuery({
    queryKey: FINANCIAL_QK.metrics(symbol),
    queryFn: () => api.financialMetrics(symbol),
    enabled: !!symbol,
    staleTime: 300_000,
  })
}

export function useFinancialIncome(symbol?: string) {
  return useQuery({
    queryKey: FINANCIAL_QK.income(symbol),
    queryFn: () => api.financialIncome(symbol),
    enabled: !!symbol,
    staleTime: 300_000,
  })
}

export function useFinancialBalanceSheet(symbol?: string) {
  return useQuery({
    queryKey: FINANCIAL_QK.balanceSheet(symbol),
    queryFn: () => api.financialBalanceSheet(symbol),
    enabled: !!symbol,
    staleTime: 300_000,
  })
}

export function useFinancialCashFlow(symbol?: string) {
  return useQuery({
    queryKey: FINANCIAL_QK.cashFlow(symbol),
    queryFn: () => api.financialCashFlow(symbol),
    enabled: !!symbol,
    staleTime: 300_000,
  })
}

export function useFinancialSync() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (table: string) => api.financialSync(table),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: FINANCIAL_QK.status })
      qc.invalidateQueries({ queryKey: ['financials'] })
    },
  })
}
