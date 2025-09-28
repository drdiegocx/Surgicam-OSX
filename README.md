# Surgicam-OSX

Aplicación de streaming y grabación para la cámara Raspberry Pi Camera Module 3 (ArduCAM UVC) usando Python, FastAPI y GStreamer.

## Requisitos

Instalar dependencias del sistema necesarias para GStreamer y PyGObject (ejemplo en Debian/Ubuntu):

```bash
sudo apt-get install -y python3-gi gstreamer1.0-tools gstreamer1.0-plugins-good gstreamer1.0-plugins-bad
```

Instalar dependencias de Python:

```bash
pip install -r requirements.txt
```

## Ejecución

Inicie el servidor con:

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Abra el navegador en `http://<IP>:8000` para ver la vista previa (transcodificada a 720p@10fps) y controlar las grabaciones.

Las grabaciones en alta resolución se almacenan en la carpeta `recordings/` en formato MJPEG (`.avi`).
Los archivos terminados se pueden descargar directamente desde `http://<IP>:8000/recordings/<nombre_del_archivo>`.
