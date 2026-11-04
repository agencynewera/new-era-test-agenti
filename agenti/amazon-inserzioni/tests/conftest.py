import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import pytest


@pytest.fixture(autouse=True)
def _niente_rete_vera(monkeypatch):
    """Rete di sicurezza (memoria rete-di-sicurezza-nei-test, 20/09/2026): nessun
    test di questa skill puo' raggiungere Amazon o LWA. Il blocco sta sul
    trasporto vero di requests, sotto ai Mock che i test iniettano sul client
    o su requests.get/post nel modulo: un finto passa, una chiamata vera no."""
    import requests.adapters

    def _vietato(self, request, *args, **kwargs):
        raise RuntimeError(f"rete vietata nei test: {request.method} {request.url}")

    monkeypatch.setattr(requests.adapters.HTTPAdapter, "send", _vietato)
