"""Duyệt input không tham số của đúng build đang theo dõi; không gửi chat."""
import contextlib
import re
import threading
import time
from urllib.parse import urljoin, urlparse

# Mặc định duyệt NGAY và song song (spacing=0, không khoá). Nếu GitLab/repo ArgoCD quá tải khi cả lượt
# được duyệt cùng lúc (đo 2026-10-12: duyệt dồn 15 build → 9/17 đỏ với 503, "Internal API unreachable",
# "Push rejected after 5 attempts"), đặt `approve_spacing_seconds` > 0 trong deploy-manifest.json:
# các lần duyệt xếp hàng qua một khoá chung, cách nhau chừng đó giây.
_approve_lock = threading.Lock()


def proceed_url(build_url, item):
    """URL duyệt input không tham số của ĐÚNG build này; None nếu proceedUrl lạ.

    wfapi trả hai dạng: `input/<id>/proceedEmpty` (cũ) hoặc `wfapi/inputSubmit?inputId=<id>`
    (Jenkins đang dùng). Dạng sau luôn quy về endpoint chuẩn `input/<id>/proceedEmpty`.
    """
    input_id = item.get('id', '')
    url = urljoin(build_url, item.get('proceedUrl', ''))
    if urlparse(url).netloc != urlparse(build_url).netloc or not re.fullmatch(r'[A-Za-z0-9_-]+', input_id):
        return None
    if url.startswith(build_url + 'input/') and url.endswith('/proceedEmpty'):
        return url
    if url == f'{build_url}wfapi/inputSubmit?inputId={input_id}':
        return f'{build_url}input/{input_id}/proceedEmpty'
    return None


def notice(build_url, input_id, seen):
    key = (build_url, input_id)
    if key not in seen:
        print('Build đang chờ duyệt. Vui lòng ping dev lead qua chat để approve: ' + build_url)
        seen.add(key)


def check_inputs(cfg, build_url, seen, requests, crumb, allowed=(), spacing=0):
    """Không retry POST không rõ kết quả; vòng sau chỉ GET trạng thái input."""
    response = requests.get(build_url + 'wfapi/pendingInputActions',
                            auth=(cfg['JENKINS_USER'], cfg['JENKINS_TOKEN']), timeout=30)
    if response.status_code in (401, 403, 404):
        if response.status_code != 404:
            notice(build_url, 'quyền-đọc-input', seen)
        return
    response.raise_for_status()
    for item in response.json():
        input_id = item.get('id', '')
        key = (build_url, input_id, 'attempt')
        url = proceed_url(build_url, item)
        # wfapi trả danh sách tham số ở `inputs` (rỗng = không cần dữ liệu).
        if not url or item.get('parameters') or item.get('inputs') or (allowed and input_id not in allowed):
            notice(build_url, input_id, seen)
            continue
        if key in seen:
            notice(build_url, input_id, seen)
            continue
        seen.add(key)
        try:
            with _approve_lock if spacing else contextlib.nullcontext():
                reply = requests.post(url, auth=(cfg['JENKINS_USER'], cfg['JENKINS_TOKEN']),
                                      headers=crumb(cfg), timeout=30, allow_redirects=False)
                if spacing:
                    time.sleep(spacing)
            redirect = urljoin(url, reply.headers.get('Location', '')) if reply.status_code in (302, 303) else ''
            accepted = reply.status_code in (200, 201) or (reply.status_code in (302, 303)
                       and redirect.startswith(build_url))
            if accepted:
                print(f'✓ đã approve input {input_id}: {build_url}')
            else:
                notice(build_url, input_id, seen)
        except requests.RequestException:
            notice(build_url, input_id, seen)


def notify_pending(cfg, build_url, seen, requests):
    """Chỉ GET: build đang chờ duyệt thì in link một lần, KHÔNG approve (PROD do người có quyền duyệt tay)."""
    try:
        response = requests.get(build_url + 'wfapi/pendingInputActions',
                                auth=(cfg['JENKINS_USER'], cfg['JENKINS_TOKEN']), timeout=30)
        if response.status_code != 200:
            return
        for item in response.json():
            notice(build_url, item.get('id', ''), seen)
    except (requests.RequestException, ValueError):
        return
