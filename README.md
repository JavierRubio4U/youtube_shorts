

# AI\_youtube\_shorts

**`AI_youtube_shorts`** es una herramienta automatizada que genera y publica **YouTube Shorts** sobre los próximos estrenos de cine. El proyecto combina la extracción de datos de la API de TMDb, la síntesis de voz, la edición de video con `MoviePy` y la publicación a través de la API de YouTube.

El objetivo es crear videos de forma autónoma con información clave, imágenes y una narración generada por inteligencia artificial.

## Características

  * **Extracción de datos:** Se conecta a la API de TMDb para descubrir los próximos estrenos de cine en España, obteniendo metadatos como sinopsis, géneros, reparto, valoraciones y tráileres.
  * **Generación de `hook` y narración:** Crea una breve introducción (`hook`) y una narración concisa para el video, seleccionando partes de la sinopsis o generando un texto descriptivo basado en los géneros de la película.
  * **Síntesis de voz (TTS):** Utiliza la biblioteca [Coqui TTS](https://github.com/coqui-ai/tts) para convertir la narración en audio, con soporte para pausas naturales entre frases.
  * **Procesamiento de imágenes:** Descarga pósteres y fondos de pantalla (`backdrops`) de TMDb y los adapta al formato vertical (9:16) de los YouTube Shorts, añadiendo bandas negras en la parte superior e inferior para el texto.
  * **Edición de video:** Compila un video combinando las imágenes procesadas con el audio de la narración y una música de fondo.
  * **Generación de metadatos de YouTube:** Crea automáticamente un título, una descripción y etiquetas (`tags`) optimizadas para YouTube, incluyendo información relevante como el reparto y los géneros.
  * **Subida a YouTube:** Automatiza el proceso de autenticación y subida del video final a tu canal de YouTube.

## Estructura del Proyecto

Los scripts principales se encuentran en la carpeta `scripts/`.

  * `pipeline.py`: Ejecuta la secuencia principal del proyecto: seleccionar el siguiente estreno, descargar los recursos y generar los metadatos de YouTube.
  * `select_next_release.py`: Elige el próximo estreno a procesar en función de su "hype" (popularidad, votos, etc.).
  * `download_assets.py`: Descarga y procesa los pósteres y `backdrops` de la película seleccionada.
  * `build_youtube_metadata.py`: Crea el archivo `JSON` con el título, descripción y etiquetas que se usarán en YouTube.
  * `build_short.py`: El script más complejo. Se encarga de la generación de la narración, la síntesis de voz, la edición de imágenes, la mezcla de audio y la creación del video final (`.mp4`).
  * `upload_youtube.py`: Gestiona la autenticación con la API de YouTube y sube el video generado.

## Requisitos

El proyecto requiere Python 3.8 o superior. Todas las dependencias necesarias están listadas en el archivo `requirements.txt`.

```txt
# Contenido de tu requirements.txt
# --- Core video ---
moviepy==1.0.3
imageio==2.35.1
imageio-ffmpeg==0.4.9
Pillow==10.4.0


# --- YouTube API ---
google-api-python-client==2.149.0
google-auth==2.35.0
google-auth-oauthlib==1.2.1
google-auth-httplib2==0.2.0

```

## Instalación

1.  **Clona el repositorio:**

    ```bash
    git clone https://github.com/JavierRubio4U/AI_youtube_shorts.git
    ```

2.  **Navega al directorio del proyecto:**

    ```bash
    cd AI_youtube_shorts
    ```

3.  **Instala las dependencias de Python:**

    ```bash
    pip install -r requirements.txt
    ```

4.  **Configura las APIs:**

      * **TMDb:** Crea una cuenta en [The Movie Database (TMDb)](https://www.themoviedb.org/) y obtén tu clave de API. Guárdala en un archivo llamado `tmdb_api_key.txt` dentro de la carpeta `config/`.
      * **YouTube:** Sigue los pasos para obtener las credenciales de la [API de YouTube Data](https://developers.google.com/youtube/v3/guides/auth/installed-apps). El archivo `client_secret.json` debe guardarse en la carpeta `config/`.

## Uso

El `pipeline` principal se ejecuta a través del script `publish.py`. Este script automatiza todo el proceso, desde la selección del próximo estreno hasta la publicación en YouTube.

```bash
python scripts/publish.py
```

Si solo quieres ejecutar pasos individuales, puedes usar los scripts correspondientes en la carpeta `scripts/`:

  * `python scripts/select_next_release.py`: Selecciona una película y la guarda para su uso posterior.
  * `python scripts/pipeline.py`: Ejecuta los pasos de selección, descarga de assets y generación de metadatos.
  * `python scripts/build_short.py`: Genera el archivo de video (`.mp4`) con el `short`.
  * `python scripts/upload_youtube.py`: Sube el video a tu canal de YouTube.

## Notas Importantes

  * La calidad de la síntesis de voz y el resultado final del video dependen de las librerías instaladas y la configuración del sistema.
  * La subida a YouTube requiere una autenticación inicial, que se gestiona de forma automática la primera vez que se ejecuta el script.
  * El proyecto está configurado para un **uso personal** y requiere de la configuración manual de las credenciales de API.

## Contribuciones

Las contribuciones, sugerencias y reportes de errores son bienvenidos. Siéntete libre de abrir una `issue` o enviar un `pull request` en el repositorio.
