# `dogfood_hub` — một server MCP cho mọi khe dogfood

Đường **mặc định** của khe `viper-user-1..3` khi chạm experience web. Đường lui khi hub hỏng: ba server
Playwright `pw-slot-1..3` ở `.mcp.json` — khai nguyên nhưng **tắt mặc định** (`disabledMcpjsonServers` ở
`.claude/settings.json`); bật lại = xoá khỏi danh sách rồi mở lại phiên.

Hub là **lớp vận chuyển**, không đổi gì của thiết kế dogfood 1.9.2: 14 thẻ vai, `DOGFOOD-PLAN.md`, đợt 0 `smoke`,
≤3 vai một đợt, mỗi vai một tài khoản, gate đối chiếu §4 — giữ nguyên. Công thức gọi tool cho từng vai:
`.claude/skills/viper-roles/SKILL.md §6`.

## Vì sao có nó

`/viper-dogfood` tràn ngữ cảnh vì hai nguồn đốt token độc lập:

1. **Catalog.** Mỗi server Playwright lộ ~24 tool; ba khe là ~72 định nghĩa nạp vào **mọi** phiên.
2. **Byte mỗi thao tác.** Thói quen chụp cây accessibility sau *mỗi* thao tác, cộng phép dò giá trị hình
   thức không giới hạn của vai `picky` (đo được ~43 KB một màn ở một dự án VIPER).

Hub lộ **bốn** tool ý-định-cấp-cao, biên dịch cả kịch bản thành **một** hàm JavaScript gửi xuống chạy
một lượt, và chỉ trả nhật ký `✓/✗`. Trình duyệt vẫn là Chromium thật, vẫn `--isolated`, một làn một khe.

## Làn

| `tag` (+ `role` tuỳ chọn: tên vai, cho sổ chi phí) | Ai dùng | Ghi chú |
|---|---|---|
| `slot-1` · `slot-2` · `slot-3` | khe `viper-user-1..3` | khe nhận **vai** theo đợt (`context/DOGFOOD-PLAN.md`) — làn không biết vai nào đang ngồi trong nó |
| `main` | phiên chính khi muốn dùng hub thay server `browser` | |

- `tag` **bắt buộc**, không có mặc định: rơi vào làn chung là hai vai dùng chung một trình duyệt.
- Mỗi làn một tiến trình gateway riêng, sinh **lười** ở lượt gọi đầu, đóng khi rảnh quá `DF_LANE_IDLE_S`
  (mặc định 900 s).
- **`fresh: true`** ở lượt `df_run_scenario` **đầu tiên của mỗi vai** — đóng trình duyệt cũ, mở trình duyệt
  `--isolated` trắng. Đây là `browser_close` của khe `pw-slot` (đã thử thật: không `fresh` thì vai sau đọc
  được localStorage vai trước ghi; có `fresh` thì trắng).
- Khung nhìn: vai cần màn hẹp truyền `viewport` (`390x844`) cho từng lượt — **dấu nhân**, không dấu phẩy.

## Gateway — hub chạy trình duyệt ở đâu

Mặc định `scripts/dogfood_hub/gateway_local.sh`: `npm exec @playwright/mcp@0.0.79 --isolated --browser chromium`
ngay trên máy chạy hub. Biến môi trường:

| Biến | Mặc định | Dùng khi |
|---|---|---|
| `DF_GATEWAY` | `scripts/dogfood_hub/gateway_local.sh` | ứng dụng chạy ở máy khác — viết gateway của mình (vd `ssh -T` sang máy ấy), cùng hợp đồng: hai đối số `<RỘNGxCAO> <nhãn>`, stdio là kênh MCP, mọi lời cho người đọc ra **stderr** |
| `DF_PLAYWRIGHT_MCP_VERSION` | `0.0.79` | **ghim version**: mỗi bản đòi đúng một revision Chromium; `@latest` đòi revision máy chưa tải và mọi lượt gọi chết với «Browser … is not installed» (đo thật 2026-10-26: 0.0.83 đòi `chromium-1247`) |
| `DF_BROWSER` | `chromium` | máy có Google Chrome và muốn dùng nó: `chrome` |
| `DF_HEADLESS` | tự chọn | `1` ép headless · `0` ép có cửa sổ · trống: Linux không có phiên đồ hoạ → headless |
| `DF_LANE_IDLE_S` | `900` | `0` = giữ làn mãi |
| `DF_DOGFOOD_LOOP` | `Vòng : N` của `STATE.md` | ghi đè thư mục sổ `.viper/dogfood/l<N>/` |

## Ranh giới — đọc trước khi sửa

**Hub là hạ tầng, không phải mô hình nghiệp vụ.** Trong `scripts/dogfood_hub/` **không** được có mã màn,
tên route, tên nút của sản phẩm, địa chỉ cổng chứng thực, tên tài khoản hay tên gói design system. Kiến thức
về sản phẩm sống ở `context/dogfood/` (meta viết ở pha I khi bật dogfood web):

| File | Là gì |
|---|---|
| `config.json` | hằng chung: `tap_min` · `forbidden_paths` · `env_required` · `lane_viewports` |
| `targets.json` | experience → URL gốc (theo `ARCHITECTURE.md §2–§3`) · `slices`: tên lát → `tokens.json` (đường intake: `intake/design-systems/<ds>/tokens.json`; phỏng vấn: xuất `DESIGN-SYSTEM.md §2` ra `context/dogfood/slices/<ds>.json`) · `default_slice` |
| `routes.json` | route → mã màn của `PROTOTYPE.md §1` — tuỳ chọn, cho sổ phủ màn |
| `flows/*.json` | luồng đặt tên, **kể cả việc vào ứng dụng** — `flows/login.json` là khuôn, sửa label/nút cho khớp màn đăng nhập thật |
| `vars/lanes.json` | tuỳ chọn: biến riêng của làn `{{lane.<tên>}}` — thường để trống |

## Không có khái niệm «đăng nhập»

Vào ứng dụng chỉ là `goto` + `fill` + `click` + `expect_*`, nên hub **không** có động từ riêng cho nó:

- **`run_flow`** nạp `context/dogfood/flows/<name>.json`. **`with`** truyền tham số: `{"run_flow": {"name":
  "login", "with": {"user": "picky@dogfood.local"}}}` thay `{{arg.user}}` trong luồng. Tài khoản đi theo **vai**
  (`<vai>@dogfood.local`, seed sẵn), nên một luồng đăng nhập dùng chung cho mọi vai.
- **`{{env.TÊN_BIẾN}}`** cho bí mật — giá trị ở `deployment/local/.env` (vd `DOGFOOD_PASSWORD`). Hub thay lúc chạy,
  **không bao giờ in giá trị**; biến có tên mang bí mật (`PASSWORD`, `TOKEN`, `SECRET`…) bị che trong log và báo cáo.
- Luồng khai `serialize` + `min_interval_ms` thì hub giữ khoá theo tên luồng — nhiều làn cùng gõ mật khẩu một lúc
  dễ chạm chống dò mật khẩu của IdP.

## Bốn tool

| Tool | Trả về |
|---|---|
| `df_run_scenario` | nhật ký `✓/✗` từng bước · URL cuối · lỗi console gộp · đường dẫn ảnh |
| `df_audit_page` | **chỉ vi phạm**: hình học · tương phản · giá trị ngoài lát · vùng chạm · console |
| `df_api_probe` | **chỉ hàng lệch kỳ vọng** + một dòng tổng |
| `df_browser_code` | cửa thoát, cắt cứng 4000 ký tự, `note` bắt buộc và vào sổ |

Danh sách động từ của DSL nằm trong mô tả tool `df_run_scenario` (`server.py`) và hằng `VERBS` (`scenario.py`).

## Tiết kiệm ở đâu

| Đòn bẩy | Cơ chế |
|---|---|
| Catalog | 4 tool thay ~24 tool mỗi khe; `pw-slot` tắt mặc định ⇒ ~72 định nghĩa tool không nạp vào phiên chính và mọi khe |
| Một lượt một mảnh việc | cả kịch bản biên dịch thành một hàm JS, chạy một lượt, trả nhật ký ✓/✗ — không kéo cây accessibility sau mỗi thao tác |
| Chỉ trả vi phạm | `df_audit_page` / `df_api_probe` không trả phần đạt |
| Smoke thành luồng | mỗi dòng `DOGFOOD-PLAN §3` một `context/dogfood/flows/smoke-<#>.json`; đợt 0, lượt cuối, production và mọi vòng sau chỉ phát lại — 1 lượt gọi mỗi dòng |
| Phiên chính | Bước 2 «tự tay dùng» trên làn `main`; server `browser` để dành cho video demo pha R |

## Sổ

`python3 scripts/dogfood_hub/server.py --wire-report [N]` tổng kết theo vai (`role` truyền ở mỗi lượt gọi) · làn · tool.

`.viper/dogfood/l<N>/`: `coverage.jsonl` (màn đã mở — màn không vai nào mở vẫn hiện tên khi có `routes.json`) ·
`wire.jsonl` (byte trên dây mỗi lượt gọi) · `escape-hatch.jsonl` (mỗi lượt cửa thoát kèm lý do — dùng nhiều nghĩa
là DSL yếu).

## Kiểm

```bash
python3 scripts/dogfood_hub/server.py --selftest   # trọn vòng với chính mình, không trình duyệt (có trong selftest của khung)
python3 scripts/dogfood_hub/server.py --dry-run    # ghi .viper/dogfood/dry-run.js + node --check
```

## Hỏng thì xem đâu

| Triệu chứng | Nhiều khả năng là |
|---|---|
| «đầu kia báo lỗi: Browser … is not installed» | version `@playwright/mcp` đòi revision Chromium máy chưa có. Giữ `DF_PLAYWRIGHT_MCP_VERSION` ghim, hoặc `npm exec -y -- @playwright/mcp@<bản> install-browser chromium` (tải ~150 MB — hỏi trước trên máy dùng chung) |
| «Chromium distribution 'chrome' is not found» | gateway tự viết thiếu `--browser chromium` |
| «hub treo ở lượt gọi đầu», không log nào | ống stderr của tiến trình con không ai hút → con bị chặn. `client.py` có task hút; sửa chỗ đó thì giữ nguyên task ấy |
| phiên chết lặng, không lỗi | một dòng chữ lạc ra **stdout** — kênh JSON-RPC. Cấm `print()` trần, mỗi khung gửi xong phải `flush()` |
| «không trả JSON đọc được» | nguồn JS sinh ra hỏng cú pháp — `--dry-run` để đọc, `--selftest` chạy `node --check` |
| làn chết sau khi rời màn có hộp thoại chặn | `browser_run_code_unsafe` từ chối chạy khi còn modal. Hub tự đồng ý riêng `beforeunload` và nối lại một lần; hộp nghiệp vụ để step `dialog` quyết |
| làn «mất phiên đăng nhập» sau quãng nghỉ | làn rảnh quá `DF_LANE_IDLE_S` bị đóng — có chủ đích, để không bỏ lại Chromium hàng giờ. Chạy lại `run_flow` đăng nhập |
| khung nhìn «kẹt» ở cỡ của `resize` lượt trước | mỗi `df_run_scenario` **bắt đầu** ở `viewport` truyền vào (trống = mặc định làn); `resize` chỉ giữ tới lượt kế |
| `expect_count` đỏ «đếm được 0» | không khai `equals`/`at_least` nghĩa là «ít nhất một». Khẳng định vắng mặt: `{"expect_count": {"selector": "…", "equals": 0}}` |
| `read_storage` báo RỖNG dù đã ghi | mặc định đọc **sessionStorage**; localStorage thì `{"read_storage": {"key": "…", "where": "local"}}`. Step chỉ in độ dài, không in giá trị |

## Chặn / giả lỗi API

`abort_route` · `route_status` nhận **chuỗi** pattern (`"**/api/bookings"`) hoặc `{"pattern": "…"}`. Route giả chỉ
ăn `fetch`/`xhr` — tài nguyên `script`/`document`/ảnh đi tiếp, nên pattern rộng (`**/graphql**`) không còn làm trang
trắng giả vì chặn nhầm module dev của bundler; vẫn nên viết pattern hẹp. `throttle` đặt bằng một phiên CDP treo trên
trang, `restore_net` ở lượt sau gỡ được.

## Nguồn

Port từ dự án DriverPlus (VIPER 1.9, 2026-10-08). Khác bản gốc: gateway cục bộ thay ống ssh · làn là khe
`slot-1..3` thay sáu vai cố định · tài khoản theo vai qua `run_flow … with` thay bảng suy từ realm Keycloak ·
`fresh` thay `browser_close` · số vòng đọc từ `STATE.md` · lỗi «đầu kia báo lỗi» tách khỏi «JS hỏng cú pháp».
