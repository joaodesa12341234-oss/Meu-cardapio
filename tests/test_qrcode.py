"""Confere o gerador de QR code (static/qrcode.js) lendo o resultado de volta com o OpenCV.
Pulado automaticamente se Node.js ou OpenCV não estiverem instalados."""
import json
import shutil
import subprocess

import pytest

from conftest import RAIZ

cv2 = pytest.importorskip("cv2")
np = pytest.importorskip("numpy")
pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="Node.js não instalado")

URLS = ["https://a.co", "https://lanchonete.exemplo.com.br/", "https://cardápio.com/açaí",
        "https://lanchonete-do-joao.exemplo.com.br/cardapio?mesa=12&utm_source=cartaz&x=" + "y" * 80,
        "https://exemplo.com.br/" + "z" * 180]


def test_qr_decodifica():
    js = (RAIZ / "static" / "qrcode.js").read_text(encoding="utf-8")
    prog = js + "\nconst urls=" + json.dumps(URLS) + ";console.log(JSON.stringify(urls.map(u=>gerarQR(u).map(l=>l.map(v=>v?1:0)))));"
    saida = subprocess.run(["node", "-e", prog], capture_output=True, text=True, check=True).stdout
    for url, matriz in zip(URLS, json.loads(saida)):
        m = np.pad(np.array(matriz, dtype=np.uint8), 4)
        img = ((1 - np.kron(m, np.ones((8, 8), dtype=np.uint8))) * 255).astype(np.uint8)
        leitores = [cv2.QRCodeDetector()] + ([cv2.QRCodeDetectorAruco()] if hasattr(cv2, "QRCodeDetectorAruco") else [])
        # o leitor padrão do OpenCV às vezes falha em padrões válidos; basta um dos leitores ler certo
        assert any(l.detectAndDecode(img)[0] == url for l in leitores), url
