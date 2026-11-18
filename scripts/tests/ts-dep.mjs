// typescript cho test của checker AST — lấy từ node_modules của target (không cài thêm).
// Template chưa scaffold target nào thì chưa có gì để kiểm: bỏ qua cả file, nói rõ ra.
// Đã có target mà vẫn không nạp được typescript thì ĐỎ — đó là "chưa kiểm được", không phải xanh.
import { existsSync, readdirSync } from 'node:fs'
import { resolve, dirname } from 'node:path'
import { fileURLToPath } from 'node:url'
import { loadDep } from '../lib/node-deps.mjs'

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '../..')

export function tsOrSkip() {
  try { return loadDep(ROOT, 'typescript') } catch (error) {
    const base = resolve(ROOT, 'srcroot/boundaries')
    const scaffolded = existsSync(base) && readdirSync(base).some(d => existsSync(resolve(base, d, 'package.json')))
    if (scaffolded) throw error
    console.log('# SKIP: chưa có boundary nào có package.json — chưa có typescript để kiểm checker AST')
    process.exit(0)
  }
}
