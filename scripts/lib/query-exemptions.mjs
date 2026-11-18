// Miễn đúng truy vấn + số lần xuất hiện, không miễn cả file hoặc quy tắc.
export function parseExemptions(text) {
  const rows = []
  for (const line of text.split('\n')) {
    if (!line.startsWith('| `srcroot/')) continue
    const cells = line.split('|').slice(1, -1).map(c => c.trim().replace(/^`|`$/g, ''))
    if (cells.length !== 8) throw new Error('Dòng EXEMPTIONS phải có 8 cột, không dùng dấu | trong lý do')
    const [file, rule, fingerprint, count, symbol, reason, owner, until] = cells
    if (!Number.isSafeInteger(Number(count))) throw new Error(`Số lượng miễn trừ không an toàn: ${file}`)
    if (!/^QS\d{3}$/.test(rule) || !/^[a-f0-9]{20}$/.test(fingerprint) || !/^[1-9]\d*$/.test(count)) throw new Error(`Miễn trừ không hợp lệ: ${file}`)
    if (!reason || !owner || !symbol || !/^\d{4}-\d{2}-\d{2}$/.test(until) || !Number.isFinite(Date.parse(until)) || new Date(until).toISOString().slice(0, 10) !== until) throw new Error(`Miễn trừ thiếu lý do/người chốt/hạn đúng: ${file}`)
    const key = `${file}:${rule}:${fingerprint}`
    if (rows.some(row => row.key === key)) throw new Error(`Miễn trừ lặp: ${key}`)
    rows.push({key, file, rule, fingerprint, count: Number(count), symbol, reason, owner, until})
  }
  return rows
}
export function applyExemptions(findings, entries, today = new Date().toISOString().slice(0, 10)) {
  const used = new Map()
  return findings.map(finding => {
    const key = `${finding.file}:${finding.rule}:${finding.fingerprint}`
    const entry = entries.find(e => e.key === key)
    if (!entry) return finding
    const count = (used.get(key) ?? 0) + 1; used.set(key, count)
    if (entry.until < today) return {...finding, message: `${finding.message}; miễn trừ hết hạn ${entry.until}`}
    if (count > entry.count) return {...finding, message: `${finding.message}; vượt số call đã rà ${entry.count}`}
    return {...finding, exempt: true, until: entry.until, reason: entry.reason}
  })
}
