# Surgicam-OSX

Aplicación web sencilla para controlar una cámara ArduCAM UVC (Raspberry Pi Camera Module 3) usando GStreamer tanto para la vista previa como para las grabaciones en alta resolución.

## Características

- Vista previa en vivo en baja resolución (configurable) generada con GStreamer.
- Inicio y detención de grabaciones en alta resolución sin interrumpir la vista previa.
- Interfaz web con WebSockets para controlar la cámara y recibir el estado en tiempo real.
- Registro de eventos recientes directamente en la interfaz.

## Requisitos

- Python 3.10 o superior.
- GStreamer (`gst-launch-1.0`) disponible en la línea de comandos.
- Cámara UVC accesible (por defecto `/dev/video0`).

Puedes sobrescribir la ruta al binario de GStreamer estableciendo la variable de entorno `GST_LAUNCH_BIN`.

## Instalación

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Ejecución

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8080
```

Al iniciar la aplicación, GStreamer se ejecutará automáticamente para ofrecer la vista previa de la cámara. La interfaz web estará disponible en [http://localhost:8080](http://localhost:8080).

Desde la interfaz puedes comenzar y detener grabaciones en alta resolución. Los archivos se almacenarán en la carpeta `recordings/`.

## Configuración

Puedes modificar los valores por defecto mediante variables de entorno antes de iniciar el servidor:

- `DEVICE_PATH`, por ejemplo `DEVICE_PATH=/dev/video2`.
- `PREVIEW_RES`, por ejemplo `PREVIEW_RES=800x600`.
- `RECORD_RES`, por ejemplo `RECORD_RES=3840x2160`.

Si se especifica un formato inválido, la aplicación mantendrá los valores por defecto y mostrará una advertencia en los logs.

Las grabaciones se almacenan en la carpeta `recordings/` como secuencias MJPEG (`frame_000001.jpg`, ...). La vista previa se actualiza periódicamente generando capturas JPEG almacenadas en `recordings/preview/`, que la interfaz web recarga de forma automática.

La aplicación intentará iniciar los pipelines de GStreamer con DMA-BUF y MJPEG directos para minimizar el uso de CPU. Si el dispositivo o la resolución solicitada no son compatibles, se prueban alternativas automáticas que deshabilitan DMA-BUF o realizan la conversión a JPEG en software, registrando advertencias en los logs cuando se utiliza un plan de contingencia.

## Advertencia

Esta aplicación controla procesos del sistema basados en GStreamer. Asegúrate de ejecutar el servidor con los permisos adecuados y de tener suficiente espacio en disco para las grabaciones.
