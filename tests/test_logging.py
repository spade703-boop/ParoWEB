from __future__ import annotations

import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_application_info_logs_reach_stderr() -> None:
    script = (
        "import logging\n"
        "import app.main\n"
        "logging.getLogger('app.services.mysekai').info('mysekai-ingest-probe')\n"
    )

    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "mysekai-ingest-probe" in result.stderr
