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

# --- Imports de utils ---
if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))

from movie_utils import (
    _load_state, is_published, api_get, get_synopsis_chain, enrich_movie_basic,
    load_config, get_deep_research_data
)

# --- Configuración ---
ROOT = Path(__file__).resolve().parents[1]
STATE_DIR = ROOT / "output" / "state"
STATE_DIR.mkdir(parents=True, exist_ok=True)
NEXT_FILE = STATE_DIR / "next_release.json"

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
        if creds.expired and creds.refresh_token: creds.refresh(Request())
        youtube = build('youtube', 'v3', credentials=creds)
    except Exception as e:
        logging.error(f"Error auth YouTube: {e}")
        return None

    # --- Paso 1: YouTube Search ---
    try:
        current_year = datetime.now().year
        queries = [
            "official movie trailer new this week",
            "netflix official movie trailer",
            "prime video official movie trailer",
            "disney plus official movie trailer",
            "max hbo official movie trailer",
            "apple tv official movie trailer"
        ]
        
        start_date = (datetime.now(timezone.utc) - timedelta(days=14)).strftime('%Y-%m-%dT%H:%M:%SZ')
        logging.info(f"📡 Buscando estrenos y novedades streaming desde {start_date}...")
        
        all_items = []
        seen_ids = set()

        for q in queries:
            logging.info(f"   > Consultando: '{q}'...")
            next_page = None
            for i in range(2): 
                req = youtube.search().list(part="id,snippet", q=q, type="video", maxResults=50, 
                                            order="relevance", pageToken=next_page, publishedAfter=start_date)
                resp = req.execute()
                items = resp.get("items", [])
                
                for item in items:
                    vid = item['id']['videoId']
                    if vid not in seen_ids:
                        seen_ids.add(vid)
                        all_items.append(item)
                
                next_page = resp.get('nextPageToken')
                if not next_page: break
        
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
        if 'official' in t and 'trailer' in t:
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
        
        prompt = f"""Analiza estos vídeos. Extrae solo PELÍCULAS (Feature Films) que sean estrenos recientes o próximos.
        EXCLUYE: Series, TV Shows.
        JSON array: [{{'pelicula': str, 'año': int, 'index': int, 'plataforma': str (opcional)}}]
        List:\n{titles_str}"""
        
        # New SDK call
        resp = client.models.generate_content(
            model=GEMINI_MODEL, 
            contents=prompt,
        )
        
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
            logging.info(f"   [x] TMDB: No encontrado '{movie_name}'")
            continue
        
        tmdb_movie = res["results"][0]
        tmdb_year = str(tmdb_movie.get("release_date", "")[:4])
        
        cand_year = cand.get('año', datetime.now().year)
        target_years = [str(cand_year-1), str(cand_year), str(cand_year+1)]
        if tmdb_year not in target_years: 
            logging.info(f"   [x] Descartado '{movie_name}': Año incorrecto ({tmdb_year} vs {target_years})")
            continue
        
        orig_lang = tmdb_movie.get("original_language", "en")
        if orig_lang in excluded_langs:
             logging.info(f"   [x] Descartado '{movie_name}': Mercado Indio/Asiático ({orig_lang})")
             continue

        if is_published(tmdb_movie["id"]): 
            logging.info(f"   [x] Descartado '{movie_name}': YA PUBLICADO.")
            continue
        
        data = enrich_movie_basic(tmdb_movie["id"], movie_name, cand_year, cand['trailer_url'])
        
        if data and data.get('has_poster'):
            is_streaming = False
            video_title_lower = cand.get('video_title_orig', '').lower()
            ia_plat = cand.get('plataforma', 'Cine')
            
            if ia_plat != 'Cine' or any(k in video_title_lower for k in streaming_keywords):
                is_streaming = True
                data['ia_platform_from_title'] = ia_plat if ia_plat != 'Cine' else "Streaming"

            days_limit = 365 if is_streaming else 60
            
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
                            logging.info(f"   [x] Descartado '{movie_name}': {type_str} antiguo ({age_days} días > {days_limit}) o no disponible en ES.")
                            continue
                except: pass
            
            data['views'] = cand['views']
            data['upload_date'] = cand['upload_date']
            data['needs_web'] = not bool(data.get('sinopsis'))
            enriched.append(data)
            logging.info(f"   [V] CANDIDATO VÁLIDO ({'Streaming 📺' if is_streaming else 'Cine 🎬'}): {movie_name}")
        else:
            logging.info(f"   [x] Descartado '{movie_name}': Sin datos básicos.")

    if not enriched: 
        logging.info("❌ No se encontraron candidatos válidos.")
        return None

    # --- Paso 5: Selección ---
    def calculate_score(item):
        views = item.get('views', 0)
        try:
            pub_time = datetime.strptime(item['upload_date'], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
            hours_ago = (datetime.now(timezone.utc) - pub_time).total_seconds() / 3600
            recency_bonus = 5 if hours_ago < 24 else 1
            return views * recency_bonus
        except:
            return views

    enriched.sort(key=calculate_score, reverse=True)
    selected = enriched[0]

    # --- DEEP RESEARCH ---
    logging.info(f"🕵️  Deep Research para: {selected['titulo']}...")
    main_actor_ref = selected.get('actors', [selected['titulo']])[0]
    deep_data = get_deep_research_data(selected['titulo'], selected['fecha_estreno'][:4], main_actor_ref, selected['tmdb_id'])

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

    # --- RESUMEN FINAL ---
    logging.info("\n" + "═"*60)
    logging.info(f"🎬 RESUMEN DE SELECCIÓN: {selected['titulo']} ({selected['fecha_estreno'][:4]})")
    logging.info(f"🔗 Trailer: {selected.get('trailer_url', 'N/A')}")
    logging.info(f"🧠 Estrategia: {selected.get('hook_angle', 'N/A')}")
    logging.info("═"*60 + "\n")

    payload = {**selected, "seleccion_generada": datetime.now(timezone.utc).isoformat() + "Z"}
    NEXT_FILE.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    logging.info(f"✅ SELECCIONADA: {selected['titulo']}")
    return payload

if __name__ == "__main__":
    find_and_select_next()