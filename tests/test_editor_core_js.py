# Testes das funcoes JavaScript do editor (editor-core.js) via Node.js.
# Sem Node na maquina, os testes sao marcados como skip (limitacao registrada).

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "tests" / "js" / "editor_core.test.mjs"


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js nao disponivel")
def test_editor_core_js():
    result = subprocess.run(
        ["node", str(SCRIPT)], capture_output=True, text=True, timeout=60, cwd=ROOT
    )
    assert result.returncode == 0, f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    assert "TODOS OK" in result.stdout
