import assert from 'node:assert/strict'
import test from 'node:test'
import { parseExemptions, applyExemptions } from '../lib/query-exemptions.mjs'

const file = 'srcroot/boundaries/d-booking/src/features/booking/booking.repository.ts'
const fingerprint = '0123456789abcdef0123'
const today = '2026-09-23'
const row = (overrides = {}) => {
  const entry = { file, rule: 'QS001', fingerprint, count: '1', symbol: 'list',
    reason: 'Chờ thay bằng phân trang giữ hợp đồng', owner: 'Nhóm phát triển', until: today, ...overrides }
  return `| \`${entry.file}\` | ${entry.rule} | \`${entry.fingerprint}\` | ${entry.count} | ${entry.symbol} | ${entry.reason} | ${entry.owner} | ${entry.until} |`
}
const finding = (overrides = {}) => ({ file, rule: 'QS001', fingerprint, level: 'error',
  line: 1, column: 1, message: 'Truy vấn thiếu giới hạn', ...overrides })

test('miễn đến hết ngày đã chốt nhưng hết hạn phải trả vi phạm trở lại', () => {
  const entries = parseExemptions(row())
  const [valid] = applyExemptions([finding()], entries, today)
  assert.equal(valid.exempt, true)
  assert.equal(valid.until, today)
  assert.ok(valid.reason)
  const [expired] = applyExemptions([finding()], entries, '2026-09-24')
  assert.notEqual(expired.exempt, true)
  assert.equal(expired.level, 'error')
  assert.match(expired.message, /hết hạn/)  // lang-ok: khớp thông điệp lỗi tiếng Việt của checker
})

test('miễn chỉ đúng fingerprint, file và rule; query mới cùng file vẫn bị chặn', () => {
  const original = finding()
  const newQuery = finding({ fingerprint: 'fedcba9876543210fedc' })
  const otherFile = finding({ file: file.replace('booking.repository', 'archive.repository') })
  const otherRule = finding({ rule: 'QS003' })
  const result = applyExemptions([original, newQuery, otherFile, otherRule], parseExemptions(row()), today)
  assert.equal(result[0].exempt, true)
  for (const item of result.slice(1)) assert.notEqual(item.exempt, true)
  assert.equal(original.exempt, undefined, 'Không sửa finding đầu vào dùng chung với báo cáo')
})

test('sao chép query vượt count đã rà không được thừa hưởng miễn trừ', () => {
  const entries = parseExemptions(row({ count: '2' }))
  const findings = [finding(), finding({ line: 20 }), finding({ line: 40 })]
  const result = applyExemptions(findings, entries, today)
  assert.deepEqual(result.map(item => item.exempt === true), [true, true, false])
  assert.match(result[2].message, /vượt số/)  // lang-ok: khớp thông điệp lỗi tiếng Việt của checker
  assert.equal(applyExemptions([finding()], entries, today)[0].exempt, true,
    'Đếm độc lập giữa hai lần chạy checker')
})

test('ngày không tồn tại hoặc thiếu metadata không được thành miễn trừ', () => {
  for (const until of ['2026-02-30', '2026-13-01', '23/09/2026', '']) {
    assert.throws(() => parseExemptions(row({ until })), undefined, until)
  }
  for (const field of ['reason', 'owner', 'symbol']) {
    assert.throws(() => parseExemptions(row({ [field]: '' })), undefined, field)
  }
  assert.throws(() => parseExemptions(row({ reason: 'lý do | thêm cột' })))
})

test('định danh và count sai không thể làm hàng rào im lặng cho qua', () => {
  for (const rule of ['', 'QS1', '*']) assert.throws(() => parseExemptions(row({ rule })))
  for (const fingerprint of ['', 'abc', '*']) assert.throws(() => parseExemptions(row({ fingerprint })))
  for (const count of ['0', '-1', '1.5', 'Infinity', '', '9007199254740992', '9'.repeat(400)]) {
    assert.throws(() => parseExemptions(row({ count })), undefined, count)
  }
})

test('hai dòng cùng query không được cộng dồn số lần miễn', () => {
  assert.throws(() => parseExemptions(`${row()}\n${row({ count: '2', until: '2026-10-01' })}`), /lặp/)  // lang-ok: khớp thông điệp lỗi tiếng Việt của checker
})
