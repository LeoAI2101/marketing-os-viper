// Git chỉ nằm ở máy giữ code; snapshot mang đúng mốc cố định sang bản rsync (máy chạy qua ống) không có .git.
import { readFileSync, readdirSync, existsSync } from 'node:fs'
import { join } from 'node:path'
import { execFileSync } from 'node:child_process'
import { createHash } from 'node:crypto'
export const CONFIG = 'context/perf/API-CONTRACT-BASE.json'
export const SNAPSHOT = '.local/contracts/api-contract-base.json'
const artifact = p => /^srcroot\/contracts\/[^/]+\/(openapi\.json|schema\.graphql)$/.test(p)
const source = p => /^srcroot\/boundaries\/[^/]+\/src\/.*\.ts$/.test(p) &&
  !/\.(spec|test|d)\.ts$|\/shared\/contracts\//.test(p)
const accepted = p => artifact(p) || source(p)
export function walk(root, prefix = '') {
  if (!existsSync(join(root, prefix))) return []
  return readdirSync(join(root, prefix), { withFileTypes: true }).flatMap(entry => {
    const path = prefix ? `${prefix}/${entry.name}` : entry.name
    if (entry.isSymbolicLink()) return []
    if (entry.isDirectory()) return ['node_modules', 'dist', 'dist-contract', '.git'].includes(entry.name) ? [] : walk(root, path)
    return [path]
  })
}
const digest = files => createHash('sha256').update(JSON.stringify(files)).digest('hex')
export function fromGit(root, ref) {
  const git = args => execFileSync('git', args, { cwd: root, encoding: 'utf8', maxBuffer: 40 * 1024 * 1024 })
  const commit = git(['rev-parse', '--verify', `${ref}^{commit}`]).trim()
  const paths = git(['ls-tree', '-r', '--name-only', commit, '--', 'srcroot/contracts', 'srcroot/boundaries'])
    .trim().split('\n').filter(accepted).sort()
  // Một tiến trình git cat-file cho toàn bộ snapshot, tránh hàng nghìn lần spawn.
  const input = paths.map(p => `${commit}:${p}\n`).join('')
  const data = execFileSync('git', ['cat-file', '--batch'], { cwd: root, input, maxBuffer: 40 * 1024 * 1024 })
  let cursor = 0
  const files = {}
  for (const path of paths) {
    const end = data.indexOf(10, cursor)
    const header = data.subarray(cursor, end).toString()
    if (!/^\w+ blob \d+$/.test(header)) throw new Error(`Git không đọc được ${path}: ${header}`)
    const bytes = Number(header.split(' ')[2]); cursor = end + 1
    files[path] = data.subarray(cursor, cursor + bytes).toString(); cursor += bytes + 1
  }
  return { version: 1, commit, digest: digest(files), files }
}
export function validateSnapshot(snapshot, expected) {
  if (snapshot.version !== 1 || snapshot.commit !== expected || snapshot.digest !== digest(snapshot.files)) {
    throw new Error('Snapshot hợp đồng thiếu/sai mốc hoặc hỏng; sync lại từ máy có Git')
  }
  const count = Object.keys(snapshot.files).filter(artifact).length
  if (count < 1) throw new Error('Baseline không có hợp đồng nào; không được báo xanh')
  return snapshot
}
export function currentFiles(root) {
  return Object.fromEntries(['srcroot/contracts', 'srcroot/boundaries'].flatMap(p => walk(root, p))
    .filter(accepted).sort().map(p => [p, readFileSync(join(root, p), 'utf8')]))
}
export function splitFiles(files) {
  return { contracts: Object.fromEntries(Object.entries(files).filter(([p]) => artifact(p))),
    sources: Object.fromEntries(Object.entries(files).filter(([p]) => source(p))) }
}
