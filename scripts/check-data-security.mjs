#!/usr/bin/env node
import {readFileSync,existsSync} from 'node:fs'
import {resolve,relative,dirname} from 'node:path'
import {fileURLToPath} from 'node:url'
import {execFileSync,spawnSync} from 'node:child_process'
import {createHash} from 'node:crypto'
import {gzipSync,gunzipSync} from 'node:zlib'
import {walk} from './lib/contract-snapshot.mjs'
import {loadDep,remoteOn} from './lib/node-deps.mjs'
import {analyzeSource} from './lib/data-security.mjs'
const ROOT=resolve(dirname(fileURLToPath(import.meta.url)),'..')
const ENGINE=['scripts/check-data-security.mjs','scripts/lib/data-security.mjs','scripts/lib/ts-ast.mjs']
const fingerprint=()=>createHash('sha256').update(ENGINE.map(p=>readFileSync(resolve(ROOT,p))).join('\n')).digest('hex')
const eligible=p=>/^srcroot\/boundaries\/[^/]+\/src\/.*\.ts$/.test(p)&&!/\.(spec|test|d)\.ts$|\/shared\/contracts\//.test(p)
function options(args){
  const opt={}
  while(args.length){
    const arg=args.shift()
    if(['--target','--file'].includes(arg)&&args[0]&&!args[0].startsWith('-')) opt[arg.slice(2)]=args.shift()
    else if(['--json','--stdin','--bundle-gzip'].includes(arg)) opt[arg.slice(2)]=true
    else throw new Error('Tham số không hợp lệ')
  }
  if(opt.stdin&&!opt.file) throw new Error('--stdin cần --file')
  return opt
}
function input(opt){
  const code="import json,sys;sys.path.insert(0,'scripts');from lib.ops_env import discover_targets;print(json.dumps(discover_targets(('boundaries',))))"
  const names=Object.keys(JSON.parse(execFileSync('python3',['-c',code],{cwd:ROOT,encoding:'utf8'})))
  if(opt.target&&!names.includes(opt.target)) throw new Error('Target không tồn tại')
  let files
  if(opt.file){
    const file=relative(ROOT,resolve(ROOT,opt.file)).replaceAll('\\','/')
    if(!eligible(file)||!names.includes(file.split('/')[2])||(opt.target&&opt.target!==file.split('/')[2])) throw new Error('File ngoài source được kiểm')
    files=[file]
  }else files=(opt.target?[opt.target]:names).flatMap(n=>walk(ROOT,`srcroot/boundaries/${n}/src`).filter(eligible))
  if(!files.length) throw new Error('Không có source để kiểm')
  return {engine:fingerprint(),files:Object.fromEntries(files.map(p=>[p,readFileSync(opt.stdin?0:resolve(ROOT,p),'utf8')]))}
}
function main(){
  const start=performance.now(),opt=options(process.argv.slice(2))
  const bundle=opt['bundle-gzip']?JSON.parse(gunzipSync(readFileSync(0),{maxOutputLength:40*1024*1024})):input(opt)
  if(bundle.engine!==fingerprint()) throw new Error('Checker ở máy chạy lệch bản máy giữ code; sync trước')
  let ts
  try{ts=loadDep(ROOT,'typescript')}
  catch(error){
    if(opt['bundle-gzip']||!remoteOn(ROOT)) throw error
    const r=spawnSync('bash',['scripts/remote/ub',`node scripts/check-data-security.mjs --bundle-gzip${opt.json?' --json':''}`],
      {cwd:ROOT,input:gzipSync(JSON.stringify(bundle)),encoding:'utf8',maxBuffer:40*1024*1024,timeout:30000})
    process.stdout.write(r.stdout??'');process.stderr.write(r.stderr??'')
    if(r.error) throw r.error
    return r.status??2
  }
  const findings=Object.entries(bundle.files).flatMap(([file,source])=>analyzeSource(ts,source,file))
  const result={files:Object.keys(bundle.files).length,findings,violations:findings.filter(f=>f.level==='error').length,
    warnings:findings.filter(f=>f.level==='warning').length,seconds:Number(((performance.now()-start)/1000).toFixed(3))}
  if(opt.json) console.log(JSON.stringify(result))
  else{
    for(const f of findings) console.error(`${f.level==='error'?'✗':'▲'} ${f.file}:${f.line} ${f.rule}: ${f.message}`)
    console.log(`${result.violations?'✗':'✓'} data-security: ${result.files} file · ${result.violations} lỗi · ${result.warnings} cảnh báo · ${result.seconds}s`)
  }
  return result.violations?1:0
}
try{process.exitCode=main()}catch(_){console.error('✗ data-security chưa kiểm được: kiểm tham số, source, dependency và pipe; không in nội dung có thể chứa bí mật.');process.exitCode=2}
