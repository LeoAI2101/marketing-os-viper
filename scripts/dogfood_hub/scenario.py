#!/usr/bin/env python3
"""Biên dịch một mảng step DSL thành MỘT hàm JavaScript chạy ở đầu kia.

Đây là chỗ tiết kiệm token của cả thiết kế: thay vì ~20 lượt gọi tool, mỗi lượt kéo về cả
cây accessibility để model tự đọc, cả kịch bản đi xuống một lần và chỉ nhật ký `✓/✗` đi
ngược lên. «Nhìn màn hình» là `read_screen` — chữ người dùng thật đọc được, không phải cây.

Ranh giới: ở đây chỉ có ĐỘNG TỪ CHUNG của web. Không mã màn, không route, không tên nút
của sản phẩm, không khái niệm «đăng nhập» — đăng nhập chỉ là một chuỗi
`goto` + `fill` + `click` + `expect_*`, và nó sống ở `context/dogfood/flows/` như dữ liệu.

⚠ Nguồn JS do Python sinh ra nên KHÔNG linter nào soi được. Ba chốt: mẫu để nguyên khối
  (không f-string vào thân JS), tham số nhúng bằng `json.dumps`, và `--dry-run` in ra
  nguyên văn nguồn sinh ra để đọc bằng mắt.
"""

from __future__ import annotations

import json
from typing import Any

from .probes import CONTRAST_JS, GEOMETRY_JS, RENDER_VALUES_JS, tap_targets_js

# Mọi động từ hub hiểu. Danh sách đóng: step lạ là lỗi có tên, không phải im lặng bỏ qua.
VERBS = (
    "goto", "back", "reload", "open_tab", "switch_tab",
    "click", "type", "fill", "select", "press", "hover", "upload", "dialog", "double_submit",
    "expect_text", "expect_no_text", "expect_url", "expect_count", "expect_enabled", "expect_storage",
    "wait_for", "read_screen", "read_storage", "screenshot", "audit", "measure", "note",
    "resize", "theme",
    "offline", "restore_net", "throttle", "abort_route", "route_status", "unroute",
)

_RUNNER = r"""
async (page) => {
  const P = __PARAMS__;
  const out = { ok: true, steps: [], url: '', consoleErrors: [], shots: [], visited: [], audits: {}, reads: {}, measures: {} };

  // ── Dấu làn ở tầng trang. Bắt được thứ phép kiểm tiến trình không thấy: hai `tag`
  //    cùng đi vào một client. Rẻ, và chạy ở mọi lượt gọi.
  const marker = 'lane:' + P.lane;
  if (page.mainFrame()) {
    const current = await page.evaluate(() => window.name).catch(() => '');
    if (current && current !== marker) {
      return { ok: false, fatal: 'lane_cross_talk',
               steps: [{ i: 0, verb: 'lane', ok: false, detail: 'trang này thuộc làn ' + current }] };
    }
  }

  const tabs = [page];
  let pg = page;

  // Khung nhìn của LƯỢT GỌI NÀY. Trình duyệt của làn sống qua nhiều lượt, nên một step `resize` ở
  // lượt trước còn nguyên — bản cũ nhận tham số `viewport` rồi bỏ đó, khung kẹt ở cỡ cũ (vai màn-nhỏ
  // xin 1024×768 vẫn đo ở 390 sau một lượt resize — R35/R44, 2026-09-29). Đặt lại ở đầu mỗi lượt.
  if (P.viewport) {
    const cur = page.viewportSize();
    if (!cur || cur.width !== P.viewport.width || cur.height !== P.viewport.height) await page.setViewportSize(P.viewport);
  }

  const watch = (p) => {
    p.setDefaultTimeout(P.stepTimeoutMs);
    p.setDefaultNavigationTimeout(P.navTimeoutMs);
    p.on('console', (m) => { if (m.type() === 'error') out.consoleErrors.push(m.text().slice(0, 200)); });
    // Rời một màn có guard `beforeunload` là trang treo một hộp thoại, mà
    // `browser_run_code_unsafe` TỪ CHỐI chạy khi còn modal — kể cả mã định đóng chính nó.
    // Nhận ngay tại nguồn. CHỈ `beforeunload`: confirm/alert nghiệp vụ vẫn để step `dialog`
    // quyết, vì bấm hộ là mất chính thứ đang đo.
    p.on('dialog', (d) => { if (d.type() === 'beforeunload') d.accept().catch(() => {}); });
  };
  watch(pg);
  await pg.evaluate((m) => { window.name = m; }, marker).catch(() => {});

  let cdp = null;
  // Route giả lập chỉ được đụng LỜI GỌI API, không đụng module nguồn của Vite: glob `**/graphql*`
  // khớp cả `/src/shared/generated/graphql-auth/graphql.ts` ⇒ đồ thị JS gãy, `#root` rỗng — trang
  // trắng vì công cụ, không vì sản phẩm (picky vòng 10, 2026-09-21). Tài nguyên `script`/`stylesheet`/
  // `document`/ảnh/font đi tiếp như thường; chỉ `fetch`/`xhr` mới ăn giả lập.
  const routeChiApi = (r, pat, lam) => {
    const rt = r.request().resourceType();
    if (rt === 'fetch' || rt === 'xhr' || rt === 'other') return lam();
    return r.fallback();
  };
  // MỘT phiên CDP cho cả đời trang, treo trên chính đối tượng trang: điều kiện mạng đặt bằng phiên A
  // KHÔNG gỡ được bằng phiên B (picky vòng 10: throttle ở lượt trước, `restore_net` lượt sau mở phiên
  // mới ⇒ làn kẹt 46 s/trang tới hết phiên). Dùng lại đúng phiên đã đặt thì gỡ được.
  const cdpFor = async () => {
    if (!cdp) {
      if (!pg.__dpCdp) { pg.__dpCdp = await pg.context().newCDPSession(pg); await pg.__dpCdp.send('Network.enable'); }
      cdp = pg.__dpCdp;
    }
    return cdp;
  };

  const abs = (path) => {
    if (/^https?:\/\//.test(path)) return path;
    return P.base + (path.startsWith('/') ? path : '/' + path);
  };
  const forbidden = (path) => P.forbiddenPaths.some((f) => String(path).split('?')[0] === f);

  const locate = (s) => {
    if (s.selector) return pg.locator(s.selector);
    if (s.role) return pg.getByRole(s.role, s.name ? { name: s.name } : undefined);
    if (s.label) return pg.getByLabel(s.label, { exact: false });
    if (s.placeholder) return pg.getByPlaceholder(s.placeholder);
    if (s.text) return pg.getByText(s.text, { exact: !!s.exact });
    throw new Error('không biết trỏ vào đâu: step cần selector | role+name | label | placeholder | text');
  };
  const one = (s) => { const l = locate(s); return s.last ? l.last() : l.first(); };

  const record = (path) => { const p2 = String(path); if (out.visited[out.visited.length - 1] !== p2) out.visited.push(p2); };
  /// Màn mở ra do BẤM cũng là màn đã mở. Bản cũ chỉ ghi lượt `goto`, nên sổ phủ màn báo «CHƯA AI MỞ»
  /// cho những màn vai đã thật sự đứng trong đó — mà sổ ấy chính là hàng rào luật #8b, nó đếm hụt thì
  /// người đọc đi tìm một lỗ không có thật và bỏ qua lỗ thật (đo 2026-09-17: một màn mở bằng nút trên
  /// màn trước vẫn nằm trong danh sách chưa ai mở).
  const recordHere = () => { try { record(new URL(pg.url()).pathname); } catch { /* URL lạ — bỏ qua */ } };

  const shoot = async (name, full) => {
    // Playwright suy KIỂU ẢNH từ đuôi tên tệp: tên không đuôi thì nó ném «unsupported mime type
    // "null"» và bước chụp hỏng — mà bước chụp hỏng làm cả kịch bản dừng, đọc lên như lỗi sản phẩm
    // (dogfood vòng 4, hai vai mất sạch ảnh vì vậy). Tên không có đuôi ảnh thì thêm `.png`.
    const safe = name.replace(/[^A-Za-z0-9._-]/g, '_');
    const file = P.shotDir + '/' + (/\.(png|jpe?g)$/i.test(safe) ? safe : safe + '.png');
    await pg.screenshot({ path: file, fullPage: !!full });
    out.shots.push(file);
    return file;
  };

  const runAudit = async (checks, key) => {
    const got = {};
    for (const c of checks) {
      if (c === 'geometry') got.geometry = await pg.evaluate(P.js.geometry);
      else if (c === 'contrast') got.contrast = await pg.evaluate(P.js.contrast);
      else if (c === 'values') got.values = await pg.evaluate(P.js.values);
      else if (c === 'tap_targets') got.tap_targets = await pg.evaluate(P.js.tap);
      else if (c === 'console') got.console = out.consoleErrors.slice();
      else got['?' + c] = 'phép đo không có tên này';
    }
    out.audits[key] = got;
    return got;
  };

  for (let i = 0; i < P.steps.length; i++) {
    const s = P.steps[i];
    const verb = Object.keys(s).find((k) => P.verbs.includes(k));
    const t0 = Date.now();
    let detail = '';
    try {
      if (!verb) throw new Error('step không có động từ nào hub hiểu: ' + JSON.stringify(s).slice(0, 120));
      const v = s[verb];

      if (verb === 'goto') {
        const path = typeof v === 'string' ? v : v.path;
        if (forbidden(path)) throw new Error('path ' + path + ' nằm trong forbidden_paths của context/dogfood/config.json');
        await pg.goto(abs(path), { waitUntil: 'domcontentloaded' });
        record(path); detail = path + ' → ' + pg.url();
      }
      else if (verb === 'back')   { await pg.goBack(); detail = pg.url(); }
      else if (verb === 'reload') { await pg.reload({ waitUntil: 'domcontentloaded' }); detail = pg.url(); }
      else if (verb === 'open_tab') {
        const path = typeof v === 'string' ? v : (v.path || '/');
        const share = typeof v === 'object' && v.share_session === true;
        // Hai nghĩa KHÁC HẲN nhau, và nhầm chỗ này cho ra một kịch bản hỏng ngay bước đầu
        // mà đọc lên như lỗi sản phẩm:
        //   share_session: true  → window.open() trong trang: tab mới thừa hưởng BẢN SAO
        //                          sessionStorage → «người dùng mở thêm tab».
        //   share_session: false → context.newPage(): sessionStorage RỖNG → «máy/tab nguội».
        let np;
        if (share) {
          const [popup] = await Promise.all([
            pg.context().waitForEvent('page'),
            pg.evaluate((u) => window.open(u, '_blank'), abs(path)),
          ]);
          np = popup;
        } else {
          np = await pg.context().newPage();
          await np.goto(abs(path), { waitUntil: 'domcontentloaded' });
        }
        watch(np); tabs.push(np); pg = np; cdp = null;
        record(path); detail = 'tab #' + (tabs.length - 1) + (share ? ' (dùng chung phiên)' : ' (phiên nguội)') + ' → ' + pg.url();
      }
      else if (verb === 'switch_tab') {
        if (!tabs[v]) throw new Error('không có tab #' + v + ' (đang mở ' + tabs.length + ')');
        pg = tabs[v]; cdp = null; await pg.bringToFront(); detail = 'tab #' + v + ' ' + pg.url();
      }
      else if (verb === 'click')  { await one(v).click(); detail = JSON.stringify(v); }
      else if (verb === 'hover')  { await one(v).hover(); detail = JSON.stringify(v); }
      // `value` là tên khai trong mô tả tool, nhưng người viết kịch bản gõ `text` là chuyện thường —
      // và bản cũ im lặng gõ CHUỖI RỖNG rồi báo «gõ 0 ký tự», đọc lên như ô nhập không nhận chữ
      // (dogfood vòng 4). Nhận cả hai tên, không có cái nào thì báo lỗi có tên thay vì gõ rỗng.
      else if (verb === 'type' || verb === 'fill') {
        const text = v.value ?? v.text;
        if (text === undefined) throw new Error('step ' + verb + ' thiếu `value` (hoặc `text`) — không biết gõ gì');
        if (verb === 'type') await one(v).type(String(text)); else await one(v).fill(String(text));
        detail = (verb === 'type' ? 'gõ ' : 'điền ') + String(text).length + ' ký tự';
      }
      else if (verb === 'select') { await one(v).selectOption(v.value); detail = String(v.value); }
      else if (verb === 'press')  { await pg.keyboard.press(typeof v === 'string' ? v : v.key); detail = String(typeof v === 'string' ? v : v.key); }
      else if (verb === 'upload') { await one(v).setInputFiles(v.file); detail = String(v.file); }
      else if (verb === 'dialog') { pg.once('dialog', (d) => (v && v.accept ? d.accept() : d.dismiss())); detail = v && v.accept ? 'sẽ đồng ý' : 'sẽ huỷ'; }
      else if (verb === 'double_submit') {
        // Hai lần trong ~cùng một tick — nhịp tay người không làm được nhưng mạng lag thì có.
        // Lần thứ hai BÁO LỖI là dấu hiệu TỐT: nút đã tự khoá khi đang gửi.
        const r = await Promise.allSettled([one(v).click(), one(v).click()]);
        detail = r.map((x) => x.status === 'fulfilled' ? 'lọt' : 'bị chặn').join(' + ');
      }
      else if (verb === 'expect_text')    { await pg.getByText(v.text ?? v, { exact: false }).first().waitFor(); detail = 'thấy «' + (v.text ?? v) + '»'; }
      else if (verb === 'expect_no_text') {
        const n = await pg.getByText(v.text ?? v, { exact: false }).count();
        if (n > 0) throw new Error('vẫn còn «' + (v.text ?? v) + '» (x' + n + ')');
        detail = 'không còn «' + (v.text ?? v) + '»';
      }
      else if (verb === 'expect_url')  { await pg.waitForURL((u) => String(u).includes(v)); detail = pg.url(); }
      else if (verb === 'expect_count') {
        const n = await locate(v).count();
        // Không khai tiêu chí = «có ít nhất một». Bản cũ báo ✓ «đếm = 0» — khẳng định không khẳng định gì,
        // một selector gõ sai vẫn đậu (R35). Muốn khẳng định vắng mặt thì ghi rõ `equals: 0`.
        if (v.equals === undefined && v.at_least === undefined && n === 0) throw new Error('đếm được 0 — không phần tử nào khớp (khẳng định vắng mặt thì ghi equals: 0)');
        if (v.equals !== undefined && n !== v.equals) throw new Error('đếm được ' + n + ', kỳ vọng ' + v.equals);
        if (v.at_least !== undefined && n < v.at_least) throw new Error('đếm được ' + n + ', cần ít nhất ' + v.at_least);
        detail = 'đếm = ' + n;
      }
      else if (verb === 'expect_enabled') {
        const on = await one(v).isEnabled();
        const want = v.value !== false;
        if (on !== want) throw new Error(want ? 'phần tử đang bị khoá' : 'phần tử đáng lẽ phải bị khoá');
        detail = want ? 'bấm được' : 'đang khoá';
      }
      else if (verb === 'wait_for') {
        // Chờ URL bằng cách ĐỌC LẠI `location.href` theo nhịp, không chờ sự kiện điều hướng: ứng dụng một
        // trang đổi đường bằng history.replaceState — `waitForURL` treo tới hết hạn dù URL đã đúng từ lâu
        // (đo 2026-09-17: luồng vào ứng dụng đã tới đúng màn đích mà bước chờ vẫn hết hạn 15 giây).
        if (v.url) { await pg.waitForFunction((needle) => location.href.includes(needle), v.url); detail = 'url ' + v.url; }
        else if (v.text) { await pg.getByText(v.text, { exact: false }).first().waitFor(); detail = 'chữ «' + v.text + '»'; }
        else if (v.gone) { await pg.getByText(v.gone, { exact: false }).first().waitFor({ state: 'detached' }); detail = '«' + v.gone + '» biến mất'; }
        else if (v.selector) { await pg.locator(v.selector).first().waitFor(); detail = v.selector; }
        else if (v.idle) { await pg.waitForLoadState('networkidle'); detail = 'mạng lặng'; }
        else if (v.ms) { await pg.waitForTimeout(v.ms); detail = v.ms + 'ms'; }
        else throw new Error('wait_for cần url | text | gone | selector | idle | ms');
      }
      else if (verb === 'read_screen') {
        const o = (v === true || v === undefined) ? {} : v;
        const sel = o.selector || 'body';
        const txt = await pg.evaluate((q) => {
          const e = document.querySelector(q);
          return e ? e.innerText : '(không có phần tử ' + q + ')';
        }, sel);
        detail = '\n--- màn hình ---\n' + txt.replace(/\n{3,}/g, '\n\n').trim().slice(0, o.limit || 1500) + '\n--- hết màn ---';
      }
      else if (verb === 'expect_storage') {
        // Khẳng định trên một khoá lưu trữ của trình duyệt. Chung cho mọi web: hub không
        // biết khoá ấy chứa gì, chỉ biết «có mặt» / «trường này khác rỗng» / «bằng đúng».
        const key = v.key;
        const where = v.where === 'local' ? 'localStorage' : 'sessionStorage';
        const raw = await pg.evaluate(([w, k]) => { try { return window[w].getItem(k); } catch { return null; } }, [where, key]);
        if (raw === null) throw new Error(where + '[' + key + '] RỖNG');
        let probe = raw;
        if (v.field) {
          let parsed = null;
          try { parsed = JSON.parse(raw); } catch { throw new Error(where + '[' + key + '] không phải JSON, không lấy được trường ' + v.field); }
          probe = String(v.field).split('.').reduce((o, k2) => (o == null ? o : o[k2]), parsed);
        }
        if (v.not_empty !== false && (probe === null || probe === undefined || probe === '')) {
          throw new Error((v.field ? v.field + ' của ' : '') + where + '[' + key + '] rỗng hoặc không có');
        }
        if (v.equals !== undefined && String(probe) !== String(v.equals)) {
          throw new Error('giá trị là ' + JSON.stringify(probe) + ', kỳ vọng ' + JSON.stringify(v.equals));
        }
        detail = (v.field ? v.field + ' của ' : '') + where + '[' + key + '] đạt';
      }
      else if (verb === 'read_storage') {
        const key = typeof v === 'string' ? v : v.key;
        const where = (typeof v === 'object' && v.where === 'local') ? 'localStorage' : 'sessionStorage';
        const raw = await pg.evaluate(([w, k]) => { try { return window[w].getItem(k); } catch { return null; } }, [where, key]);
        out.reads[key] = raw;
        detail = where + '[' + key + '] ' + (raw === null ? 'RỖNG' : 'có ' + String(raw).length + ' ký tự');
      }
      else if (verb === 'screenshot') { detail = await shoot(typeof v === 'string' ? v : v.name, typeof v === 'object' && v.full); }
      else if (verb === 'measure') {
        // Cửa thoát `df_browser_code` bị gọi 118 lượt trong vòng 4, và lý do lặp lại nhiều nhất là
        // «DSL không có động từ đọc getComputedStyle hàng loạt kèm bounding box» — vai `picky` phải tự
        // viết lại cả ba phép đo bằng JS thô. Đo là việc CHÍNH của vai ấy, nên nó phải là một động từ.
        const sel = typeof v === 'string' ? v : v.selector;
        const props = (typeof v === 'object' && v.props) || ['color', 'background-color', 'font-size', 'border-color'];
        const limit = (typeof v === 'object' && v.limit) || 20;
        const key = (typeof v === 'object' && v.as) || sel;
        const got = await pg.evaluate(([s2, ps, lim]) => {
          const rows = [];
          document.querySelectorAll(s2).forEach((el) => {
            if (rows.length >= lim) return;
            const r = el.getBoundingClientRect();
            if (!r.width && !r.height) return;
            const cs = getComputedStyle(el);
            const vals = {};
            for (const p2 of ps) vals[p2] = cs.getPropertyValue(p2);
            rows.push({
              tag: el.tagName.toLowerCase() + (el.className && typeof el.className === 'string' ? '.' + el.className.trim().split(/\s+/).join('.') : ''),
              text: (el.textContent || '').trim().slice(0, 40),
              box: Math.round(r.width) + '×' + Math.round(r.height),
              at: Math.round(r.x) + ',' + Math.round(r.y),
              css: vals,
            });
          });
          return { count: document.querySelectorAll(s2).length, rows };
        }, [sel, props, limit]);
        out.measures[key] = got;
        detail = sel + ' → ' + got.count + ' phần tử';
      }
      else if (verb === 'audit') {
        const checks = Array.isArray(v) ? v : (v.checks || ['geometry']);
        const key = (typeof v === 'object' && v.as) || ('step' + (i + 1));
        const got = await runAudit(checks, key);
        detail = Object.keys(got).join(' · ');
      }
      else if (verb === 'note')   { detail = String(v); }
      else if (verb === 'resize') { await pg.setViewportSize({ width: v.width, height: v.height }); detail = v.width + '×' + v.height; }
      else if (verb === 'theme')  { await pg.emulateMedia({ colorScheme: v === 'dark' ? 'dark' : 'light' }); detail = String(v); }
      else if (verb === 'offline')     { await pg.context().setOffline(true); detail = 'mất mạng'; }
      else if (verb === 'restore_net') {
        // `cdp` là biến của MỘT lượt chạy, còn độ trễ thì sống trên trang. Bản cũ chỉ gỡ khi biến ấy
        // khác null — tức chỉ gỡ được throttle đặt trong CÙNG lượt gọi. Đặt throttle ở lượt trước rồi
        // gọi `restore_net` ở lượt sau là gỡ hụt, và làn kẹt 2,2 giây cho tới hết phiên: mọi `wait_for`
        // mặc định 8 giây hỏng oan, đọc lên như lỗi sản phẩm (dogfood picky vòng 4, §7.3).
        await pg.context().setOffline(false);
        const c = await cdpFor();
        await c.send('Network.emulateNetworkConditions',
          { offline: false, latency: 0, downloadThroughput: -1, uploadThroughput: -1 }).catch(() => {});
        await pg.context().unrouteAll().catch(() => {});
        detail = 'có mạng lại (gỡ cả độ trễ và mọi lượt chặn route)';
      }
      else if (verb === 'throttle') {
        const t = (v === true) ? {} : v;
        const lat = t.latency_ms ?? 400, dl = t.download_kbps ?? 400, ul = t.upload_kbps ?? 400;
        const c = await cdpFor();
        await c.send('Network.emulateNetworkConditions',
          { offline: false, latency: lat, downloadThroughput: dl * 128, uploadThroughput: ul * 128 });
        detail = lat + 'ms · ' + dl + '/' + ul + ' kbps';
      }
      else if (verb === 'abort_route')  {
        // Pattern PHẢI là chuỗi: truyền object thì Playwright vẫn đăng ký route rồi mọi request sau ném
        // lỗi, làn kẹt tới khi `unroute *` (picky đợt 1 vòng 9, 2026-09-20). Nhận cả {pattern} cho tiện.
        const pat = (typeof v === 'string') ? v : v?.pattern;
        if (typeof pat !== 'string' || !pat) throw new Error('abort_route: pattern phải là chuỗi (vd "**/ops/graphql")');
        await pg.context().route(pat, (r) => routeChiApi(r, pat, () => r.abort())); detail = 'chặn ' + pat;
      }
      else if (verb === 'route_status') {
        if (typeof v?.pattern !== 'string' || !v.pattern) throw new Error('route_status: pattern phải là chuỗi');
        const body = JSON.stringify(v.body ?? { error: { message: 'lỗi giả lập từ dogfood_hub' } });
        await pg.context().route(v.pattern, (r) => routeChiApi(r, v.pattern,
          () => r.fulfill({ status: v.status, contentType: 'application/json', body })));
        detail = v.pattern + ' → ' + v.status;
      }
      else if (verb === 'unroute') {
        // Nhận cả {pattern}: bản trước ép object thành chuỗi "[object Object]" ⇒ không gỡ gì, route 500
        // sống tới hết phiên và MỌI màn sau đó trắng — đọc lên như "vỏ app thiếu khuôn lỗi" (picky
        // vòng 10, 2026-09-21, phát hiện #1 giả).
        const pat = (typeof v === 'string') ? v : v?.pattern;
        if (pat === '*' || pat === undefined) await pg.context().unrouteAll({ behavior: 'ignoreErrors' }).catch(() => {});
        else await pg.context().unroute(pat).catch(() => {});
        detail = 'bỏ chặn ' + (pat ?? '*');
      }
      else throw new Error('động từ ' + verb + ' chưa hiện thực');

      recordHere();
      out.steps.push({ i: i + 1, verb, ok: true, detail, ms: Date.now() - t0 });
    } catch (e) {
      // Bước hỏng CHÍNH LÀ phát hiện: chụp ảnh + trích DOM quanh chỗ lỗi rồi dừng.
      let shot = null, around = '';
      try { shot = await shoot(P.lane + '-fail-step' + (i + 1) + '-' + Date.now().toString(36) + '.png', true); } catch {}
      try {
        around = await pg.evaluate(() => ['[role=alert]', '[role=dialog]', 'button', 'table']
          .map((h) => document.querySelector(h)?.outerHTML?.slice(0, 200)).filter(Boolean).join('\n'));
      } catch {}
      out.steps.push({
        i: i + 1, verb: verb || '?', ok: false, ms: Date.now() - t0,
        detail: String(e && e.message || e).slice(0, 300),
        shot, around: around.slice(0, 400),
      });
      out.ok = false;
      break;
    }
  }
  out.url = pg.url();
  return out;
}
"""


def parse_viewport(viewport: str | None) -> dict[str, int] | None:
    """`"1024x768"` → `{"width": 1024, "height": 768}`; rỗng/sai dạng → None (không đổi khung)."""
    if not viewport:
        return None
    width, _, height = str(viewport).partition("x")
    if not (width.isdigit() and height.isdigit()) or not int(width) or not int(height):
        return None
    return {"width": int(width), "height": int(height)}


def compile_scenario(
    *,
    lane: str,
    base: str,
    steps: list[dict[str, Any]],
    shot_dir: str,
    forbidden_paths: list[str],
    tap_min: float = 44.0,
    viewport: str | None = None,
    step_timeout_ms: int = 8000,
    nav_timeout_ms: int = 15000,
) -> str:
    """Một mảng step → nguồn `async (page) => {…}` gửi xuống đầu kia."""
    params = {
        "lane": lane,
        "base": base.rstrip("/"),
        "steps": steps,
        "shotDir": shot_dir,
        "forbiddenPaths": forbidden_paths,
        "verbs": list(VERBS),
        "stepTimeoutMs": step_timeout_ms,
        "navTimeoutMs": nav_timeout_ms,
        "viewport": parse_viewport(viewport),
        "js": {
            "geometry": GEOMETRY_JS.strip(),
            "contrast": CONTRAST_JS.strip(),
            "values": RENDER_VALUES_JS.strip(),
            "tap": tap_targets_js(tap_min).strip(),
        },
    }
    return _RUNNER.replace("__PARAMS__", json.dumps(params, ensure_ascii=False))
