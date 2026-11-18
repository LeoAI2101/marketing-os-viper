import { createHash } from 'node:crypto'
import { parseSource, visitNodes, callName, property, enclosingName, unwrap, canonicalTokens, callbackCall } from './ts-ast.mjs'
const READS = new Set(['findMany', 'findFirst', 'findUnique', 'count', 'aggregate', 'groupBy'])
const DB_CALLS = new Set([...READS, 'create', 'createMany', 'update', 'updateMany', 'upsert', 'delete', 'deleteMany'])
const ITERATORS = new Set(['map', 'flatMap', 'forEach', 'filter', 'reduce'])

function canonical(ts, source) {
  return JSON.stringify(canonicalTokens(ts, source))
}
function hasBound(ts, arg) {
  let take = property(ts, arg, 'take')
  if (!take) return false
  take = unwrap(ts, take)
  if (ts.isVoidExpression(take)) return false
  if (ts.isIdentifier(take) && ['undefined', 'NaN', 'Infinity'].includes(take.text)) return false
  if (take.kind === ts.SyntaxKind.NullKeyword) return false
  if (ts.isStringLiteralLike(take) || ts.isBigIntLiteral(take) || ts.isObjectLiteralExpression(take) || ts.isArrayLiteralExpression(take) ||
      [ts.SyntaxKind.TrueKeyword, ts.SyntaxKind.FalseKeyword].includes(take.kind)) return false
  if (ts.isNumericLiteral(take)) return Number(take.text) !== 0 && Number.isSafeInteger(Number(take.text))
  if (ts.isPrefixUnaryExpression(take)) return [ts.SyntaxKind.PlusToken, ts.SyntaxKind.MinusToken].includes(take.operator) && hasBound(ts, ts.factory.createObjectLiteralExpression([
    ts.factory.createPropertyAssignment('take', take.operand),
  ]))
  return true // take động: có cấu trúc, KHÔNG chứng minh validation giá trị ở runtime.
}
function dbCall(ts, node, file) {
  if (!DB_CALLS.has(callName(ts, node))) return false
  const expr = node.expression.getText()
  return /\b(prisma|tx|db)\b/i.test(expr) || file.endsWith('.repository.ts')
}
function inLoop(ts, node) {
  for (let p = node.parent; p; p = p.parent) {
    if (ts.isForStatement(p) || ts.isForOfStatement(p) || ts.isForInStatement(p) || ts.isWhileStatement(p) || ts.isDoStatement(p)) return true
    if (ts.isFunctionLike(p)) {
      const call = callbackCall(ts, p)
      // Callback của iterator thực thi mỗi dòng, callback bất kỳ khác chưa kết luận được.
      if (call && ITERATORS.has(callName(ts, call))) return true
      return false
    }
  }
  return false
}
function inTransaction(ts, node) {
  for (let p = node.parent; p; p = p.parent) {
    if (!ts.isFunctionLike(p)) continue
    const call = callbackCall(ts, p)
    if (call) {
      if (callName(ts, call) === '$transaction') return true
      if (ITERATORS.has(callName(ts, call))) continue
    }
    return false
  }
  return false
}
function networkCall(ts, node) {
  const name = callName(ts, node), expr = unwrap(ts, node.expression).getText()
  return name === 'fetch' || /^axios(?:\.|\[|$)/.test(expr) ||
    /(?:http|httpService|httpClient|https)\.(?:request|get|post|put|patch|delete)\b/i.test(expr)
}
function indexModels(schema) {
  schema = schema.replace(/\/\*[\s\S]*?\*\//g, '').replace(/\/\/[^\n]*/g, '')
  const models = new Map()
  for (const match of schema.matchAll(/\bmodel\s+(\w+)\s*\{([^}]*)\}/g)) {
    const fields = new Set(), leading = new Set()
    for (const line of match[2].split('\n')) {
      const field = line.trim().match(/^(\w+)\s+(?:String|Int|BigInt|Float|Decimal|Boolean|DateTime|Bytes)\??\s*(.*)$/)
      if (field) { fields.add(field[1]); if (/@(?:id|unique)\b/.test(field[2])) leading.add(field[1]) }
      const index = line.match(/@@(?:id|index|unique)\(\s*\[\s*(\w+)/)
      if (index) leading.add(index[1])
    }
    models.set(match[1][0].toLowerCase() + match[1].slice(1), {fields, leading})
  }
  return models
}
function unindexed(ts, call, models) {
  const expr = call.expression
  if (!ts.isPropertyAccessExpression(expr) || !ts.isPropertyAccessExpression(expr.expression)) return []
  const model = models.get(expr.expression.name.text)
  const where = property(ts, call.arguments[0], 'where')
  if (!model || !where || !ts.isObjectLiteralExpression(where)) return []
  const fields = where.properties.map(p => p.name?.text).filter(k => model.fields.has(k))
  if (fields.some(k => model.leading.has(k))) return []
  return fields
}
export function analyzeSource(ts, source, file, schemaText = '') {
  const tree = parseSource(ts, source, file), found = [], models = indexModels(schemaText)
  function add(node, rule, level, message) {
    const call = node.getText(tree), symbol = enclosingName(ts, node)
    const position = tree.getLineAndCharacterOfPosition(node.getStart(tree))
    const fingerprint = createHash('sha256').update(rule + ':' + symbol + ':' + canonical(ts, call)).digest('hex').slice(0, 20)
    found.push({rule, level, file, line: position.line + 1, column: position.character + 1, symbol, fingerprint, message})
  }
  visitNodes(ts, tree, node => {
    if (!ts.isCallExpression(node)) return
    const name = callName(ts, node)
    if (name === 'findMany' && !hasBound(ts, node.arguments[0])) add(node, 'QS001', 'error', 'findMany chưa có take chứng minh trần cấu trúc; không tự cắt dữ liệu để làm xanh')
    if (dbCall(ts, node, file) && inLoop(ts, node)) add(node, 'QS002', 'warning', 'Lời gọi DB trong vòng lặp: rà N+1, batch hoặc lý do tập hữu hạn')
    if (networkCall(ts, node) && inTransaction(ts, node)) add(node, 'QS003', 'error', 'Lời gọi mạng trong transaction giữ kết nối DB chờ I/O')
    if (name === 'findMany') {
      const fields = unindexed(ts, node, models)
      if (fields.length) add(node, 'QS004', 'warning', `Chưa thấy index ứng viên dẫn đầu bởi ${fields.join(', ')}; cần EXPLAIN trước khi thêm index`)
    }
  })
  return found
}
