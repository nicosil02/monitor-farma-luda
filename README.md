# monitor-farma-luda

Monitor de medios para LUDA Partners: España + Europa (del resto del mundo, solo menciones de LUDA). Llega por Telegram.

```bash
pip install -r requirements.txt
cp .env.example .env       # TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
python monitor.py          # 1 vez al dia (mañana de Madrid): fetch + AEMPS + resumen por Telegram
python monitor.py alertas  # cada 2 h: menciones nuevas de LUDA apenas salen
python -m pytest -q
```

**En el servidor (sin tu PC):** `.github/workflows/monitor.yml` corre en GitHub Actions cada 2 h (alertas) y a las 06 UTC (resumen). Secrets del repo: `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`. `monitor.db` persiste en el cache de Actions; para mandar el resumen a mano: Actions → Monitor LUDA → Run workflow → resumen.

- `fuentes.py`: ~85 fuentes (RSS + Google News para medios sin RSS o que bloquean bots). Probadas el 2026-10-29.
- `monitor.py`: descarga, clasifica por tema, junta la misma historia contada por varios medios, arma el resumen y lo manda.
- `reputacion.py`: por cada mención abre la nota, confirma que LUDA aparece (descarta falsos positivos de Google), saca la cita y evalúa tono PARA LUDA (🟢 ⚪ 🔴), rol (protagonista / fuente de datos / secundaria) y riesgo, con reglas medidas contra modelos de Hugging Face (ver docstring). Negativo → alerta con 🚨.
- Agrupado de la misma historia: embeddings `intfloat/multilingual-e5-small` (Hugging Face, local), umbral 0.93 calibrado con notas reales; sin modelo cae a TF-IDF.
- AEMPS (CIMA): foto diaria de problemas de suministro; el resumen muestra los que entran y los que se resuelven.

Viene del motor de noticias del Radar Legislativo (Base_legislativa/noticias), sin la parte Perú/Ecuador.
