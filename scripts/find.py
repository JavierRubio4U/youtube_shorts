# scripts/find.py
import logging
import json
from pathlib import Path
from datetime import datetime, timezone, timedelta
from googleapiclient.discovery import build
from google import genai
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from gemini_config import GEMINI_MODEL
import os
import sys
import time

# --- Imports de utils ---
if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))

from movie_utils import (
    _load_state, is_published, api_get, get_synopsis_chain, enrich_movie_basic,
    load_config, get_deep_research_data, log_discard
)

# --- Configuración ---
ROOT = Path(__file__).resolve().parents[1]
STATE_DIR = ROOT / "output" / "state"
STATE_DIR.mkdir(parents=True, exist_ok=True)
TMP_DIR = ROOT / "assets" / "tmp"
TMP_DIR.mkdir(parents=True, exist_ok=True)
NEXT_FILE = TMP_DIR / "next_release.json"

logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')

def find_and_select_next():
    config = load_config()
    if not config: return None

    logging.info("🔎 INICIANDO BÚSQUEDA DE PELÍCULA (MODO HÍBRIDO + ANTI-BOLLYWOOD)...")

    # --- YouTube Service ---
    TOKEN_FILE = STATE_DIR / "youtube_token.json"
    if not TOKEN_FILE.exists():
        logging.error("Falta youtube_token.json")
        return None

    try:
        with open(TOKEN_FILE, 'r') as f: token_data = json.load(f)
        creds = Credentials(token=token_data['token'], refresh_token=token_data['refresh_token'],
                            token_uri=token_data['token_uri'], client_id=token_data['client_id'],
                            client_secret=token_data['client_secret'], scopes=token_data['scopes'])
        if creds.expired and creds.refresh_token: 
            creds.refresh(Request())
            with open(TOKEN_FILE, 'w') as token:
                token.write(creds.to_json())
        youtube = build('youtube', 'v3', credentials=creds)
    except Exception as e:
        logging.error(f"Error auth YouTube: {e}")
        return None

    # --- Paso 1: YouTube Search (Optimizado para ahorrar cuota) ---
    try:
        current_year = datetime.now().year
        next_year = current_year + 1
        
        # Consolidamos en 3 búsquedas potentes
        queries = [
            f"official movie trailer {current_year} {next_year}", # General estrenos cine
            "netflix disney amazon prime apple movie trailer", # General streaming
            f"tráiler oficial película {current_year} {next_year}", # Búsqueda específica en español
            f"super bowl movie trailers {current_year}" # Eventos grandes
        ]
        
        # Ampliamos el margen a 15 días para capturar mejor las novedades de catálogo y trailers mensuales
        start_date = (datetime.now(timezone.utc) - timedelta(days=15)).strftime('%Y-%m-%dT%H:%M:%SZ')
        logging.info(f"📡 Buscando novedades desde {start_date} (Modo Ahorro Cuota)...")
        
        all_items = []
        seen_ids = set()

        for q in queries:
            logging.info(f"   > Consultando: '{q}'...")
            # Hacemos solo 1 petición por query (maxResults=50)
            req = youtube.search().list(part="id,snippet", q=q, type="video", maxResults=50, 
                                        order="relevance", publishedAfter=start_date)
            resp = req.execute()
            items = resp.get("items", [])
            
            for item in items:
                vid = item['id']['videoId']
                if vid not in seen_ids:
                    seen_ids.add(vid)
                    all_items.append(item)
        
        logging.info(f"📥 Total vídeos únicos encontrados: {len(all_items)}")

        videos = []
        video_ids = [item['id']['videoId'] for item in all_items]
        
        # Batch stats
        stats_dict = {}
        for i in range(0, len(video_ids), 50):
            chunk = video_ids[i:i+50]
            if not chunk: continue
            s_resp = youtube.videos().list(part="statistics", id=','.join(chunk)).execute()
            for item in s_resp.get('items', []):
                stats_dict[item['id']] = int(item['statistics'].get('viewCount', 0))

        for item in all_items:
            vid = item['id']['videoId']
            videos.append({
                'title': item['snippet']['title'],
                'videoId': vid,
                'upload_date': item['snippet']['publishedAt'],
                'url': f"https://www.youtube.com/watch?v={vid}",
                'views': stats_dict.get(vid, 0)
            })

    except Exception as e:
        logging.error(f"Error API YouTube: {e}")
        return None

    # --- Paso 2: Filtros Anti-Serie ---
    banned_words = [
        "season", "temporada", "series", "episode", "capitulo", "capítulo", "vol.", "part 2",
        "hindi", "dubbed", "fan made", "concept trailer", "un-official", "parody",
        "tamil", "telugu", "kannada", "malayalam" # Bloqueo regional extra
    ]
    filtered = []
    for v in videos:
        t = v['title'].lower()
        # Aceptamos Trailer o Teaser
        is_promo = any(kw in t for kw in ['trailer', 'tráiler', 'teaser'])
        
        if is_promo:
            # Para 'Trailer', seguimos exigiendo 'Official' para evitar basura fan-made
            # Pero para 'Teaser' de grandes eventos, somos más flexibles
            is_official = 'official' in t or 'oficial' in t
            
            # Un 'Teaser' suele ser oficial de por sí si viene de canales grandes,
            # pero el script confía en el Gemini Filter posterior para descartar fan-made.
            if is_official or 'teaser' in t:
                if not any(bw in t for bw in banned_words):
                    filtered.append(v)
    
    logging.info(f"🔍 Filtrado (Anti-Series): Quedan {len(filtered)} candidatos.")
    if not filtered: return None

    # --- Paso 3: Gemini Filter ---
    try:
        client = genai.Client(api_key=config["GEMINI_API_KEY"])
        
        top_candidates = filtered[:120]
        titles_str = "\n".join(f"{i+1}. {v['title']}" for i, v in enumerate(top_candidates)) 
        
        logging.info(f"🤖 Enviando {len(top_candidates)} títulos a Gemini...")
        
        prompt = f"""Analiza estos vídeos de YouTube (trailers, novedades). 
        Extrae PELÍCULAS (Feature Films) que cumplan UNA de estas condiciones:
        1. Son estrenos recientes o próximos ({current_year}-{next_year}).
        2. Son películas RECIENTES ({current_year-1}-{current_year}) que están en plataformas de streaming (Netflix, Amazon Prime, Apple TV+, Disney+, etc.).
        
        **VALIDACIÓN CRÍTICA**: Si el título de la película es común (ej: "Dolly", "Smile", "Alone"), verifica doblemente que el tráiler de YouTube pertenece realmente a esa producción. Si el tráiler es de una película indie y hay un blockbuster con el mismo nombre en desarrollo, NO los confundas.
        
        EXCLUYE: Series, documentales, películas antiguas (anteriores a 2024).
        
        **FORMATO PLATAFORMA**: Usa solo nombres simples (ej: Cine, Netflix, Disney+, Prime Video). NADA de chistes ni comentarios adicionales.
        
        JSON array: [{{'pelicula': str, 'año': int, 'index': int, 'plataforma': str (opcional)}}]
        List:\n{titles_str}"""
        
        # New SDK call with retry
        max_retries = 3
        resp = None
        for attempt in range(max_retries):
            try:
                resp = client.models.generate_content(
                    model=GEMINI_MODEL, 
                    contents=prompt,
                )
                break
            except Exception as e:
                error_str = str(e)
                if "503" in error_str or "Deadline" in error_str or "429" in error_str:
                    if attempt < max_retries - 1:
                        logging.warning(f"⚠️ Error temporal de Gemini en búsqueda ({e}). Reintentando... ({attempt+1}/{max_retries})")
                        time.sleep(5)
                        continue
                raise e
        
        # --- FIX: Limpieza robusta y Debug ---
        raw_text = resp.text if resp.text else ""
        if not raw_text:
            logging.error(f"❌ Gemini devolvió respuesta vacía.")
            return None

        # Intentar limpiar JSON markdown
        clean_text = raw_text.strip()
        if "```json" in clean_text:
            clean_text = clean_text.split("```json")[1].split("```")[0].strip()
        elif "```" in clean_text:
            clean_text = clean_text.split("```")[1].split("```")[0].strip()

        try:
            ai_movies = json.loads(clean_text)
            logging.info(f"✅ Gemini identificó {len(ai_movies)} películas potenciales.")
        except json.JSONDecodeError as je:
            logging.error(f"❌ Error decodificando JSON de Gemini. Respuesta recibida:\n{raw_text[:200]}...") # Loguea solo el inicio
            return None
        
        candidates = []
        for am in ai_movies:
            idx = am.get('index', 0) - 1
            if 0 <= idx < len(filtered):
                v = filtered[idx]
                candidates.append({
                    **am, 
                    'trailer_url': v['url'], 
                    'views': v['views'], 
                    'upload_date': v['upload_date'],
                    'video_title_orig': v['title']
                })
    except Exception as e:
        logging.error(f"Error Gemini Filter (General): {e}")
        return None

    # --- Paso 4: TMDB Enrich & Filtros Estrictos ---
    logging.info("📚 Validando con Lógica Híbrida (Cine vs Streaming)...")
    enriched = []
    
    streaming_keywords = ["netflix", "prime video", "disney", "hbo", "max", "apple tv", "hulu", "peacock"]
    excluded_langs = ['hi', 'te', 'ta', 'ml', 'kn', 'pa', 'ur']

    for cand in candidates:
        movie_name = cand['pelicula']
        
        res = api_get("/search/movie", {"query": movie_name})
        if not res or not res.get("results"): 
            reason = "No encontrado en TMDB"
            logging.info(f"   [x] TMDB: {reason} '{movie_name}'")
            log_discard(movie_name, reason)
            continue
        
        tmdb_movie = res["results"][0]
        tmdb_id = tmdb_movie["id"]
        tmdb_year = str(tmdb_movie.get("release_date", "")[:4])
        
        cand_year = cand.get('año')
        if not cand_year:
            cand_year = datetime.now().year
        
        is_streaming_ia = cand.get('plataforma', 'Cine') not in ['Cine', 'Teatros', 'None', None]
        
        # Filtro de año: Estricto para cine, y ventana de 2 años para streaming (catálogo reciente)
        min_year = int(datetime.now().year) - 2 # Permitimos hasta 2024 si estamos en 2026
        if not is_streaming_ia:
            target_years = [str(int(cand_year)-1), str(int(cand_year)), str(int(cand_year)+1)]
            if tmdb_year not in target_years: 
                reason = f"Año incorrecto para estreno cine ({tmdb_year} vs {target_years})"
                logging.info(f"   [x] Descartado '{movie_name}': {reason}")
                log_discard(movie_name, reason, tmdb_id)
                continue
        else:
            if int(tmdb_year) < min_year:
                reason = f"Catálogo demasiado antiguo ({tmdb_year} < {min_year})"
                logging.info(f"   [x] Descartado '{movie_name}': {reason}")
                log_discard(movie_name, reason, tmdb_id)
                continue
        
        orig_lang = tmdb_movie.get("original_language", "en")
        if orig_lang in excluded_langs:
             reason = f"Mercado Indio/Asiático ({orig_lang})"
             logging.info(f"   [x] Descartado '{movie_name}': {reason}")
             log_discard(movie_name, reason, tmdb_id)
             continue

        if is_published(tmdb_id): 
            reason = "YA PUBLICADO"
            logging.info(f"   [x] Descartado '{movie_name}': {reason}.")
            log_discard(movie_name, reason, tmdb_id)
            continue
        
        data = enrich_movie_basic(tmdb_movie["id"], movie_name, cand_year, cand['trailer_url'])
        
        if not data:
            reason = "Error al enriquecer datos o película no encontrada en TMDB"
            logging.info(f"   [x] Descartado '{movie_name}': {reason}.")
            log_discard(movie_name, reason, tmdb_id)
            continue
            
        if data.get('has_poster'):
            is_streaming = False
            video_title_lower = cand.get('video_title_orig', '').lower()
            ia_plat = cand.get('plataforma', 'Cine')
            
            if ia_plat != 'Cine' or any(k in video_title_lower for k in streaming_keywords):
                is_streaming = True
                data['ia_platform_from_title'] = ia_plat if ia_plat != 'Cine' else "Streaming"

            # Si es streaming de las plataformas TOP, permitimos hasta 2 años de antigüedad (catálogo reciente)
            days_limit = 730 if is_streaming else 60
            
            if data.get('fecha_estreno'):
                try:
                    release_date = datetime.strptime(data['fecha_estreno'], "%Y-%m-%d")
                    age_days = (datetime.now() - release_date).days
                    
                    if age_days > days_limit:
                        trailer_date = datetime.strptime(cand['upload_date'], "%Y-%m-%dT%H:%M:%SZ")
                        trailer_age = (datetime.now() - trailer_date).days
                        
                        streaming_platforms = data.get('platforms', {}).get('streaming', [])
                        target_platforms = ["Netflix", "Amazon Prime Video", "Disney Plus"]
                        is_available_es = any(p in sp for sp in streaming_platforms for p in target_platforms if "(US)" not in sp)

                        if is_streaming and trailer_age <= 7 and is_available_es:
                             logging.info(f"   [!] Aceptado '{movie_name}' (Streaming ES): Estreno antiguo ({age_days}d) pero trailer NUEVO ({trailer_age}d) y disponible en España.")
                        else:
                            type_str = "Streaming" if is_streaming else "Cine"
                            reason = f"{type_str} antiguo ({age_days} días > {days_limit}) o no disponible en ES"
                            logging.info(f"   [x] Descartado '{movie_name}': {reason}.")
                            log_discard(movie_name, reason, tmdb_id)
                            continue
                except: pass
            
            data['views'] = cand['views']
            data['upload_date'] = cand['upload_date']
            data['needs_web'] = not bool(data.get('sinopsis'))
            enriched.append(data)
            logging.info(f"   [V] CANDIDATO VÁLIDO ({'Streaming 📺' if is_streaming else 'Cine 🎬'}): {movie_name}")
        else:
            reason = "No tiene póster ni backdrop (imprescindible para la intro del Short)"
            logging.info(f"   [x] Descartado '{movie_name}': {reason}.")
            log_discard(movie_name, reason, tmdb_id)

    if not enriched: 
        logging.info("❌ No se encontraron candidatos válidos.")
        return None

    # --- Paso 5: Selección ---
    for item in enriched:
        views = item.get('views', 0)
        score = views
        try:
            pub_time = datetime.strptime(item['upload_date'], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
            hours_ago = (datetime.now(timezone.utc) - pub_time).total_seconds() / 3600
            recency_bonus = 5 if hours_ago < 24 else 1
            score = views * recency_bonus
            logging.info(f"   [SCORE] {item.get('titulo', 'N/A')}: {views:,} views × {recency_bonus} bonus = {score:,}")
        except:
            logging.info(f"   [SCORE] {item.get('titulo', 'N/A')}: {views:,} views (sin recency bonus)")
        item['score'] = score

    enriched.sort(key=lambda x: x.get('score', 0), reverse=True)
    selected = enriched[0]
    # Usar el score ya calculado para evitar log duplicado
    final_score = selected['score']

    # --- DEEP RESEARCH ---
    logging.info(f"🕵️  Deep Research para: {selected['titulo']}...")
    main_actor_ref = selected.get('actors', [selected['titulo']])[0]
    deep_data = get_deep_research_data(selected['titulo'], selected['fecha_estreno'][:4], main_actor_ref, selected['tmdb_id'], selected.get('sinopsis', ''))

    if deep_data:
        strategy = deep_data.get('hook_angle', 'CURIOSITY')
        logging.info("\n" + "█"*60)
        logging.info(f"🧠 ESTRATEGIA: {strategy}")
        logging.info(f"🤫 Salseo: {deep_data.get('movie_curiosity', 'N/A')}")
        logging.info("-" * 60 + "\n")
        
        if deep_data.get('synopsis'): selected['sinopsis'] = deep_data['synopsis']
        if deep_data.get('platform'): selected['ia_platform_from_title'] = deep_data['platform']
        selected['actor_reference'] = deep_data.get('actor_reference')
        selected['director'] = deep_data.get('director')
        selected['movie_curiosity'] = deep_data.get('movie_curiosity')
        selected['hook_angle'] = strategy
    elif selected.get('needs_web'):
        selected['sinopsis'] = get_synopsis_chain(selected['titulo'], selected['año'], selected['tmdb_id'])
        selected['hook_angle'] = 'PLOT'

    # --- VALIDACIÓN FINAL SINOPSIS ---
    final_sinopsis = selected.get('sinopsis', '').strip()
    if not final_sinopsis or len(final_sinopsis) < 10:
        logging.error(f"❌ RECHAZADA: '{selected['titulo']}' no tiene sinopsis válida tras todos los intentos. Pasando a la siguiente...")
        return None

    payload = {**selected, "seleccion_generada": datetime.now(timezone.utc).isoformat() + "Z"}
    NEXT_FILE.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    logging.info(f"✅ SELECCIONADA: {selected['titulo']} (Score: {int(selected.get('score', 0)):,})")
    return payload

if __name__ == "__main__":
    find_and_select_next()