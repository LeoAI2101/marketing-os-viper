#!/usr/bin/env python3
"""Hook PreToolUse (Write|Edit|MultiEdit): một cổng cho các bộ luật ghi code của skill dữ liệu.

Bộ luật (mỗi bộ nhận cùng payload, chỉ tính lỗi MỚI so với bản trên đĩa — nợ cũ không chặn):

  lifecycle_check  CHẶN  thêm model vào schema Prisma mà sổ context/DATA-LIFECYCLE.md thiếu dòng (skill data-lifecycle)
  security_check   CHẶN  SQL Unsafe / nối chuỗi, log thân request hay trường nhạy cảm (skill data-security)
  query_check      CẢNH BÁO  findMany thiếu trần — không chặn, trả additionalContext (skill query-safety)

Chỉ soi `srcroot/boundaries/**` và sổ lifecycle; file khác đi thẳng. Bộ luật chưa có checker
(dự án chưa bật skill) không chạy — nhưng checker CÓ mà chạy hỏng thì chặn, không giả kết quả
an toàn. Thêm bộ luật mới: thêm vào CHECKERS, không đăng ký matcher trùng ở settings.json.
Design system có hook riêng (scripts/guard_ds.py).
"""
from __future__ import annotations

import json
import sys

from lib import lifecycle_check, query_check, security_check

CHECKERS = (lifecycle_check.check, security_check.check, query_check.check)
STRICT = (lifecycle_check.check, security_check.check)


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        return 0
    if not isinstance(payload, dict) or not isinstance(payload.get('tool_input') or {}, dict):
        return 0
    for check in CHECKERS:
        try:
            result = check(payload)
        except Exception:
            if check in STRICT:
                print('[VIPER guard_write] CHẶN: checker dữ liệu không hoàn thành; không in payload.', file=sys.stderr)
                return 2
            raise
        if result:
            return result
    return 0


def cli() -> int:
    try:
        return main()
    except Exception as error:  # bộ luật cảnh báo hỏng không được làm hỏng phiên
        print(f"guard_write.py: {error}", file=sys.stderr)
        return 0


if __name__ == "__main__":
    sys.exit(cli())
