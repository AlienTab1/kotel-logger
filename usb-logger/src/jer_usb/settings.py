"""Validated service configuration, shared by the runtime and installer."""
import ipaddress
import json
from pathlib import Path

DEFAULTS = dict(host='127.0.0.1', port=9109, stale_s=10)


def validate(config):
    if not isinstance(config, dict):
        raise ValueError('Configuration must be a JSON object')
    if set(config) - {'passive_port', 'host', 'port', 'stale_s'}:
        raise ValueError('Unknown configuration keys')
    result = DEFAULTS | config
    device = result.get('passive_port', '')
    if not isinstance(device, str) or not device.startswith('/dev/') or '\n' in device or '\0' in device:
        raise ValueError('passive_port must be an absolute /dev/ path')
    ipaddress.IPv4Address(result['host'])
    for key, low, high in (('port', 1, 65535), ('stale_s', 1, 3600)):
        if type(result[key]) is not int or not low <= result[key] <= high:
            raise ValueError(f'{key} must be an integer in {low}..{high}')
    return result


def load(path):
    return validate(json.loads(Path(path).read_text(encoding='utf-8')))
