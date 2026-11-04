"""
Server log: the level from LOG_LEVEL, and the log file in Settings (where it is, how much
space it takes and how it rotates).
"""
import logging
import os
import subprocess
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

import pytest

from utils import format_size, server_log_info

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def log_file(tmp_path):
    """A log file like the one serve.py sets up (10 MB, 5 old files), with two rotations."""
    path = tmp_path / 'logs' / 'errorengine.log'
    path.parent.mkdir()
    handler = RotatingFileHandler(path, maxBytes=10 * 1024 * 1024, backupCount=5, encoding='utf-8')
    path.write_bytes(b'x' * 1000)
    (tmp_path / 'logs' / 'errorengine.log.1').write_bytes(b'x' * 2000)
    (tmp_path / 'logs' / 'errorengine.log.2').write_bytes(b'x' * 3000)
    (tmp_path / 'logs' / 'other.log').write_bytes(b'x' * 50000)   # not ours: not counted
    root = logging.getLogger()
    root.addHandler(handler)
    try:
        yield path
    finally:
        root.removeHandler(handler)
        handler.close()


def test_sizes_are_readable():
    assert format_size(0) == '0 B'
    assert format_size(512) == '512 B'
    assert format_size(1536) == '1.5 KB'
    assert format_size(250_000) == '244 KB'
    assert format_size(10 * 1024 * 1024) == '10 MB'
    assert format_size(5 * 1024 ** 3) == '5.0 GB'


def test_log_file_with_its_rotated_copies(log_file):
    info = server_log_info()
    assert info['path'] == str(log_file)
    assert info['files'] == 3
    assert info['size'] == format_size(6000) == '5.9 KB'
    assert info['rotation'] == 'new file every 10 MB, the last 5 kept'


def test_settings_show_the_log_file(app, client, log_file):
    html = client.get('/settings').get_data(as_text=True)
    block = html[html.index('id="serverLog"'):html.index('Run cleanup now')]
    assert 'Server log' in block
    assert '5.9 KB' in block
    assert '3 files · new file every 10 MB, the last 5 kept' in block
    assert str(log_file) in block


def test_settings_without_a_log_file(app, client):
    # Tests, like the development server, log only to the console
    assert server_log_info() is None
    html = client.get('/settings').get_data(as_text=True)
    assert 'Console only: the log file is written when ErrorEngine runs with serve.py' in html


def test_a_plain_file_without_rotation(tmp_path):
    path = tmp_path / 'server.log'
    handler = logging.FileHandler(path, encoding='utf-8')
    root = logging.getLogger()
    root.addHandler(handler)
    try:
        info = server_log_info()
    finally:
        root.removeHandler(handler)
        handler.close()
    assert info['files'] == 1 and info['rotation'] == 'no rotation, the file keeps growing'


def test_log_level_from_env_is_applied():
    env = dict(os.environ, LOG_LEVEL='warning')
    out = subprocess.run([sys.executable, '-c', 'import serve, logging; print(logging.getLogger().level)'],
                         cwd=ROOT, env=env, capture_output=True, text=True, timeout=60)
    assert out.stdout.strip().splitlines()[-1] == '30', out.stderr
