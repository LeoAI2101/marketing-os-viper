# {{PROJECT_NAME}} — hợp đồng lệnh VIPER
#
# Sáu lệnh dưới đây là giao diện chung của MỌI stack. Quy trình VIPER và scripts/gate.py
# chỉ gọi các lệnh này, không cần biết bên dưới là Next.js hay Spring Boot.
#
# Pha I: mở .claude/skills/stack-<tên>/SKILL.md §4 và điền phần thân cho từng target.
# Dòng đầu của `check` là máy kiểm luật #7 (scripts/check_lang.py) — đứng TRƯỚC mọi lời
# uỷ quyền xuống target con, và không được gỡ: nó là nửa còn lại của hook check_lang.
# Giữ nguyên TÊN target — đổi tên là phá hợp đồng, gate.py sẽ không chạy được.
#
# Artifact để CHẠY nằm ở deployment/, không rải ra gốc repo (deployment/README.md):
#   make dev  → docker compose -f deployment/local/docker-compose.yml up -d db
#   biến env  → deployment/.env.example (danh sách) · deployment/local/.env (giá trị)
# Đa target (đường intake): thân lệnh điều phối xuống `make -C srcroot/<nhóm>/<tên> …`;
# deploy LUÔN đi qua `make deploy` ở gốc — đó là chỗ hook guard_bc đứng.

.PHONY: help dev check test migrate deploy doctor gate

STACK_SKILL ?= chưa-chốt
PYTHON ?= python3

# Nhóm kiểm PT (skill perf-testing) TÁCH KHỎI check/test thường: mặc định không chạy.
#   make check-pt / make test-pt       chỉ chạy nhóm PT
#   make check PT=1 / make test PT=1   chạy luồng thường, đạt rồi mới thêm nhóm PT
PT ?= 0
ifneq ($(filter-out 0 1,$(PT)),)
$(error PT chỉ nhận 0 hoặc 1)
endif
# Checker của các skill dữ liệu/hiệu năng (NestJS + Prisma, đọc srcroot/boundaries/*). Skill nào
# `n/a` với dự án thì gỡ khỏi danh sách và ghi một dòng DECISIONS — còn trong danh sách mà chưa
# có sổ/cấu hình thì checker trả 2 (chưa kiểm), nhóm PT đỏ: đúng ý, không phải lỗi.
PT_CHECK_TARGETS ?= check-api-contract check-query-safety check-data-security check-data-lifecycle check-connection-budget
PT_TEST_TARGETS  ?= test-pt-tools

help:
	@echo "{{PROJECT_NAME}} — VIPER"
	@echo ""
	@echo "  make dev      chạy local (app + db)"
	@echo "  make check    lint + typecheck + build"
	@echo "  make test     smoke + luồng quan trọng"
	@echo "  make migrate  DB migration có version"
	@echo "  make deploy   đẩy lên PaaS"
	@echo "  make doctor   kiểm env + kết nối, in cái gì thiếu"
	@echo ""
	@echo "  make gate     kiểm gate của pha hiện tại (hoặc: make gate P=I)"
	@echo ""
	@echo "  bật ống remote: touch .local/remote/.remote-on   (scripts/remote/README.md)"
	@echo ""
	@echo "  make check-pt / make test-pt       chỉ chạy nhóm kiểm PT"
	@echo "  make check PT=1 / make test PT=1   chạy thường rồi thêm nhóm PT"
	@echo "  make check-query-safety | check-data-security | check-data-lifecycle   [target=… file=…]"
	@echo "  make check-connection-budget env=<SIT|UAT|…>  ·  make check-api-contract [base=<git-ref>]"
	@echo "  make perf-regression baseline=A.json current=B.json   so hồi quy ba lượt"
	@echo "  make perf-evidence report=<gói.json>                  xác minh gói bằng chứng"
	@echo "  make perf-gate env=<SIT|UAT|…>                        canh bằng chứng trước deploy"
	@echo ""
	@echo "Stack skill: $(STACK_SKILL)"

define NOT_IMPLEMENTED
	@echo ""
	@echo "  ✗ 'make $@' chưa được hiện thực."
	@echo ""
	@echo "  Pha I chưa xong. Mở .claude/skills/stack-<tên>/SKILL.md §4"
	@echo "  rồi điền phần thân cho target '$@' trong Makefile này."
	@echo ""
	@echo "  Stack đã chốt ở context/TECHSTACK.md: $(STACK_SKILL)"
	@echo ""
	@exit 1
endef

# ═══ Chế độ remote (scripts/remote/README.md) ═══════════════════════════════
# `.local/remote/.remote-on` có mặt = bật: dev/check/test/migrate/doctor đẩy code sang MÁY
# CHẠY qua ống rồi chạy ở đó. Xoá file = về chạy local. RP_LOCAL=1 do run.sh đặt ở phía máy
# chạy để CẮT ĐỆ QUY. `deploy` KHÔNG đi qua ống — chạy ở đây, nơi hook guard_bc đứng.
# Thân LOCAL đứng TRƯỚC: gate.py đọc recipe đầu tiên của mỗi target để biết đã hiện thực chưa.
REMOTE := $(and $(wildcard .local/remote/.remote-on),$(if $(RP_LOCAL),,1))

ifneq ($(REMOTE),1)
dev:
	$(NOT_IMPLEMENTED)

check:
	@$(PYTHON) scripts/check_lang.py check
	$(NOT_IMPLEMENTED)
ifeq ($(PT),1)
	@$(MAKE) --no-print-directory check-pt
endif

test:
	$(NOT_IMPLEMENTED)
ifeq ($(PT),1)
	@$(MAKE) --no-print-directory test-pt
endif

migrate:
	$(NOT_IMPLEMENTED)

doctor:
	$(NOT_IMPLEMENTED)

else

dev:
	@bash scripts/remote/run.sh dev

check:
	@$(PYTHON) scripts/check_lang.py check
	@bash scripts/remote/run.sh check

test:
	@bash scripts/remote/run.sh test

migrate:
	@bash scripts/remote/run.sh migrate

doctor:
	@bash scripts/remote/run.sh doctor

endif

deploy:
	$(NOT_IMPLEMENTED)

# --- nhóm PT (skill perf-testing) — chỉ đo và kiểm, không seed, không phát tải ---
.PHONY: check-pt test-pt test-pt-tools perf-regression perf-evidence perf-gate
.PHONY: check-query-safety check-data-security check-data-lifecycle check-connection-budget check-api-contract

check-pt:
	@bash scripts/perf/pt-gates.sh check $(PT_CHECK_TARGETS)

test-pt:
	@bash scripts/perf/pt-gates.sh test $(PT_TEST_TARGETS)

# Hồi quy bộ công cụ (checker, hook, perf, deploy) — không seed, không mở DB/container, không phát tải.
test-pt-tools:
	@py=0; js=0; \
	 $(PYTHON) -m unittest discover -s scripts/tests -p 'test_*.py' || py=$$?; \
	 node --test scripts/tests/*.test.mjs || js=$$?; \
	 test $$py -eq 0 -a $$js -eq 0

# Checker của skill — mỗi cái exit 0 đạt · 1 đỏ · 2 chưa kiểm được (không bao giờ là xanh).
check-query-safety:
	@node scripts/check-query-safety.mjs $(if $(target),--target "$(target)") $(if $(file),--file "$(file)")

check-data-security:
	@node scripts/check-data-security.mjs $(if $(target),--target "$(target)") $(if $(file),--file "$(file)")

check-data-lifecycle:
	@$(PYTHON) scripts/check-data-lifecycle.py $(if $(file),--file "$(file)")

check-connection-budget:
	@$(PYTHON) scripts/check-connection-budget.py --env "$(or $(env),$(ENV),SIT)" $(if $(observation),--observation "$(observation)")

check-api-contract:
	@API_CONTRACT_BASE="$(base)" node scripts/check-api-contract.mjs

perf-regression:
	@$(PYTHON) scripts/check-perf-report.py --baseline "$(baseline)" --current "$(current)"

perf-evidence:
	@$(PYTHON) scripts/perf/evidence.py --input "$(report)" $(args)

perf-gate:
	@$(PYTHON) scripts/check-perf-report.py --gate --env "$(env)" $(if $(report),--current "$(report)")

# --- không đổi ---

gate:
	@$(PYTHON) scripts/gate.py $(P)
