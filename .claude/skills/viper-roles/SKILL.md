---
name: viper-roles
description: >
  Danh mục 14 vai dogfood của VIPER (smoke hồi quy · newbie · edge · picky · a11y · locale · mobile ·
  power · rushed · breaker · concurrent · money · operator · integrator), tín hiệu bật từng vai, luật chia
  đợt, và cách lập context/DOGFOOD-PLAN.md. Nạp skill này ở pha V của MỌI vòng khi lập kế hoạch dogfood,
  khi chạy /viper-dogfood, hoặc khi là khe viper-user-k nhận một thẻ vai. Không vai nào bật mặc định —
  trừ smoke hồi quy, luôn bật.
---

# viper-roles — danh mục vai dogfood + cách chọn vai cho từng vòng

> Luật #8: không báo xong khi chưa tự dùng. Skill này trả lời **dùng bằng con mắt nào**. Sáu vai cố định
> của bản cũ bỏ sót đúng những chỗ hay hỏng nhất của phần mềm thật (tiền, hai người cùng lúc, quản trị,
> tích hợp, trợ năng, ngày giờ) — và bắt dự án không có mobile vẫn chạy vai mobile. Giờ vai **chọn theo
> dự án và theo vòng**, ghi trong `context/DOGFOOD-PLAN.md`.

## 1. Danh mục

Mỗi vai là một **thẻ** ở `roles/<vai>.md`: tín hiệu bật · tài khoản · đợt gợi ý · kịch bản · khuôn báo cáo.

| Vai | Đi tìm | Bật khi (tóm tắt — đủ ở thẻ) | Đợt |
|---|---|---|---|
| `smoke` | Luồng lõi + mọi AC đã giao (vòng này + các vòng trước) còn chạy | **LUÔN BẬT** | 0 |
| `newbie` | Người mới tự hiểu phải làm gì | Có UI người dùng cuối + vòng có luồng/màn người mới chạm | 1 |
| `edge` | Rỗng · lỗi · chậm · mất mạng · nhiều dữ liệu | Có UI đọc dữ liệu từ server | 1 |
| `picky` | Giao diện thật lệch design system đã chốt | Có DESIGN-SYSTEM + vòng có màn mới/đổi giao diện | 1 |
| `a11y` | Chỉ bàn phím · trình đọc màn hình · phóng to 200% | Yêu cầu trợ năng · đại chúng/khu vực công · persona nhìn kém, lớn tuổi | 1 |
| `locale` | Dấu tiếng Việt · ngày giờ/múi giờ · tiền/số · tìm/sắp xếp | Có ngày giờ nghiệp vụ, tiền, nhiều ngôn ngữ/múi giờ, tìm theo tên | 1–2 |
| `mobile` | Màn nhỏ, chạm, bàn phím ảo; app native | Persona dùng điện thoại · đòi responsive · **có mobile experience (bắt buộc bật)** | 2 |
| `power` | Việc lặp lại hằng ngày tốn bao nhiêu bước; danh sách dài | Persona dùng hằng ngày · màn quản lý nhiều bản ghi | 2 |
| `rushed` | Bấm hai lần · quay lại · bỏ giữa chừng · nhiều tab | Có form ghi / luồng nhiều bước | 2 |
| `breaker` | Nhập bậy · vượt quyền theo ma trận · injection | Có ô nhập hoặc ≥2 vai (gần như mọi dự án có đăng nhập) | 2 (cuối) |
| `concurrent` | Hai người tranh một tài nguyên · ghi đè im lặng | Đặt chỗ/tồn kho/khung giờ · nhiều người sửa chung · thời gian thực | 2 |
| `money` | Từng đồng · trừ hai lần · server tin giá client | Có thu tiền, giá, giảm giá, số dư, hoàn tiền | 2 |
| `operator` | Hỗ trợ khách không cần kỹ sư · lịch sử · vượt quyền nội bộ | Có persona/experience quản trị, vận hành, CSKH | 2 (cuối) |
| `integrator` | API ngoài · webhook · import/export · email/SMS · bên thứ ba lỗi | Có API công khai, webhook, file, thông báo, tích hợp | 2 |

Sản phẩm **không có UI** (marker `KHÔNG CÓ UI`): `picky` · `mobile` · `a11y` luôn tắt; `smoke` chạy bằng `curl`
trên API thật; `breaker` · `integrator` · `concurrent` · `money` vẫn chạy được qua HTTP.

## 2. Lập kế hoạch dogfood — pha V, mỗi vòng

`context/DOGFOOD-PLAN.md` viết lại mỗi vòng (bản cũ nằm ở `context/archive/loop-N/`). Trình tự:

1. **Quét tín hiệu** trong `PRD.md` (§3 AC, §7 quyết định: đăng nhập, thu tiền…), `PERSONAS.md` (thiết bị chính,
   tần suất dùng, có vai quản trị không, ma trận §2), `ARCHITECTURE.md` (§3 mobile, §5 contract, §6 ca biên,
   §9 dịch vụ ngoài), `DESIGN-SYSTEM.md`, và **phạm vi của vòng này** (`intake/loops/l<N>/` hoặc tài liệu vòng).
2. **§1 — mọi vai trong danh mục một dòng**: `bật` hoặc `tắt` + **căn cứ** trỏ đúng file và mục
   (`PERSONAS §1 — thiết bị chính điện thoại`, `PRD §7 — không thu tiền`). Tắt cũng phải có căn cứ — để
   thấy là đã cân nhắc, không phải quên. Vai vào **đúng vòng có tín hiệu**: vòng đưa thanh toán vào mới
   bật `money`, vòng chưa có màn mới thì `picky` có thể tắt.
3. **§2 — mỗi vai bật một dòng**: đợt · persona · tài khoản · nơi thử · **việc phải làm vòng này** (AC, luồng,
   màn cụ thể — không chép lại thẻ vai, chỉ nói vòng này soi chỗ nào).
4. **§3 — danh sách smoke hồi quy**: luồng lõi (`ARCHITECTURE.md §7`) + **mọi AC đã giao** ở các vòng trước
   (`context/archive/loop-*/PRD.md §3`) + AC vòng này. Mỗi dòng 2–4 bước kiểm được PASS/FAIL.
5. Seed tài khoản cho mọi vai bật (`deployment/local/README.md`).

Ngắn: cả file **một trang**. Authority đọc §1–§2 để biết vòng này được thử bằng con mắt nào.

## 3. Luật chia đợt

Server dev và DB **dùng chung** giữa các vai; trình duyệt và tài khoản thì riêng. Nên:

| Luật | Vì sao |
|---|---|
| **Đợt 0 = `smoke` một mình, chạy đầu tiên** | Luồng lõi gãy thì mọi vai khác chỉ báo lại đúng cái gãy đó — sửa trước rồi mới thả đợt sau |
| **Tối đa 3 vai một đợt** (= 3 khe `viper-user-1..3`) | Ba trình duyệt riêng; nhiều hơn là giành khe |
| Vai cần **cảnh sạch** (`edge` tài khoản trống, `newbie`, `picky`, `a11y`) đi trước | Trạng thái rỗng chết ngay khi có bản ghi đầu tiên |
| Vai **ghi dữ liệu chung** (`breaker`, `operator`, `concurrent`) đi sau, không chung đợt với vai đang đọc đúng dữ liệu đó | Dữ liệu rác của vai này là cảnh hỏng của vai kia |
| **Seed lại** giữa hai đợt nếu đợt trước ghi dữ liệu chung | Mỗi đợt bắt đầu từ mốc dữ liệu biết trước |
| Experience mobile native: các vai trong đợt chạy **tuần tự** | Simulator/emulator là một thiết bị dùng chung (`viper-mobile §1`) |
| Sửa phát hiện xong → **`smoke` chạy lại lần cuối** | Sửa là chỗ dễ làm gãy thứ đang chạy nhất |

## 4. Khe chạy thẻ như thế nào

Ba khe `viper-user-1` · `-2` · `-3`, mỗi khe **một trình duyệt riêng**: làn `tag: "slot-k"` của server
`dogfood_hub` — mỗi làn một Playwright `--isolated` (`scripts/dogfood_hub/README.md`). Khe chặn trình duyệt
của phiên chính bằng `disallowedTools` và **không bao giờ** truyền tag của khe khác. Một khe được **dùng lại**
qua nhiều đợt, nên lượt gọi đầu tiên của mỗi vai truyền **`fresh: true`** — trình duyệt mở lại trắng, không
còn cookie, localStorage, tab của vai trước (đã thử thật). Mọi lượt gọi truyền thêm `role: "<vai>"` để sổ
`.viper/dogfood/l<N>/wire.jsonl` tính được chi phí theo vai.

Đường lui (hub hỏng): ba server Playwright `pw-slot-1..3` vẫn khai ở `.mcp.json` nhưng tắt mặc định
(`disabledMcpjsonServers` ở `.claude/settings.json` — tiết kiệm ~72 định nghĩa tool mỗi phiên). Bật lại = xoá
khỏi danh sách rồi mở lại phiên; khe khi đó dùng `mcp__pw-slot-k__browser_*` + `browser_close` đầu lượt như 1.9.2.

Phiên chính giao cho mỗi khe: tên vai (khe đọc `roles/<vai>.md`) · tài khoản riêng · persona · việc phải làm
(dòng §2) · tài liệu thẻ đòi (ma trận cho `breaker`, lát token cho `picky`, bảng §3 cho `smoke`).

## 5. Vai ngoài danh mục

Ngành có góc nhìn riêng (bác sĩ đọc kết quả xét nghiệm, tài xế dùng một tay khi dừng đèn đỏ…) → chép
một thẻ gần nhất thành `roles/<vai-mới>.md`, sửa bảng đầu thẻ + kịch bản, thêm một dòng vào bảng §1
của `DOGFOOD-PLAN.md`. Không phải sửa agent hay `.mcp.json` — khe chạy được mọi thẻ.

## 6. Công thức `dogfood_hub` cho từng vai — tiết kiệm ngữ cảnh

Thẻ vai nói **soi gì**; bảng này nói **gọi tool nào** khi chạy qua hub. Chỗ thẻ nhắc `browser_*` (đường 1.9.2)
thì làm bằng cột "Công cụ". Ba luật chung, áp mọi vai:

1. **Một mảnh việc = một `df_run_scenario`**: gom cả đường đi vào `steps`, đừng gọi từng thao tác. Hub dừng ở
   bước hỏng và tự chụp ảnh + trích DOM quanh đó — bước hỏng chính là phát hiện.
2. **`read_screen` thay ảnh chụp cây**, có `limit`. `screenshot` chỉ khi cần bằng chứng cho phát hiện.
3. **Cửa thoát `df_browser_code`** chỉ ở các ô ghi "cửa thoát" dưới đây — `note` bắt buộc; dùng ngoài bảng là
   dấu DSL thiếu động từ, ghi vào báo cáo.

| Vai | Công cụ | Lượt gọi dự kiến |
|---|---|---|
| `smoke` | **Mỗi dòng §3 một `df_run_scenario`** gồm `run_flow` đăng nhập + `run_flow smoke-<#>` (luồng lưu ở `context/dogfood/flows/smoke-<#>.json`, xem bên dưới). `FAIL` = bước `expect_*` hỏng | = số dòng §3 |
| `newbie` | `df_run_scenario` theo từng màn: `goto` → `read_screen` → thao tác như người mới → `read_screen`. Không đọc code, không đoán selector — chọn phần tử bằng `role`+`name`/`label`/`text` như người thấy | 1–2 mỗi màn |
| `edge` | Trong `steps`: `offline` · `restore_net` · `throttle` · `abort_route` · `route_status` · `unroute` (pattern hẹp `**/api/<x>`). Lỗi console: `audit {checks:["console"]}`. Tài khoản trống `edge@` cho trạng thái rỗng | 1 mỗi trạng thái |
| `picky` | **`df_audit_page`** mỗi màn của experience được giao, `checks: ["geometry","contrast","values","tap_targets"]`, `slice` = lát của design system experience mặc. Số đo cụ thể: step `measure {selector, props}`. Chỉ trả vi phạm kèm số — đúng thứ thẻ đòi | 1 mỗi màn (+1 `measure` khi nghi) |
| `a11y` | Bàn phím: chuỗi `press: "Tab"` + `measure {selector: ":focus"}` đọc phần tử đang focus. Phóng to: `viewport: "640x800"` + `audit geometry`. Tương phản: `audit contrast`. Cây accessibility đầy đủ: **cửa thoát** `page.accessibility.snapshot()` cắt gọn | 2–4 mỗi màn |
| `locale` | Nhập/đọc dấu, tiền, ngày: `fill` + `read_screen`. Đổi múi giờ/ngôn ngữ trình duyệt: **cửa thoát** (`browser().newContext({timezoneId, locale})`) | 1–2 mỗi luồng |
| `mobile` | Web: **`viewport: "390x844"`** mọi lượt (và `360x640`) + `audit tap_targets`. App native: skill `viper-mobile`, không qua hub | 1–2 mỗi màn |
| `power` | Một `df_run_scenario` cho mỗi việc lặp hằng ngày — số bước trong nhật ký **chính là** số đo «tốn bao nhiêu bước» | 1 mỗi việc |
| `rushed` | `double_submit` · `back` · `reload` · `open_tab {share_session: true}` + `switch_tab` (hai tab cùng phiên) | 1–2 mỗi form |
| `breaker` | **`df_api_probe`** bắn ma trận `PERSONAS §2` từ bên trong làn (đúng cookie của vai) — chỉ trả hàng lệch kỳ vọng. Nhập bậy/injection: `fill` chuỗi độc trong `df_run_scenario` | 1 cho cả ma trận + 1 mỗi form |
| `concurrent` | Hai người = hai context: **cửa thoát** (`page.context().browser().newContext()`), bắn hai thao tác bằng `Promise.all` | 1–3 |
| `money` | Server có tin giá client: `df_api_probe` gửi body sửa giá. Trừ hai lần: `double_submit`. Đọc số gửi lên: **cửa thoát** (`page.on('request')`) | 2–4 |
| `operator` | `df_run_scenario` trên experience quản trị (URL ở `context/dogfood/targets.json`); vượt quyền nội bộ: `df_api_probe` | 1 mỗi việc hỗ trợ |
| `integrator` | `upload` cho import; bên thứ ba lỗi: `route_status`/`abort_route` domain của họ; webhook/API công khai: `df_api_probe` | 1 mỗi điểm tích hợp |

**Smoke thành luồng — chỗ tiết kiệm lớn nhất.** Smoke hồi quy chạy ít nhất ba lần mỗi vòng (đợt 0, lượt cuối,
production ở P2) và **dài thêm mỗi vòng** (mọi AC đã giao). Mỗi dòng §3 có một
`context/dogfood/flows/smoke-<#>.json` (số = cột `#`): đúng các bước của dòng, kết thúc bằng `expect_*` theo cột
"Kỳ vọng". Lần đầu khe `smoke` viết luồng (đi tay rồi ghi lại); các lần sau chỉ phát lại — vẫn là thao tác thật
trên app thật. Luật giữ cho nó trung thực:

- Luồng **đỏ là phát hiện**, không sửa luồng cho xanh. Chỉ sửa khi AC đổi — kèm dòng `DECISIONS.md` nêu AC nào.
- Dòng §3 mới (AC vòng này) → luồng mới. Dòng có luồng hỏng vì giao diện đổi mà AC không đổi → báo `FAIL`
  ở dòng đó + ghi "luồng cũ không còn khớp màn" — phiên chính quyết đó là lỗi sản phẩm hay luồng cần viết lại.
- Báo cáo `smoke` vẫn đủ khuôn của thẻ: mỗi dòng §3 một `PASS`/`FAIL` + "thấy gì" (lấy từ `read_screen` cuối luồng).

**Đo chi phí sau mỗi lượt dogfood**: `python3 scripts/dogfood_hub/server.py --wire-report` — lượt gọi, byte vào
model, ước token theo vai/làn/tool, lượt dùng cửa thoát. Vai tốn bất thường hoặc cửa thoát nhiều → xem lại công thức.

