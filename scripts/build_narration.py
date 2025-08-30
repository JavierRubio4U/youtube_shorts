# scripts/build_narration.py
import json, re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / "output" / "state"
SEL = STATE / "next_release.json"
OUT = STATE / "narration_es.txt"

def clean(txt):
    if not txt: return ""
    return re.sub(r"\s+", " ", txt).strip()

def limit_words(txt, max_words=85):
    words = txt.split()
    return " ".join(words[:max_words]) + ("…" if len(words) > max_words else "")

def main():
    if not SEL.exists():
        raise SystemExit("Falta next_release.json. Ejecuta el pipeline/selector primero.")
    sel = json.loads(SEL.read_text(encoding="utf-8"))

    titulo = sel.get("titulo") or ""
    fecha  = sel.get("fecha_estreno") or ""
    generos = ", ".join(sel.get("generos") or [])
    reparto = ", ".join((sel.get("reparto_top") or [])[:3])
    sinopsis = clean(sel.get("sinopsis") or "")

    # Guion teaser, sin spoilers (tomamos primeras frases y lo acotamos)
    base = (
        f"Esta semana llega a cines {titulo}. "
        f"Una propuesta de {generos.lower()} "
        f"con {reparto}. "
    ).replace("..", ".")

    # Usa la primera frase/segmento de sinopsis como gancho, sin destripar.
    cut = sinopsis.split(". ")[0].split("! ")[0].split("? ")[0]
    cuerpo = clean(cut)

    cierre = f" Estreno en España el {fecha}. ¿Te la vas a perder?"

    texto = base + cuerpo + cierre
    texto = limit_words(texto, 85)  # ~28s a ~180–190 wpm

    OUT.write_text(texto, encoding="utf-8")
    print("✅ Narración guardada en:", OUT)
    print()
    print(texto)

if __name__ == "__main__":
    main()
