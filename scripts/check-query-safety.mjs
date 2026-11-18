#!/usr/bin/env node
import { readFileSync, existsSync } from 'node:fs'
import { resolve, relative, dirname } from 'node:path'
import { fileURLToPath } from 'node:url'
import { execFileSync, spawnSync } from 'node:child_process'
import { createHash } from 'node:crypto'
import { gzipSync, gunzipSync } from 'node:zlib'
import { walk } from './lib/contract-snapshot.mjs'
import { loadDep, remoteOn } from './lib/node-deps.mjs'
import { analyzeSource } from './lib/query-safety.mjs'
import { parseExemptions, applyExemptions } from './lib/query-exemptions.mjs'
const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '..')
const REGISTRY = 'context/perf/EXEMPTIONS.md'
const ENGINE = ['scripts/check-query-safety.mjs', 'scripts/lib/ts-ast.mjs', 'scripts/lib/query-safety.mjs', 'scripts/lib/query-exemptions.mjs']
const fingerprint = () => createHash('sha256').update(ENGINE.map(p => readFileSync(resolve(ROOT, p))).join('\n')).digest('hex')
function options(args) {
  const opt = {}
  while (args.length) {
    const arg = args.shift()
    if (['--target', '--file'].includes(arg) && args[0] && !args[0].startsWith('-')) opt[arg.slice(2)] = args.shift()
    else if (['--json', '--stdin', '--bundle-gzip'].includes(arg)) opt[arg.slice(2)] = true
    else throw new Error(`Tham số không hợp lệ: ${arg}; dùng --target, --file, --stdin, --json`)
  }
  if (opt.stdin && !opt.file) throw new Error('--stdin cần --file để xác định source và schema')
  return opt
}
function targets() {
  const code = "import json,sys;sys.path.insert(0,'scripts');from lib.ops_env import discover_targets;print(json.dumps(discover_targets(('boundaries',))))"
  return Object.keys(JSON.parse(execFileSync('python3', ['-c', code], {cwd: ROOT, encoding: 'utf8'})))
}
function input(opt) {
  const names = targets()
  if (opt.target && !names.includes(opt.target)) throw new Error(`Target không tồn tại: ${opt.target}`)
  let files
  if (opt.file) {
    const file = relative(ROOT, resolve(ROOT, opt.file)).replaceAll('\\', '/')
    if (!/^srcroot\/boundaries\/[^/]+\/src\/.*\.ts$/.test(file) || /\.(spec|test|d)\.ts$|\/shared\/contracts\//.test(file)) throw new Error(`File ngoài source được kiểm: ${file}`)
    if (!names.includes(file.split('/')[2]) || (opt.target && file.split('/')[2] !== opt.target)) throw new Error('--file không thuộc target đã chọn')
    files = [file]
  } else files = (opt.target ? [opt.target] : names).flatMap(n => {
    const sources = walk(ROOT, `srcroot/boundaries/${n}/src`)
      .filter(p => p.endsWith('.ts') && !/\.(spec|test|d)\.ts$|\/shared\/contracts\//.test(p))
    if (!sources.length) throw new Error(`Target thiếu source để kiểm: ${n}`)
    return sources
  })
  if (!files.length) throw new Error('Không có source để kiểm')
  const schemas = {}
  for (const name of names) {
    const path = resolve(ROOT, `srcroot/boundaries/${name}/prisma/schema.prisma`)
    if (name.startsWith('d-') && files.some(p => p.split('/')[2] === name) && !existsSync(path)) throw new Error(`Thiếu Prisma schema: ${name}`)
    schemas[name] = existsSync(path) ? readFileSync(path, 'utf8') : ''
  }
  return {engine: fingerprint(), schemas,
    files: Object.fromEntries(files.map(p => [p, readFileSync(opt.stdin ? 0 : resolve(ROOT, p), 'utf8')])),
    exemptions: existsSync(resolve(ROOT, REGISTRY)) ? readFileSync(resolve(ROOT, REGISTRY), 'utf8') : '',
    focused: Boolean(opt.file || opt.target), prospective: Boolean(opt.stdin)}
}
function evaluate(bundle, ts) {
  if (bundle.engine !== fingerprint()) throw new Error('Checker ở máy chạy lệch bản máy giữ code; sync code trước')
  const entries = parseExemptions(bundle.exemptions)
  const all = Object.entries(bundle.files).flatMap(([p, source]) => analyzeSource(ts, source, p, bundle.schemas[p.split('/')[2]] ?? ''))
  const findings = bundle.prospective ? all : applyExemptions(all, entries)
  const expired = bundle.prospective ? [] : entries.filter(e => e.until < new Date().toISOString().slice(0,10) &&
    (!bundle.focused || Object.hasOwn(bundle.files,e.file))).map(e => `${e.file}: miễn trừ ${e.rule} đã hết hạn ${e.until}`)
  return {files: Object.keys(bundle.files).length, findings, expired,
    violations: findings.filter(f => f.level === 'error' && !f.exempt).length + expired.length,
    warnings: findings.filter(f => f.level === 'warning' && !f.exempt).length,
    exemptions: findings.filter(f => f.exempt).length}
}
function main() {
  const start = performance.now(), opt = options(process.argv.slice(2))
  const bundle = opt['bundle-gzip'] ? JSON.parse(gunzipSync(readFileSync(0), {maxOutputLength: 40 * 1024 * 1024}).toString()) : input(opt)
  let ts
  try { ts = loadDep(ROOT, 'typescript') }
  catch (e) {
    if (opt['bundle-gzip'] || !remoteOn(ROOT)) throw e
    const r = spawnSync('bash', ['scripts/remote/ub', `node scripts/check-query-safety.mjs --bundle-gzip${opt.json ? ' --json' : ''}`],
      {cwd:ROOT, input:gzipSync(JSON.stringify(bundle)), encoding:'utf8', maxBuffer:40*1024*1024, timeout:30000})
    process.stdout.write(r.stdout ?? '');process.stderr.write(r.stderr ?? '')
    if (r.error) throw r.error
    return r.status ?? 2
  }
  const result = evaluate(bundle, ts)
  result.seconds = Number(((performance.now()-start)/1000).toFixed(3))
  if (opt.json) console.log(JSON.stringify(result))
  else {
    for (const f of result.findings.filter(f=>!f.exempt)) console.error(`${f.level === 'error' ? '✗':'▲'} ${f.file}:${f.line} ${f.rule}: ${f.message}`)
    for (const message of result.expired) console.error(`✗ ${message}`)
    console.log(`${result.violations ? '✗':'✓'} query-safety: ${result.files} file · ${result.violations} lỗi · ${result.warnings} cảnh báo · ${result.exemptions} miễn trừ có hạn · ${result.seconds}s`)
  }
  return result.violations ? 1 : 0
}
try {process.exitCode=main()} catch(e) {console.error(`✗ query-safety: ${e.message}`);process.exitCode=2}
