// Tiện ích chỉ parse AST; không tạo type checker hoặc thêm dependency.
export function parseSource(ts, source, file) {
  const tree = ts.createSourceFile(file, source, ts.ScriptTarget.Latest, true)
  if (tree.parseDiagnostics.length) throw new Error(`${file}: TypeScript không parse được: ${ts.flattenDiagnosticMessageText(tree.parseDiagnostics[0].messageText, ' ')}`)
  return tree
}
export function visitNodes(ts, node, visit) {
  visit(node)
  ts.forEachChild(node, child => visitNodes(ts, child, visit))
}
export function unwrap(ts, expr) {
  while (expr && (ts.isParenthesizedExpression(expr) || ts.isAsExpression(expr) || ts.isNonNullExpression(expr) || ts.isSatisfiesExpression(expr))) expr = expr.expression
  return expr
}
export function callName(ts, call) {
  const expr = unwrap(ts, call.expression)
  if (ts.isPropertyAccessExpression(expr)) return expr.name.text
  if (ts.isElementAccessExpression(expr) && ts.isStringLiteralLike(expr.argumentExpression)) return expr.argumentExpression.text
  if (ts.isIdentifier(expr)) return expr.text
  return ''
}
export function property(ts, object, name) {
  object = unwrap(ts, object)
  if (!object || !ts.isObjectLiteralExpression(object)) return undefined
  // Chỉ nhận trường cuối; spread sau take có thể ghi đè trần.
  let value
  for (const item of object.properties) {
    if (ts.isSpreadAssignment(item)) value = undefined
    if (item.name && ts.isComputedPropertyName(item.name)) {
      const key = unwrap(ts, item.name.expression)
      if (!ts.isStringLiteralLike(key) && !ts.isNumericLiteral(key)) value = undefined
      else if (key.text === name) value = item.initializer
    }
    if (item.name?.text === name) value = item.initializer ?? (ts.isShorthandPropertyAssignment(item) ? item.name : undefined)
  }
  return value
}
export function enclosingName(ts, node) {
  for (let p = node.parent; p; p = p.parent) {
    if (ts.isMethodDeclaration(p) || ts.isFunctionDeclaration(p)) return p.name?.getText() ?? '(ẩn danh)'
  }
  return '(ngoài hàm)'
}

// Scanner phải trở về chế độ template sau dấu }; nếu không sẽ mất khoảng trắng literal.
export function canonicalTokens(ts, source) {
  const tree = parseSource(ts, source, 'fingerprint.ts'), tails = new Set()
  visitNodes(ts, tree, n => {
    if ([ts.SyntaxKind.TemplateMiddle, ts.SyntaxKind.TemplateTail].includes(n.kind)) tails.add(n.getStart(tree))
  })
  const scanner = ts.createScanner(ts.ScriptTarget.Latest, true, ts.LanguageVariant.Standard, source), parts = []
  for (let kind = scanner.scan(); kind !== ts.SyntaxKind.EndOfFileToken; kind = scanner.scan()) {
    if (tails.has(scanner.getTokenPos())) kind = scanner.reScanTemplateToken(false)
    parts.push([kind, ts.isStringLiteralLike({kind}) ? scanner.getTokenValue() : scanner.getTokenText()])
  }
  return parts
}

// Chỉ suy literal từ const trong lexical scope; let/var và parameter che tên không phải bằng chứng.
function bindsName(ts, name, text) {
  if (ts.isIdentifier(name)) return name.text === text
  return (ts.isObjectBindingPattern(name) || ts.isArrayBindingPattern(name)) && name.elements.some(e => e.name && bindsName(ts, e.name, text))
}
export function resolveConstant(ts, input, seen = new Set()) {
  const node = unwrap(ts, input)
  if (!node || !ts.isIdentifier(node) || seen.has(node)) return node
  for (let parent = node.parent; parent; parent = parent.parent) {
    if (ts.isFunctionLike(parent) && parent.parameters.some(p => bindsName(ts, p.name, node.text))) return node
    if (ts.isCatchClause(parent) && parent.variableDeclaration && bindsName(ts, parent.variableDeclaration.name, node.text)) return node
    if ((ts.isForOfStatement(parent) || ts.isForInStatement(parent) || ts.isForStatement(parent)) && parent.initializer && ts.isVariableDeclarationList(parent.initializer)
      && parent.initializer.declarations.some(d => bindsName(ts, d.name, node.text))) return node
    if (!ts.isBlock(parent) && !ts.isSourceFile(parent)) continue
    const declarations = []
    for (const statement of parent.statements) {
      if (ts.isVariableStatement(statement)) declarations.push(...statement.declarationList.declarations)
    }
    const binding = declarations.find(d => bindsName(ts, d.name, node.text))
    if (!binding) continue
    if (!ts.isIdentifier(binding.name) || binding.pos >= node.pos || !(binding.parent.flags & ts.NodeFlags.Const) || !binding.initializer) return node
    return resolveConstant(ts, binding.initializer, new Set([...seen, node]))
  }
  return node
}

export function callbackCall(ts, fn) {
  let wrapped = fn
  while (wrapped.parent && unwrap(ts, wrapped.parent) === fn) wrapped = wrapped.parent
  return ts.isCallExpression(wrapped.parent) && wrapped.parent.arguments.includes(wrapped) ? wrapped.parent : undefined
}
