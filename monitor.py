"""Monitor de medios para LUDA Partners (España + Europa).

    python monitor.py           # fetch + aemps + digest (lo de todos los dias)
    python monitor.py fetch     # baja las fuentes de fuentes.py a monitor.db
    python monitor.py aemps     # foto del dia de los problemas de suministro (CIMA)
    python monitor.py digest    # resumen de 24 h por Telegram (+ reportes/AAAA-MM-DD.html)
    python monitor.py alertas   # fetch + menciones nuevas de LUDA al toque (cada 2 h)

Credenciales en .env: TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID.

Parser RSS, agrupado de la misma historia y busquedas de Google News vienen
del Radar Legislativo (Base_legislativa/noticias), sin la parte PE/EC.
"""
from __future__ import annotations

import html
import os
import re
import sqlite3
import sys
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlparse

import requests

from fuentes import FUENTES

ROOT = Path(__file__).parent
DB = ROOT / "monitor.db"
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120 Safari/537.36"}
DIAS_FRESCURA = 2     # Google News devuelve notas viejas como si fueran nuevas
DIAS_OLA = 21         # un informe de LUDA rebota en la prensa ~3 semanas
UMBRAL_TFIDF = 0.45   # respaldo sin embeddings; mismo valor que historias.py del Radar
MAX_POR_SECCION = 8   # que el digest se lea en el celular


# ---------------------------------------------------------------- texto

def norm(s: str | None) -> str:
    """Minusculas, sin tildes, espacios simples."""
    s = "".join(c for c in unicodedata.normalize("NFD", (s or "").lower())
                if unicodedata.category(c) != "Mn")
    return re.sub(r"\s+", " ", s).strip()


def _limpio(s: str) -> str:
    m = re.match(r"^\s*<!\[CDATA\[(.*?)\]\]>\s*$", s or "", re.DOTALL)
    # unescape antes de sacar tags: muchos feeds mandan el HTML escapado (&lt;p&gt;)
    s = re.sub(r"<[^>]+>", " ", html.unescape(m.group(1) if m else (s or "")))
    return re.sub(r"\s+", " ", s).strip()


def _fecha(s: str) -> str | None:
    try:
        dt = parsedate_to_datetime(s)
    except (TypeError, ValueError):
        try:
            dt = datetime.fromisoformat(s.strip().replace("Z", "+00:00"))
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


_ITEM = re.compile(r"<(item|entry)\b[^>]*>(.*?)</\1>", re.DOTALL | re.I)


def _tag(item: str, *tags: str) -> str:
    t = "|".join(tags)
    m = re.search(rf"<({t})\b[^>]*>(.*?)</\1>", item, re.DOTALL | re.I)
    return m.group(2) if m else ""


def parse_rss(xml: str) -> list[dict]:
    """RSS 2.0 / Atom -> [{url, titulo, resumen, fecha_pub}]."""
    out = []
    for _, item in _ITEM.findall(xml):
        titulo = _limpio(_tag(item, "title"))
        link = _limpio(_tag(item, "link"))
        if not link:  # Atom: <link href="..."/>
            m = re.search(r'<link[^>]*href=["\']([^"\']+)', item, re.I)
            link = m.group(1) if m else ""
        # Google News: el medio real va en <source url="...">
        src = re.search(r'<source[^>]*url=["\']https?://([^/"\']+)', item, re.I)
        if titulo and link:
            out.append({"url": link, "titulo": titulo[:500],
                        "resumen": _limpio(_tag(item, "description", "summary", "content:encoded"))[:500],
                        "fecha_pub": _fecha(_limpio(_tag(item, "pubDate", "published", "dc:date", "updated"))),
                        "dominio": src.group(1).lower() if src else urlparse(link).netloc.lower()})
    return out


def separar_medio(titulo: str) -> tuple[str, str | None]:
    """Google News: 'Titular - Medio' -> ('Titular', 'Medio')."""
    t, sep, medio = titulo.rpartition(" - ")
    return (t, medio) if sep and t and len(medio) < 60 else (titulo, None)


# ---------------------------------------------------------------- temas

# Orden = prioridad: la seccion de una nota es su primer tema.
# Palabras sin tildes; matchean al inicio de palabra (plurales incluidos).
TEMAS: dict[str, list[str]] = {
    "Mención LUDA": ["luda partners", "luda trends", "luda farma"],
    # Reputacion relativa: que se dice de distribuidores y plataformas con
    # los que LUDA compite o se asocia. ponytail: lista armada desde prensa,
    # confirmarla con LUDA.
    "Competencia y socios": [
        "cofares", "bidafarma", "hefame", "alliance healthcare", "fedefarma", "promofarma",
        "docmorris", "redcare", "atida", "mifarma", "uber eats", "glovo"],
    "Desabastecimiento": [
        "desabastec", "problemas de suministro", "problema de suministro", "falta de medicamento",
        "faltas de medicamento", "escasez de medicamento", "escasez de farmaco", "rotura de stock",
        "roturas de stock", "medicamentos en falta", "suministro de medicamento", "sin alternativa terapeutica",
        "shortage", "penurie", "rupture de stock", "ruptures de stock", "lieferengpass",
        "lieferengpasse", "carenza", "carenze", "rutura de medicamento"],
    # El negocio de la farmacia (clientes de LUDA), no la palabra "farmacia":
    # con "farmacia" suelta entraban "Gripe y vacunas: consulta en tu
    # farmacia" o "Citricos - Farmacia y Nutricion" (2026-10-30).
    "Farmacia comunitaria": [
        "farmacia comunitaria", "farmacias comunitarias", "oficina de farmacia", "oficinas de farmacia",
        "farmacia rural", "farmacias rurales", "cierre de farmacias", "apertura de farmacias",
        "titulares de farmacia", "farmaceuticos comunitarios", "colegio de farmaceuticos",
        "colegio oficial de farmaceuticos", "colegios de farmaceuticos", "consejo general de colegios",
        "cgcof", "sefac", "fefe", "parafarmacia", "receta electronica", "dispensacion", "fedifar",
        "distribucion farmaceutica", "venta online de medicamentos", "venta a distancia",
        "farmacia online", "farmacias online", "farmacia digital", "digitalizacion de la farmacia",
        "servicios profesionales farmaceuticos", "guardias de farmacia",
        "online pharmacy", "online pharmacies", "e-pharmacy", "community pharmacy",
        "community pharmacies", "versandapotheke", "pharmacie en ligne"],
    "Regulación": [
        "aemps", "ley del medicamento", "ley de los medicamentos", "ley de garantias",
        "precios de referencia", "precio de referencia", "financiacion de medicamentos",
        "consejo interterritorial", "ministerio de sanidad", "agencia europea del medicamento",
        "european medicines agency",
        "critical medicines act", "paquete farmaceutico", "pharmaceutical package",
        "pharmaceutical legislation", "legislacion farmaceutica"],
    "Sanidad general": [
        "medicamento", "farmaco", "medicine", "medicines", "drug", "drugs", "sanidad",
        "sistema nacional de salud", "atencion primaria", "paciente", "vacuna", "antibiotico",
        "medicament", "arzneimittel", "farmaci", "medicamentos", "industria farmaceutica",
        "laboratorio farmaceutico", "laboratorios farmaceuticos", "pharma"],
}
_RE_TEMA = {t: re.compile(r"\b(?:" + "|".join(map(re.escape, kws)) + ")", re.I)
            for t, kws in TEMAS.items()}

# Ruido visto en las corridas reales (2026-10-29/30): avisos de empleo UK,
# oposiciones del BOE, protocolo institucional (audiencia con el Papa,
# condolencias por el accidente de Urda) y cursos/eventos de los colegios.
# Sin "muere"/"fallece": "muere un paciente por falta de un farmaco" si va.
_RUIDO = re.compile(r"\bjobs? with\b|\bvacanc|proceso selectivo|concurso-oposicion|\boposicion(?:es)?\b"
                    r"|libre designacion|\bse nombra\b|\bbolsa de empleo"
                    r"|\bpapa\b|leon xiv|vatican|pontific|condolencia|profundo pesar|pesame|\bluto\b"
                    r"|accidente (?:ferroviario|de tren|de trafico)|\burda\b"
                    r"|sesion formativa|edicion de tarde|aula dispensacion|webinar|curso online|jornada formativa"
                    # retiradas de cosmeticos (AEMPS) y NHS britanico / empleo UK
                    r"|cosmetic|blanqueador|\bnhs\b|\bassessor\b|\bclosed on\b",
                    re.I)
# Google News con gl=ES igual trae medios de LatAm: por dominio, y por
# palabras en el titular para los .com latinoamericanos.
_TLD_FUERA = (".mx", ".ar", ".co", ".cl", ".pe", ".ec", ".br", ".ve", ".uy", ".py", ".bo",
              ".gt", ".hn", ".sv", ".ni", ".cr", ".pa", ".do", ".cu", ".pr", ".us")
# .com latinoamericanos que se colaron (Teletica CR, Diario Panorama AR) y
# otros grandes del mismo tipo.
_DOMINIOS_FUERA = {"teletica.com", "diariopanorama.com", "crhoy.com", "nacion.com", "eltiempo.com",
                   "elespectador.com", "semana.com", "milenio.com", "eluniverso.com", "listindiario.com",
                   # sin infobae.com: tiene edicion España en el mismo dominio
                   "prensalibre.com", "elsalvador.com", "clarin.com", "pagina12.com",
                   "eldeber.com", "larepublica.co", "biobiochile.com", "emol.com"}
_LATAM = re.compile(r"\b(?:mexic|argentin|colombia|chile|peru|ecuador|venezuel|paraguay|uruguay|"
                    r"bolivia|brasil|imss|issste|mendoza|buenos aires|bogota|guatemala|honduras|"
                    r"costa rica|panama|republica dominicana|puerto rico|estados unidos|eeuu)", re.I)


def temas(texto: str) -> list[str]:
    t = norm(texto)
    out = [tema for tema, rx in _RE_TEMA.items() if rx.search(t)]
    # Uber Eats / Glovo solo cuentan si la nota es de farmacia (no riders, no comida)
    if "Competencia y socios" in out and not re.search(r"\bfarmac|\bparafarmac", t) \
            and not re.search(r"\b(?:cofares|bidafarma|hefame|alliance healthcare|fedefarma|promofarma|"
                              r"docmorris|redcare|atida|mifarma)", t):
        out.remove("Competencia y socios")
    return out


def seccion(n: dict) -> str | None:
    """Seccion del digest, o None si la nota no va. Mencion > Europa > tema."""
    if n["bloque"] == "mencion" or "Mención LUDA" in n["temas"]:
        return "Mención LUDA"
    if n["region"] == "GLOBAL":
        return None  # fuera de España/Europa solo interesan las menciones
    titulo = norm(n.get("titulo"))
    dominio = (n.get("dominio") or "").removeprefix("www.")
    if _RUIDO.search(titulo) or dominio.endswith(_TLD_FUERA) or dominio in _DOMINIOS_FUERA \
            or (n["region"] == "ES" and _LATAM.search(titulo)):
        return None
    if n["filtro"] and not n["temas"]:
        return None
    if n["region"] == "EU":
        # Pharma de negocios (Endpoints: Lilly, BMS) y lo que arrastran las
        # busquedas de Google (France 24 sobre Ucrania) no; desabastecimiento,
        # farmacia y regulacion si. EMA/Comision pasan enteras.
        especifico = set(n["temas"]) - {"Sanidad general"}
        return "Europa" if especifico or n["bloque"] == "oficial" else None
    return n["temas"][0] if n["temas"] else "Sanidad general"


# ---------------------------------------------------------------- db + fetch

def conectar() -> sqlite3.Connection:
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    c.executescript("""
        CREATE TABLE IF NOT EXISTS noticias (
            id INTEGER PRIMARY KEY, url TEXT UNIQUE NOT NULL, titulo TEXT, resumen TEXT,
            fecha_pub TEXT, medio TEXT, dominio TEXT, fuente TEXT, region TEXT, bloque TEXT, filtro INTEGER,
            first_seen TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')));
        CREATE TABLE IF NOT EXISTS aemps (
            cn TEXT PRIMARY KEY, nombre TEXT, tipo INTEGER, fini TEXT, ffin TEXT, observ TEXT,
            activo INTEGER, visto_desde TEXT, resuelto_en TEXT);
        CREATE TABLE IF NOT EXISTS extractos (url TEXT PRIMARY KEY, texto TEXT);
        CREATE TABLE IF NOT EXISTS reputacion (
            url TEXT PRIMARY KEY, url_real TEXT, verificada INTEGER, tono TEXT, rol TEXT,
            riesgo TEXT, resumen TEXT, cita TEXT, enviada INTEGER DEFAULT 0,
            evaluada_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')));""")
    return c


def _bajar(f: dict) -> tuple[dict, list[dict] | str]:
    try:
        r = requests.get(f["url"], headers=UA, timeout=25)
        r.raise_for_status()
        return f, parse_rss(r.text)
    except Exception as e:  # una fuente caida no frena las demas
        return f, f"{type(e).__name__}: {e}"[:200]


def fetch() -> None:
    c = conectar()
    nuevas, caidas = 0, []
    with ThreadPoolExecutor(12) as ex:
        for f, items in ex.map(_bajar, FUENTES):
            if isinstance(items, str):
                caidas.append(f"{f['nombre']}: {items}")
                continue
            for it in items:
                titulo, medio = separar_medio(it["titulo"]) if f["google"] else (it["titulo"], f["nombre"])
                cur = c.execute("""INSERT OR IGNORE INTO noticias
                    (url, titulo, resumen, fecha_pub, medio, dominio, fuente, region, bloque, filtro)
                    VALUES (?,?,?,?,?,?,?,?,?,?)""", (it["url"], titulo, it["resumen"], it["fecha_pub"],
                    medio or f["nombre"], it["dominio"], f["nombre"], f["region"], f["bloque"], int(f["filtro"])))
                nuevas += cur.rowcount
    c.commit()
    print(f"fetch: {nuevas} notas nuevas de {len(FUENTES)} fuentes")
    for x in caidas:
        print("  caida:", x)


# ---------------------------------------------------------------- AEMPS

CIMA = "https://cima.aemps.es/cima/rest/psuministro"


def _ms(v) -> str | None:
    return datetime.fromtimestamp(v / 1000, timezone.utc).date().isoformat() if v else None


def bajar_cima() -> list[dict]:
    out, pagina = [], 1
    while True:
        for intento in range(3):     # CIMA a veces no responde (2026-10-30)
            try:
                d = requests.get(CIMA, params={"pagina": pagina}, headers=UA, timeout=60).json()
                break
            except requests.RequestException:
                if intento == 2:
                    raise            # nunca devolver una lista a medias: daria falsos "resueltos"
        out += d["resultados"]
        if pagina * d["tamanioPagina"] >= d["totalFilas"]:
            return out
        pagina += 1


def aemps(filas: list[dict] | None = None, hoy: str | None = None) -> tuple[int, int]:
    """Compara la lista activa de hoy con la guardada. Devuelve (nuevos, resueltos).
    La primera corrida es la linea base: no cuenta como 'nuevos'."""
    c = conectar()
    hoy = hoy or datetime.now(timezone.utc).date().isoformat()
    filas = bajar_cima() if filas is None else filas
    base = c.execute("SELECT COUNT(*) FROM aemps").fetchone()[0] == 0
    activos_antes = {r[0] for r in c.execute("SELECT cn FROM aemps WHERE activo=1")}
    activos_hoy = {str(f["cn"]) for f in filas if f.get("activo")}
    for f in filas:
        cn = str(f["cn"])
        c.execute("""INSERT INTO aemps (cn, nombre, tipo, fini, ffin, observ, activo, visto_desde)
                     VALUES (?,?,?,?,?,?,?,?)
                     ON CONFLICT(cn) DO UPDATE SET nombre=excluded.nombre, tipo=excluded.tipo,
                       fini=excluded.fini, ffin=excluded.ffin, observ=excluded.observ,
                       activo=excluded.activo, resuelto_en=NULL,
                       visto_desde=CASE WHEN aemps.activo=0 THEN excluded.visto_desde ELSE aemps.visto_desde END""",
                  (cn, f.get("nombre"), f.get("tipoProblemaSuministro"), _ms(f.get("fini")),
                   _ms(f.get("ffin")), f.get("observ"), int(bool(f.get("activo"))),
                   "base" if base else hoy))
    resueltos = activos_antes - activos_hoy
    c.executemany("UPDATE aemps SET activo=0, resuelto_en=? WHERE cn=?", [(hoy, cn) for cn in resueltos])
    c.commit()
    nuevos = 0 if base else len(activos_hoy - activos_antes)
    print(f"aemps: {len(activos_hoy)} activos, {nuevos} nuevos, {len(resueltos)} resueltos"
          + (" (linea base)" if base else ""))
    return nuevos, len(resueltos)


CIMA_LISTA = "https://cima.aemps.es/cima/publico/listadesabastecimiento.html"


def ficha_cima(cn: str) -> str:
    """Ficha del medicamento en CIMA (trae la seccion de problemas de
    suministro). La ficha va por n. de registro, no por CN: se busca en la API."""
    try:
        nreg = requests.get(f"https://cima.aemps.es/cima/rest/medicamento?cn={cn}", timeout=15).json()["nregistro"]
        return f"https://cima.aemps.es/cima/publico/detalle.html?nregistro={nreg}"
    except Exception:
        return CIMA_LISTA


# ---------------------------------------------------------------- agrupado

# Embeddings multilingues de Hugging Face (local, ~470 MB, CPU ~5 s para 280
# notas). Calibrado 2026-10-29 con 280 notas reales: entre medios distintos,
# desde 0.93 18 de 20 pares eran el mismo hecho (Faes Farma "ingresos +24%" /
# "gana 74 millones", que TF-IDF no junta); entre 0.91 y 0.93 ya se mezclan
# hechos distintos del mismo tema (condolencias de COF vs Farmaindustria).
MODELO_EMB = "intfloat/multilingual-e5-small"
UMBRAL_EMB = 0.93


@lru_cache(maxsize=1)
def _modelo_emb():
    from sentence_transformers import SentenceTransformer
    return SentenceTransformer(MODELO_EMB, device="cpu")


def similitudes(textos: list[str]):
    """(matriz, umbral): e5 si esta, si no TF-IDF (sin red / sin torch)."""
    try:
        v = _modelo_emb().encode(["query: " + t for t in textos], normalize_embeddings=True, batch_size=64)
        return v @ v.T, UMBRAL_EMB
    except Exception as e:
        print(f"agrupar: sin embeddings ({type(e).__name__}), uso TF-IDF")
        from sklearn.feature_extraction.text import TfidfVectorizer
        from sklearn.metrics.pairwise import cosine_similarity
        return cosine_similarity(TfidfVectorizer().fit_transform(textos)), UMBRAL_TFIDF


def agrupar(textos: list[str], sims=None, umbral: float | None = None) -> list[int]:
    """Indice de grupo por texto: dos notas van juntas si se parecen a
    CUALQUIER nota del grupo (no solo a la primera, como el greedy del
    Radar). El greedy dejo partido "La PGEU traslada a Leon XIV..." /
    "La farmacia europea traslada al Papa..." (2026-10-30): se parecian a
    traves de una tercera nota. Con 0.93 casi no hay pares falsos, asi que
    el encadenamiento es seguro."""
    if not textos:
        return []
    if sims is None:
        sims, umbral = similitudes(textos)
    padre = list(range(len(textos)))

    def raiz(i: int) -> int:
        while padre[i] != i:
            padre[i] = padre[padre[i]]
            i = padre[i]
        return i

    for i in range(len(textos)):
        for j in range(i):
            if sims[i][j] >= umbral:
                padre[raiz(i)] = raiz(j)
    ids: dict[int, int] = {}
    return [ids.setdefault(raiz(i), len(ids)) for i in range(len(textos))]




# ---------------------------------------------------------------- telegram

def cargar_env(path: Path = ROOT / ".env") -> None:
    """KEY=valor por linea; lo que ya esta en el entorno manda."""
    if path.exists():
        for linea in path.read_text(encoding="utf-8").splitlines():
            k, sep, v = linea.partition("=")
            if sep and not k.strip().startswith("#"):
                os.environ.setdefault(k.strip(), v.strip().strip('"'))


def telegram(texto: str) -> bool:
    """Manda en trozos de <4000 caracteres cortando por linea (limite de
    Telegram: 4096). Sin TELEGRAM_BOT_TOKEN/CHAT_ID solo avisa."""
    token, chat = os.environ.get("TELEGRAM_BOT_TOKEN"), os.environ.get("TELEGRAM_CHAT_ID")
    if not (token and chat):
        print("telegram: falta TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID en .env, no se envia")
        return False
    # No cortar dentro de un <blockquote>: Telegram rechaza HTML sin cerrar.
    trozos, actual, dentro = [], "", False
    for linea in texto.split("\n"):
        # cortar en linea en blanco (entre notas); a mitad de nota solo si no queda otra
        largo = len(actual) + len(linea)
        if not dentro and (largo > 3900 or (largo > 3300 and actual.endswith("\n\n"))):
            trozos.append(actual)
            actual = ""
        actual += linea + "\n"
        dentro = actual.count("<blockquote>") > actual.count("</blockquote>")
    trozos.append(actual)
    ok = True
    for t in filter(str.strip, trozos):
        r = requests.post(f"https://api.telegram.org/bot{token}/sendMessage", timeout=20,
                          data={"chat_id": chat, "text": t, "parse_mode": "HTML",
                                "disable_web_page_preview": "true"})
        if not r.ok:
            print("telegram rechazo:", r.text[:200])
            ok = False
    return ok


def correo(asunto: str, texto: str) -> bool:
    """Mismo HTML que Telegram, por SMTP de Gmail (patron de monitor-avisos/
    daily/send_email.py: con cuenta real Google firma el correo, sin dominio
    propio). GMAIL_APP_PASSWORD es la contrasena de aplicacion, no la normal."""
    import smtplib
    from email.mime.text import MIMEText
    user, clave, para = (os.environ.get(k) for k in ("GMAIL_USER", "GMAIL_APP_PASSWORD", "EMAIL_TO"))
    if not (user and clave and para):
        print("correo: falta GMAIL_USER/GMAIL_APP_PASSWORD/EMAIL_TO, no se envia")
        return False
    msg = MIMEText(f'<div style="white-space:pre-wrap;font-family:sans-serif">{texto}</div>', "html", "utf-8")
    msg["Subject"], msg["From"], msg["To"] = asunto, f"Monitor LUDA <{user}>", para
    if os.environ.get("EMAIL_CC"):
        msg["Cc"] = os.environ["EMAIL_CC"]
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=30) as s:
        s.login(user, clave.replace(" ", ""))   # Google la muestra en 4 grupos con espacios
        s.send_message(msg)
    print(f"correo: enviado a {para}")
    return True


# ---------------------------------------------------------------- reputacion

TONO_ICONO = {"positivo": "🟢", "neutro": "⚪", "negativo": "🔴", "sin evaluar": "❔"}


def esc(s: str | None) -> str:
    return html.escape(s or "", quote=True)


def _evaluar_pendientes(c: sqlite3.Connection, notas: list[dict]) -> None:
    import reputacion
    ya = {r[0] for r in c.execute("SELECT url FROM reputacion")}
    for n in notas:
        if n["seccion"] == "Mención LUDA" and n["url"] not in ya:
            e = reputacion.evaluar(n)
            c.execute("""INSERT INTO reputacion (url, url_real, verificada, tono, rol, riesgo, resumen, cita)
                         VALUES (:url, :url_real, :verificada, :tono, :rol, :riesgo, :resumen, :cita)""",
                      {"url": n["url"], **e})
            c.commit()


def _con_reputacion(c: sqlite3.Connection, notas: list[dict]) -> list[dict]:
    """Suma la evaluacion a las menciones y saca las que no nombran a LUDA."""
    rep = {r["url"]: dict(r) for r in c.execute("SELECT * FROM reputacion")}
    out = []
    for n in notas:
        if n["seccion"] == "Mención LUDA":
            n["rep"] = rep.get(n["url"])
            if n["rep"] and n["rep"]["verificada"] == 0:
                continue  # Google la trajo pero LUDA no esta en la nota
        out.append(n)
    return out


def _mencion(n: dict) -> str:
    r = n.get("rep") or {}
    """Recuadro (blockquote de Telegram): que se distinga a simple vista del
    resto de noticias."""
    url = r.get("url_real") or n["url"]
    det = [x for x in (r.get("rol") and f"LUDA: {r['rol']}", r.get("riesgo") and f"riesgo {r['riesgo']}") if x]
    if r.get("verificada") is None:
        det.append("no se pudo leer la nota, verificar")
    lineas = [f"<b>{esc(n['titulo'])}</b>",
              f"{TONO_ICONO.get(r.get('tono'), '❔')} <a href=\"{esc(url)}\">{esc(n['medio'])}</a>"
              + (f" · <i>{esc(' · '.join(det))}</i>" if det else "")]
    if r.get("resumen"):
        lineas.append(esc(r["resumen"]))
    if r.get("cita"):
        lineas.append(f"<i>«{esc(r['cita'][:220])}»</i>")
    return "<blockquote>" + "\n".join(lineas) + "</blockquote>"


def _menciones(notas: list[dict]) -> list[str]:
    """Una linea para la ola (LUDA citada como fuente neutra: el mismo
    informe rebotado por 20 medios regionales con titulares distintos) y el
    detalle solo de las que tienen tono o tratan sobre LUDA."""
    def rep(n): return n.get("rep") or {}
    ola = [n for n in notas if rep(n).get("tono") == "neutro" and rep(n).get("rol") == "fuente de datos"]
    out = [_mencion(n) for n in notas if n not in ola]
    if ola:
        links = ", ".join(f"<a href=\"{esc(rep(n).get('url_real') or n['url'])}\">{esc(n['medio'])}</a>" for n in ola)
        out.append(f"<blockquote>📊 <b>Citada como fuente de datos en {len(ola)} nota(s)</b>\n{links}</blockquote>")
    return out


def alertas() -> None:
    """Menciones nuevas de LUDA apenas aparecen (correr cada 2 h, tras fetch).
    Cada mencion sale una sola vez: aca o en el digest."""
    c = conectar()
    notas = [n for n in _notas(c, horas=48) if n["seccion"] == "Mención LUDA"]
    _evaluar_pendientes(c, notas)
    enviadas = {r[0] for r in c.execute("SELECT url FROM reputacion WHERE enviada=1")}
    nuevas = [n for n in _con_reputacion(c, notas) if n["url"] not in enviadas]
    if not nuevas:
        print("alertas: sin menciones nuevas")
        return
    urgente = any((n.get("rep") or {}).get("riesgo") == "alto" or (n.get("rep") or {}).get("tono") == "negativo"
                  for n in nuevas)
    texto = (f"<b>{'🚨' if urgente else '🔔'} LUDA EN LOS MEDIOS — {len(nuevas)} mención(es) nueva(s)</b>\n\n"
             + "\n".join(_menciones(nuevas)))
    if telegram(texto):
        c.executemany("UPDATE reputacion SET enviada=1 WHERE url=?", [(n["url"],) for n in nuevas])
        c.commit()
    print(f"alertas: {len(nuevas)} menciones")


# ---------------------------------------------------------------- digest

# Orden por cercania al negocio de LUDA. "Sanidad general" (estudios
# clinicos, hospitales) se clasifica pero no va al resumen: no le sirve.
ORDEN = ["Desabastecimiento", "Competencia y socios", "Regulación", "Farmacia comunitaria", "Europa"]
ICONO = {"Competencia y socios": "🏁", "Desabastecimiento": "💊",
         "Farmacia comunitaria": "🏪", "Regulación": "⚖️", "Europa": "🇪🇺"}
TOPE = {"Europa": 4}   # España primero; Europa solo lo grueso


_VIGENTES = {f["nombre"] for f in FUENTES}


def _notas(c: sqlite3.Connection, horas: int) -> list[dict]:
    ahora = datetime.now(timezone.utc)
    desde = (ahora - timedelta(hours=horas)).strftime("%Y-%m-%dT%H:%M:%SZ")
    fresca = (ahora - timedelta(days=DIAS_FRESCURA)).strftime("%Y-%m-%dT%H:%M:%SZ")
    filas = c.execute("""SELECT * FROM noticias WHERE first_seen >= ?
                         AND (fecha_pub IS NULL OR fecha_pub >= ?)
                         ORDER BY fecha_pub DESC""", (desde, fresca)).fetchall()
    vistos, out = set(), []
    for r in map(dict, filas):
        clave = norm(r["titulo"])[:80]  # misma nota por RSS y por Google News
        if clave in vistos:
            continue
        vistos.add(clave)
        # El "resumen" de Google News es titular + nombre del medio: con el
        # nombre "Consejo General de Colegios Farmaceuticos" sus notas de
        # divulgacion (Finerenona, cambio de hora) caian en Farmacia.
        gn = "news.google.com" in r["url"]
        r["temas"] = temas(r["titulo"] if gn else f"{r['titulo']} {r['resumen'] or ''}")
        if r["fuente"] not in _VIGENTES:   # fuente ya quitada (ej. busquedas en italiano)
            continue
        r["seccion"] = seccion(r)
        if r["seccion"]:
            out.append(r)
    return out


# ---------------------------------------------------------------- resumen de cada nota

# Entradilla escrita por el propio medio (og:description o meta description),
# no un resumen generado: no inventa datos. Probado 2026-10-30 con notas
# reales: Sandoz, Hefame, convenio FEFE, Critical Medicines Act salen bien;
# ~1 de 4 no trae entradilla y va sin resumen.
_META_DESC = [re.compile(p, re.I | re.S) for p in (
    r'<meta[^>]+property=["\']og:description["\'][^>]+content=["\']([^"\']+)',
    r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:description',
    r'<meta[^>]+name=["\']description["\'][^>]+content=["\']([^"\']+)')]
# Paginas de error/bloqueo que se cuelan como "primer parrafo" (Akamai en MarketScreener)
_BASURA = re.compile(r"reference #|edgesuite|access denied|cookies|javascript|suscri"
                     r"|google news|aggregated from sources|nel play rsi", re.I)
LARGO_RESUMEN = 230


def _corto(s: str) -> str:
    s = re.sub(r"\s+", " ", html.unescape(s or "")).strip()
    if s.endswith(("...", "…")):     # el medio ya la recorto
        s = s.rstrip(". …").strip() + "…"
    return s if len(s) <= LARGO_RESUMEN else s[:LARGO_RESUMEN].rsplit(" ", 1)[0] + "…"


def entradilla(page: str) -> str:
    """og:description / description del medio; si no, primer parrafo largo."""
    for rx in _META_DESC:
        m = rx.search(page)
        if m and len(m.group(1)) > 60 and not _BASURA.search(m.group(1)):
            return _corto(m.group(1))
    for p in re.findall(r"<p\b[^>]*>(.*?)</p>", page, re.DOTALL | re.I):
        t = re.sub(r"<[^>]+>", " ", p)
        if len(t.strip()) > 80 and not _BASURA.search(t):
            return _corto(t)
    return ""


def _resumen_de(n: dict) -> str:
    """El resumen del RSS si sirve; si no (Google News solo trae el titular),
    la entradilla de la nota."""
    r = _corto(n.get("resumen") or "")
    if "news.google.com" not in n["url"] and len(r) > 60 and not norm(r).startswith(norm(n["titulo"])[:40]):
        return r
    import reputacion
    url = reputacion.url_real(n["url"])
    if "news.google.com" in url:     # no se pudo decodificar: seria la portada de Google News
        return ""
    try:
        resp = requests.get(url, headers=UA, timeout=20)
    except Exception:
        return ""
    # sin charset en la cabecera requests asume latin-1 (salia "Ã¨" en RSI)
    if not re.search(r"charset", resp.headers.get("content-type", ""), re.I):
        resp.encoding = resp.apparent_encoding
    return entradilla(resp.text)


def resumenes(c: sqlite3.Connection, notas: list[dict]) -> None:
    """Llena n["extracto"]; cachea en la tabla `extractos` (una bajada por nota)."""
    ya = {r[0]: r[1] for r in c.execute("SELECT url, texto FROM extractos")}
    faltan = [n for n in notas if n["url"] not in ya]
    with ThreadPoolExecutor(6) as ex:
        for n, t in zip(faltan, ex.map(_resumen_de, faltan)):
            ya[n["url"]] = t
            c.execute("INSERT OR REPLACE INTO extractos (url, texto) VALUES (?, ?)", (n["url"], t))
    c.commit()
    for n in notas:
        n["extracto"] = ya.get(n["url"], "")


def _linea(grupo: list[dict]) -> str:
    """Bloque por nota: medio en negrita, titulo con link, entradilla en
    cursiva, linea en blanco. Antes iba todo seguido y el resumen se pegaba a
    la nota siguiente: no se entendia de que medio era cada cosa (2026-10-30)."""
    n = grupo[0]
    medios = sorted({g["medio"] for g in grupo})
    fuente = f"{len(medios)} medios: {', '.join(medios[:4])}{' …' if len(medios) > 4 else ''}" \
        if len(medios) > 1 else medios[0]
    # Telegram no pone en negrita un link: titular en negrita y el link en el medio
    lineas = [f"<b>{esc(n['titulo'])}</b>", f"📰 <a href=\"{esc(n['url'])}\">{esc(fuente)}</a>"]
    # el resumen de cualquier nota del grupo que lo tenga
    resumen = next((g.get("extracto") for g in grupo if g.get("extracto")), "")
    if resumen:
        lineas.append(f"<i>{esc(resumen)}</i>")
    return "\n".join(lineas) + "\n"


def digest(horas: int = 24, enviar: bool = True) -> Path:
    c = conectar()
    notas = _notas(c, horas)
    _evaluar_pendientes(c, notas)
    notas = _con_reputacion(c, notas)
    hoy = datetime.now().date().isoformat()

    # 1) LUDA en los medios: mensaje propio, primero, con recuadros
    desde_ola = (datetime.now(timezone.utc) - timedelta(days=DIAS_OLA)).strftime("%Y-%m-%dT%H:%M:%SZ")
    tonos = c.execute("""SELECT r.tono, COUNT(*) FROM reputacion r JOIN noticias n ON n.url = r.url
                         WHERE r.verificada IS NOT 0 AND n.fecha_pub >= ? GROUP BY r.tono""",
                      (desde_ola,)).fetchall()
    menciones = _menciones([n for n in notas if n["seccion"] == "Mención LUDA"])
    balance = (f"<i>Últimos {DIAS_OLA} días: "
               + " · ".join(f"{TONO_ICONO.get(t, '❔')} {k} {t}" for t, k in tonos) + "</i>") if tonos else ""
    msg_luda = "\n".join([f"<b>🔔 LUDA EN LOS MEDIOS — {hoy}</b>", balance, ""] + menciones) if menciones else ""

    # 2) El sector
    lineas = [f"<b>📰 Monitor farma España — {hoy}</b>"]
    lineas += [""] if menciones else [f"🔕 Hoy sin menciones de LUDA. {balance}", ""]
    elegidos: dict[str, list[list[dict]]] = {}
    for sec in ORDEN:
        notas_sec = [n for n in notas if n["seccion"] == sec]
        textos = [f"{n['titulo']} {n['resumen'] or ''}" for n in notas_sec]
        grupos: dict[int, list[dict]] = {}
        for n, g in zip(notas_sec, agrupar(textos)):
            grupos.setdefault(g, []).append(n)
        # Las historias con mas medios primero
        elegidos[sec] = sorted(grupos.values(), key=len, reverse=True)[:TOPE.get(sec, MAX_POR_SECCION)]
    # Resumen solo de lo que va al mensaje (no de las ~250 notas del dia)
    resumenes(c, [n for gs in elegidos.values() for g in gs for n in g[:2]])
    for sec, gs in elegidos.items():
        if gs:
            lineas += [f"<b>{ICONO[sec]} {sec.upper()}</b>", "", *map(_linea, gs), ""]

    nuevos = c.execute("SELECT cn, nombre FROM aemps WHERE visto_desde=? AND activo=1", (hoy,)).fetchall()
    resueltos = c.execute("SELECT cn, nombre FROM aemps WHERE resuelto_en=?", (hoy,)).fetchall()
    if nuevos or resueltos:
        lineas += [f"<b>🏛️ AEMPS: {len(nuevos)} nuevos problemas de suministro, {len(resueltos)} resueltos</b>",
                   f"<a href=\"{CIMA_LISTA}\">Listado completo en CIMA</a>", ""]
        lineas += [f"🔴 <a href=\"{ficha_cima(r['cn'])}\">{esc(r['nombre'])}</a>" for r in nuevos[:MAX_POR_SECCION]]
        lineas += [f"🟢 <a href=\"{ficha_cima(r['cn'])}\">{esc(r['nombre'])}</a>" for r in resueltos[:MAX_POR_SECCION]]

    texto = "\n".join(lineas)
    out = ROOT / "reportes" / f"{hoy}.html"
    out.parent.mkdir(exist_ok=True)
    out.write_text('<meta charset="utf-8"><body style="white-space:pre-wrap;font-family:sans-serif">'
                   + msg_luda + "\n\n" + texto, encoding="utf-8")
    if enviar:
        # Correo diario de LUDA en los medios (a Diego), haya o no menciones
        try:
            correo(f"LUDA en los medios — {hoy}",
                   msg_luda or f"<b>🔔 LUDA EN LOS MEDIOS — {hoy}</b>\n🔕 Sin menciones nuevas en las últimas 24 h.\n{balance}")
        except Exception as e:      # que un fallo de SMTP no tumbe el Telegram
            print("correo fallo:", e)
    if enviar and (not msg_luda or telegram(msg_luda)) and telegram(texto):
        c.execute("UPDATE reputacion SET enviada=1 WHERE enviada=0")  # ya salieron en el digest
        c.commit()
    print(f"digest: {len(notas)} notas -> {out}")
    return out


if __name__ == "__main__":
    cargar_env()
    cmd = sys.argv[1] if len(sys.argv) > 1 else "todo"
    if cmd in ("fetch", "todo", "alertas"):
        fetch()
    if cmd == "alertas":
        alertas()
    if cmd in ("aemps", "todo"):
        try:
            aemps()
        except Exception as e:   # sin AEMPS hoy, pero el resumen y el correo salen igual
            print("aemps fallo:", e)
    if cmd in ("digest", "todo"):
        digest()
