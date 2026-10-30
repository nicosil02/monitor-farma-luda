"""Reputacion: abre cada mencion de LUDA, confirma que LUDA aparece en el
texto, saca la cita y evalua tono, rol y riesgo PARA LUDA.

Tono por reglas, no por modelo. Medido 2026-10-29 sobre 22 menciones reales
de LUDA + 6 casos de prueba con negativos (no hay negativos reales):
  reglas                              21/28, 0 negativos falsos, 5/5 negativos detectados
  xlm-roberta-large-xnli (zero-shot)   8/22, 10 negativos falsos
  mDeBERTa-v3 xnli multilingue        11/28, 15 negativos falsos
  Qwen2.5-1.5B-Instruct (local)       10/28, 16 negativos falsos
Los modelos confunden "la noticia es mala" (escasez) con "es mala para LUDA",
y casi todas las menciones son LUDA dando datos de escasez. Los negativos de
prueba los escribi yo junto con la lista: re-medir cuando aparezca uno real.
"""
from __future__ import annotations

import html
import re
import unicodedata

import requests

_UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120 Safari/537.36"}
_P = re.compile(r"<p\b[^>]*>(.*?)</p>", re.DOTALL | re.I)
_LUDA = re.compile(r"\bluda\b", re.I)
VENTANA = 8   # palabras alrededor de "luda": la critica tiene que ser A LUDA

# Sin "demanda" ni "critica" sueltas: "demanda de medicamentos", "situacion critica".
NEG = re.compile(r"\b(denunci|critican|criticas? (?:a|contra|de)|sancion|multa|expediente|investigacion (?:a|contra|sobre)"
                 r"|demandan?(?: a| contra)|querella|fallos?\b|caida del servicio|errores|polemic|quejas?|cuestionan|acusa"
                 r"|abusiv|irregularidad|ilegal|fraude|filtracion|brecha de (?:datos|seguridad)|sin (?:su )?consentimiento"
                 r"|despid|concurso de acreedores|boicot)")
POS = re.compile(r"\b(ronda|financiacion|capta|levant|alianza|acuerdo|lanza|pioner|innova|permite|anticip|predic|herramienta"
                 r"|solucion|ventaja|premio|galardon|lider|crece|crecimiento|expansion|digitaliza|tecnologia al servicio|comprometid)")
FUENTE = re.compile(r"\b(segun|datos|informe|estudio|analisis|registr|notificacion|recoge|cifra|alerta)")


def norm(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", (s or "").lower()) if unicodedata.category(c) != "Mn")


def url_real(url: str) -> str:
    """Los links de Google News son redirecciones: el decoder da la del medio."""
    if "news.google.com" not in url:
        return url
    try:
        from googlenewsdecoder import gnewsdecoder
        return gnewsdecoder(url, interval=1).get("decoded_url") or url
    except Exception:
        return url


def fragmentos(page: str, ventana: int = 1) -> list[str]:
    """Parrafos que nombran a LUDA, con `ventana` parrafos de contexto."""
    ps = [re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", p))).strip() for p in _P.findall(page)]
    ps = [p for p in ps if len(p) > 30]
    idx = {j for i, p in enumerate(ps) if _LUDA.search(p)
           for j in range(max(0, i - ventana), min(len(ps), i + ventana + 1))}
    return [ps[i] for i in sorted(idx)]


def _cerca(texto: str, rx: re.Pattern) -> str | None:
    """Primera palabra de `rx` a <= VENTANA palabras de "luda", o None."""
    w = norm(texto).split()
    for i, x in enumerate(w):
        if x.startswith("luda"):
            m = rx.search(" ".join(w[max(0, i - VENTANA): i + VENTANA + 1]))
            if m:
                return m.group(0)
    return None


def clasificar(titulo: str, oraciones: list[str]) -> dict:
    """{tono, rol, riesgo, resumen}. `oraciones`: las que nombran a LUDA.
    resumen = por que (la palabra que disparo la regla), para auditar."""
    texto = " ".join(oraciones) or titulo
    neg, pos = _cerca(texto, NEG), _cerca(texto, POS)
    tono = "negativo" if neg else "positivo" if pos else "neutro"
    if _LUDA.search(titulo):
        rol = "protagonista"
    elif _cerca(texto, FUENTE):
        rol = "fuente de datos"
    else:
        rol = "secundaria"
    riesgo = ("alto" if rol == "protagonista" else "medio") if neg else "bajo"
    return {"tono": tono, "rol": rol, "riesgo": riesgo,
            "resumen": f"crítica cerca de LUDA: «{neg}»" if neg else ""}


def evaluar(n: dict) -> dict:
    """{url_real, verificada, tono, rol, riesgo, resumen, cita} de una mencion."""
    real = url_real(n["url"])
    out = {"url_real": real, "verificada": None, "tono": "sin evaluar", "rol": "", "riesgo": "",
           "resumen": "", "cita": ""}
    try:
        page = requests.get(real, headers=_UA, timeout=20).text
    except Exception:
        page = ""
    frags = fragmentos(page) if page else []
    # Nota leida entera (>= 5 parrafos) y sin LUDA: Google la trajo por un
    # link lateral (pasó con IM Farmacias 2026-10-29). Con menos texto puede
    # ser muro de pago: queda como "no se pudo leer".
    leida = len([p for p in _P.findall(page) if len(p) > 30]) >= 5
    if leida and not frags and not _LUDA.search(f"{n['titulo']} {n.get('resumen') or ''}"):
        out.update(verificada=0, rol="no aparece")
        return out
    out["verificada"] = 1 if frags else None    # None = no se pudo leer (muro de pago, JS)
    oraciones = [s for f in frags for s in re.split(r"(?<=[.!?])\s+", f) if _LUDA.search(s)]
    out["cita"] = (oraciones or [""])[0][:300]
    out.update(clasificar(n["titulo"], oraciones or [n.get("resumen") or ""]))
    return out
