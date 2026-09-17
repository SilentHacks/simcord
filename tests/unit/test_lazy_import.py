"""`import simcord` must not pull in the optional preview dependencies.

A clean subprocess is required: this test session's own collection may have
already imported PIL/playwright/simcord.preview, which would make an
in-process ``sys.modules`` check meaningless.
"""

import subprocess
import sys


def test_base_import_loads_no_optional_deps():
    code = (
        "import sys\n"
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
