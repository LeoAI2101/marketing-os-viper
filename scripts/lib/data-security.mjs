// Rà cấu trúc AST, không chứng minh taint liên hàm hoặc quyền tại runtime.
import {createHash} from 'node:crypto'
import {parseSource,visitNodes,unwrap,callName,enclosingName,resolveConstant,canonicalTokens} from './ts-ast.mjs'
const UNSAFE=new Set(['$queryRawUnsafe','$executeRawUnsafe'])
const RAW=new Set(['$queryRaw','$executeRaw','query','execute','raw'])
const LOG=new Set(['log','error','warn','info','debug','trace','verbose','fatal'])
const SENSITIVE=/^(body|rawBody|headers|password|token|accessToken|refreshToken|authorization|cookie|phoneNumber|email|secret)$/i
const SQL=/^\s*(SELECT|INSERT|UPDATE|DELETE|WITH|ALTER|CREATE|DROP|TRUNCATE)\b/i

function resolveLocal(ts,node){return resolveConstant(ts,node)}
function sqlDynamic(ts,node,decls){
  node=resolveLocal(ts,node,decls)
  return Boolean(node && ((ts.isBinaryExpression(node)&&node.operatorToken.kind===ts.SyntaxKind.PlusToken)
    || ts.isTemplateExpression(node)))
}
function sensitive(ts,input,decls,seen=new Set()){
  const node=unwrap(ts,input)
  if(!node||seen.has(node)) return 0
  const next=new Set([...seen,node]), level=n=>sensitive(ts,n,decls,next)
  if(ts.isStringLiteralLike(node)||ts.isNumericLiteral(node)) return 0
  if(ts.isPropertyAssignment(node)) {
    const value=resolveConstant(ts,node.initializer)
    if(ts.isStringLiteralLike(value)||ts.isNumericLiteral(value)) return 0
    return Math.max(SENSITIVE.test(node.name.text??'')?2:0,level(node.initializer))
  }
  if(ts.isIdentifier(node)){
    const resolved=resolveConstant(ts,node)
    if(resolved!==node) return level(resolved)
    return SENSITIVE.test(node.text)?2:0
  }
  if(ts.isPropertyAccessExpression(node)||ts.isElementAccessExpression(node)){
    const key=ts.isPropertyAccessExpression(node)?node.name.text:node.argumentExpression?.text
    if(key==='length') return 0
    if(SENSITIVE.test(key??'')) return 2
    // Chỉ lấy một member không có nghĩa toàn bộ dữ liệu của receiver đi vào log.
    return level(node.expression)>0?1:0
  }
  if(ts.isCallExpression(node)){
    const levels=node.arguments.map(level), maximum=Math.max(0,...levels)
    const name=unwrap(ts,node.expression).getText().replaceAll(/\s/g,'')
    // Các phép chuyển nguyên nội dung; không tự tin sanitizer chỉ theo tên.
    if(['JSON.stringify','String'].includes(name)) return maximum
    return maximum>0?1:0
  }
  let result=0;ts.forEachChild(node,child=>{result=Math.max(result,level(child))});return result
}
function logEnabledByDefault(ts,node){
  if(!ts.isBinaryExpression(node)) return false
  const left=unwrap(ts,node.left),right=unwrap(ts,node.right)
  // Biến kiểu LOG_*BODY* / LOG_*REQUEST* / LOG_*PAYLOAD* (skill data-security SEC004).
  const env=n=>/^process\.env(?:\.|\[['"])LOG_[A-Z0-9_]*(?:BODY|REQUEST|PAYLOAD)[A-Z0-9_]*(?:['"]\])?$/.test(n?.getText().replaceAll(/\s/g,'')??'')
  const zero=n=>ts.isStringLiteralLike(n)&&n.text==='0'
  const op=node.operatorToken.kind
  if([ts.SyntaxKind.ExclamationEqualsToken,ts.SyntaxKind.ExclamationEqualsEqualsToken].includes(op)) return (env(left)&&zero(right))||(env(right)&&zero(left))
  return [ts.SyntaxKind.BarBarToken,ts.SyntaxKind.QuestionQuestionToken].includes(op)&&env(left)
    && ((ts.isStringLiteralLike(right)&&right.text!=='0'&&right.text!=='')||right.kind===ts.SyntaxKind.TrueKeyword)
}
export function analyzeSource(ts,source,file){
  const tree=parseSource(ts,source,file),findings=[],decls=[]
  visitNodes(ts,tree,node=>{if(ts.isVariableDeclaration(node)&&ts.isIdentifier(node.name)) decls.push(node)})
  const add=(node,rule,level,message)=>{
    const tokens=canonicalTokens(ts,node.getText(tree))
    const symbol=enclosingName(ts,node)
    findings.push({file,line:tree.getLineAndCharacterOfPosition(node.getStart(tree)).line+1,rule,level,message,symbol,
      fingerprint:createHash('sha256').update(JSON.stringify([rule,symbol,tokens])).digest('hex').slice(0,24)})
  }
  visitNodes(ts,tree,node=>{
    if(logEnabledByDefault(ts,node)) add(node,'SEC004','error','Biến log thân request/payload mặc định bật khi môi trường không khai; chỉ ghi nhận, chưa sửa')
    if(!ts.isCallExpression(node)&&!ts.isTaggedTemplateExpression(node)) return
    const name=ts.isTaggedTemplateExpression(node)?callName(ts,{expression:node.tag}):callName(ts,node)
    if(UNSAFE.has(name)) add(node,'SEC001','error','API SQL Unsafe bị cấm, kể cả SQL literal; chưa chứng minh injection đang xảy ra')
    if(!ts.isCallExpression(node)) return // Nội suy vẫn được visit riêng, gồm Prisma.raw.
    const expr=unwrap(ts,node.expression).getText(tree)
    const arg=node.arguments[0],value=resolveLocal(ts,arg,decls)
    if(name==='raw'&&/^Prisma(?:\.|\[)/.test(expr)&&(!value||!ts.isStringLiteralLike(value)))
      add(node,'SEC002','error','Prisma.raw nhận dữ liệu động, bỏ qua binding tham số')
    const db=RAW.has(name)&&(/\b(prisma|tx|db|pool|connection|queryRunner)\b/i.test(expr)
      || (value&&ts.isStringLiteralLike(value)&&SQL.test(value.text)))
    if(db&&sqlDynamic(ts,arg,decls)) add(node,'SEC002','error','SQL vào DB được nối chuỗi hoặc nội suy trước binding')
    else if(db&&arg&&!ts.isStringLiteralLike(value)&&!ts.isTaggedTemplateExpression(value)) add(node,'SEC005','warning','SQL động chưa chứng minh binding; cần rà luồng dữ liệu/alias')
    if(LOG.has(name)&&/\b(console|logger|log)\b/i.test(expr)){
      const certainty=Math.max(0,...node.arguments.map(a=>sensitive(ts,a,decls)))
      if(certainty) add(node,'SEC003',certainty===2?'error':'warning',certainty===2
        ?'Log nhận trực tiếp thân request/header hoặc trường nhạy cảm'
        :'Log nhận kết quả biến đổi/member có nguồn nhạy cảm; chưa chứng minh đã rò hoặc đã che, cần rà ngữ cảnh')
    }
  })
  return findings
}
