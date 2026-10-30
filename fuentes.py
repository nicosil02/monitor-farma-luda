"""Catalogo de fuentes para LUDA Partners: España + Europa.

Cada fuente: region (ES / EU / GLOBAL), bloque, nombre, url y `filtro`:
  filtro=False  todo lo que trae es del sector (prensa farma, busquedas tematicas)
  filtro=True   fuente amplia (generalista, BOE, EUR-Lex): solo entra lo que
                matchea algun tema de monitor.TEMAS
Bloque "mencion" = busquedas de LUDA: todo lo que traen es mencion, aunque
el titular no lo diga (Google News busca en el texto completo).

Feeds probados en vivo el 2026-10-29. Los medios sin RSS o que bloquean
bots (Diariofarma, CGCOF, El Farmaceutico...) van por Google News site:.
"""
from urllib.parse import quote_plus

_GN_LOCALE = {"es": "hl=es&gl=ES&ceid=ES:es", "en": "hl=en-GB&gl=GB&ceid=GB:en",
              "fr": "hl=fr&gl=FR&ceid=FR:fr", "de": "hl=de&gl=DE&ceid=DE:de",
              "it": "hl=it&gl=IT&ceid=IT:it", "pt": "hl=pt-PT&gl=PT&ceid=PT:pt-150"}


def gn(region: str, bloque: str, nombre: str, query: str, idioma: str = "es",
       filtro: bool = False) -> dict:
    # when:2d: Google News ordena por relevancia y devuelve max 100; sin
    # ventana corta las busquedas amplias se saturan con notas viejas.
    url = (f"https://news.google.com/rss/search?q={quote_plus(query + ' when:2d')}"
           f"&{_GN_LOCALE[idioma]}")
    return {"region": region, "bloque": bloque, "nombre": f"Google News — {nombre}",
            "url": url, "filtro": filtro, "google": True}


def rss(region: str, bloque: str, nombre: str, url: str, filtro: bool = False) -> dict:
    return {"region": region, "bloque": bloque, "nombre": nombre, "url": url,
            "filtro": filtro, "google": False}


FUENTES: list[dict] = [
    # --- Menciones de LUDA (cualquier pais) ---
    gn("ES", "mencion", "LUDA (ES)", '"LUDA Partners" OR "LUDA Trends" OR "LUDA Farma"'),
    gn("ES", "mencion", "LUDA farmacia (ES)", "LUDA farmacia OR farmacias OR desabastecimiento"),
    gn("ES", "mencion", "Voceros LUDA", '"Daniel de Carvajal" OR ("Luis Martín" LUDA)'),
    *[gn("GLOBAL", "mencion", f"LUDA ({i})", '"LUDA Partners"', i) for i in ("en",)],

    # --- Oficiales ES ---
    rss("ES", "oficial", "AEMPS", "https://www.aemps.gob.es/feed/"),
    rss("ES", "oficial", "BOE disposiciones", "https://www.boe.es/rss/canal_per.php?l=p&c=140", filtro=True),
    rss("ES", "oficial", "BOE Sección I", "https://www.boe.es/rss/boe.php?s=1", filtro=True),
    rss("ES", "oficial", "La Moncloa — Sanidad", "https://www.lamoncloa.gob.es/Paginas/rss.aspx?tipo=13"),
    # Sin site:sanidad.gob.es: Google devuelve paginas fijas del sitio
    # ("Detalle del Centro"), no notas. Sanidad llega via prensa y BOE.
    gn("ES", "oficial", "CGCOF", "site:farmaceuticos.com"),
    rss("ES", "oficial", "Farmaindustria", "https://www.farmaindustria.es/web/feed/"),
    rss("ES", "oficial", "Fedifar", "https://fedifar.net/feed/"),
    rss("ES", "oficial", "SEFAC", "https://www.sefac.org/rss.xml"),

    # --- Prensa sanitaria / farmaceutica ES ---
    rss("ES", "sector", "Redacción Médica", "https://www.redaccionmedica.com/rss"),
    rss("ES", "sector", "ConSalud", "https://www.consalud.es/rss"),
    rss("ES", "sector", "El Global", "https://elglobal.es/feed/"),
    rss("ES", "sector", "Gaceta Médica", "https://gacetamedica.com/feed/"),
    rss("ES", "sector", "Diario Médico", "https://www.diariomedico.com/rss/portada.xml"),
    rss("ES", "sector", "IM Farmacias", "https://www.imfarmacias.es/rss.xml"),
    rss("ES", "sector", "iSanidad", "https://isanidad.com/feed/"),
    rss("ES", "sector", "Farmacosalud", "https://farmacosalud.com/feed/"),
    rss("ES", "sector", "EFE Salud", "https://efesalud.com/feed/"),
    rss("ES", "sector", "New Medical Economics", "https://www.newmedicaleconomics.es/feed/"),
    rss("ES", "sector", "Salud a Diario", "https://www.saludadiario.es/feed/"),
    gn("ES", "sector", "Diariofarma", "site:diariofarma.com"),
    gn("ES", "sector", "El Farmacéutico", "site:elfarmaceutico.es"),
    gn("ES", "sector", "Farmaventas", "site:farmaventas.es"),
    gn("ES", "sector", "El Médico Interactivo", "site:elmedicointeractivo.com"),
    gn("ES", "sector", "Infosalus", "site:infosalus.com farmacia OR medicamentos"),
    gn("ES", "sector", "Correo Farmacéutico", "site:diariomedico.com farmacia"),

    # --- Busquedas tematicas ES (cubren la prensa regional) ---
    gn("ES", "tema", "Problemas de suministro", '"problemas de suministro" medicamentos'),
    gn("ES", "tema", "Desabastecimiento", "desabastecimiento OR escasez medicamentos farmacias"),
    gn("ES", "tema", "Colegios de farmacéuticos", '"colegio de farmacéuticos" OR "colegio oficial de farmacéuticos"'),
    gn("ES", "tema", "Farmacia comunitaria", '"farmacia comunitaria" OR "oficinas de farmacia"'),
    gn("ES", "tema", "Venta online", '"venta online de medicamentos" OR "parafarmacia online" OR "farmacia online"'),
    gn("ES", "tema", "Distribución", "Cofares OR Bidafarma OR Hefame OR \"Alliance Healthcare\" OR \"distribución farmacéutica\""),
    gn("ES", "tema", "Receta electrónica", '"receta electrónica" farmacia'),
    gn("ES", "tema", "Ley del medicamento", '"ley del medicamento" OR "ley de los medicamentos" OR "anteproyecto de ley de medicamentos"'),

    # --- Generalistas y economicas ES (solo lo que matchea tema) ---
    rss("ES", "general", "El País Salud", "https://feeds.elpais.com/mrss-s/pages/ep/site/elpais.com/section/salud-y-bienestar/portada", True),
    rss("ES", "general", "El Mundo Salud", "https://e00-elmundo.uecdn.es/elmundosalud/rss/portada.xml", True),
    rss("ES", "general", "La Vanguardia Salud", "https://www.lavanguardia.com/rss/vida/salud.xml", True),
    rss("ES", "general", "ABC Sociedad", "https://www.abc.es/rss/2.0/sociedad/", True),
    rss("ES", "general", "El Confidencial", "https://rss.elconfidencial.com/alma-corazon-vida/", True),
    rss("ES", "general", "El Español Salud", "https://www.elespanol.com/rss/ciencia/salud/", True),
    rss("ES", "general", "El Debate Sociedad", "https://www.eldebate.com/rss/sociedad.xml", True),
    rss("ES", "general", "20minutos Salud", "https://www.20minutos.es/rss/salud/", True),
    rss("ES", "general", "Europa Press Salud", "https://www.europapress.es/rss/rss.aspx?ch=00667", True),
    rss("ES", "general", "COPE", "https://www.cope.es/api/es/news/rss.xml", True),
    rss("ES", "general", "eldiario.es", "https://www.eldiario.es/rss/", True),
    rss("ES", "general", "Newtral", "https://www.newtral.es/feed/", True),
    rss("ES", "general", "Maldita", "https://maldita.es/feed/", True),
    rss("ES", "general", "Cinco Días", "https://feeds.elpais.com/mrss-s/pages/ep/site/cincodias.elpais.com/portada", True),
    rss("ES", "general", "Expansión Empresas", "https://e00-expansion.uecdn.es/rss/empresas.xml", True),
    rss("ES", "general", "Invertia", "https://www.elespanol.com/rss/invertia/", True),
    gn("ES", "general", "La Razón", "site:larazon.es medicamentos OR farmacia", filtro=True),
    gn("ES", "general", "Cadena SER", "site:cadenaser.com medicamentos OR farmacia", filtro=True),
    gn("ES", "general", "El Economista", "site:eleconomista.es medicamentos OR farmacia", filtro=True),

    # --- Startups / inversion (rondas de LUDA y competidores) ---
    rss("ES", "startup", "El Referente", "https://elreferente.es/feed/", True),
    rss("ES", "startup", "Emprendedores", "https://emprendedores.es/feed/", True),
    rss("ES", "startup", "Startupxplore", "https://startupxplore.com/es/blog/feed", True),
    rss("EU", "startup", "Sifted", "https://sifted.eu/feed", True),

    # --- Europa: oficiales ---
    rss("EU", "oficial", "EMA", "https://www.ema.europa.eu/en/news.xml"),
    rss("EU", "oficial", "Comisión Europea — Salud", "https://health.ec.europa.eu/node/2/rss_en", True),
    rss("EU", "oficial", "EUR-Lex DOUE L", "https://eur-lex.europa.eu/EN/display-feed.rss?rssId=162", True),
    rss("EU", "oficial", "Medicines for Europe", "https://www.medicinesforeurope.com/feed/"),

    # --- Europa: prensa ---
    rss("EU", "sector", "PharmaTimes", "https://pharmatimes.com/feed/", True),
    rss("EU", "sector", "Endpoints", "https://endpts.com/feed/", True),
    rss("EU", "sector", "Health Policy Watch", "https://healthpolicy-watch.news/feed/", True),
    gn("EU", "sector", "Euractiv Health", "site:euractiv.com medicines OR pharmacy OR shortages", "en"),
    gn("EU", "sector", "Politico EU Health", "site:politico.eu medicines OR pharmaceutical", "en"),
    gn("EU", "sector", "Pharmaceutical Journal", "site:pharmaceutical-journal.com", "en"),
    gn("EU", "tema", "Shortages EU", '"medicine shortages" OR "drug shortages" Europe', "en"),
    gn("EU", "tema", "Critical Medicines Act", '"Critical Medicines Act"', "en"),
    gn("EU", "tema", "EU pharma package", '"pharmaceutical package" OR "pharmaceutical legislation" EU', "en"),
    gn("EU", "tema", "Online pharmacy EU", '"online pharmacy" Europe OR "e-pharmacy" Europe', "en"),
]
