# scripts/find.py (versión principal reducida)
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
os.environ['ABSL_LOGGING_VERBOSITY'] = '1'  # Reduce warnings de absl

# --- Imports de utils ---
import sys
if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
from movie_utils import (
    _load_state, is_published, api_get, get_synopsis_chain, enrich_movie_basic,  # ← Añade enrich_movie_basic aquí
    load_config  # Para Gemini/TMDB keys
)

# --- Configuración de Paths ---
ROOT = Path(__file__).resolve().parents[1]
CONFIG_DIR = ROOT / "config"
STATE_DIR = ROOT / "output" / "state"
STATE_DIR.mkdir(parents=True, exist_ok=True)
NEXT_FILE = STATE_DIR / "next_release.json"
DEBUG_DIR = STATE_DIR / "debug"  # Carpeta para JSONs opcionales
DEBUG_DIR.mkdir(exist_ok=True)

# --- Configuración del Logging ---
logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')

DEBUG = False  # Cambia a True para JSON full en files (debug/)

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
        logging.error(f"ERROR: No se encuentra {TOKEN_FILE}. Genera uno con upload_youtube.py.")
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

        # Define cuántos días hacia atrás quieres buscar. Puedes cambiar este número.
        days_to_search = 7
        
        # 1. Calcula la fecha de inicio para la búsqueda
        start_date = datetime.now(timezone.utc) - timedelta(days=days_to_search)
        
        # 2. Formatea la fecha al formato RFC 3339 que requiere la API (ej: '2025-10-05T08:30:00Z')
        published_after_str = start_date.strftime('%Y-%m-%dT%H:%M:%SZ')
        
        logging.info(f"Filtrando resultados de YouTube publicados después de: {published_after_str}")
        
        # --- BÚSQUEDA PAGINADA ---
        all_items = []
        next_page_token = None
        num_pages_to_fetch = 2 # <-- Puedes ajustar este número (ej: 2 para 100, 3 para 150)

        logging.info(f"Realizando hasta {num_pages_to_fetch} búsquedas paginadas para obtener más resultados...")

        for i in range(num_pages_to_fetch):
            logging.info(f"  -> Obteniendo página {i + 1}/{num_pages_to_fetch}...")
            request = youtube.search().list(
                part="id,snippet",
                q=query,
                type="video",
                maxResults=50,  # Límite máximo real por página
                order="relevance",
                pageToken=next_page_token, # Usamos el token para pedir la siguiente página
                publishedAfter=published_after_str
            )
            response = request.execute()
            all_items.extend(response.get("items", []))
            
            # Obtenemos el token para la siguiente iteración
            next_page_token = response.get('nextPageToken')
            # Si no hay más páginas, detenemos el bucle
            if not next_page_token:
                logging.info("  -> No hay más páginas de resultados.")
                break
        
        # --- FIN DE BUSQUEDA PAGINADA ---
             
        videos = []
        video_ids = []
        for item in all_items:
            vid = item['id']['videoId']
            title = item['snippet']['title']
            videos.append({'title': title, 'videoId': vid})
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
                    logging.error(f"Error obteniendo estadísticas para un bloque de vídeos: {e}")
            
            for v in videos:
                v['views'] = stats_dict.get(v['videoId'], 0)
                v['url'] = f"https://www.youtube.com/watch?v={v['videoId']}"

        logging.info(f"Query: '{query}' | Total: {len(videos)} videos.")
        logging.info("Top 5 (views):")
        for i, v in enumerate(videos[:5], 1):
            logging.info(f"  {i}. '{v['title'][:60]}...' ({v['views']:,} views)")
        if len(videos) > 5:
            logging.info(f"  ... +{len(videos)-5} más")
        
        # JSON opcional
        if DEBUG:
            json_path = DEBUG_DIR / "step1_videos.json"
            json_path.write_text(json.dumps(videos, indent=2))
            logging.info(f"[DEBUG] JSON full en {json_path}")
        
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
    logging.info("Top 5 (views):")
    for i, v in enumerate(filtered_videos[:5], 1):
        logging.info(f"  {i}. '{v['title'][:60]}...' ({v['views']:,} views)")
    if len(filtered_videos) > 5:
        logging.info(f"  ... +{len(filtered_videos)-5} más")
    
    if DEBUG:
        json_path = DEBUG_DIR / "step2_filtered.json"
        json_path.write_text(json.dumps(filtered_videos, indent=2))
        logging.info(f"[DEBUG] JSON full en {json_path}")
    
    logging.info(f"✓ Conteo: {len(filtered_videos)} | Descartados: {discarded}")

    if not filtered_videos:
        logging.error("No videos after pre-filter.")
        return None

    # --- PASO 3: Gemini Filter ---
    logging.info("")
    logging.info(f"=== 🧠 PASO 3: Gemini Filter (Progreso: 3/6) ===")
    try:
        genai.configure(api_key=config["GEMINI_API_KEY"])
        model = genai.GenerativeModel(GEMINI_MODEL)
        titles_str = "\n".join(f"{i+1}. {v['title']}" for i, v in enumerate(filtered_videos))
        
        # --- 🔴 INICIO DEL CAMBIO: Prompt actualizado ---
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
        # --- 🔴 FIN DEL CAMBIO ---

        response = model.generate_content(prompt)
        
        # Raw solo si DEBUG
        if DEBUG:
            logging.info("Respuesta RAW de Gemini:")
            logging.info(response.text)
            raw_path = DEBUG_DIR / "step3_raw_gemini.txt"
            raw_path.write_text(response.text)
            logging.info(f"[DEBUG] Raw guardado en {raw_path}")

        cleaned_response = response.text.strip().lstrip("```json").rstrip("```").strip()
        if not cleaned_response:
            logging.warning("Respuesta de Gemini vacía después de limpiar.")
            return None

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
                    'plataforma': ai_movie.get('plataforma', 'Cine') # <-- 🔴 CAMBIO: Extraer plataforma
                })
            else:
                logging.warning(f"Índice inválido en Gemini: {idx}")

        discarded = len(filtered_videos) - len(gemini_candidates)
        logging.info(f"IA procesó {len(filtered_videos)} → {len(gemini_candidates)} candidatos (2025+).")
        logging.info("Top 5 por views:")
        sorted_gemini = sorted(gemini_candidates, key=lambda x: x['views'], reverse=True)[:5]
        for i, cand in enumerate(sorted_gemini, 1):
            # --- 🔴 CAMBIO: Log actualizado para mostrar plataforma ---
            logging.info(f"  {i}. '{cand['pelicula']} ({cand['año']})' (Plataforma: {cand['plataforma']}) ({cand['views']:,} views)")
        if len(gemini_candidates) > 5:
            logging.info(f"  ... +{len(gemini_candidates)-5} más")
        
        if DEBUG:
            json_path = DEBUG_DIR / "step3_gemini.json"
            json_path.write_text(json.dumps(gemini_candidates, indent=2))
            logging.info(f"[DEBUG] JSON full en {json_path}")
        
        logging.info(f"✓ Conteo: {len(gemini_candidates)} | Descartados: {discarded} (series, indias, etc.)")
    except Exception as e:
        logging.error(f"Error en Gemini: {e}")
        return None

    if not gemini_candidates:
        logging.error("No candidatos after Gemini.")
        return None

    # --- PASO 4: TMDB Verify ---
    logging.info("")
    logging.info(f"=== 📺 PASO 4: TMDB Verify (Progreso: 4/6) ===")
    valid_candidates = []
    for cand in gemini_candidates:
        name = cand['pelicula']
        year = cand['año']

        movie = None
                
        # 1. Primer Intento: Búsqueda en Español
        search_results_es = api_get("/search/movie", {"query": name, "language": "es-ES"})
        if search_results_es and search_results_es.get("results"):
            for result in search_results_es["results"][:3]:
                # --- CAMBIO 2: Verificación de año más flexible (año actual o siguiente) ---
                release_year_str = result.get("release_date", "0000")[:4]
                if release_year_str in (str(year), str(year + 1)):
                    movie = result
                    break
        
        # 2. Segundo Intento (Fallback): Búsqueda en Inglés si el primero falla
        if not movie:
            # Log de eficiencia para vigilar cuándo se usa el fallback
            logging.info(f"✗ '{name}' No encontrado en español. Reintentando en inglés")
            search_results_en = api_get("/search/movie", {"query": name}) # Sin 'language' para default a inglés
            if search_results_en and search_results_en.get("results"):
                for result in search_results_en["results"][:3]:
                    # --- CAMBIO 2: Verificación de año flexible (también aquí) ---
                    release_year_str = result.get("release_date", "0000")[:4]
                    if release_year_str in (str(year), str(year + 1)):
                        movie = result
                        logging.info(f" ✓ -> Éxito. Encontrado en inglés como '{result.get('title')}' (ID: {result.get('id')}).")
                        break
                
        if movie:
            if not is_published(movie["id"]):
                # --- 🔴 CAMBIO: Pasar la plataforma de IA ---
                valid_candidates.append({
                    'tmdb_id': movie['id'],
                    'pelicula': name,
                    'año': year,
                    'trailer_url': cand['trailer_url'],
                    'views': cand['views'],
                    'ia_platform_from_title': cand.get('plataforma', 'Cine')
                })
            else:
                logging.info(f"✗ {name} ({year} (ya publicado)") # Ahora dice "ya publicado"
        else:
            logging.info(f"✗ {name} ({year} (sin match en TMDB)") # Ahora dice "sin match en TMDB"

    # --- DEDUPLICACIÓN DE CANDIDATOS ---
    if valid_candidates:
        deduplicated_dict = {}
        for cand in valid_candidates:
            tmdb_id = cand['tmdb_id']
            # Si no hemos visto este ID, o si el candidato actual tiene más views que el guardado...
            if tmdb_id not in deduplicated_dict or cand['views'] > deduplicated_dict[tmdb_id]['views']:
                deduplicated_dict[tmdb_id] = cand
        
        # La nueva lista de candidatos es la que no tiene duplicados
        unique_candidates = list(deduplicated_dict.values())
        duplicates_removed = len(valid_candidates) - len(unique_candidates)
        
        # Actualizamos la lista original
        valid_candidates = unique_candidates
        
        if duplicates_removed > 0:
            logging.info(f"Se eliminaron {duplicates_removed} duplicados, conservando el tráiler de más views.")
    # --- FIN DEL DEDUPLICACIÓN ---

    discarded = len(gemini_candidates) - len(valid_candidates)
    logging.info(f"Verificados {len(gemini_candidates)} → {len(valid_candidates)} IDs nuevos.")
    logging.info("Top 5 por views (IDs):")
    sorted_valid = sorted(valid_candidates, key=lambda x: x['views'], reverse=True)[:5]
    for i, cand in enumerate(sorted_valid, 1):
        # --- 🔴 CAMBIO: Log actualizado para mostrar plataforma ---
        logging.info(f"  {i}. '{cand['pelicula']} (ID: {cand['tmdb_id']})' (Plataforma IA: {cand['ia_platform_from_title']}) ({cand['views']:,} views)")
    if len(valid_candidates) > 5:
        logging.info(f"  ... +{len(valid_candidates)-5} más")
    
    if DEBUG:
        json_path = DEBUG_DIR / "step4_valid.json"
        json_path.write_text(json.dumps(valid_candidates, indent=2))
        logging.info(f"[DEBUG] JSON full en {json_path}")
    
    logging.info(f"✓ Conteo: {len(valid_candidates)} | Descartados: {discarded}")

    if not valid_candidates:
        logging.error("No valid candidates after TMDB.")
        return None

    # --- PASO 5: Enrich Data (básico: TMDB solo) ---
    logging.info("")
    logging.info(f"=== ✨ PASO 5: Enrich Data (Progreso: 5/6) ===")
    enriched = []
    for vid in valid_candidates:
        # Enrich básico: TMDB sin web (rápido)
        enriched_data = enrich_movie_basic(vid['tmdb_id'], vid['pelicula'], vid['año'], vid['trailer_url'])  # Nueva func básica
        if enriched_data and enriched_data.get('has_poster'):
            enriched_data['needs_web'] = not bool(enriched_data.get('sinopsis', ''))  # Marca si necesita web
            enriched_data['año'] = vid['año']  # ← ¡Aquí el fix! Guarda 'año' para Paso 6
            enriched_data['views'] = vid['views']
            # --- 🔴 CAMBIO: Pasar la plataforma de IA ---
            enriched_data['ia_platform_from_title'] = vid.get('ia_platform_from_title', 'Cine')
            enriched.append(enriched_data)
        else:
            logging.info(f"✗ {vid['pelicula']} (sin póster)")

    discarded = len(valid_candidates) - len(enriched)
    logging.info(f"Enriquecidos básicos {len(valid_candidates)} → {len(enriched)} (TMDB OK, pósters ✓).")
    logging.info("Top 10 (views):")
    sorted_enriched = sorted(enriched, key=lambda x: x['views'], reverse=True)[:10]
    for i, e in enumerate(sorted_enriched, 1):
        # --- 🔴 CAMBIO: Log actualizado para mostrar plataforma IA vs TMDB ---
        sin_status = "✓" if e.get('sinopsis') else "🕵️ (necesita web)"
        
        # Plataforma TMDB
        streaming_platforms = e.get('platforms', {}).get('streaming', [])
        streaming_status = "🎬 Cine"
        if streaming_platforms:
            streaming_status = "📺 " + ", ".join(streaming_platforms)
            
        # Plataforma IA
        ia_plat = e.get('ia_platform_from_title', 'Cine')
        
        estreno_status = f"📅 {e.get('fecha_estreno', 'N/A')[:10]}" if e.get('fecha_estreno') else "📅 N/A"
        
        logging.info(f"  {i}. '{e['titulo']}' ({e['views']:,} views | IA: {ia_plat} | TMDB: {streaming_status} | Sinopsis: {sin_status} | Estreno: {estreno_status})")
    
    if len(enriched) > 5:
        logging.info(f"  ... +{len(enriched)-5} más")
    
    logging.info(f"✓ Conteo: {len(enriched)} | Descartados: {discarded} (sin póster)")

    if not enriched:
        logging.error("No enriched after step 5.")
        return None

    # --- PASO 6: Rank & Select + Web final ---
    logging.info("")
    logging.info(f"=== 🏆 PASO 6: Rank & Select (Progreso: 6/6) ===")
    enriched.sort(key=lambda x: x['views'], reverse=True)
    selected = enriched[0]

    # --- 📌 SINOPSIS ---
    # 1. Determinar la fuente de la sinopsis y obtener el texto final
    synopsis_source = "TMDB"  # Por defecto, la sinopsis viene de TMDB

    # Si la de TMDB estaba vacía, intentamos con Gemini (a través de get_synopsis_chain)
    if selected.get('needs_web'):
        logging.info(f"🕵️ Sinopsis de TMDB vacía. Buscando con IA para '{selected['titulo']}'...")
        gemini_synopsis = get_synopsis_chain(selected['titulo'], selected['año'], selected['id'])   
        
        if gemini_synopsis:
            selected['sinopsis'] = gemini_synopsis
            synopsis_source = "Gemini"  # La fuente ahora es Gemini
            logging.info("✅ Sinopsis encontrada con IA.")
        else:
            logging.warning(f"La búsqueda con IA tampoco encontró sinopsis para '{selected['titulo']}'.")
            # La sinopsis sigue vacía, la fuente es TMDB (Vacía)

    synopsis_text_for_log = selected.get('sinopsis') or "Vacía."

    # --- FIN DEL SINOPSIS ---

    # --- 🔴 CAMBIO: Guardar la plataforma de IA en el payload ---
    payload = {
        "tmdb_id": selected["id"],
        "titulo": selected["titulo"],
        "poster_principal": selected["poster_principal"],
        "sinopsis": selected["sinopsis"],
        "trailer_url": selected["trailer_url"],
        "fecha_estreno": selected["fecha_estreno"],
        "platforms": selected["platforms"], # Plataformas de TMDB
        "ia_platform_from_title": selected.get("ia_platform_from_title", "Cine"), # Plataforma de IA
        "seleccion_generada": datetime.now(timezone.utc).isoformat() + "Z",
        "generos": selected["generos"],  # Ya en enrich_basic
        "reparto_top": []  # O fetch aquí si quieres full
    }
    NEXT_FILE.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    logging.info(f"Top 1: '{selected['titulo']}' ({selected['views']:,} views).")
    logging.info(f"  ID: {selected['id']} | Trailer: {selected['trailer_url'][:50]}...")
    sin_preview = selected['sinopsis'][:80] + "..." if selected['sinopsis'] else "Vacía (OK)"
    logging.info(f"  Sinopsis: {sin_preview}")
    # logging.info(f"  Póster: {selected['poster_principal'][:50]}...")
    
    fecha_estreno_log = selected['fecha_estreno'][:10] if selected['fecha_estreno'] else "N/A"
    logging.info(f"  Fecha de estreno: {fecha_estreno_log}")
    
    # --- 🔴 CAMBIO: Log final muestra ambas plataformas ---
    ia_plat_final = selected.get('ia_platform_from_title', 'Cine')
    logging.info(f"  Plataforma IA (del Título): {ia_plat_final}")
    
    streaming_info = ""
    if selected['platforms'].get('streaming'):
        streaming_info = f"Streaming: {', '.join(selected['platforms']['streaming'])}"
        logging.info(f"  Plataforma TMDB: {streaming_info}")
    else:
        logging.info(f"  Plataforma TMDB: Cine.")
    
    logging.info(f"🎉 ¡Seleccionado y guardado en {NEXT_FILE}!")

    return payload
    

if __name__ == "__main__":
    print("--- Ejecutando 'find.py' en modo de prueba ---")
    result = find_and_select_next()
    if result:
        print("\n" + "="*60)
        print("      ✅ PRUEBA COMPLETADA CON ÉXITO")
        print("="*66)
        print(f" Título: {result['titulo']}")
        print(f" Fichero: {NEXT_FILE}")
        print(f" Plataforma IA: {result.get('ia_platform_from_title')}")
        print(f" Plataforma TMDB: {result.get('platforms', {}).get('streaming', 'Cine')}")
    else:
        print("\n" + "="*60)
        print("      🛑 PRUEBA FALLIDA: NO SE SELECCIONÓ CANDIDATO")
        print("="*60)