const STATUS_CONFIG: Record<string, { color: string; label: string }> = {
  pending: { color: '#faad14', label: '等待中' },
  processing: { color: '#1890ff', label: '处理中' },
  indexed: { color: '#52c41a', label: '已索引' },
  failed: { color: '#ff4d4f', label: '失败' },
  stale: { color: '#fa8c16', label: '索引待更新' },
  disappeared: { color: '#999', label: '文件已消失' },
  active: { color: '#52c41a', label: '活跃' },
}

export function StatusBadge({ status, type = 'index' }: { status: string; type?: 'index' | 'file' }) {
  const config = STATUS_CONFIG[status] || { color: '#999', label: status }
  return (
    <span
      style={{
        display: 'inline-block',
        padding: '2px 8px',
        borderRadius: 4,
        fontSize: 12,
        color: '#fff',
        background: config.color,
      }}
    >
      {config.label}
    </span>
  )
}
