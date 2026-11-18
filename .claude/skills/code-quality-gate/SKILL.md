---
name: code-quality-gate
description: >
  Ngưỡng chất lượng code dùng chung cho MỌI target, không phụ thuộc thư viện: cognitive complexity ≤15,
  file ≤400 dòng, hàm ≤50 dòng, tham số ≤4, độ lồng ≤3, chuỗi lặp ≥3 lần thành hằng, coverage ≥90%,
  duplication ≤3%. CHỈ bật khi Authority chốt (DECISIONS / _PROPOSAL / ARCHITECTURE) — thường ở đường intake,
  hệ nhiều target sống lâu; đường phỏng vấn một tuần không dùng. Nạp khi scaffold target mới (lắp gate vào
  `make check`) hoặc khi `make check` đỏ vì ngưỡng. Gồm: đo bằng gì theo ngôn ngữ, cách lắp vào 6 lệnh,
  ba ngưỡng không được nới, đối chiếu SonarQube.
---

# Code quality gate — ngưỡng dùng chung mọi target

> **Bật khi nào**: Authority chốt rõ ở một trong các chỗ `DECISIONS.md` · `intake/loops/l<N>/_PROPOSAL.md` ·
> `ARCHITECTURE.md` (mục chất lượng). Đây là **ngoại lệ có chủ đích** của luật "Không đặt mục tiêu coverage %" trong
> `shared-production-ready` nhóm 3: luật đó giữ tốc độ cho MVP một tuần; hệ lớn chia vòng, nhiều người/agent cùng
> sửa thì cần ngưỡng máy đo. **Không chốt → không bật**, và nhóm 3 giữ nguyên luật cũ.
>
> Ngưỡng **theo lane, không theo thư viện** — cùng một bộ số cho mọi stack của dự án.

## 1. Tám ngưỡng

Luật chung: **mọi gate đo bằng phần trăm phải ≥ 90 %**. Coverage ≥ 90 % (và file luật phân quyền phải 100 %);
duplication ≤ 3 % — tức mã không trùng lặp ≥ 97 %. Thêm ngưỡng phần trăm mới thì áp cùng luật.

| # | Ngưỡng | Bắt cái gì thật |
|---|---|---|
| 1 | cognitive complexity ≤ **15** | hàm phải giữ nhiều nhánh trong đầu cùng lúc — chỗ bug ẩn lâu nhất |
| 2 | file ≤ **400** dòng | file dài là feature chưa được tách |
| 3 | hàm ≤ **50** dòng | hàm dài không test được từng nhánh |
| 4 | tham số ≤ **4** | nhiều hơn thì thứ tự đối số thành bẫy; gom thành object có kiểu |
| 5 | độ lồng ≤ **3** | lồng sâu = điều kiện chưa được đảo thành early-return |
| 6 | chuỗi lặp ≥ **3** lần → hằng | chuỗi rải rác thì sửa sót một chỗ, và không rà lại được giọng ở `DESIGN-SYSTEM.md §1` |
| 7 | coverage ≥ **90 %** | nhánh không có test là nhánh **chưa ai từng chạy thử** |
| 8 | duplication ≤ **3 %** | client gọi boundary viết tay thay vì sinh máy hay lộ ra ở đây |

## 2. Đo bằng gì

**TypeScript** — `eslint.config.js` + `eslint-plugin-sonarjs`:

```js
rules: {
  'sonarjs/cognitive-complexity': ['error', 15],
  'max-lines':             ['error', { max: 400, skipBlankLines: true, skipComments: true }],
  'max-lines-per-function':['error', { max: 50,  skipBlankLines: true, skipComments: true }],
  'max-params':            ['error', 4],
  'max-depth':             ['error', 3],
  'sonarjs/no-duplicate-string': ['error', { threshold: 3 }],
}
```

Coverage: `jest --coverage` / `vitest run --coverage`, `coverageThreshold` **90** cho cả `lines` / `statements` /
`branches` / `functions` — ngưỡng **global**, không phải vài file chọn lọc.

**Python** — `ruff` (`C901` max-complexity, `PLR0913` max-args, `PLR0915`), `pytest --cov --cov-fail-under=90`.
**Go** — `gocognit -over 15`, `golangci-lint` (`funlen`, `nestif`, `goconst`), `go test -coverprofile` + kiểm 90.
**Java** — Checkstyle/PMD cho độ dài + tham số, JaCoCo `minimum 0.90`.
**Dart** — `flutter_lints` + `dart_code_metrics` cho complexity; không có công cụ thì **khai rõ "đo tay ở review"** —
một ngưỡng không ai đo được tệ hơn không có ngưỡng, vì nó dạy người đọc bỏ qua cả bảng.

Duplication: `jscpd --threshold 3` (mọi ngôn ngữ) hoặc SonarQube.

**Loại khỏi mẫu đo coverage — danh sách đóng, khai một lần ở đây:**

| Loại | Vì sao loại |
|---|---|
| composition root của tiến trình (`main.ts`, `main.go`, `__main__`) | gọi nó trong unit test là khởi động cả app — phủ bằng smoke test |
| file thuần khai báo DI / module (`*.module.ts`) | không có nhánh nào để phủ |

Loại thêm file khác để qua ngưỡng chính là nới ngưỡng bằng cửa sau — không ai thấy trong config để mà chất vấn.

## 3. Lắp vào 6 lệnh

Gate nằm **trong** `make check`, không phải lệnh riêng — thêm lệnh là phá hợp đồng 6 lệnh (`CLAUDE.md §6`), và gate
phải gõ riêng mới chạy là gate sẽ bị quên. Coverage chạy ở `make test`. Dòng đầu của `make check` vẫn là
`check_lang.py check` (luật #7).

```make
check:
	@$(PYTHON) scripts/check_lang.py check
	npm run lint && npx tsc --noEmit && npx jscpd --threshold 3 src && npm run build
```

**SonarQube** (tuỳ chọn) là lớp **đối chiếu** trên server, không phải nguồn ngưỡng thứ hai: `make check` phải fail
đúng thứ Sonar sẽ fail. Quét: `bash scripts/sonar_scan.sh [--coverage] [target…]`. Cấu hình ở `.local/env/.env.sonar` (khuôn `.local/templates/.env.sonar.example`); mỗi target
một project — gộp chung thì một target đỏ kéo cả hệ đỏ, và duplication đo chéo giữa các target cố ý lặp khung.

## 4. Ba ngưỡng KHÔNG được nới

Năm ngưỡng đầu là kỷ luật đọc — nới một lần cho một chỗ cụ thể, kèm `// eslint-disable-next-line` (hoặc tương đương)
**có ghi lý do**, là chấp nhận được. Ba ngưỡng dưới thì không:

| Ngưỡng | Vì sao không nới |
|---|---|
| **coverage ≥ 90 % global**, file luật phân quyền **100 %** | file luật là nơi ma trận `PERSONAS.md §2` sống: mỗi ô ✗ là một nhánh, nhánh không test là phép chặn chưa ai chạy thử |
| **duplication ≤ 3 % ở tầng contract** | client gọi boundary phải **sinh máy**; trùng lặp ở đó = ai đó viết tay client, mất hàng rào chống lệch FE–BFF–BE |
| **file ≤ 400 dòng cho controller / resolver / handler** | tầng này chỉ vào/ra; dài ra là logic rơi xuống sai tầng — lỗi kiến trúc, không phải phong cách |

## 5. Khi gate đỏ

Thứ tự xử, từ đúng nhất tới tệ nhất:

1. **Tách feature** — file dài/hàm dài gần như luôn là hai việc bị nhét chung.
2. **Đảo điều kiện thành early-return** — hạ cả độ lồng lẫn complexity một lượt.
3. **Gom tham số thành object có kiểu.**
4. **Viết test còn thiếu** — nếu đỏ là coverage. Đây là cách xử đúng, không phải cách cuối.
5. **Disable một dòng kèm lý do** — chỉ khi bốn cách trên làm code khó đọc hơn.
6. ~~Nới ngưỡng trong config~~ — **không**. Nới một lần là nới cho toàn repo (luật #6: không bypass test/lint).
