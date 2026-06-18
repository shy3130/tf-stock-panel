// capability 内部名 → 用户能理解的中文标签
export const CAP_LABELS: Record<string, { name: string; hint: string }> = {
  'quote.by_symbol':         { name: '实时行情(按标的)', hint: '查询单只股票当前价' },
  'quote.batch':             { name: '实时行情(批量)',   hint: '一次拿多只股票的价' },
  'quote.pool':              { name: '标的池查询',        hint: '按沪深300等池子拿行情' },
  'kline.daily.by_symbol':   { name: '日 K(按标的)',    hint: '单只股票历史日 K' },
  'kline.daily.batch':       { name: '日 K(批量)',      hint: '一次拿多只股票的日 K — 选股 / 信号扫描 必需' },
  'kline.minute.by_symbol':  { name: '分钟 K(按标的)',  hint: '单股 1m/5m/15m/30m/60m K 线' },
  'kline.minute.batch':      { name: '分钟 K(批量)',    hint: '多股分钟 K' },

  'depth5':                  { name: '五档盘口',          hint: '买卖五档报价' },
  'websocket':               { name: '实时推送(WS)',    hint: '免轮询的实时行情订阅' },
  'financial':               { name: '财务数据',          hint: '利润表 / 资负表 / 现金流 / 关键指标' },
  'adj_factor':              { name: '复权因子',          hint: '让 MA/MACD 等指标在分红送转日不失真' },
}

// 套餐等级 —— 用于按档位门控功能(如专线端点 / 按月扩展分钟K)。
// 基础档提取与后端 quote_service.py 一致:取 label 第一个词("Pro +" → "pro")。
export const TIER_RANK: Record<string, number> = { free: 0, starter: 1, pro: 2, expert: 3 }
export const EXPERT_RANK = TIER_RANK.expert

export function tierRank(label: string): number {
  const base = (label.split(' ')[0] ?? '').split('+')[0].trim().toLowerCase()
  return TIER_RANK[base] ?? -1
}

export function isExpertOrAbove(label: string): boolean {
  return tierRank(label) >= EXPERT_RANK
}

