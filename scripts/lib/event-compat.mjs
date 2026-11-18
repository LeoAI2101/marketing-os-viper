// Chốt nguồn event trước khi có JSON Schema: thay đổi code không tự được coi là tương thích.
import { posix } from 'node:path'
import { canonicalTokens } from './ts-ast.mjs'

const ROOT_PATTERN = /\/(?:outbox|streams|kafka|platform)\/|(?:outbox|inbox|consumer|producer)(?:\.[^/]+)?\.ts$/
const TRANSPORT = /\b(?:outboxEvent|[a-zA-Z]+Outbox|AccountDeletionApplied|UserBootstrapped)\b|\b(?:xadd|xreadgroup|publishEvent|emitEvent)\s*\(|['"]dp\.[\w.-]+(?:events|commands)['"]/
const IMPORT = /(?:from\s*|import\s*\(|require\s*\()\s*['"](\.[^'"]+)['"]/g

export function eventSources(files) {
  const roots = Object.keys(files).filter(p => ROOT_PATTERN.test(p) || TRANSPORT.test(files[p]))
  const selected = new Set(roots), pending = [...roots]
  while (pending.length) {
    const file = pending.pop()
    for (const match of files[file].matchAll(IMPORT)) {
      const stem = posix.normalize(posix.join(posix.dirname(file), match[1]))
      const found = [stem, `${stem}.ts`, `${stem}/index.ts`].find(p => Object.hasOwn(files, p))
      if (found && !selected.has(found)) { selected.add(found); pending.push(found) }
    }
  }
  return [...selected].sort()
}
function tokens(ts, source) {
  return JSON.stringify(canonicalTokens(ts, source))
}
export function compareEvents(oldFiles, newFiles, ts) {
  const before = eventSources(oldFiles), after = eventSources(newFiles)
  const errors = []
  for (const path of new Set([...before, ...after])) {
    if (!oldFiles[path] || !newFiles[path] || tokens(ts, oldFiles[path]) !== tokens(ts, newFiles[path])) {
      errors.push(`${path}: chưa chứng minh tương thích event — nguồn phát/nhận hoặc phụ thuộc đã đổi; rà registry trước khi cập nhật mốc`)
    }
  }
  return { errors, count: new Set([...before, ...after]).size }
}
