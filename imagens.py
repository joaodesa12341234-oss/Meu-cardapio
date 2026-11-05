"""Fotos dos produtos: converte para WebP (leve para a rede móvel) e gera miniatura.

Cada foto enviada vira dois arquivos em static/uploads/:
    <id>.webp       versão grande (até 1000 px)
    <id>_400.webp   miniatura (até 400 px), usada nos cards do cardápio
O <id> é aleatório; o nome que o usuário deu ao arquivo é ignorado.
"""
import io
import os
import re
import time
import uuid
import warnings
from pathlib import Path

from PIL import Image, ImageOps, UnidentifiedImageError

UPLOADS = Path(os.environ.get("UPLOADS_DIR", Path(__file__).parent / "static" / "uploads"))
URL_BASE = "/static/uploads/"

RE_IMAGEM = re.compile(r"^/static/uploads/([a-f0-9]{32})\.webp$")
RE_LEGADA = re.compile(r"^/static/uploads/([a-f0-9]{32})\.(?:jpg|png|gif|webp)$")
RE_ARQUIVO_WEBP = re.compile(r"^[a-f0-9]{32}(?:_400)?\.webp$")
RE_ARQUIVO = re.compile(r"^([a-f0-9]{32})(?:_400)?\.(?:webp|jpg|png|gif)$")

FORMATOS_ACEITOS = {"JPEG", "PNG", "GIF", "WEBP"}
TAMANHO_MAX_BYTES = 5 * 1024 * 1024
MAX_PIXELS = 40_000_000  # recusa fotos gigantes antes de decodificar (proteção de memória)
LADO_GRANDE = 1000
LADO_MINIATURA = 400
QUALIDADE = 80

Image.MAX_IMAGE_PIXELS = MAX_PIXELS


class ErroImagem(ValueError):
    """Foto recusada; a mensagem pode ser mostrada ao usuário."""


def miniatura_de(url):
    """'/static/uploads/<id>.webp' -> '/static/uploads/<id>_400.webp' ('' se não for foto válida)."""
    achou = RE_IMAGEM.match(url or "")
    return f"{URL_BASE}{achou.group(1)}_400.webp" if achou else ""


def existe(url):
    achou = RE_IMAGEM.match(url or "")
    return bool(achou) and (UPLOADS / f"{achou.group(1)}.webp").is_file()


def _abrir(dados):
    with warnings.catch_warnings():
        warnings.simplefilter("error", Image.DecompressionBombWarning)
        try:
            img = Image.open(io.BytesIO(dados))
        except (Image.DecompressionBombError, Image.DecompressionBombWarning):
            raise ErroImagem("A foto tem dimensões grandes demais. Use uma foto menor.")
        except (UnidentifiedImageError, OSError, ValueError, SyntaxError):
            raise ErroImagem("Envie uma imagem JPG, PNG, WEBP ou GIF.")
    if img.format not in FORMATOS_ACEITOS:
        raise ErroImagem("Envie uma imagem JPG, PNG, WEBP ou GIF.")
    if img.width * img.height > MAX_PIXELS:
        raise ErroImagem("A foto tem dimensões grandes demais. Use uma foto menor.")
    return img


def _preparar(img):
    """Decodifica, endireita pela orientação do celular e padroniza o modo de cor."""
    try:
        img.load()
        img = ImageOps.exif_transpose(img)  # foto tirada em pé não fica deitada
    except (OSError, SyntaxError, ValueError, Image.DecompressionBombError):
        raise ErroImagem("Não foi possível ler esta foto. Ela pode estar corrompida.")
    transparente = img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info)
    return img.convert("RGBA" if transparente else "RGB")  # também descarta EXIF/GPS ao regravar


def _salvar(img, lado, destino):
    copia = img.copy()
    copia.thumbnail((lado, lado), Image.Resampling.LANCZOS)  # só reduz, nunca amplia
    copia.save(destino, "WEBP", quality=QUALIDADE, method=4)


def processar(dados):
    """Valida os bytes enviados, grava as duas versões WebP e devolve a URL da grande."""
    if len(dados) > TAMANHO_MAX_BYTES:
        raise ErroImagem("A foto passa de 5 MB.")
    img = _preparar(_abrir(dados))
    UPLOADS.mkdir(parents=True, exist_ok=True)
    nome = uuid.uuid4().hex
    grande, mini = UPLOADS / f"{nome}.webp", UPLOADS / f"{nome}_400.webp"
    try:
        _salvar(img, LADO_GRANDE, grande)
        _salvar(img, LADO_MINIATURA, mini)
    except Exception:
        grande.unlink(missing_ok=True)
        mini.unlink(missing_ok=True)
        raise ErroImagem("Não foi possível converter esta foto.")
    return f"{URL_BASE}{nome}.webp"


def converter_legada(url):
    """Foto de versões antigas (.jpg/.png...): converte para WebP. Devolve a nova URL ou ''."""
    achou = RE_LEGADA.match(url or "")
    if not achou:
        return ""
    arquivo = next((a for a in UPLOADS.glob(f"{achou.group(1)}.*") if a.is_file()), None)
    if arquivo is None:
        return ""
    try:
        return processar(arquivo.read_bytes())  # o original fica intacto: migração nunca apaga dados
    except (ErroImagem, OSError):
        return ""


def apagar(url):
    achou = RE_IMAGEM.match(url or "")
    if achou:
        for nome in (f"{achou.group(1)}.webp", f"{achou.group(1)}_400.webp"):
            (UPLOADS / nome).unlink(missing_ok=True)


def limpar_orfas(urls_em_uso, idade_minima=3600):
    """Apaga fotos enviadas que nenhum produto usa (e com mais de 1 hora, para não
    apagar uma foto que acabou de subir e ainda não foi salva no produto)."""
    if not UPLOADS.is_dir():
        return
    ids_em_uso = {m.group(1) for u in urls_em_uso if (m := RE_IMAGEM.match(u or ""))}
    agora = time.time()
    for arq in UPLOADS.iterdir():
        achou = RE_ARQUIVO.match(arq.name)
        if (achou and arq.is_file() and achou.group(1) not in ids_em_uso
                and agora - arq.stat().st_mtime > idade_minima):
            arq.unlink(missing_ok=True)
