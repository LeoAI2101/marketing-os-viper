"""Đối chiếu tên key và ghim phiên bản SM trước khi gửi Jenkins."""
import json
from deploy_manifest import PLATFORM, TOOLING
from private_config import secret_value


def snapshot(sm, cfg, env, values, naming, platform=PLATFORM):
    result = {}
    for target, local in values.items():
        name = naming(cfg, target, env)
        payload, version = secret_value(sm, name)
        current = json.loads(payload)
        if not isinstance(current, dict):
            raise ValueError('Payload SM phải là JSON object.')
        incoming = {k: v for k, v in local.items() if v != '' and k not in (*platform, *TOOLING)}
        added = sorted(set(incoming) - set(current))
        changed = sorted(k for k in incoming if k in current and str(current[k]) != str(incoming[k]))
        result[target] = {'name': name, 'version': version, 'added': added, 'changed': changed}
    return result

