# scripts/publish.py
from pathlib import Path
import sys, json

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import pipeline
import select_next_release
import build_short              # monta el MP4
import upload_youtube           # sube


STATE = ROOT / "output" / "state"

def main():
    print("▶ Pipeline (select + assets + metadata)…")
    sel = pipeline.run()

    if not sel:
        print("🛑 No se seleccionó una nueva película. Proceso detenido.")
        return

    print("▶ Build Short (MP4)…")
    mp4_path = build_short.main()

    print("▶ Upload a YouTube…")
    video_id = upload_youtube.main(mp4_path)
    
    # Añade una verificación antes de marcar como publicado
    if video_id:
        # marca como publicado
        meta = json.loads((STATE / "youtube_metadata.json").read_text(encoding="utf-8"))
        select_next_release.mark_published(int(meta["tmdb_id"]))

        print("✅ Publicado y marcado. Video:", f"https://studio.youtube.com/video/{video_id}/edit")
    else:
        print("🛑 La subida falló o se omitió. No se marcó como publicado.")

if __name__ == "__main__":
    main()