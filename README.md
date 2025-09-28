# Surgicam-OSX

Aplicación web sencilla para controlar una cámara ArduCAM UVC (Raspberry Pi Camera Module 3) capturando los cuadros directamente desde el dispositivo `/dev/video0`. La vista previa y las grabaciones comparten el mismo flujo interno para evitar interrupciones en la experiencia del usuario.

## Características

- Vista previa en vivo en baja resolución (configurable) enviada mediante WebSockets como cuadros JPEG.
- Inicio y detención de grabaciones en alta resolución reutilizando el mismo flujo de captura sin interrumpir la vista previa.
- Interfaz web con WebSockets para controlar la cámara y recibir el estado en tiempo real.
- Registro de eventos recientes directamente en la interfaz.

## Requisitos

- Python 3.10 o superior.
- Dependencias de Python listadas en `requirements.txt` (incluye `opencv-python-headless` y `numpy`).
- Cámara UVC accesible (por defecto `/dev/video0`).

## Instalación

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Ejecución

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Al iniciar la aplicación se crea un hilo dedicado que lee continuamente desde la cámara. La interfaz web y el stream de vista previa están disponibles en [http://localhost:8000](http://localhost:8000).

Desde la interfaz puedes comenzar y detener grabaciones en alta resolución. Los archivos se almacenarán en la carpeta `recordings/`.

## Configuración

Modifica los valores por defecto actualizando la instancia `VideoManager` en `app/main.py`:

- `device`: ruta al dispositivo UVC.
- `preview_resolution`: resolución usada para la vista previa (los cuadros se escalan automáticamente).
- `record_resolution`: resolución usada durante la grabación.
- `record_dir`: carpeta de destino para los archivos resultantes.
- `preview_fps` y `record_fps`: cadencia objetivo para la vista previa y la grabación.
- `jpeg_quality`: calidad de compresión JPEG en la vista previa.

También puedes ajustar las resoluciones por medio de variables de entorno antes de iniciar el servidor:

- `PREVIEW_RESOLUTION`, por ejemplo `PREVIEW_RESOLUTION=800x600`.
- `RECORD_RESOLUTION`, por ejemplo `RECORD_RESOLUTION=3840x2160`.
- `PREVIEW_FPS`, `RECORD_FPS` y `PREVIEW_JPEG_QUALITY` para ajustar la cadencia y la compresión del stream.

Si la cámara no soporta un parámetro determinado, OpenCV intentará usar el valor más cercano admitido y se registrará una advertencia.

Si se especifica un formato inválido, la aplicación mantendrá los valores por defecto y mostrará una advertencia en los logs.

## Advertencia

Las grabaciones se realizan con `cv2.VideoWriter` (`mp4v`). Asegúrate de contar con recursos suficientes (CPU, ancho de banda del bus USB y almacenamiento) para manejar la resolución y el FPS configurados sin perder cuadros.
