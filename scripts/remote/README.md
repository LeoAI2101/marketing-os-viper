# `scripts/remote/` — chạy lệnh ở máy khác qua MỘT đường ống SSH

Hai đầu:

| | **Máy giữ code** (đầu chờ) | **Máy chạy** |
|---|---|---|
| Là gì | Máy có repo này, git, Claude Code | ideapad hoặc máy Linux/WSL bất kỳ |
| Chạy gì | Soạn code, `git`, hook `guard_*`, `make deploy` | docker, cụm local, build/test, checker cần `node_modules` |
| Cần gì | `sshd` + `rsync` + repo | `tmux` `rsync` `make` `python3` `openssh-server` (+ công cụ của stack) |

```
   MÁY CHẠY (Linux/WSL)                           MÁY GIỮ CODE (đầu chờ)
   ssh -N -R <ctl>:127.0.0.1:22 … ────── MỘT KẾT NỐI ──────►  sshd
                                                    127.0.0.1:<ctl> ở đây = sshd của máy chạy
```

Máy chạy thường nằm sau NAT/firewall, nên ống **mọc từ máy chạy** bằng `-R`. Máy giữ code chỉ
cần chờ. Mỗi máy chạy có **một cổng ctl riêng**, nên nhiều máy có thể sống song song; đổi máy chỉ
là đổi con trỏ (`use`).

## 1. File

| File | Chạy ở | Việc |
|---|---|---|
| `pipe.sh` | **máy chạy** | Thứ duy nhất gõ ở đó: `--install` một lần, sau đó chạy trần mỗi ngày |
| `_map.sh` | máy giữ code | Nạp bản đồ cổng, dựng `RP_SSH`. Mọi script khác source nó |
| `ub` | máy giữ code | `scripts/remote/ub '<lệnh>'`: thực thi ở máy chạy |
| `sync.sh` | máy giữ code | rsync **một chiều** sang máy chạy (`--watch` để lặp; `PT=1` kèm snapshot mốc hợp đồng) |
| `run.sh` | máy giữ code | sync rồi chạy `dev/check/test/migrate/doctor` ở máy chạy. Makefile gọi cái này |
| `pull-generated.sh` | máy giữ code | Kéo ngược allowlist hẹp: hợp đồng, client và migration sinh máy, để commit |
| `doctor.sh` | máy giữ code | Soi từng chặng của **ống** (soi cụm là việc của `make doctor`) |
| `use` | máy giữ code | Liệt kê hoặc chọn máy chạy đang ở đầu ống |

Bản đồ nằm ở máy giữ code, ngoài repo: `~/.config/remote-pipe/<slug>.<máy>.map` và con trỏ
`<slug>.active`. `<slug>` là tên thư mục repo.

## 2. Dựng lần đầu

**Ở máy chạy**, làm một lần cho mỗi tài khoản:

```bash
# 0. công cụ
sudo apt install -y tmux rsync build-essential python3 openssh-server

# 1. khoá vào máy giữ code (nếu chưa có), rồi thử: ssh -p <cổng> <user>@<máy-giữ-code> true
ssh-copy-id -p <cổng> <user>@<máy-giữ-code>

# 2. lối vào máy giữ code — DANH SÁCH, thử lần lượt (trong LAN trước, ngoài LAN sau)
mkdir -p ~/.config/remote-pipe
cat > ~/.config/remote-pipe/config <<'CFG'
RP_HOST_CANDIDATES="<user>@<ip-lan> <user>@<tên-miền-ngoài>:<cổng>"
# RP_SSH_KEY="$HOME/.ssh/<khoá>"        # nếu không dùng ~/.ssh/id_ed25519
CFG

# 3. dựng ống — RP_REPO_HOST là đường dẫn repo ở máy giữ code, tính từ $HOME của tài khoản đó
ssh -p <cổng> <user>@<máy-giữ-code> 'cat <đường/repo>/scripts/remote/pipe.sh' \
  | RP_REPO_HOST=<đường/repo> bash -s -- --install
```

`--install` làm bảy bước: tiền kiểm, chọn lối vào, trao đổi khoá hai chiều, tạo `~/<slug>`, cấp cổng
ctl (bind thật để né cổng của người khác), ghi bản đồ vào máy giữ code, mở ống trong tmux rồi kéo
code lần đầu.

**Ở máy giữ code**:

```bash
mkdir -p .local/remote && touch .local/remote/.remote-on     # bật chế độ remote
bash scripts/remote/doctor.sh                                # mọi chặng phải xanh
```

**Mỗi ngày, ở máy chạy:** `bash ~/.config/remote-pipe/<slug>.pipe.sh`. tmux không sống qua lần
tắt máy, nên mở máy lại là phải gõ lệnh này.

Biến tuỳ chọn khi `--install`:

| Biến | Dùng khi |
|---|---|
| `RP_REPO_RUNNER=<đường>` | Repo ở máy chạy không đặt tại `~/<slug>`. WSL: **đừng** đặt dưới `/mnt/` (đi đường 9p, chậm hàng chục lần) |
| `RP_RUNNER_SSHD_PORT=<cổng>` | `sshd` của máy chạy không nghe ở 22, ví dụ WSL đã có Windows giữ cổng 22 |
| `RP_PROFILE=<tên>` | Một user mở nhiều ống bằng nhiều tài khoản. Mỗi profile có config, tmux và bản sao repo riêng |
| `RP_BASE=<cổng>` | Muốn cấp cổng ctl từ một mốc khác. Mặc định suy từ uid |

## 3. Dùng

Bật `.local/remote/.remote-on` thì các lệnh hợp đồng quen thuộc chạy ở máy chạy, gõ y như cũ:

| Lệnh ở máy giữ code | Chạy ở đâu |
|---|---|
| `make dev` | Máy chạy, trong `tmux -L <slug>`, log tail về đây (Ctrl-C thoát tail, cụm vẫn chạy) |
| `make check` | `check_lang` chạy ở đây, rồi sync sang và `make check` ở máy chạy; kéo artifact sinh máy về, **lệch git là đỏ** |
| `make test` · `make migrate` · `make doctor` | Máy chạy |
| `make check-pt` · `make test-pt` (hoặc `PT=1`) | Máy chạy (`scripts/perf/pt-gates.sh`, sync kèm snapshot mốc hợp đồng) |
| `make deploy` | **Ở đây.** Không đi qua ống, vì hook `guard_bc` đứng ở đây |
| Checker AST (`check-query-safety`, `check-data-security`, `check-api-contract`) | Ở đây nếu có `node_modules`; không có thì tự đẩy bundle qua ống |
| Lệnh bất kỳ | `scripts/remote/ub 'RP_LOCAL=1 make <target>'` |

Xoá `.local/remote/.remote-on` là về chạy local. `RP_LOCAL=1` (do `run.sh` đặt) cắt đệ quy ở phía máy chạy.

Mọi lệnh kết thúc được đều in đúng **một dòng kết** ra stderr rồi thoát đúng mã: `✅ … XONG ở <máy> — Ns`
hoặc `❌ … DỪNG (mã N)`. Gọi `ub` trong vòng lặp thì đặt `UB_QUIET=1`.

## 4. Bốn quyết định không được đảo

1. **Đồng bộ một chiều.** Máy giữ code là nguồn duy nhất; sync ngược sẽ ăn mất thay đổi. Thứ sinh
   máy cần vào git thì kéo về bằng `pull-generated.sh` (allowlist), không sync ngược cả cây.
2. **Không lấy `.gitignore` làm exclude-list.** Danh sách ở `sync.sh` viết tay. Env của máy chạy
   (`deployment/local/.env*`) **ở lại máy chạy**: cổng và mật khẩu local khác nhau từng máy. Nếu dự án
   đã có cụm local mà máy chạy thiếu env thì `sync.sh` **dừng hẳn**.
3. **Không sửa cổng của project.** Service ở máy chạy giữ nguyên cổng repo khai; `localhost` trong env
   đúng vì mọi thứ gọi nhau đều ở máy chạy.
4. **Không bịa lệnh mới.** `gate.py` chỉ biết 6 lệnh hợp đồng. Lớp remote chỉ **bọc** chúng.

## 5. Ghim host key theo TÊN MÁY

`_map.sh` dùng `-o HostKeyAlias=rp-<tên-máy-chạy>`, **không** ghim theo `127.0.0.1:<ctl>`. Cổng ctl là
cửa ống: hôm nay dẫn tới máy A, mai tới máy B. Ghim theo địa chỉ thì đổi máy sẽ gặp
`REMOTE HOST IDENTIFICATION HAS CHANGED`, vì `accept-new` chỉ nhận host mới, không nhận host đổi.

## 6. Khi hỏng

| Triệu chứng | Nguyên nhân thường gặp |
|---|---|
| `✗ Chưa chọn được máy chạy nào` | Chưa `--install`, hoặc nhiều máy mà chưa `scripts/remote/use <tên>` |
| `doctor`: cổng ctl không có listener | Ống rớt. Ở máy chạy gõ `bash ~/.config/remote-pipe/<slug>.pipe.sh` |
| `pipe.sh`: `no matching host key type` | Địa chỉ trong `RP_HOST_CANDIDATES` dẫn tới máy KHÁC (router…), không phải sai cổng |
| `npm: command not found` qua ống | Node cài bằng nvm ở `.bashrc`; `run.sh` đã thêm bin nvm vào PATH, máy chạy vẫn cần có node |
| Máy chạy báo `permission denied /var/run/docker.sock` | User chưa vào nhóm docker lúc tmux server khởi động. `run.sh` dùng socket tmux riêng; đăng xuất/đăng nhập lại máy chạy |

Bản này rút từ lớp remote của driverplus-viper. Các phần riêng của DriverPlus không mang sang: trình
duyệt/mobile qua ống, emulator, APK, Kong, mirror.
