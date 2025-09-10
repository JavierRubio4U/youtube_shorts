import json
from pathlib import Path
from datetime import datetime
try:
    from datetime import UTC
except ImportError:
    from datetime import timezone as _tz
    UTC = _tz.utc

from get_releases import get_week_releases_enriched

ROOT = Path(__file__).resolve().parents[1]
STATE_DIR = ROOT / "output" / "state"
STATE_DIR.mkdir(parents=True, exist_ok=True)

PUBLISHED_FILE = STATE_DIR / "published.json"
NEXT_FILE = STATE_DIR / "next_release.json"

import re

def _is_latin_text(text: str) -> bool:
    """Devuelve True si el texto contiene al menos un 50% de caracteres latinos."""
    if not text:
        return False
    latin_chars = re.findall(r"[a-zA-ZáéíóúüñÁÉÍÓÚÜÑ\s]", text)
    return len(latin_chars) / len(text) > 0.5

def _load_state():
    if PUBLISHED_FILE.exists():
        return json.loads(PUBLISHED_FILE.read_text(encoding="utf-8"))
    return {"published_ids": [], "picked_ids": []}

def _save_state(state):
    PUBLISHED_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")

def _seed_with_previous_pick(state):
    """Si hay un next_release previo, métele su id en picked_ids para no repetir en esta ejecución."""
    if NEXT_FILE.exists():
        try:
            prev = json.loads(NEXT_FILE.read_text(encoding="utf-8"))
            pid = int(prev.get("tmdb_id"))
            if pid and pid not in state.get("picked_ids", []):
                state.setdefault("picked_ids", []).append(pid)
        except Exception:
            pass
    return state

def pick_next():
    state = _load_state()
    state = _seed_with_previous_pick(state)

    published = set(state.get("published_ids", []))
    picked = set(state.get("picked_ids", []))
    exclude = published | picked

    movies = get_week_releases_enriched()  # ordenadas por HYPE desc
    

    print("--- Análisis de candidatos ---")
    for m in movies:
        tiene_titulo = bool(m["titulo"])
        tiene_backdrops = bool(len(m["backdrops"]) >= 4)
        
        cumple_requisitos = tiene_titulo and tiene_backdrops
        
        print(f"- {m['titulo']}:")
        print(f"  - Título válido: {'✅' if tiene_titulo else '❌'}")
        print(f"  - Backdrops (>=4): {'✅' if tiene_backdrops else '❌'} ({len(m['backdrops'])})")
        if not cumple_requisitos:
            print("  - Razón: no cumple todos los requisitos.")
        
    print("--- Fin de análisis ---")

    candidate = next((m for m in movies if m["id"] not in exclude and m["titulo"] and len(m["backdrops"]) >= 4), None)
    if not candidate:
        print("No hay candidatos nuevos (todo publicado o ya elegido).")
        return None



    payload = {
        "tmdb_id": candidate["id"],
        "titulo": candidate["titulo"],
        "fecha_estreno": candidate["fecha_estreno"],
        "hype": candidate["hype"],
        "vote_average": candidate["vote_average"],
        "vote_count": candidate["vote_count"],
        "popularity": candidate["popularity"],
        "generos": candidate["generos"],
        "sinopsis": candidate["sinopsis"],
        "poster_principal": candidate["poster_principal"],
        "posters": candidate["posters"][:5],
        "backdrops": candidate["backdrops"][:8],
        "trailer_url": candidate["trailer"],
        "providers_ES": candidate["providers_ES"],
        "certificacion_ES": candidate["certificacion_ES"],
        "reparto_top": candidate["reparto_top"],
        "keywords": candidate["keywords"],
        "seleccion_generada": datetime.now(UTC).isoformat().replace("+00:00", "Z")
    }

    NEXT_FILE.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    # Actualiza picked_ids (histórico acotado)
    picked_list = state.get("picked_ids", [])
    picked_list.append(candidate["id"])
    state["picked_ids"] = picked_list[-50:]
    _save_state(state)

    print("✅ Siguiente selección guardada en:", NEXT_FILE)
    print(f"- {payload['titulo']} ({payload['fecha_estreno']})  HYPE={payload['hype']}")
    print("  Trailer:", payload["trailer_url"])
    return payload

def mark_published(tmdb_id: int):
    state = _load_state()
    if tmdb_id not in state.get("published_ids", []):
        state.setdefault("published_ids", []).append(tmdb_id)
        state["published_ids"] = sorted(set(state["published_ids"]))
        _save_state(state)
        print("📝 Marcado como publicado:", tmdb_id)
    else:
        print("(ya estaba publicado)", tmdb_id)

if __name__ == "__main__":
    pick_next()