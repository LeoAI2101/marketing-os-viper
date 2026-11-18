// Nạp gói Node (typescript, graphql…) từ node_modules của target đã scaffold — checker không
// tự cài dependency. Thử package.json ở gốc rồi từng target theo thứ tự tên; không thấy ở
// đâu thì ném lỗi để checker trả exit 2 (chưa kiểm được), hoặc chuyển qua ống remote.
import { createRequire } from 'node:module'
import { existsSync, readdirSync } from 'node:fs'
import { resolve } from 'node:path'

export function loadDep(root, name) {
  const anchors = [resolve(root, 'package.json')]
  for (const group of ['boundaries', 'web-experiences']) {
    const base = resolve(root, 'srcroot', group)
    if (existsSync(base)) for (const dir of readdirSync(base).sort()) anchors.push(resolve(base, dir, 'package.json'))
  }
  for (const anchor of anchors) {
    if (!existsSync(anchor)) continue
    try { return createRequire(anchor)(name) } catch { /* thử target kế */ }
  }
  // Cuối cùng: đường tìm mặc định của Node từ gốc repo (gồm NODE_PATH) — test dựng sẵn
  // gói ở ngoài repo, hoặc máy cài toàn cục có chủ đích.
  try { return createRequire(resolve(root, 'package.json'))(name) } catch { /* rơi xuống lỗi */ }
  throw new Error(`Không tìm thấy gói '${name}' trong node_modules của target nào — npm ci ở một boundary`)
}

// Chế độ remote (scripts/remote/README.md): máy giữ code có thể không có node_modules —
// checker đẩy bundle qua ống sang máy chạy. RP_LOCAL=1 là phía bên kia, cắt đệ quy.
export function remoteOn(root) {
  return !process.env.RP_LOCAL && existsSync(resolve(root, '.local/remote/.remote-on'))
}
