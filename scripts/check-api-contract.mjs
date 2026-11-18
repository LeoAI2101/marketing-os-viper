#!/usr/bin/env node
// Không DB, không build; cùng engine dùng ở make check và guard deploy.
import { readFileSync, existsSync, mkdirSync, writeFileSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { execFileSync, spawnSync } from 'node:child_process'
import { createHash } from 'node:crypto'
import { gzipSync, gunzipSync } from 'node:zlib'
import { compareOpenApi } from './lib/openapi-compat.mjs'
import { compareEvents } from './lib/event-compat.mjs'
import { loadDep, remoteOn } from './lib/node-deps.mjs'
import { CONFIG, SNAPSHOT, fromGit, validateSnapshot, currentFiles, splitFiles } from './lib/contract-snapshot.mjs'
const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '..')
const ENGINE = ['scripts/check-api-contract.mjs', 'scripts/lib/openapi-compat.mjs',
  'scripts/lib/event-compat.mjs', 'scripts/lib/contract-snapshot.mjs', 'scripts/lib/ts-ast.mjs']
const fingerprint = () => createHash('sha256').update(ENGINE.map(p => readFileSync(resolve(ROOT, p))).join('\n')).digest('hex')
const read = path => JSON.parse(readFileSync(resolve(ROOT, path), 'utf8'))
function options(args) {
  const opt = { base: process.env.API_CONTRACT_BASE }
  while (args.length) {
    const arg = args.shift()
    if (arg === '--base' && args[0] && !args[0].startsWith('-')) opt.base = args.shift()
    else if (['--snapshot', '--stdin', '--stdin-gzip', '--json'].includes(arg)) opt[arg.slice(2)] = true
    else throw new Error(`Tham số không hợp lệ: ${arg}; dùng --base <git-ref>, --snapshot hoặc --json`)
  }
  if (opt.base?.startsWith('-')) throw new Error('Mốc Git không được bắt đầu bằng dấu -')
  return opt
}
function baseline(opt) {
  const configured = read(CONFIG).base
  if (!/^[a-f0-9]{40}$/.test(configured)) throw new Error('Mốc mặc định phải là SHA Git đầy đủ, không dùng HEAD trôi')
  if (existsSync(resolve(ROOT, '.git'))) return fromGit(ROOT, opt.base || configured)
  if (opt.base && opt.base !== configured) throw new Error('--base khác mốc phải chạy từ máy có Git')
  if (!existsSync(resolve(ROOT, SNAPSHOT))) throw new Error('Thiếu snapshot baseline; chạy PT=1 bash scripts/remote/sync.sh từ máy giữ code')
  return validateSnapshot(read(SNAPSHOT), configured)
}
function dependencies() {
  // graphql chỉ cần khi có schema.graphql — dự án REST thuần không phải cài nó.
  let graphql
  try { graphql = loadDep(ROOT, 'graphql') } catch (e) { graphql = { missing: e } }
  return { graphql, ts: loadDep(ROOT, 'typescript') }
}
function compare(bundle, deps) {
  if (bundle.engine !== fingerprint()) throw new Error('Checker ở máy chạy lệch bản máy giữ code; sync code rồi kiểm lại')
  validateSnapshot(bundle.baseline, bundle.baseline.commit)
  const old = splitFiles(bundle.baseline.files), next = splitFiles(bundle.current)
  const errors = []
  for (const [path, text] of Object.entries(old.contracts)) {
    const now = next.contracts[path]
    if (!now) { errors.push(`${path}: mất hợp đồng`); continue }
    try {
      const diagnostics = path.endsWith('.json') ? compareOpenApi(JSON.parse(text), JSON.parse(now)) :
        compareGraphql(text, now, deps.graphql)
      errors.push(...diagnostics.map(e => `${path}: ${e}`))
    } catch (e) { errors.push(`${path}: không đọc được hợp đồng: ${e.message}`) }
  }
  const events = compareEvents(old.sources, next.sources, deps.ts)
  errors.push(...events.errors)
  return { base: bundle.baseline.commit, contracts: Object.keys(old.contracts).length,
    eventSources: events.count, errors }
}
function compareGraphql(old, next, gql) {
  if (gql.missing) throw gql.missing
  const a = gql.buildSchema(old), b = gql.buildSchema(next)
  const breaking = gql.findBreakingChanges(a, b)
  // Enum output mới dễ làm client exhaustive switch vỡ; đánh dấu cả thay đổi nguy hiểm để rà.
  return [...breaking, ...gql.findDangerousChanges(a, b)].map(c => `${c.type}: ${c.description}`)
}
function main() {
  const start = performance.now(), opt = options(process.argv.slice(2))
  const streamed = opt.stdin || opt['stdin-gzip']
  const bundle = streamed ? JSON.parse(opt['stdin-gzip'] ? gunzipSync(readFileSync(0), { maxOutputLength: 40 * 1024 * 1024 }).toString() : readFileSync(0, 'utf8')) :
    { engine: fingerprint(), baseline: baseline(opt) }
  if (opt.snapshot) {
    if (streamed) throw new Error('Không dùng --snapshot cùng --stdin')
    mkdirSync(dirname(resolve(ROOT, SNAPSHOT)), { recursive: true })
    writeFileSync(resolve(ROOT, SNAPSHOT), JSON.stringify(bundle.baseline))
    console.log(`✓ snapshot hợp đồng ${bundle.baseline.commit}`); return 0
  }
  if (!streamed) bundle.current = currentFiles(ROOT)
  let deps
  try { deps = dependencies() } catch (e) {
    if (streamed || !remoteOn(ROOT)) throw e
    const result = spawnSync('bash', ['scripts/remote/ub', `node scripts/check-api-contract.mjs --stdin-gzip${opt.json ? ' --json' : ''}`],
      { cwd: ROOT, input: gzipSync(JSON.stringify(bundle)), encoding: 'utf8', maxBuffer: 40 * 1024 * 1024, timeout: 30000 })
    process.stdout.write(result.stdout ?? ''); process.stderr.write(result.stderr ?? '')
    if (result.error) throw result.error
    return result.status ?? 2
  }
  const result = compare(bundle, deps)
  result.seconds = Number(((performance.now() - start) / 1000).toFixed(3))
  if (opt.json) console.log(JSON.stringify(result))
  else {
    for (const e of result.errors) console.error(`✗ ${e}`)
    console.log(`${result.errors.length ? '✗' : '✓'} api-contract: ${result.contracts} hợp đồng · ${result.eventSources} nguồn event/phụ thuộc · ${result.seconds}s · mốc ${result.base.slice(0, 12)}`)
  }
  return result.errors.length ? 1 : 0
}
try { process.exitCode = main() } catch (e) { console.error(`✗ api-contract: ${e.message}`); process.exitCode = 2 }
