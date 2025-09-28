# Surgicam-OSX

Aplicación web sencilla para controlar una cámara ArduCAM UVC (Raspberry Pi Camera Module 3) usando `ustreamer` como servidor de vista previa y `ffmpeg` para las grabaciones en alta resolución.

## Características

- Vista previa en vivo en baja resolución (configurable) servida por `ustreamer`.
- Inicio y detención de grabaciones en alta resolución sin interrumpir la vista previa.
- Interfaz web con WebSockets para controlar la cámara y recibir el estado en tiempo real.
- Registro de eventos recientes directamente en la interfaz.

## Requisitos

- Python 3.10 o superior.
- `ustreamer` disponible como binario (por defecto en `/usr/bin/ustreamer`).
- `ffmpeg` disponible en la línea de comandos.
- Cámara UVC accesible (por defecto `/dev/video0`).

Puedes sobrescribir las rutas a los binarios estableciendo las variables de entorno `USTREAMER_BIN` y `FFMPEG_BIN`.

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

Al iniciar la aplicación, `ustreamer` se ejecutará automáticamente para ofrecer la vista previa (por defecto en el puerto 8080). La interfaz web estará disponible en [http://localhost:8000](http://localhost:8000).

Desde la interfaz puedes comenzar y detener grabaciones en alta resolución. Los archivos se almacenarán en la carpeta `recordings/`.

## Configuración

Modifica los valores por defecto actualizando la instancia `VideoManager` en `app/main.py`:

- `device`: ruta al dispositivo UVC.
- `preview_port`, `preview_resolution`: valores para la vista previa.
- `record_resolution`: resolución usada durante la grabación.
- `record_dir`: carpeta de destino para los archivos resultantes.

## Advertencia

Esta aplicación controla procesos del sistema (`ustreamer` y `ffmpeg`). Asegúrate de ejecutar el servidor con los permisos adecuados y de tener suficiente espacio en disco para las grabaciones.
