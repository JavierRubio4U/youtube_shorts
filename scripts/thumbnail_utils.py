import os
from googleapiclient.http import MediaFileUpload

def set_short_thumbnail(youtube, video_id, image_path):
    """
    Establece una imagen (o un fotograma extraído) como miniatura de un Short.
    
    Args:
        youtube: Instancia del servicio de la API de YouTube.
        video_id: El ID del video de YouTube Shorts.
        image_path: Ruta local al archivo de imagen (.jpg, .png).
    """
    if not os.path.exists(image_path):
        print(f"Error: El archivo {image_path} no existe.")
        return None

    try:
        request = youtube.thumbnails().set(
            videoId=video_id,
            media_body=MediaFileUpload(image_path)
        )
        response = request.execute()
        print(f"Miniatura actualizada correctamente para el video {video_id}.")
        return response
    except Exception as e:
        print(f"Ocurrió un error al subir la miniatura: {e}")
        return None

def extract_frame(video_path, output_image_path, timestamp="00:00:02"):
    """
    Extrae un fotograma de un vídeo usando ffmpeg.
    
    Args:
        video_path: Ruta al archivo de vídeo.
        output_image_path: Ruta donde guardar la imagen.
        timestamp: Tiempo del fotograma (formato HH:MM:SS o segundos).
    """
    import subprocess
    try:
        command = [
            'ffmpeg', '-y',
            '-i', video_path,
            '-ss', timestamp,
            '-vframes', '1',
            output_image_path
        ]
        subprocess.run(command, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)
        print(f"Fotograma extraído en {output_image_path}")
        return True
    except Exception as e:
        print(f"Error al extraer fotograma: {e}")
        return False

# Nota: Para extraer un fotograma específico vía código antes de subirlo:
# ffmpeg -i video_input.mp4 -ss 00:00:02 -vframes 1 fotograma.jpg