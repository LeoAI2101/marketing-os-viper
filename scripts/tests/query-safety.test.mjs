import assert from 'node:assert/strict'
import test from 'node:test'
import { analyzeSource } from '../lib/query-safety.mjs'
import { tsOrSkip } from './ts-dep.mjs'

const ts = tsOrSkip()
const file = 'srcroot/boundaries/d-booking/src/features/booking/booking.repository.ts'
const scan = (source, schema = '') => analyzeSource(ts, source, file, schema)
const rule = (source, name, schema = '') => scan(source, schema).filter((finding) => finding.rule === name)

test('chặn đọc toàn bảng khi thiếu take hoặc không chứng minh được giới hạn từ đối số', () => {
  for (const args of ['', '{}', '{ where: { ownerId } }', 'options', '{ ...options }']) {
    const found = rule(`this.prisma.booking.findMany(${args})`, 'QS001')
    assert.equal(found.length, 1, args)
    assert.equal(found[0].level, 'error')
  }
})

test('take rỗng, không nguyên, sai kiểu hoặc vô hạn không được coi là đã phân trang', () => {
  for (const take of [
    '0', '-0', 'undefined', 'null', 'Infinity', '-Infinity', 'NaN', 'void 0',
    '1.5', '"20"', 'true', 'false', '20n', '{}', '[]', '!20', '~20',
  ]) {
    assert.equal(rule(`this.prisma.booking.findMany({ take: ${take} })`, 'QS001').length, 1, take)
  }
})

test('chấp nhận phân trang xuôi, ngược và take động mà không giả vờ kiểm được validation', () => {
  for (const args of ['{ take: 20 }', '{ take: -20 }', '{ take: size }', '{ take }', '{ ...options, take: 20 }']) {
    assert.equal(rule(`this.prisma.booking.findMany(${args})`, 'QS001').length, 0, args)
  }
})

test('spread sau take có thể xoá giới hạn nên vẫn phải chặn', () => {
  assert.equal(rule('this.prisma.booking.findMany({ take: 20, ...options })', 'QS001').length, 1)
})

test('bọc callee hoặc đối số bằng cú pháp TypeScript không che truy vấn thiếu giới hạn', () => {
  const callees = [
    '(this.prisma.booking.findMany)',
    '(this.prisma.booking.findMany as any)',
    'this.prisma.booking.findMany!',
    '(this.prisma.booking.findMany satisfies Function)',
  ]
  const args = ['({})', '({} as any)', '({})!', '({} satisfies Options)']
  for (const callee of callees) {
    assert.equal(rule(`${callee}({})`, 'QS001').length, 1, callee)
  }
  for (const arg of args) {
    assert.equal(rule(`this.prisma.booking.findMany(${arg})`, 'QS001').length, 1, arg)
    assert.equal(rule(`this.prisma.booking.findMany(${arg.replace('{}', '{ take: 20 }')})`, 'QS001').length, 0, arg)
  }
})

test('take bọc trong ngoặc hoặc ép kiểu vẫn phải kiểm được giá trị rỗng', () => {
  for (const wrapper of ['(VALUE)', '(VALUE as number)', '(VALUE)!', '(VALUE satisfies number)']) {
    for (const value of ['undefined', 'null', '0', 'void 0']) {
      const take = wrapper.replace('VALUE', value)
      assert.equal(rule(`this.prisma.booking.findMany({ take: ${take} })`, 'QS001').length, 1, take)
    }
    const take = wrapper.replace('VALUE', '20')
    assert.equal(rule(`this.prisma.booking.findMany({ take: ${take} })`, 'QS001').length, 0, take)
  }
})

test('computed key ghi đè take không được giữ giới hạn cũ', () => {
  for (const args of ['{ take: 20, ["take"]: undefined }', '{ take: 20, [key]: value }']) {
    assert.equal(rule(`this.prisma.booking.findMany(${args})`, 'QS001').length, 1, args)
  }
  for (const args of ['{ ["take"]: 20 }', '{ [key]: value, take: 20 }', '{ take: 20, ["skip"]: 0 }']) {
    assert.equal(rule(`this.prisma.booking.findMany(${args})`, 'QS001').length, 0, args)
  }
})

test('comment và chuỗi chứa mã nguy hiểm không sinh cảnh báo giả', () => {
  assert.deepEqual(scan(`
    // this.prisma.booking.findMany()
    /* this.prisma.$transaction(async tx => fetch(url)) */
    const example = 'for (const id of ids) this.prisma.booking.findMany()';
    const sample = "this.prisma.$transaction(async tx => axios.post(url))";
  `), [])
})

test('cảnh báo N+1 trong vòng lặp và callback duyệt danh sách', () => {
  const call = 'this.prisma.booking.findUnique({ where: { id } })'
  for (const source of [
    `for (const id of ids) { await ${call} }`,
    `for (let i = 0; i < ids.length; i++) { await ${call} }`,
    `while (next) { await ${call} }`,
    `ids.map(async id => ${call})`,
    `ids.forEach(async id => { await ${call} })`,
  ]) {
    const found = rule(source, 'QS002')
    assert.equal(found.length, 1, source)
    assert.equal(found[0].level, 'warning')
  }
})

test('truy vấn một lần trước vòng lặp không bị gắn nhãn N+1', () => {
  assert.equal(rule(`
    const bookings = await this.prisma.booking.findMany({ take: 100 });
    for (const booking of bookings) { consume(booking); }
    const ids = bookings.map(booking => booking.id);
  `, 'QS002').length, 0)
})

test('chặn mạng trong callback transaction để không giữ khoá DB chờ dịch vụ ngoài', () => {
  for (const network of ['fetch(url)', 'axios.post(url, body)', 'http.request(url)']) {
    const found = rule(`this.prisma.$transaction(async tx => {
      await tx.booking.update({ where: { id }, data });
      await ${network};
    })`, 'QS003')
    assert.equal(found.length, 1, network)
    assert.equal(found[0].level, 'error')
  }
})

test('gọi mạng sau transaction hoặc bên ngoài callback không bị quy là giữ khoá', () => {
  assert.equal(rule(`
    await this.prisma.$transaction(async tx => {
      await tx.booking.update({ where: { id }, data });
    });
    await fetch(url);
    await axios.post(url, body);
    await this.prisma.$transaction([this.prisma.booking.update({ where: { id }, data })]);
    http.request(url);
  `, 'QS003').length, 0)
})

test('callback map lồng trong transaction vẫn giữ kết nối DB khi gọi mạng', () => {
  const source = `this.prisma.$transaction(async tx => {
    await tx.booking.update({ where: { id }, data });
    await Promise.all(urls.map(async url => {
      await fetch(url);
    }));
  })`
  assert.equal(rule(source, 'QS003').length, 1)
})

const schema = `
model Booking {
  id String @id
  externalId String @unique
  ownerId String
  status String
  @@index([ownerId])
}
`

test('gợi ý index cho lọc bằng không có ứng viên, giữ yên id, unique và index đã khai', () => {
  const query = (field) => `this.prisma.booking.findMany({ where: { ${field}: value }, take: 20 })`
  const missing = rule(query('status'), 'QS004', schema)
  assert.equal(missing.length, 1)
  assert.equal(missing[0].level, 'warning')
  for (const field of ['id', 'externalId', 'ownerId']) {
    assert.equal(rule(query(field), 'QS004', schema).length, 0, field)
  }
})

test('index chỉ nhắc trong chú thích schema không làm mất cảnh báo thiếu index', () => {
  const query = 'this.prisma.booking.findMany({ where: { status: value }, take: 20 })'
  const commented = schema.replace('status String', 'status String // dự kiến @unique')
    .replace('@@index([ownerId])', '@@index([ownerId])\n  // @@index([status])')
  assert.equal(rule(query, 'QS004', commented).length, 1)
})

test('vị trí finding trỏ đúng câu gọi và fingerprint không lệ thuộc dòng hoặc comment', () => {
  const source = 'this.prisma.booking.findMany({ where: { ownerId } })'
  const original = rule(source, 'QS001')[0]
  const formatted = rule(`// chú thích mới\n\nthis.prisma.booking.findMany( {\n where: { ownerId } /* cùng truy vấn */\n} )`, 'QS001')[0]
  const changed = rule('this.prisma.booking.findMany({ where: { status } })', 'QS001')[0]
  assert.equal(original.file, file)
  assert.equal(original.line, 1)
  assert.equal(original.column, 1)
  assert.equal(formatted.line, 3)
  assert.ok(original.message.length > 0)
  assert.ok(original.fingerprint.length > 0)
  assert.equal(original.fingerprint, formatted.fingerprint)
  assert.notEqual(original.fingerprint, changed.fingerprint)
})

test('QS-P01..03 callback và network callee bọc vẫn bị phát hiện',()=>{
  for(const body of [
    'prisma.$transaction((async tx=>{await fetch(url)}))',
    'prisma.$transaction(async tx=>{await items.map((async x=>fetch(url)))})',
    'prisma.$transaction(async tx=>{await (axios.post)(url)})',
  ]) assert.equal(rule(body,'QS003').length,1,body)
  assert.equal(rule('ids.map((async id=>db.user.findUnique({where:{id}})))','QS002').length,1)
})
test('QS-P04 fingerprint giữ khoảng trắng sau nội suy và template lồng',()=>{
  const fingerprint=value=>rule('db.user.findMany({where:{name:'+value+'}})','QS001')[0].fingerprint
  assert.notEqual(fingerprint('`x${owner} tail`'),fingerprint('`x${owner}tail`'))
  assert.notEqual(fingerprint('`x${`a${owner} b`} tail`'),fingerprint('`x${`a${owner}b`} tail`'))
})
