"""Lancia i test di ogni agente nella sua cartella, uno alla volta.

Gli agenti hanno moduli con lo stesso nome (giro.py, modello.py): provati insieme
si pestano i piedi, provati separati no. Ogni cartella ha la sua rete di sicurezza
(conftest.py) che blocca account e posta veri.
"""
import subprocess
import sys
from pathlib import Path

import pytest

AGENTI = Path(__file__).resolve().parent.parent / "agenti"
CON_TEST = sorted(p for p in AGENTI.iterdir() if (p / "tests").is_dir())


@pytest.mark.parametrize("cartella", CON_TEST, ids=lambda p: p.name)
def test_agente(cartella):
    r = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider"],
                       cwd=cartella, capture_output=True, text=True, timeout=600)
    assert r.returncode == 0, (r.stdout + r.stderr)[-2000:]
