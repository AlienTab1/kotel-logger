import os
import subprocess
import sys


def test_help_works_with_windows_console_encoding():
    env = dict(os.environ, PYTHONIOENCODING="cp1250")
    result = subprocess.run([sys.executable, "-m", "kotel_reader", "--help"], env=env, capture_output=True)
    assert result.returncode == 0, result.stderr
    assert b"Prometheus + MQTT" in result.stdout
    assert b"snapshot" in result.stdout
    assert b"migrate" not in result.stdout
