---
name: api-contract
description: Kiểm tương thích ngược REST/GraphQL so với một mốc Git cố định (release đang chạy, bản app đã phát hành) — sinh lại schema ở cả hai phía từ nguồn, so breaking/dangerous change, và validate thao tác client ở mốc với schema mới; rà nguồn event. Không cần DB hay cụm. Dùng trước khi đổi hợp đồng API, trước deploy vòng ≥2, cùng BACKWARD-COMPATIBILITY-CHECKLIST.
---

# Hợp đồng API — tương thích ngược bằng máy

> Máy hoá một phần `context/BACKWARD-COMPATIBILITY-CHECKLIST.md §3` cho surface API (legacy là hợp đồng,
> `VIPER.md §1.2`). Checklist vẫn phải tick tay — checker là bằng chứng, không thay người rà.

## 1. Mốc

`context/perf/API-CONTRACT-BASE.json` giữ **SHA đầy đủ** của mốc mặc định (release đang chạy / bản app cũ nhất còn
được hỗ trợ). Mốc thiếu hoặc hỏng ⇒ exit 2 — **không bao giờ rơi về HEAD**. Đổi mốc chỉ khi mốc cũ thật sự hết
được hỗ trợ, ghi `DECISIONS.md`; không đổi SHA để làm xanh.

## 2. Kiểm gì

**GraphQL**
1. Schema ở mốc vs schema hiện tại — `findBreakingChanges` (thư viện `graphql`) là **lỗi**, `findDangerousChanges`
   là **cảnh báo**.
2. Thao tác client **ở mốc** (document app/web đã phát hành) phải `validate` được với schema mới — đây là câu hỏi
   thật: bản app cũ còn chạy không.
3. Thao tác client hiện tại validate được với schema hiện tại.

**REST (OpenAPI)** — endpoint, tham số, body/response, kiểu/enum/nullable, required, constraint, content type,
security. Request phải còn **nhận** dữ liệu cũ; response phải còn **trả** thứ client cũ hiểu. Cấu trúc checker
chưa hiểu mà đổi ⇒ đỏ bảo thủ.

**Event** (queue/stream/webhook) — cổng rà nguồn bảo thủ: producer/consumer của event đổi thì đỏ, cần rà tay.
Chưa so schema theo ngữ nghĩa — ghi rõ giới hạn này.

## 3. Sinh lại, không tin bản in

Hai phía đều **sinh lại** schema từ nguồn (code-first: chạy builder; schema-first: đọc file nguồn) — cây mốc lấy
bằng `git archive <sha>`, cây hiện tại là cây làm việc, **cùng một đường sinh**. Không tin `schema.gql`/`openapi.json`
đã commit: bản in hay lệch nguồn; lệch thì báo cảnh báo riêng.

## 4. Hợp đồng checker

```
scripts/check-api-contract.mjs [--base <git-ref>] [--snapshot] [--json]
```

Template có sẵn bản OpenAPI + GraphQL + nguồn event (`scripts/lib/openapi-compat.mjs` · `event-compat.mjs` ·
`contract-snapshot.mjs`; `graphql` chỉ cần khi có `schema.graphql`). Hợp đồng sinh máy ở `srcroot/contracts/<target>/
{openapi.json,schema.graphql}`. Chạy lẻ `make check-api-contract [base=<git-ref>]`; nằm trong nhóm PT. Máy chạy qua ống
remote không có `.git`: `PT=1 bash scripts/remote/sync.sh` mang snapshot mốc sang (`--snapshot`).

Exit **0** tương thích trong phạm vi kiểm · **1** có lỗi · **2** chưa kiểm được (thiếu Git, thiếu dependency, mốc
hỏng). Dependency lấy từ target đã cài (`node_modules` của BFF…), không cài thêm. Checker thuần tĩnh — sửa API xong
phải sinh lại rồi mới chạy.

Có `context/perf/API-CONTRACT-BASE.json` (Authority chốt mốc, ghi DECISIONS) thì `guard_bc` gọi cùng checker trước
mọi deploy — kể cả vòng 1, kể cả khi checklist §3 đã xanh: checker lỗi hoặc thiếu mốc vẫn chặn.

## 5. Giới hạn

Không thay smoke test, kiểm phân quyền, data migration hay dogfood vòng cũ. Breaking change có chủ đích chỉ hợp lệ
khi Authority đã chốt ở một trong ba nơi của `VIPER.md §1.2` — checker đỏ vẫn đỏ, người rà ghi `n/a` kèm chỗ chốt.
