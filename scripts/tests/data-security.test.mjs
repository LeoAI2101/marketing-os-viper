import assert from 'node:assert/strict'
import test from 'node:test'
import {analyzeSource} from '../lib/data-security.mjs'
import { tsOrSkip } from './ts-dep.mjs'
const ts=tsOrSkip()
const scan=source=>analyzeSource(ts,source,'srcroot/boundaries/d-profile/src/sample.ts')
const has=(source,rule)=>scan(source).some(f=>f.rule===rule)

test('Unsafe literal, computed và callee bọc vẫn bị chặn',()=>{
  for(const source of ['prisma.$queryRawUnsafe("SELECT 1")','(prisma["$executeRawUnsafe"] as any)(sql)','prisma.$queryRawUnsafe`SELECT 1`'])
    assert.ok(has(source,'SEC001'),source)
})
test('binding tagged template và query có parameters không bị coi là nối SQL',()=>{
  for(const source of ['prisma.$queryRaw`SELECT * FROM users WHERE id = ${id}`','db.query("SELECT * FROM users WHERE id=$1", [id])','prisma.$queryRaw(Prisma.sql`SELECT ${id}`)'])
    assert.deepEqual(scan(source),[],source)
})
test('SQL nội suy và biến local nối chuỗi bị chặn',()=>{
  for(const source of ['db.query(`SELECT * FROM users WHERE id=${id}`)','const sql="SELECT * FROM users WHERE id="+id; db.query(sql)','pool.execute("DELETE FROM users WHERE id="+id)'])
    assert.ok(has(source,'SEC002'),source)
})
test('SQL động không chứng minh được binding phải cảnh báo',()=>assert.ok(has('db.query(loadSql())','SEC005')))
test('body, computed header, alias local và token đi vào logger phải báo',()=>{
  for(const source of ['logger.error(req.body)','console.log(req["headers"])','const payload=req.body; logger.warn(payload)','logger.info({accessToken:value})'])
    assert.ok(has(source,'SEC003'),source)
})
test('không giả redaction theo tên khoá là đã loại PII',()=>assert.ok(has('logger.error(maskSecret(req.body))','SEC003')))
test('LOG_REQUEST_BODY mặc định bật và opt-in được phân biệt',()=>{
  assert.ok(has("process.env.LOG_REQUEST_BODY !== '0'",'SEC004'))
  assert.ok(has("process.env['LOG_REQUEST_BODY'] ?? '1'",'SEC004'))
  assert.deepEqual(scan("process.env.LOG_REQUEST_BODY === '1'"),[])
})
test('comment và chuỗi minh hoạ không kích hoạt luật',()=>assert.deepEqual(scan('// logger.error(req.body)\nconst sample="prisma.$queryRawUnsafe(sql)"'),[]))
test('fingerprint bỏ whitespace nhưng giữ literal để bắt thay source',()=>{
  assert.equal(scan('logger.error(req.body)')[0].fingerprint,scan('logger.error( req.body )')[0].fingerprint)
  assert.notEqual(scan('logger.error(req.body,"a b")')[0].fingerprint,scan('logger.error(req.body,"ab")')[0].fingerprint)
})
test('source hỏng cú pháp không được trả mảng rỗng',()=>assert.throws(()=>scan('const = ;')))

// Ca review phải giữ đỏ khi chưa sửa; không đổi thành kiểm hành vi lỗi là đúng.
test('DS-T02 Prisma.raw trong tagged SQL phải bị phát hiện',()=>{
  assert.ok(scan('prisma.$queryRaw`SELECT * FROM users WHERE id=${Prisma.raw(input)}`').some(f=>f.level==='error'))
})
test('DS-T03 parameter shadowing không được coi là literal SQL đã biết',()=>{
  assert.ok(scan('const sql="SELECT 1"; function run(sql:string){db.query(sql)}').length>0)
})
test('DS-T03 gán lại SQL phải làm mất bằng chứng literal an toàn',()=>{
  assert.ok(scan('let sql="SELECT 1"; sql += input; db.query(sql)').length>0)
})
test('DS-T04 log placeholder cố định hoặc số ký tự không phải lộ secret/body',()=>{
  assert.deepEqual(scan('logger.info({password:"<redacted>"}); logger.info({bodyLength:req.body.length})'),[])
})

test('lexical scope: destructured parameter, catch và loop không mượn literal bên ngoài',()=>{
 for(const source of [
  'const sql="SELECT 1"; function run({sql}){db.query(sql)}',
  'const sql="SELECT 1"; for(const sql of inputs){db.query(sql)}',
  'const sql="SELECT 1"; try{run()}catch(sql){db.query(sql)}',
  'const sql="SELECT 1"; {const {sql}=input; db.query(sql)}',
 ]) assert.ok(scan(source).some(f=>f.rule==='SEC005'),source)
})
test('log giữ error cho chuyển nguyên nội dung; opaque transform/member chỉ là nghi vấn',()=>{
 for(const source of ['logger.info(JSON.stringify(req.body))','logger.info(String(password))','logger.info(`${password}`)','logger.info({...req.body})','const p=req.body;logger.info(p)'])
  assert.ok(scan(source).some(f=>f.rule==='SEC003'&&f.level==='error'),source)
 for(const source of ['logger.info(maskPhone(phoneNumber))','const result=await fetch(url,{headers:{authorization:token}});logger.info(result.status)','logger.info(envelope.headers.MessageStep)'])
  assert.ok(scan(source).some(f=>f.rule==='SEC003'&&f.level==='warning'),source)
})
