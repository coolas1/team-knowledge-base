const STATUS_CONFIG: Record<string, { color: string; label: string }> = {
  pending: { color: '#faad14', label: '等待中' },
  processing: { color: '#1890ff', label: '处理中' },
  indexed: { color: '#52c41a', label: '已索引' },
  failed: { color: '#ff4d4f', label: '失败' },
}

export function StatusBadge({ status }: { status: string }) {
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
