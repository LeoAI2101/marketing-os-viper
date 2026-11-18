// So tương thích theo chiều dữ liệu. Không nới một cấu trúc chưa hiểu thành xanh.
const VERBS = ['get', 'put', 'post', 'delete', 'options', 'head', 'patch', 'trace']
const ANNOTATIONS = new Set(['description', 'title', 'example', 'examples', 'externalDocs', 'deprecated'])
const HANDLED = new Set(['type', 'properties', 'required', 'items', 'enum', 'nullable',
  'additionalProperties', 'minimum', 'maximum', 'minLength', 'maxLength', 'minItems',
  'maxItems', 'minProperties', 'maxProperties', '$ref', 'allOf'])

function semantic(value) {
  if (Array.isArray(value)) return value.map(semantic)
  if (!value || typeof value !== 'object') return value
  return Object.fromEntries(Object.keys(value).sort().filter(k => !ANNOTATIONS.has(k))
    .map(k => [k, semantic(value[k])]))
}
const same = (a, b) => JSON.stringify(semantic(a)) === JSON.stringify(semantic(b))

function intersect(parts) {
  const result = {}
  for (const part of parts) {
    for (const [key, value] of Object.entries(part)) {
      if (key === 'allOf') continue
      if (key === 'required') { result.required = [...new Set([...(result.required ?? []), ...value])]; continue }
      if (key === 'properties') {
        result.properties ??= {}
        for (const [name, schema] of Object.entries(value)) {
          const previous = result.properties[name]
          result.properties[name] = previous && !same(previous, schema) ? { allOf: [previous, schema] } : schema
        }
        continue
      }
      if (result[key] === undefined || same(result[key], value) || ANNOTATIONS.has(key)) { result[key] = value; continue }
      if (['minimum', 'minLength', 'minItems', 'minProperties'].includes(key)) result[key] = Math.max(result[key], value)
      else if (['maximum', 'maxLength', 'maxItems', 'maxProperties'].includes(key)) result[key] = Math.min(result[key], value)
      else if (key === 'enum') result.enum = result.enum.filter(v => value.some(w => same(v, w)))
      else throw new Error(`allOf giao ràng buộc ${key} chưa được hỗ trợ`)
    }
  }
  return result
}
function resolver(doc) {
  const cache = new WeakMap()
  function resolve(node = {}, trail = new Set()) {
    if (!node || typeof node !== 'object') return node
    if (cache.has(node)) return cache.get(node)
    if (trail.has(node)) throw new Error('Vòng tham chiếu allOf/$ref không có cấu trúc')
    const next = new Set([...trail, node])
    let result = { ...node }
    if (node.$ref) {
      if (!node.$ref.startsWith('#/')) throw new Error(`Không hỗ trợ $ref ngoài: ${node.$ref}`)
      const target = node.$ref.slice(2).split('/').reduce((v, k) => v?.[k.replace(/~1/g, '/').replace(/~0/g, '~')], doc)
      if (!target) throw new Error(`Không tìm thấy ${node.$ref}`)
      result = { ...resolve(target, next), ...result }
      delete result.$ref
    }
    if (result.allOf) {
      const parts = [...result.allOf.map(p => resolve(p, next)), result]
      result = intersect(parts)
    }
    cache.set(node, result)
    return result
  }
  return resolve
}

function schemaContext(oldDoc, newDoc, errors) {
  return { old: resolver(oldDoc), next: resolver(newDoc), errors, seen: new WeakMap() }
}
function markSeen(ctx, old, next, direction) {
  let pairs = ctx.seen.get(old)
  if (!pairs) { pairs = new WeakMap(); ctx.seen.set(old, pairs) }
  let directions = pairs.get(next)
  if (!directions) { directions = new Set(); pairs.set(next, directions) }
  if (directions.has(direction)) return true
  directions.add(direction)
  return false
}

function compareRequired(old, next, input, report) {
  const before = old.required ?? [], after = next.required ?? []
  for (const key of input ? after : before) {
    if (!(input ? before : after).includes(key)) report(`đổi tính bắt buộc của ${key}`)
  }
}
function compareValues(old, next, input, report) {
  if (!same(old.type, next.type)) report('đổi kiểu dữ liệu')
  if (input ? old.nullable && !next.nullable : !old.nullable && next.nullable) report('đổi nullable')
  const allowed = input ? next.enum : old.enum
  const sent = input ? old.enum : next.enum
  if (allowed && (!sent || sent.some(v => !allowed.some(w => same(v, w))))) report('đổi tập giá trị enum')
  for (const key of ['minimum', 'minLength', 'minItems', 'minProperties']) {
    const a = old[key] ?? -Infinity, b = next[key] ?? -Infinity
    if (input ? b > a : b < a) report(`đổi ${key}`)
  }
  for (const key of ['maximum', 'maxLength', 'maxItems', 'maxProperties']) {
    const a = old[key] ?? Infinity, b = next[key] ?? Infinity
    if (input ? b < a : b > a) report(`đổi ${key}`)
  }
  for (const key of new Set([...Object.keys(old), ...Object.keys(next)])) {
    if (!HANDLED.has(key) && !ANNOTATIONS.has(key) && !same(old[key], next[key])) {
      report(`đổi ${key} — cần rà cấu trúc chưa có luật nới tương thích`)
    }
  }
}
function compareAdditional(ctx, old, next, at, direction) {
  const a = old.additionalProperties ?? true, b = next.additionalProperties ?? true
  if (typeof a === 'object' && typeof b === 'object') return compareSchema(ctx, a, b, at, direction)
  if (same(a, b)) return
  const input = direction === 'yêu cầu' 
  if (input ? (b === true || a === false) : (a === true || b === false)) return
  ctx.errors.push(`${at}: thay đổi additionalProperties không tương thích (${direction})`)
}
function compareSchema(ctx, a, b, at, direction) {
  if (a === undefined && b === undefined) return
  if (a === undefined || b === undefined) { ctx.errors.push(`${at}: thêm/bỏ schema (${direction})`); return }
  const old = ctx.old(a), next = ctx.next(b)
  if (typeof old !== 'object' || typeof next !== 'object' || !old || !next) {
    if (!same(old, next)) ctx.errors.push(`${at}: đổi schema`)
    return
  }
  if (markSeen(ctx, old, next, direction)) return
  const input = direction === 'yêu cầu'
  const report = message => ctx.errors.push(`${at} (${direction}): ${message}`)
  compareValues(old, next, input, report)
  compareRequired(old, next, input, report)
  for (const [key, value] of Object.entries(old.properties ?? {})) {
    if (!Object.hasOwn(next.properties ?? {}, key)) report(`mất trường ${key}`)
    else compareSchema(ctx, value, next.properties[key], `${at}.${key}`, direction)
  }
  if (old.items || next.items) compareSchema(ctx, old.items, next.items, `${at}[]`, direction)
  compareAdditional(ctx, old, next, `${at}.*`, direction)
}

function content(ctx, old, next, at, direction) {
  for (const [mime, body] of Object.entries(old ?? {})) {
    if (!next?.[mime]) ctx.errors.push(`${at}: mất kiểu nội dung ${mime}`)
    else compareSchema(ctx, body.schema, next[mime].schema, `${at}/${mime}`, direction)
  }
}
function parameters(ctx, oldList, newList, at) {
  const key = p => `${p.in}:${p.name}`
  const effective = (list, resolve) => [...new Map(list.map(p => { const x = resolve(p); return [key(x), x] })).values()]
  const old = effective(oldList, ctx.old), next = effective(newList, ctx.next)
  for (const p of old) {
    const q = next.find(n => key(n) === key(p))
    if (!q) { ctx.errors.push(`${at}: mất tham số ${key(p)}`); continue }
    if (!p.required && q.required) ctx.errors.push(`${at}: bắt buộc ${key(p)}`)
    for (const k of ['style', 'explode', 'allowReserved']) {
      if (!same(p[k], q[k])) ctx.errors.push(`${at}: đổi ${k} của ${key(p)}`)
    }
    compareSchema(ctx, p.schema, q.schema, `${at}/${key(p)}`, 'yêu cầu')
    content(ctx, p.content, q.content, `${at}/${key(p)}`, 'yêu cầu')
  }
  for (const q of next) {
    if (q.required && !old.some(p => key(p) === key(q))) ctx.errors.push(`${at}: thêm tham số bắt buộc ${key(q)}`)
  }
}
function operation(ctx, old, next, at, docs, paths) {
  parameters(ctx, [...(paths[0].parameters ?? []), ...(old.parameters ?? [])],
    [...(paths[1].parameters ?? []), ...(next.parameters ?? [])], at)
  const a = ctx.old(old.requestBody), b = ctx.next(next.requestBody)
  if (!a?.required && b?.required) ctx.errors.push(`${at}: bắt buộc requestBody`)
  content(ctx, a?.content, b?.content, `${at}/body`, 'yêu cầu')
  for (const [status, response] of Object.entries(old.responses ?? {})) {
    if (!next.responses?.[status]) { ctx.errors.push(`${at}: mất phản hồi ${status}`); continue }
    const x = ctx.old(response), y = ctx.next(next.responses[status])
    content(ctx, x.content, y.content, `${at}/${status}`, 'phản hồi')
    for (const [name, header] of Object.entries(x.headers ?? {})) {
      if (!y.headers?.[name]) ctx.errors.push(`${at}: mất header ${name}`)
      else compareSchema(ctx, ctx.old(header).schema, ctx.next(y.headers[name]).schema, `${at}/${name}`, 'phản hồi')
    }
  }
  for (const k of ['security', 'servers']) {
    if (!same(old[k] ?? (k === 'servers' ? paths[0][k] : undefined) ?? docs[0][k], next[k] ?? (k === 'servers' ? paths[1][k] : undefined) ?? docs[1][k])) ctx.errors.push(`${at}: đổi ${k}, cần rà tương thích`)
  }
}

export function compareOpenApi(oldDoc, newDoc) {
  const errors = [], ctx = schemaContext(oldDoc, newDoc, errors)
  try {
    if (oldDoc.openapi !== newDoc.openapi) errors.push('Đổi phiên bản OpenAPI')
    for (const [path, item] of Object.entries(oldDoc.paths ?? {})) {
      const old = ctx.old(item), next = ctx.next(newDoc.paths?.[path])
      for (const verb of VERBS.filter(v => old[v])) {
        if (!next?.[verb]) errors.push(`${verb.toUpperCase()} ${path}: mất endpoint`)
        else operation(ctx, old[verb], next[verb], `${verb.toUpperCase()} ${path}`, [oldDoc, newDoc], [old, next])
      }
    }
    for (const [name, scheme] of Object.entries(oldDoc.components?.securitySchemes ?? {})) {
      if (!same(scheme, newDoc.components?.securitySchemes?.[name])) errors.push(`Đổi cơ chế xác thực ${name}`)
    }
  } catch (error) { errors.push(`Không thể chứng minh tương thích: ${error.message}`) }
  return [...new Set(errors)]
}
