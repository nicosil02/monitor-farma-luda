"""python -m pytest -q   (o python test_monitor.py)"""
import monitor


def test_parse_rss_y_atom():
    rss = """<rss><channel><item><title><![CDATA[Faltan parches &amp; geles]]></title>
    <link>https://a.es/1</link><pubDate>Wed, 21 Oct 2026 07:00:00 GMT</pubDate>
    <description>&lt;p&gt;Según LUDA Partners&lt;/p&gt;</description></item></channel></rss>"""
    atom = """<feed><entry><title>EMA news</title><link href="https://ema.eu/x"/>
    <updated>2026-10-29T12:00:00+01:00</updated></entry></feed>"""
    a, = monitor.parse_rss(rss)
    assert a == {"url": "https://a.es/1", "titulo": "Faltan parches & geles",
                 "resumen": "Según LUDA Partners", "fecha_pub": "2026-10-21T07:00:00Z", "dominio": "a.es"}
    b, = monitor.parse_rss(atom)
    assert b["url"] == "https://ema.eu/x" and b["fecha_pub"] == "2026-10-29T11:00:00Z"


def test_separar_medio():
    assert monitor.separar_medio("La aspirina falta - El Debate") == ("La aspirina falta", "El Debate")
    assert monitor.separar_medio("Sin medio") == ("Sin medio", None)


def test_temas_y_seccion():
    assert monitor.temas("Más de 850 medicamentos con problemas de suministro en farmacias")[0] == "Desabastecimiento"
    # "farmacia" suelta ya no es tema; el negocio de la farmacia si
    assert "Farmacia comunitaria" not in monitor.temas("Gripe y vacunas: las dudas que puedes consultar en tu farmacia")
    assert "Farmacia comunitaria" in monitor.temas("Los colegios de farmacéuticos piden ayudas para la farmacia rural")
    assert monitor.seccion({"bloque": "oficial", "region": "ES", "filtro": 0, "temas": ["Regulación"],
                            "titulo": "Aemps retira un blanqueador dental de venta en Temu"}) is None


def test_entradilla():
    og = ('<meta property="og:description" content="Los problemas de suministro en su división de '
          'biológicos lastran los ingresos del tercer trimestre de la farmacéutica suiza.">')
    assert monitor.entradilla(og).startswith("Los problemas de suministro")
    # sin meta: primer parrafo largo, saltando paginas de error (MarketScreener 2026-10-30)
    err = "<p>Reference #18.35a162be https://errors.edgesuite.net/18.35a162be.1793331708.7d5524dd</p>"
    assert monitor.entradilla(err) == ""
    largo = "<p>" + "palabra " * 80 + "</p>"
    r = monitor.entradilla(largo)
    assert r.endswith("…") and len(r) <= monitor.LARGO_RESUMEN + 1


def test_telegram_no_corta_recuadros(monkeypatch):
    enviados = []
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "x")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "y")
    monkeypatch.setattr(monitor.requests, "post",
                        lambda url, data, timeout: enviados.append(data["text"]) or type("R", (), {"ok": True})())
    bloque = "<blockquote>" + "\n".join(["linea " * 60] * 4) + "</blockquote>"
    assert monitor.telegram("\n".join([bloque] * 6))
    assert len(enviados) > 1
    assert all(t.count("<blockquote>") == t.count("</blockquote>") for t in enviados)
    assert monitor.temas("Lieferengpässe bei Arzneimitteln")[0] == "Desabastecimiento"
    assert monitor.temas("La industria farmacéutica invierte en I+D") == ["Sanidad general"]
    assert monitor.temas("Nueva herramienta de IA para bancos") == []
    base = {"bloque": "general", "region": "ES", "filtro": 1, "titulo": "x"}
    assert monitor.seccion({**base, "temas": []}) is None              # amplia sin tema: fuera
    assert monitor.seccion({**base, "region": "EU", "temas": ["Regulación"]}) == "Europa"
    assert monitor.seccion({**base, "region": "GLOBAL", "bloque": "tema", "temas": ["Desabastecimiento"]}) is None
    assert monitor.seccion({**base, "region": "GLOBAL", "bloque": "mencion", "temas": []}) == "Mención LUDA"
    # ruido de la primera corrida real
    farma = {**base, "filtro": 0, "temas": ["Farmacia comunitaria"]}
    assert monitor.seccion({**farma, "titulo": "Reforma de la Ley de Farmacias en Mendoza"}) is None
    assert monitor.seccion({**farma, "titulo": "Farmacia rural", "dominio": "diario.com.mx"}) is None
    assert monitor.seccion({**farma, "titulo": "Pharmacist job with CV-Library"}) is None
    assert monitor.seccion({**farma, "titulo": "Las farmacias de Baleares sin Loniten"}) == "Farmacia comunitaria"
    assert monitor.seccion({**base, "region": "EU", "titulo": "Lilly cash-pay", "temas": ["Sanidad general"]}) is None
    assert monitor.temas("El farmacéutico que ayudó en el accidente") == []


def test_competencia_solo_en_contexto_farmacia():
    assert "Competencia y socios" in monitor.temas("Hefame refuerza su marca Interapothek")
    assert "Competencia y socios" in monitor.temas("Uber Eats amplía la venta de parafarmacia")
    assert "Competencia y socios" not in monitor.temas("Los riders de Glovo vuelven a la huelga")


def test_fragmentos_de_la_nota():
    import reputacion
    page = ("<p>Menú del sitio con poco texto</p><p>La escasez de parches se agrava en Almería este mes.</p>"
            "<p>Según los datos de LUDA Partners, el Lenzetto es el que más falta.</p>"
            "<p>El colegio pide calma a las pacientes de la provincia.</p><p>Otra nota sin relación alguna aquí.</p>")
    f = reputacion.fragmentos(page)
    assert len(f) == 3 and "LUDA" in f[1]
    assert reputacion.fragmentos("<p>Nada que ver con la empresa ni con farmacias.</p>") == []


def test_agrupar_por_cualquier_miembro():
    # c se parece a b pero no a a (caso PGEU / Papa): igual va con el grupo
    sims = [[1, .95, .90, .2], [.95, 1, .94, .2], [.90, .94, 1, .2], [.2, .2, .2, 1]]
    assert monitor.agrupar(["a", "b", "c", "d"], sims, 0.93) == [0, 0, 0, 1]


def test_ruido_institucional_fuera():
    base = {"bloque": "sector", "region": "ES", "filtro": 0, "temas": ["Farmacia comunitaria"]}
    for t in ["La PGEU traslada a León XIV el compromiso de la farmacia",
              "El Colegio de Farmacéuticos traslada su profundo pesar por el accidente de Urda",
              "NUEVA EDICIÓN DE TARDE. AULA DISPENSACIÓN: RECETA ELECTRÓNICA"]:
        assert monitor.seccion({**base, "titulo": t}) is None, t
    assert monitor.seccion({**base, "titulo": "Colegio registra denuncias", "dominio": "www.teletica.com"}) is None
    # "a pesar de" y muertes por falta de farmacos si van
    assert monitor.seccion({**base, "titulo": "Las farmacias abren a pesar de la huelga"}) == "Farmacia comunitaria"
    assert monitor.seccion({**base, "titulo": "Muere un paciente sin su fármaco en una farmacia"}) == "Farmacia comunitaria"


def test_tono_es_para_luda_no_para_la_noticia():
    """Casos de la medicion 2026-10-29 (reputacion.py): reales + negativos de prueba."""
    from reputacion import clasificar
    t = lambda titulo, frase: clasificar(titulo, [frase])
    # escasez citando datos de LUDA: neutro, no negativo
    r = t("La escasez de medicamentos afecta a Evopad y Loniten en Castilla y León",
          "Son los fármacos que acumulan más notificaciones de falta en las farmacias de la red digital LUDA Partners, según el estudio.")
    assert (r["tono"], r["rol"], r["riesgo"]) == ("neutro", "fuente de datos", "bajo")
    assert t("x", "Según LUDA Partners, el desabastecimiento de Ozempic se agravó en marzo.")["tono"] == "neutro"
    assert t("x", "En un mes de alta demanda, LUDA Partners registró 11.785 incidencias.")["tono"] == "neutro"
    assert t("x", "La situación es crítica, advierte LUDA Partners.")["tono"] == "neutro"
    # positivos
    assert t("Aptadel y LUDA Partners cierran rondas", "LUDA Partners ha captado más de 750.000 euros")["tono"] == "positivo"
    assert t("x", "La escasez ya había sido anticipada por LUDA Trends, un sistema de IA predictiva.")["tono"] == "positivo"
    # negativos: protagonista -> riesgo alto
    r = t("La CNMC abre un expediente a LUDA Partners", "La CNMC abre un expediente sancionador a LUDA Partners por prácticas anticompetitivas.")
    assert (r["tono"], r["rol"], r["riesgo"]) == ("negativo", "protagonista", "alto")
    assert t("x", "Farmacéuticos denuncian que LUDA Partners cobra comisiones abusivas.")["tono"] == "negativo"
    assert t("x", "Un fallo en la plataforma de LUDA dejó a cientos de farmacias sin servicio.")["tono"] == "negativo"
    assert t("x", "Expertos cuestionan la fiabilidad de las predicciones de LUDA Trends.")["tono"] == "negativo"


def test_aemps_diff(tmp_path, monkeypatch):
    monkeypatch.setattr(monitor, "DB", tmp_path / "t.db")
    assert monitor.aemps([{"cn": 1, "activo": True}, {"cn": 2, "activo": True}], "2026-10-28") == (0, 0)
    assert monitor.aemps([{"cn": 2, "activo": True}, {"cn": 3, "activo": True}], "2026-10-29") == (1, 1)
    c = monitor.conectar()
    assert c.execute("SELECT visto_desde FROM aemps WHERE cn='3'").fetchone()[0] == "2026-10-29"
    assert c.execute("SELECT resuelto_en FROM aemps WHERE cn='1'").fetchone()[0] == "2026-10-29"


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-q"]))
