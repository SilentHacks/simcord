"""`import simcord` must not pull in the optional preview dependencies.

A clean subprocess is required: this test session's own collection may have
already imported PIL/playwright/simcord.preview, which would make an
in-process ``sys.modules`` check meaningless.
"""

import subprocess
import sys


def test_base_import_loads_no_optional_deps():
    code = (
        "import builtins\n"
        "import sys\n"
        "real_open = builtins.open\n"
        "def audited_open(file, *args, **kwargs):\n"
        "    if 'preview/static/fonts' in str(file):\n"
        "        raise AssertionError('base import read preview font assets')\n"
        "    return real_open(file, *args, **kwargs)\n"
        "builtins.open = audited_open\n"
        "import simcord\n"
        "leaked = {'PIL', 'playwright', 'markdown_it'} & set(sys.modules)\n"
        "assert not leaked, f'optional deps imported eagerly: {leaked}'\n"
        "assert 'simcord.preview' not in sys.modules, 'simcord.preview must stay lazy'\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
