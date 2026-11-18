---
name: perf-testing
description: Kiểm hiệu năng (PT) bằng k6 từ máy phát tải vào môi trường chịu tải riêng — dựng cụm PT có giới hạn tài nguyên như production, sinh case từ workload Authority chốt, chạy load/spike/stress/soak/journey, lưu báo cáo có hash nguồn và so hồi quy ba lượt. Chỉ đo, không sửa sản phẩm/runtime để số đẹp, không gọi probe là nghiệm thu. Nạp khi PRD/intake có SLO hoặc mức tải, hoặc vòng khai việc PT.
---

# Kiểm hiệu năng (PT)

> **Bật khi nào**: PRD/intake nêu SLO (p95, RPS, số người đồng thời), `ROADMAP` có vòng PT, hoặc Authority hỏi
> "chịu được bao nhiêu". MVP một tuần chưa có người dùng thật → thường chưa bật; ghi lý do.
> Đi cùng: `query-safety` (đáng nghi trước khi đo), `connection-budget` (trần kết nối khi tăng replica).

## 1. Đọc trước khi đo

`context/perf/WORKLOAD.md` — **Authority chốt**: các nhóm thao tác (đọc `R1…`, ghi `W1…`) và tỷ trọng, mức tải
(danh nghĩa / đỉnh / spike), SLO từng nhóm, ngưỡng lỗi. Mọi con số nằm trong **khối ```json** của file đó — nguồn duy
nhất công cụ đọc, không gõ cứng ngưỡng vào script. Chưa chốt (`"confirmed": false`) ⇒ chỉ được chạy **probe** (tập
con, ngắn), báo cáo ghi rõ "probe, không phải nghiệm thu", và `make perf-gate` luôn đỏ. Đổi một con số là đổi
`workload_hash` — báo cáo đo theo bản cũ hết hiệu lực.

## 2. Môi trường

- **Cụm PT riêng**, không bao giờ production hay môi trường dùng chung. Mỗi boundary chạy dưới giới hạn tài nguyên
  **như production** (CPU/memory limit, swap 0, restart policy) — đo trên máy rộng rãi hơn production là đo sai.
- **Cờ như production**: `NODE_ENV=production` hoặc tương đương; tắt introspection/dev tool. Gỡ lỗi cần cờ dev thì số
  đo lượt đó không so với lượt cờ production.
- **Hai máy**: máy **phát tải** (k6, ghim CPU riêng) và máy **chịu tải** (cụm). Agent truyền đúng máy theo lời người
  dùng, không tự chọn. Hai đầu cùng một máy chỉ khi khai rõ (`same_host`) — số đo không so chung với lượt hai máy.
- **Rate limit** của gateway/BFF tính theo IP/danh tính: k6 phát từ một IP sẽ ăn 429 rất sớm. Nâng trần cho **riêng
  cụm PT** qua cấu hình của cụm đó, khôi phục khi dọn — không sửa cấu hình production, không gọi thẳng boundary để
  né gateway rồi gọi đó là số qua gateway.

Lệnh cụm theo hợp đồng Makefile của dự án (thêm khi bật skill, ghi `DECISIONS`): `make pt-up` · `make pt-status` ·
`make pt-down`. `pt-status` đọc trạng thái thật (cgroup, restart, OOM) — lượt đo có restart/OOM khác 0 thì đọc kèm.

## 3. Case

Mảng JSON: `group` (thuộc WORKLOAD), `weight`, `method`, `path` tương đối, `body`, `headers`, `graphql`,
`expectedStatus`, `expect` (`value`/`exists`/`lengthMin`), tuỳ chọn `steps`/`capture` cho chuỗi thao tác.
GraphQL 200 kèm `errors` = **lỗi nghiệp vụ**. Fixture (tài khoản, token) sinh trên máy chịu tải, **ngoài repo**,
mode 0600, xoá khi xong — không commit credential hay URL ký. Văn bản query lấy từ document của client (sinh máy),
không chép tay. Runner chặn **trước khi phát tải**: nhóm ngoài WORKLOAD, đỉnh RPS vượt rate limit, token hết hạn
trong cửa sổ đo.

## 4. Loại bài

| `kind` | Mục đích |
|---|---|
| `load` | giữ RPS mức danh nghĩa/đỉnh (`constant-arrival-rate`) |
| `spike` | nhảy lên spike rồi về |
| `stress` | tăng chậm tới khi gãy — tìm trần, không phải nghiệm thu |
| `soak` | giữ lâu (≥ vài giờ) — rò bộ nhớ, outbox/queue tồn, pool cạn |
| `journey` | mô phỏng **người** (`ramping-vus`): vào app, tải màn chính, thao tác theo trọng số, nghỉ ngẫu nhiên; RPS là kết quả |
| `matrix` | 1 VU, mỗi case một lượt — kiểm case đúng trước khi đo |

Không so số `journey`/`stress` với `load`. Chạy ít request trước; không dùng 429 để kết luận hệ thống bão hoà.

## 5. Báo cáo và hồi quy

Báo cáo JSON ở `context/perf/reports/<ISO>-<kind>.json` (+ `.md` tóm tắt): hash source/workload/case, hostname
**thật** hai máy, đường truyền, phiên bản k6, môi trường; p95/p99 từng nhóm và từng API (tag `name`), tỷ lệ lỗi,
ngưỡng hỏng. **Không** ghi request hay credential. p95 trên dashboard phải là `histogram_quantile` trên histogram gộp —
trung bình các p95 không phải p95; số chính xác là JSON của runner.

So hồi quy: **ba lượt** mỗi bộ, cùng điều kiện. p95 tăng > 10 % hoặc ra ngoài khoảng tin cậy 95 % của baseline ⇒ đỏ.
Probe không thay headroom; healthcheck không thay luồng thật.

Công cụ (không phát tải, không seed — chỉ đọc báo cáo đã có):

| Lệnh | Việc |
|---|---|
| `make perf-regression baseline=A.json current=B.json` | so hai bộ ba lượt (`scripts/check-perf-report.py`); khác một điều kiện đo (hash workload/dataset/topology/case, máy, k6, môi trường, `same_host`…) ⇒ không so được, exit 2 |
| `python3 scripts/perf/bundle.py --load … --peak … --soak … --telemetry … --stress … --recovery … --plans … --baseline … --current … --output <gói.json>` | gom artifact thành gói, giữ sha256 từng file; không tự tạo `accepted-<env>.json` |
| `make perf-evidence report=<gói.json>` | xác minh bảy tiêu chí (`scripts/lib/perf_evidence.py`): `latency` · `throughput` · `soak` · `headroom` · `recovery` · `plans` · `regression` — mỗi tiêu chí `pass` / `fail` / `missing`; cờ tự khai trong báo cáo không thay số đo |
| `make perf-gate env=<SIT\|UAT\|…>` | canh `context/perf/reports/accepted-<env>.json` trước deploy: đúng môi trường, đúng `source_hash` hiện tại (tài liệu `context/`, `.claude/`… không tính), WORKLOAD đã chốt và cùng hash, cả bảy tiêu chí `pass` |

Exit chung: `0` đạt · `1` đỏ · `2` **chưa kiểm được** (thiếu/hỏng báo cáo) — `2` không bao giờ là xanh. Runner k6,
kịch bản và collector telemetry là phần **theo dự án** (stack, gateway, topology) — viết ở pha I khi bật skill, xuất
đúng các trường mà `lib/perf_report.py` · `lib/perf_evidence.py` đọc (test mẫu: `scripts/tests/perf_fixtures.py`).

## 5b. Nhóm kiểm PT — tách khỏi `make check` / `make test`

Nhóm PT **không** chạy trong `make check` / `make test` mặc định:

| Lệnh | Phạm vi |
|---|---|
| `make check-pt` | các target ở `PT_CHECK_TARGETS` (Makefile) — mặc định đủ năm checker: `api-contract` · `query-safety` · `data-security` · `data-lifecycle` · `connection-budget` (`env=PT`). Skill `n/a` thì gỡ khỏi danh sách + ghi DECISIONS; còn trong danh sách mà chưa có sổ/cấu hình thì exit 2 "chưa kiểm" — đỏ là đúng. Bật ống remote thì cả nhóm chạy ở máy chạy |
| `make test-pt` | các target ở `PT_TEST_TARGETS` — mặc định `test-pt-tools` (hồi quy toàn bộ công cụ: checker, hook, perf, deploy); thêm phép kiểm hành vi trên cụm PT khi có |
| `make check PT=1` / `make test PT=1` | chạy luồng thường, **đạt rồi** mới thêm nhóm PT tương ứng. `PT` chỉ nhận `0`/`1` |

`scripts/perf/pt-gates.sh` chạy từng target tuần tự với `PT=0` (cắt đệ quy), target đỏ vẫn chạy tiếp target sau để
một lượt thấy hết, log + `summary.tsv` ở `.viper/pt-gates/`. Nhóm PT không seed/reset, không dựng cụm, không tự chạy
load/stress/soak. Luồng thường xanh **không** có nghĩa PT đạt; nhóm PT xanh **không** có nghĩa nghiệm thu tải.

## 6. Quyền và dọn dẹp

Sửa công cụ PT (runner, collector, test của chúng) được. **Không** sửa sản phẩm, schema, runtime, throttler, pool để
số đo đẹp — phát hiện là nợ sản phẩm, ghi `ROADMAP.md` hoặc blocker `STATE.md`. Không chạy perf trong `make check` mặc định (§5b).
Dọn đúng thứ của mình: job k6, tunnel, case riêng, cấu hình rate limit tạm của cụm PT — kể cả khi lượt đo lỗi giữa
chừng. Mua dịch vụ đo đám mây, phát tải vào môi trường dùng chung = hướng ra ngoài → **hỏi thật** (§3 CLAUDE.md).
