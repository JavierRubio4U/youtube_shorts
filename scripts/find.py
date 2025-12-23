# scripts/find.py
import logging
import json
from pathlib import Path
from datetime import datetime, timezone, timedelta
from googleapiclient.discovery import build
import google.generativeai as genai
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from gemini_config import GEMINI_MODEL
import os
import sys

# --- Imports de utils ---
if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))

# Importamos las funciones NECESARIAS de movie_utils
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

    logging.info("🔎 INICIANDO BÚSQUEDA DE PELÍCULA...")

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
        query = "official movie trailer 2025 new this week"
        start_date = (datetime.now(timezone.utc) - timedelta(days=7)).strftime('%Y-%m-%dT%H:%M:%SZ')
        
        all_items = []
        next_page = None
        for _ in range(4): 
            req = youtube.search().list(part="id,snippet", q=query, type="video", maxResults=50, 
                                        order="relevance", pageToken=next_page, publishedAfter=start_date)
            resp = req.execute()
            all_items.extend(resp.get("items", []))
            next_page = resp.get('nextPageToken')
            if not next_page: break
        
        videos = []
        video_ids = [item['id']['videoId'] for item in all_items]
        
        # Batch stats
        stats_dict = {}
        for i in range(0, len(video_ids), 50):
            chunk = video_ids[i:i+50]
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

    # --- Paso 2: Filtros ---
    filtered = [v for v in videos if 'official' in v['title'].lower() and 'trailer' in v['title'].lower()]
    if not filtered: return None

    # --- Paso 3: Gemini Filter ---
    try:
        genai.configure(api_key=config["GEMINI_API_KEY"])
        model = genai.GenerativeModel(GEMINI_MODEL)
        titles_str = "\n".join(f"{i+1}. {v['title']}" for i, v in enumerate(filtered[:60])) 
        prompt = f"""Analiza estos vídeos. Extrae películas de 2025+. Ignora recopilaciones, series, bollywood.
        JSON array: [{{'pelicula': str, 'año': int, 'index': int, 'plataforma': str (opcional)}}]
        List:\n{titles_str}"""
        
        resp = model.generate_content(prompt)
        ai_movies = json.loads(resp.text.strip().lstrip("```json").rstrip("```").strip())
        
        candidates = []
        for am in ai_movies:
            idx = am.get('index', 0) - 1
            if 0 <= idx < len(filtered):
                v = filtered[idx]
                candidates.append({
                    **am, 'trailer_url': v['url'], 'views': v['views'], 'upload_date': v['upload_date']
                })
    except Exception:
        return None

    # --- Paso 4: TMDB Enrich ---
    enriched = []
    for cand in candidates:
        res = api_get("/search/movie", {"query": cand['pelicula']})
        if not res or not res.get("results"): continue
        
        tmdb_movie = res["results"][0]
        if str(tmdb_movie.get("release_date", "")[:4]) not in [str(cand['año']), str(cand['año']+1)]: continue
        if is_published(tmdb_movie["id"]): continue
        
        data = enrich_movie_basic(tmdb_movie["id"], cand['pelicula'], cand['año'], cand['trailer_url'])
        if data and data.get('has_poster'):
            if data.get('fecha_estreno'):
                try:
                    if datetime.strptime(data['fecha_estreno'], "%Y-%m-%d") < datetime.now() - timedelta(days=14):
                        continue
                except: pass
            
            data['views'] = cand['views']
            data['upload_date'] = cand['upload_date']
            data['ia_platform_from_title'] = cand.get('plataforma', 'Cine')
            data['needs_web'] = not bool(data.get('sinopsis'))
            enriched.append(data)

    if not enriched: 
        logging.info("❌ No se encontraron candidatos válidos nuevos.")
        return None

    # --- Paso 5: Selección ---
    enriched.sort(key=lambda x: x['views'], reverse=True)
    selected = enriched[0]

    # --- DEEP RESEARCH (SIEMPRE ACTIVO) ---
    logging.info("🕵️  Consultando al Editor IA (Deep Research)...")
    main_actor_ref = selected.get('actors', [selected['titulo']])[0]
    
    deep_data = get_deep_research_data(selected['titulo'], selected['fecha_estreno'][:4], main_actor_ref, selected['tmdb_id'])

    if deep_data:
        strategy = deep_data.get('hook_angle', 'CURIOSITY')

        # --- LOG VISUAL ---
        logging.info("\n" + "█"*60)
        logging.info(f"🧠 ESTRATEGIA ELEGIDA: {strategy} 🔥")
        logging.info("█"*60)
        logging.info(f"🤫 Salseo:       {deep_data.get('movie_curiosity', 'N/A')}")
        logging.info(f"🎭 Actor Ref:    {deep_data.get('actor_reference', 'N/A')}")
        logging.info(f"📝 Sinopsis:     {deep_data.get('synopsis', 'N/A')[:80]}...")
        logging.info("-" * 60 + "\n")
        
        # Guardado de datos
        if deep_data.get('synopsis'): selected['sinopsis'] = deep_data['synopsis']
        if deep_data.get('platform'): selected['ia_platform_from_title'] = deep_data['platform']
        
        selected['actor_reference'] = deep_data.get('actor_reference')
        selected['director'] = deep_data.get('director')
        selected['movie_curiosity'] = deep_data.get('movie_curiosity')
        selected['hook_angle'] = strategy
        
    elif selected.get('needs_web'):
        selected['sinopsis'] = get_synopsis_chain(selected['titulo'], 2025, selected['tmdb_id'])
        selected['hook_angle'] = 'PLOT'

    # Payload Final
    payload = {
        **selected,
        "seleccion_generada": datetime.now(timezone.utc).isoformat() + "Z"
    }

    NEXT_FILE.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    logging.info(f"✅ SELECCIONADA: {selected['titulo']} ({selected.get('views',0):,} views)")
    return payload

if __name__ == "__main__":
    find_and_select_next()