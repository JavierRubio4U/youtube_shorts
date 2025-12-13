# scripts/find.py
import logging
import json
from pathlib import Path
from datetime import datetime, timezone, timedelta
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
import google.generativeai as genai
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from gemini_config import GEMINI_MODEL
import os
os.environ['ABSL_LOGGING_VERBOSITY'] = '1'

# --- Imports de utils ---
import sys
if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
from movie_utils import (
    _load_state, is_published, api_get, get_synopsis_chain, enrich_movie_basic,
    load_config
)

# --- Configuración de Paths ---
ROOT = Path(__file__).resolve().parents[1]
CONFIG_DIR = ROOT / "config"
STATE_DIR = ROOT / "output" / "state"
STATE_DIR.mkdir(parents=True, exist_ok=True)
NEXT_FILE = STATE_DIR / "next_release.json"
DEBUG_DIR = STATE_DIR / "debug"
DEBUG_DIR.mkdir(exist_ok=True)

logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')

DEBUG = False

def find_and_select_next():
    config = load_config()
    if not config:
        return None

    # --- PASO 0: Cargando estado ---
    state = _load_state()
    published_ids = [pub.get("id") for pub in state.get("published_ids", [])]
    logging.info("")
    logging.info(f"📂 Cargando {len(published_ids)} IDs de películas ya publicadas.")

    # --- Construir servicio YouTube ---
    TOKEN_FILE = STATE_DIR / "youtube_token.json"
    if not TOKEN_FILE.exists():
        logging.error(f"ERROR: No se encuentra {TOKEN_FILE}.")
        return None

    try:
        with open(TOKEN_FILE, 'r') as f:
            token_data = json.load(f)

        creds = Credentials(
            token=token_data['token'],
            refresh_token=token_data['refresh_token'],
            token_uri=token_data['token_uri'],
            client_id=token_data['client_id'],
            client_secret=token_data['client_secret'],
            scopes=token_data['scopes']
        )

        if creds.expired and creds.refresh_token:
            creds.refresh(Request())

        youtube = build('youtube', 'v3', credentials=creds)
    except Exception as e:
        logging.error(f"Error construyendo servicio YouTube: {e}")
        return None

    # --- PASO 1: YouTube Search ---
    logging.info("")
    logging.info(f"=== 🔍 PASO 1: YouTube Search (Progreso: 1/6) ===")
    try:
        query = "official movie trailer 2025 new this week"
        days_to_search = 7
        start_date = datetime.now(timezone.utc) - timedelta(days=days_to_search)
        published_after_str = start_date.strftime('%Y-%m-%dT%H:%M:%SZ')
        
        logging.info(f"Filtrando resultados de YouTube publicados después de: {published_after_str}")
        
        all_items = []
        next_page_token = None
        num_pages_to_fetch = 3 # cuantas paginas de 50 vamos a añadir... actualment 150

        logging.info(f"Realizando hasta {num_pages_to_fetch} búsquedas paginadas para obtener más resultados...")

        for i in range(num_pages_to_fetch):
            logging.info(f"  -> Obteniendo página {i + 1}/{num_pages_to_fetch}...")
            request = youtube.search().list(
                part="id,snippet",
                q=query,
                type="video",
                maxResults=50,
                order="relevance",
                pageToken=next_page_token,
                publishedAfter=published_after_str
            )
            response = request.execute()
            all_items.extend(response.get("items", []))
            next_page_token = response.get('nextPageToken')
            if not next_page_token:
                logging.info("  -> No hay más páginas de resultados.")
                break
             
        videos = []
        video_ids = []
        for item in all_items:
            vid = item['id']['videoId']
            title = item['snippet']['title']
            # --- NUEVO: CAPTURAR FECHA DE SUBIDA ---
            upload_date = item['snippet']['publishedAt']
            
            videos.append({
                'title': title, 
                'videoId': vid,
                'upload_date': upload_date # Guardada aquí
            })
            video_ids.append(vid)

        # Fetch views
        if video_ids:
            stats_dict = {}
            for i in range(0, len(video_ids), 50):
                chunk = video_ids[i:i + 50]
                try:
                    stats_req = youtube.videos().list(part="statistics", id=','.join(chunk))
                    stats_resp = stats_req.execute()
                    for item in stats_resp.get('items', []):
                        stats_dict[item['id']] = int(item['statistics'].get('viewCount', 0))
                except HttpError as e:
                    logging.error(f"Error obteniendo estadísticas: {e}")
            
            for v in videos:
                v['views'] = stats_dict.get(v['videoId'], 0)
                v['url'] = f"https://www.youtube.com/watch?v={v['videoId']}"

        logging.info(f"Query: '{query}' | Total: {len(videos)} videos.")
        
        if DEBUG:
            json_path = DEBUG_DIR / "step1_videos.json"
            json_path.write_text(json.dumps(videos, indent=2))
        
        logging.info(f"✓ Listo ")
    except HttpError as e:
        logging.error(f"Error en API YouTube: {e}")
        return None

    # --- PASO 2: Pre-filtro ---
    logging.info("")
    logging.info(f"=== 🧹 PASO 2: Pre-filtro (Progreso: 2/6) ===")
    filtered_videos = [v for v in videos if 'official' in v['title'].lower() and 'trailer' in v['title'].lower()]
    if len(filtered_videos) > 50:
        filtered_videos = filtered_videos[:50]
    discarded = len(videos) - len(filtered_videos)
    logging.info(f"Filtrado 'official trailer': {len(filtered_videos)} válidos (de {len(videos)}).")
    
    if DEBUG:
        json_path = DEBUG_DIR / "step2_filtered.json"
        json_path.write_text(json.dumps(filtered_videos, indent=2))
    
    logging.info(f"✓ Conteo: {len(filtered_videos)} | Descartados: {discarded}")

    if not filtered_videos: return None

    # --- PASO 3: Gemini Filter ---
    logging.info("")
    logging.info(f"=== 🧠 PASO 3: Gemini Filter (Progreso: 3/6) ===")
    try:
        genai.configure(api_key=config["GEMINI_API_KEY"])
        model = genai.GenerativeModel(GEMINI_MODEL)
        titles_str = "\n".join(f"{i+1}. {v['title']}" for i, v in enumerate(filtered_videos))
        
        prompt = f"""Eres un analista de cine. Analiza esta lista de títulos de vídeos de YouTube y extrae las películas más prometedoras para 2025 o posterior. Descartar si: recopilación ("BEST TRAILERS"), serie ("Season"), india (nombres como "Ranbir", "hindi"), viejo (<2025), no película oficial.

Responde SÓLO con un array JSON de objetos con claves:
1. 'pelicula' (nombre película)
2. 'año' (int 2025+)
3. 'index' (índice del título en la lista 1-based)
4. 'plataforma' (string: "Netflix", "Disney+", "Prime Video", "HBO", "Apple TV+", etc. si se menciona explícitamente en el título. Si no se menciona ninguna, usa "Cine").

Si no es válido, ignóralo.
---
{titles_str}
---"""

        response = model.generate_content(prompt)
        cleaned_response = response.text.strip().lstrip("```json").rstrip("```").strip()
        if not cleaned_response: return None

        ai_movies = json.loads(cleaned_response)
        gemini_candidates = []
        for ai_movie in ai_movies:
            idx = ai_movie.get('index', 0) - 1
            if 0 <= idx < len(filtered_videos):
                v = filtered_videos[idx]
                gemini_candidates.append({
                    'pelicula': ai_movie['pelicula'],
                    'año': ai_movie['año'],
                    'trailer_url': v['url'],
                    'views': v['views'],
                    'upload_date': v['upload_date'], # --- NUEVO: PASAR LA FECHA ---
                    'plataforma': ai_movie.get('plataforma', 'Cine')
                })

        logging.info(f"IA procesó {len(filtered_videos)} → {len(gemini_candidates)} candidatos (2025+).")
        
        if DEBUG:
            json_path = DEBUG_DIR / "step3_gemini.json"
            json_path.write_text(json.dumps(gemini_candidates, indent=2))
        
    except Exception as e:
        logging.error(f"Error en Gemini: {e}")
        return None

    if not gemini_candidates: return None

    # --- PASO 4: TMDB Verify ---
    logging.info("")
    logging.info(f"=== 📺 PASO 4: TMDB Verify (Progreso: 4/6) ===")
    valid_candidates = []
    for cand in gemini_candidates:
        name = cand['pelicula']
        year = cand['año']
        movie = None
                
        # 1. Búsqueda Español
        search_results_es = api_get("/search/movie", {"query": name, "language": "es-ES"})
        if search_results_es and search_results_es.get("results"):
            for result in search_results_es["results"][:3]:
                release_year_str = result.get("release_date", "0000")[:4]
                if release_year_str in (str(year), str(year + 1)):
                    movie = result
                    break
        
        # 2. Búsqueda Inglés
        if not movie:
            search_results_en = api_get("/search/movie", {"query": name})
            if search_results_en and search_results_en.get("results"):
                for result in search_results_en["results"][:3]:
                    release_year_str = result.get("release_date", "0000")[:4]
                    if release_year_str in (str(year), str(year + 1)):
                        movie = result
                        break
                
        if movie:
            if not is_published(movie["id"]):
                valid_candidates.append({
                    'tmdb_id': movie['id'],
                    'pelicula': name,
                    'año': year,
                    'trailer_url': cand['trailer_url'],
                    'views': cand['views'],
                    'upload_date': cand['upload_date'], # --- NUEVO: PASAR LA FECHA ---
                    'ia_platform_from_title': cand.get('plataforma', 'Cine')
                })
            else:
                logging.info(f"✗ {name} ({year} (ya publicado)")

    # Deduplicación
    if valid_candidates:
        deduplicated_dict = {}
        for cand in valid_candidates:
            tmdb_id = cand['tmdb_id']
            if tmdb_id not in deduplicated_dict or cand['views'] > deduplicated_dict[tmdb_id]['views']:
                deduplicated_dict[tmdb_id] = cand
        valid_candidates = list(deduplicated_dict.values())
        
    logging.info(f"Verificados {len(gemini_candidates)} → {len(valid_candidates)} IDs nuevos.")

    if not valid_candidates: return None

    # --- PASO 5: Enrich Data ---
    logging.info("")
    logging.info(f"=== ✨ PASO 5: Enrich Data (Progreso: 5/6) ===")
    enriched = []
    for vid in valid_candidates:
        enriched_data = enrich_movie_basic(vid['tmdb_id'], vid['pelicula'], vid['año'], vid['trailer_url'])
        if enriched_data and enriched_data.get('has_poster'):
            # Filtro fecha de estreno (Pasado)
            estreno_str = enriched_data.get('fecha_estreno')
            if estreno_str:
                try:
                    fecha_obj = datetime.strptime(estreno_str.split('T')[0], "%Y-%m-%d")
                    limite = datetime.now() - timedelta(days=14)
                    if fecha_obj < limite:
                        logging.info(f"✗ {vid['pelicula']} descartada (Estreno pasado: {estreno_str[:10]})")
                        continue
                except ValueError: pass
            
            enriched_data['needs_web'] = not bool(enriched_data.get('sinopsis', ''))
            enriched_data['año'] = vid['año']
            enriched_data['views'] = vid['views']
            enriched_data['upload_date'] = vid['upload_date'] # --- NUEVO: PASAR LA FECHA ---
            enriched_data['ia_platform_from_title'] = vid.get('ia_platform_from_title', 'Cine')
            enriched.append(enriched_data)
        else:
            logging.info(f"✗ {vid['pelicula']} (sin póster)")

    logging.info(f"Enriquecidos básicos {len(valid_candidates)} → {len(enriched)}.")
    if not enriched: return None

    # --- PASO 6: Rank & Select ---
    logging.info("")
    logging.info(f"=== 🏆 PASO 6: Rank & Select (Progreso: 6/6) ===")
    enriched.sort(key=lambda x: x['views'], reverse=True)
    selected = enriched[0]

    # Sinopsis
    if selected.get('needs_web'):
        logging.info(f"🕵️ Sinopsis de TMDB vacía. Buscando con IA para '{selected['titulo']}'...")
        gemini_synopsis = get_synopsis_chain(selected['titulo'], selected['año'], selected['id'])   
        if gemini_synopsis: selected['sinopsis'] = gemini_synopsis

    payload = {
        "tmdb_id": selected["id"],
        "titulo": selected["titulo"],
        "poster_principal": selected["poster_principal"],
        "sinopsis": selected["sinopsis"],
        "trailer_url": selected["trailer_url"],
        "fecha_estreno": selected["fecha_estreno"],
        "platforms": selected["platforms"],
        "ia_platform_from_title": selected.get("ia_platform_from_title", "Cine"),
        "seleccion_generada": datetime.now(timezone.utc).isoformat() + "Z",
        "generos": selected["generos"],
        
        "cast": selected.get("actors", []), # --- NUEVO: GUARDAR ACTORES ---
        
        "views": selected.get("views", 0),
        "upload_date": selected.get("upload_date") # --- NUEVO: GUARDAR FECHA SUBIDA ---
    }

    NEXT_FILE.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    
    logging.info("="*60)
    logging.info(f"  Título:          {selected['titulo']}")
    logging.info(f"  Visualizaciones: {selected.get('views', 0):,}")
    logging.info(f"  Trailer URL:     {selected['trailer_url']}")
    logging.info("-" * 60)
    logging.info(f"🎉 ¡Seleccionado y guardado en {NEXT_FILE}!")

    return payload

if __name__ == "__main__":
    find_and_select_next()