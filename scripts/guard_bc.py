#!/usr/bin/env python3
"""guard_bc.py — hook PreToolUse (Bash): chặn deploy khi vòng ≥2 còn nợ tương thích ngược.

VÌ SAO CÓ FILE NÀY
    Từ vòng 2, production có người dùng và dữ liệu thật — legacy là hợp đồng (VIPER.md §1.2).
    `context/BACKWARD-COMPATIBILITY-CHECKLIST.md §3` là danh sách phải rà trước khi deploy;
    gate P1 đếm nó nhưng gate chỉ BÁO. Deploy là hành động phá hợp đồng không thu hồi được
    với client/hệ thống ngoài đang bám vào schema cũ — nên chỗ này cần chặn cứng, cùng
    triết lý với guard_ask: cấm bằng văn xuôi không giữ được luật, nhất là sau compact.

CHẠY NHƯ THẾ NÀO
    Claude Code gọi qua hook PreToolUse matcher "Bash" (xem .claude/settings.json).
    stdin : JSON có tool_input.command
    exit 0: cho qua · exit 2: CHẶN, stderr được đưa lại cho model

    Chỉ soi lệnh deploy (danh sách DEPLOY_PATTERNS — `make deploy` là cổng chính thức
    theo hợp đồng lệnh; `deploy_workflow.py execute` là cổng của pipeline nhiều môi
    trường). Lệnh shell được tách bằng parser bảo thủ (heredoc, `bash -c`, wrapper
    env/sudo, ssh/ống remote) — deploy chưa chứng minh được môi trường thì CHẶN.
    `git push` KHÔNG chặn ở đây: nó đã nằm ở lớp `ask` permission, và chặn push trong
    lúc đang code giữa vòng là ma sát sai chỗ.

    Cổng theo skill — mỗi cổng chỉ chạy khi dự án đã bật skill đó (sổ/cấu hình tồn tại):
      connection-budget  context/perf/CONNECTION-BUDGET.json  → check-connection-budget.py --env
      perf-testing       WORKLOAD.md `"confirmed": true`      → check-perf-report.py --gate --env
      api-contract       context/perf/API-CONTRACT-BASE.json  → check-api-contract.mjs
      tương thích ngược  vòng ≥2: BACKWARD-COMPATIBILITY-CHECKLIST §3 phải tick hết

    Template cũ chưa khai baseline giữ hành vi fail-open khi thiếu STATE/checklist.
    Repo có API-CONTRACT-BASE.json phải qua checker thật, kể cả checklist bị mất;
    checker lỗi/thiếu dependency/sai mốc đều chặn. JSON hook hỏng vẫn fail-open.

Exit codes: 0 cho qua · 2 chặn
"""

from __future__ import annotations

import json
import re
import sys
import subprocess
import os
import shlex
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BC = "context/BACKWARD-COMPATIBILITY-CHECKLIST.md"

# Lệnh coi là deploy. Cố tình hẹp: make deploy (hợp đồng lệnh) + dạng deploy tường minh
# của CLI các PaaS trong kho stack. KHÔNG gồm `vercel dev` / `vercel env pull` (không deploy);
# `vercel` trần đứng cuối lệnh thì tính (mặc định của nó là deploy).
DEPLOY_PATTERNS = (
    # make: quét theo token — mọi thứ đứng giữa `make` và target `deploy` (cờ -j4,
    # -C dir, -Cdir, --directory=dir, biến VAR=val…) đều không thoát được; chỉ chặn
    # khi `deploy` là token trọn vẹn (make deploy-docs không tính). Liệt kê từng
    # dạng cờ là thua thực tế — đường intake đa target chạy make đủ kiểu.
    r"\bmake\b(?:\s+\S+)*?\s+deploy(?![\w.-])",
    r"\bvercel\s+(deploy|--prod)\b",
    r"\bvercel\s*$",
    r"\brailway\s+up\b",
    # fly/flyctl: global flag đứng trước subcommand (fly --app x deploy) vẫn chặn.
    r"\bfly(?:ctl)?\b(?:\s+\S+)*?\s+deploy(?![\w.-])",
    r"\brender\s+deploy\b",
    # Pipeline nhiều môi trường (deployment/DEPLOY-WORKFLOW.md): chỉ `execute` gửi build
    # thật — prepare/dry-run/status chỉ đọc nên không chặn.
    r"\bdeploy_workflow\.py\b(?:\s+\S+)*?\s+execute(?![\w.-])",
)


def executable_commands(command: str) -> list[list[str]]:
    """Nhận lệnh thật; bỏ nội dung heredoc và đối số minh hoạ của echo/printf.

    Đây là parser bảo thủ cho shell, không thực thi expansion. Command substitution
    không phân tích chắc chắn sẽ chặn nếu chứa dấu hiệu deploy.
    """
    lines, pending = [], []
    for line in command.splitlines():
        if pending:
            marker, tabs, executable, expands, body = pending[0]
            if (line.lstrip('\t') if tabs else line) == marker:
                text = '\n'.join(body)
                if any(re.search(p, text) for p in DEPLOY_PATTERNS) and (executable or (expands and ('$' in text or '`' in text))):
                    raise ValueError('Heredoc có deploy chưa chứng minh được môi trường')
                pending.pop(0)
            else:
                body.append(line)
            continue
        if '<<' in line:
            redirections = shlex.shlex(line, posix=False, punctuation_chars='<>&|;')
            redirections.whitespace_split = True
            words = list(redirections)
            for index, token in enumerate(words):
                if token != '<<': continue
                if index+1 >= len(words): raise ValueError('Thiếu delimiter heredoc')
                raw = words[index+1]
                tabs = raw.startswith('-')
                if tabs: raw = raw[1:]
                quoted = raw.startswith(("'", '"'))
                marker = raw[1:-1] if quoted else raw
                head = words[:index]
                executable = bool(head and Path(head[0]).name in ('bash','sh','zsh')
                                  and (len(head)==1 or '-s' in head[1:] or all(w.startswith('-') for w in head[1:])))
                pending.append((marker, tabs, executable, not quoted, []))
        lines.append(line)
    if pending:
        raise ValueError('Heredoc chưa đóng')
    source = '\n'.join(lines)
    if ('$(' in source or '`' in source) and any(re.search(p, source) for p in DEPLOY_PATTERNS):
        raise ValueError('Deploy trong expansion chưa chứng minh được môi trường')
    lexer = shlex.shlex(source, posix=True, punctuation_chars=';&|()\n')
    lexer.whitespace = ' \t\r'
    lexer.whitespace_split = True
    segments, current = [], []
    for token in lexer:
        if token and all(c in ';&|()\n' for c in token):
            if current: segments.append(current)
            current = []
        else: current.append(token)
    if current: segments.append(current)
    commands = []
    for tokens in segments:
        prefix = []
        while tokens:
            if re.match(r'^[A-Za-z_]\w*=', tokens[0]):
                prefix.append(tokens.pop(0)); continue
            if Path(tokens[0]).name in ('env','command','sudo','then','do','if','!'):
                tokens.pop(0)
                if tokens and tokens[0].startswith('-'):
                    if any(re.search(p, ' '.join(tokens)) for p in DEPLOY_PATTERNS):
                        raise ValueError('Wrapper có cờ chưa chứng minh được môi trường deploy')
                    break
                continue
            break
        if not tokens:
            if prefix: commands.append(prefix)
            continue
        tokens = prefix + tokens
        index = len(prefix)
        head = Path(tokens[index]).name
        remote = head == 'ssh' or (head in ('bash','sh') and any(t.endswith('scripts/remote/ub') for t in tokens[index+1:]))
        if remote and any(re.search(p, ' '.join(tokens[index+1:])) for p in DEPLOY_PATTERNS):
            raise ValueError('Deploy qua SSH/pipe chưa chứng minh target và môi trường; không bỏ qua gate')
        if head in ('bash', 'sh', 'zsh') and any(t in ('-c', '-lc') for t in tokens[index+1:]):
            flag = next(i for i,t in enumerate(tokens) if t in ('-c','-lc'))
            if flag+1 < len(tokens): commands.extend(executable_commands(' '.join(tokens[:index])+' '+tokens[flag+1]))
        else: commands.append(tokens)
    return commands


def deploy_commands(command: str) -> list[list[str]]:
    found = []
    commands = executable_commands(command)
    for tokens in commands:
        args = [t for t in tokens if not re.match(r'^[A-Za-z_]\w*=', t)]
        if not args: continue
        head = Path(args[0]).name
        deploy = ((head in ('make','gmake','fly','flyctl','render') and 'deploy' in args[1:])
                  or (head == 'railway' and 'up' in args[1:])
                  or (head == 'vercel' and (len(args)==1 or 'deploy' in args[1:] or '--prod' in args[1:]))
                  or (any(Path(a).name == 'deploy_workflow.py' for a in args) and 'execute' in args))
        if deploy: found.append(tokens)
    if found:
        for tokens in commands:
            if tokens not in found and any(re.match(r'^(ENV|env)=', t) for t in tokens):
                raise ValueError('Môi trường được đổi ở lệnh trước; cần ENV trực tiếp trên deploy')
    return found


def deployment_environment(command: str) -> str:
    commands = deploy_commands(command)
    values = []
    for tokens in commands:
        # scripts/deploy.sh chỉ đọc ENV; env=PT là biến khác và phải sửa lệnh gọi.
        if any(t.startswith('env=') for t in tokens):
            raise ValueError('Deploy dùng ENV viết hoa; env không đổi môi trường runtime')
        supplied = [t.split('=',1)[1].upper() for t in tokens if t.startswith('ENV=')]
        # deploy_workflow.py: --env <tên> | --env=<tên>
        supplied += [tokens[i+1].upper() for i, t in enumerate(tokens[:-1]) if t == '--env']
        supplied += [t.split('=',1)[1].upper() for t in tokens if t.startswith('--env=')]
        if '--prod' in tokens: supplied.append('PROD')
        values.extend(supplied or [os.environ.get('ENV','SIT').upper()])
    if not values or len(set(values)) != 1 or values[0] not in ('SIT','UAT','PREPROD','PROD','PT','LOCAL'):
        raise ValueError('Không xác định duy nhất môi trường deploy')
    return values[0]


def read(rel: str) -> str:
    try:
        return (ROOT / rel).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return ""


def contract_gate() -> int:
    checker = ROOT / "scripts/check-api-contract.mjs"
    # Template cũ không có checker; repo đã khai baseline thì thiếu checker là lỗi.
    config = ROOT / "context/perf/API-CONTRACT-BASE.json"
    if not config.exists():
        return 0
    try:
        base = json.loads(config.read_text())["base"]
        result = subprocess.run(
            ["node", str(checker), "--base", base], cwd=ROOT,
            capture_output=True, text=True, timeout=40,
        )
        if result.returncode == 0:
            return 0
        reason = result.stdout + result.stderr
    except (OSError, ValueError, KeyError, subprocess.TimeoutExpired) as error:
        reason = str(error)
    print("[VIPER guard_bc] CHẶN deploy — chưa chứng minh tương thích hợp đồng:\n" + reason,
          file=sys.stderr)
    return 2


def connection_gate(command: str) -> int:
    checker = ROOT / 'scripts/check-connection-budget.py'
    config = ROOT / 'context/perf/CONNECTION-BUDGET.json'
    if not config.exists():
        return 0  # Dự án chưa bật skill connection-budget (chưa có sổ ngân sách).
    try:
        env = deployment_environment(command)
        result = subprocess.run([sys.executable, str(checker), '--env', env],
                                cwd=ROOT, capture_output=True, text=True, timeout=15)
        if result.returncode == 0:
            return 0
        reason = result.stdout + result.stderr
    except (OSError, ValueError, subprocess.TimeoutExpired) as error:
        reason = str(error)
    print('[VIPER guard_bc] CHẶN deploy — ngân sách kết nối chưa đạt:\n' + reason, file=sys.stderr)
    return 2


def perf_gate(command: str) -> int:
    checker = ROOT / 'scripts/check-perf-report.py'
    # Chỉ canh khi Authority đã chốt WORKLOAD (skill perf-testing bật thật); chưa chốt thì
    # chưa có nghiệm thu nào để đòi — gate P/gate perf-gate tự đỏ ở chỗ khác.
    workload = read('context/perf/WORKLOAD.md')
    if not checker.exists() or not re.search(r'"confirmed"\s*:\s*true', workload):
        return 0
    try:
        env = deployment_environment(command)
        result = subprocess.run([sys.executable, str(checker), '--gate', '--env', env],
                                cwd=ROOT, capture_output=True, text=True, timeout=30)
        if result.returncode == 0:
            return 0
        reason = result.stdout + result.stderr
    except (OSError, ValueError, subprocess.TimeoutExpired) as error:
        reason = str(error)
    print('[VIPER guard_bc] CHẶN deploy — bằng chứng perf chưa đạt:\n' + reason, file=sys.stderr)
    return 2


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        return 0  # fail-open
    command = str((payload.get("tool_input") or {}).get("command") or "")
    if not any(re.search(p, command) for p in DEPLOY_PATTERNS):
        return 0
    try:
        if not deploy_commands(command):
            return 0
    except ValueError:
        print('[VIPER guard_bc] CHẶN: cú pháp shell chưa chứng minh được lệnh/môi trường deploy.', file=sys.stderr)
        return 2

    if connection_gate(command):
        return 2
    if perf_gate(command):
        return 2

    m = re.search(r"Vòng\s*:\s*(\d+)", read("STATE.md"))
    if not m or int(m.group(1)) < 2:
        return contract_gate()  # có mốc hợp đồng thì vẫn phải kiểm

    bc = read(BC)
    if not bc.strip():
        return contract_gate()  # template cũ không khai mốc thì giữ hành vi cũ

    # CHỈ đếm §3 — cùng phạm vi với gate P1 (bc_section3 trong gate.py) và repeat.py
    # bước 6b (bỏ tick §3 khi mở vòng). Đếm cả file thì một checkbox ghi chú ở §1/§4
    # chặn deploy vĩnh viễn mà repeat.py không bao giờ re-arm nó.
    sec3: list[str] | None = None
    for line in bc.splitlines():
        if re.match(r"## 3(?!\d)", line):  # đúng §3 — "## 30. Ghi chú" không tính
            sec3 = []
            continue
        if sec3 is not None and line.startswith("## "):
            break
        if sec3 is not None:
            sec3.append(line)
    if sec3 is None:
        return contract_gate()  # mất khung không được bỏ qua checker hợp đồng

    unticked = [l.strip() for l in sec3 if l.strip().startswith("- [ ]")]
    if not unticked:
        return contract_gate()

    print(
        f"[VIPER guard_bc] CHẶN deploy — vòng ≥2 còn {len(unticked)} mục tương thích ngược "
        f"chưa rà trong {BC} §3:\n"
        + "\n".join(f"  {l}" for l in unticked[:4])
        + ("\n  …" if len(unticked) > 4 else "")
        + "\n\nLegacy là hợp đồng (VIPER.md §1.2). Rà từng mục theo luật §2 của checklist "
        "(đối chiếu sổ hợp đồng §1), tick xong chạy lại. Mục không áp dụng → tick kèm 'n/a'. "
        "Chỗ nào buộc phải phá mà Authority CHƯA chốt ở một trong ba nơi hợp lệ — "
        "tài liệu vòng intake/loops/l<N>/*.md · mục 'Legacy được phép phá' trong "
        "intake/loops/l<N>/_PROPOSAL.md · chốt qua chat ở pha V (meta ghi hộ vào đó) "
        "→ đó là ngoại lệ hỏi thật, dừng lại hỏi Authority.",
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:  # hook KHÔNG BAO GIỜ được làm hỏng phiên
        print(f"guard_bc.py: {e}", file=sys.stderr)
        sys.exit(0)
