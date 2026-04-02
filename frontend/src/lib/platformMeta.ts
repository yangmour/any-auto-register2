export const PLATFORM_LABELS: Record<string, string> = {
  chatgpt: 'OpenAI / Codex CLI',
  trae: 'Trae.ai',
  cursor: 'Cursor',
  kiro: 'Kiro',
  grok: 'Grok',
  tavily: 'Tavily',
  openblocklabs: 'OpenBlockLabs',
}

export const PLATFORM_COLORS: Record<string, string> = {
  chatgpt: '#10b981',
  trae: '#3b82f6',
  cursor: '#f59e0b',
  kiro: '#8b5cf6',
  grok: '#111827',
  tavily: '#06b6d4',
  openblocklabs: '#ef4444',
}

export const PLATFORM_OPTIONS = [
  { value: 'chatgpt', label: PLATFORM_LABELS.chatgpt },
  { value: 'trae', label: PLATFORM_LABELS.trae },
  { value: 'cursor', label: PLATFORM_LABELS.cursor },
  { value: 'kiro', label: PLATFORM_LABELS.kiro },
  { value: 'grok', label: PLATFORM_LABELS.grok },
  { value: 'tavily', label: PLATFORM_LABELS.tavily },
  { value: 'openblocklabs', label: PLATFORM_LABELS.openblocklabs },
] as const

export function getPlatformLabel(platform?: string) {
  if (!platform) return ''
  return PLATFORM_LABELS[platform] || platform
}
