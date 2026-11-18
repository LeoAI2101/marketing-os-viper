import assert from 'node:assert/strict'
import test from 'node:test'
import { compareEvents, eventSources } from '../lib/event-compat.mjs'
import { tsOrSkip } from './ts-dep.mjs'
const ts = tsOrSkip()
const path = 'srcroot/boundaries/d-booking/src/shared/streams/outbox.service.ts'
const files = { [path]: "import { payload } from '../../features/data'; redis.xadd('dp.booking.events', payload)",
  'srcroot/boundaries/d-booking/src/features/data.ts': 'export const payload = { id: 1, state: "created" }' }
test('event theo cả dependency payload, đổi trường inline phải đỏ', () => {
  const next = structuredClone(files)
  next['srcroot/boundaries/d-booking/src/features/data.ts'] = 'export const payload = { id: 1 }'
  assert.equal(eventSources(files).length, 2)
  assert.equal(compareEvents(files, next, ts).errors.length, 1)
})
test('thêm hoặc bỏ producer chưa rà đều đỏ', () => {
  assert.ok(compareEvents({}, files, ts).errors.length)
  assert.ok(compareEvents(files, {}, ts).errors.length)
})
test('chỉ đổi comment/khoảng trắng không đổi payload', () => {
  const next = Object.fromEntries(Object.entries(files).map(([p, text]) => [p, `// chú thích\n${text}\n`]))
  assert.equal(compareEvents(files, next, ts).errors.length, 0)
})
test('inventory giữ cả model outbox có tiền tố và file .outbox.ts', () => {
  const sources = {
    'srcroot/boundaries/d-identity-trust/src/features/device-token/device-token.repository.ts': 'tx.deviceTokenOutbox.create({data: payload})',
    'srcroot/boundaries/d-notification/src/features/notification-events/notification-events.repository.ts': 'tx.notificationOutbox.create({data: payload})',
    'srcroot/boundaries/d-notification/src/features/notification-events/notification.outbox.ts': 'client.banPush(payload)',
  }
  assert.equal(eventSources(sources).length, 3)
  for (const path of Object.keys(sources)) {
    const next = { ...sources, [path]: sources[path].replace('payload', '{ action: "new" }') }
    assert.ok(compareEvents(sources, next, ts).errors.length > 0, path)
  }
})

test('khoảng trắng trong payload template sau nội suy vẫn là đổi hợp đồng',()=>{
 const before={[path]:'redis.xadd(stream, `x${id} tail`)'}
 const after={[path]:'redis.xadd(stream, `x${id}tail`)'}
 assert.equal(compareEvents(before,after,ts).errors.length,1)
})
