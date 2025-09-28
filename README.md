# Mini-DVR Web para Raspberry Pi

Solución end-to-end para previsualización MJPEG de baja latencia y grabación segmentada en alta resolución utilizando una Raspberry Pi con cámara ArduCAM UVC IMX708. La aplicación se basa en FastAPI con WebSockets para mantener el control sin interrumpir la vista previa.

## Arquitectura

- **uStreamer** expone la vista previa MJPEG a `http://PI:8000/stream`.
- **FastAPI** sirve la interfaz web, la API REST y la gestión de procesos.
- **FFmpeg** captura el flujo MJPEG y genera segmentos MP4 de 10 minutos, aplicando recorte ROI cuando se solicita.
- **WebSocket** coordina los comandos *Start/Stop* y notifica el estado en tiempo real al navegador.

## Requisitos de hardware y sistema

- Raspberry Pi con Raspberry Pi OS Bookworm 64 bits.
- Cámara ArduCAM UVC IMX708 (Raspberry Pi Camera Module 3) en modo MJPEG.
- Conectividad de red estable.

## Estructura del proyecto

```
app/
  __init__.py
  config.py
  main.py
  manager.py
  routes.py
  static/
    css/styles.css
    js/app.js
  templates/index.html
recordings/
  photos/
scripts/
  install.sh
  cleanup_recordings.sh
systemd/
  mini-dvr.service
requirements.txt
README.md
```

## Instalación

Ejecuta el script de instalación en la Raspberry Pi:

```bash
sudo ./scripts/install.sh
```

El script realiza lo siguiente:

1. Habilita la cámara vía `raspi-config` (modo no interactivo).
2. Instala dependencias del sistema (`ustreamer`, `ffmpeg`, `python3-venv`, `python3-pip`).
3. Crea un entorno virtual en `.venv` e instala los paquetes Python.
4. Asegura los directorios `recordings/` y `recordings/photos/` con permisos de escritura.

> **Nota:** si el usuario final no es `pi`, ajusta el propietario del directorio `recordings/` y el servicio systemd según corresponda.

## Ejecución manual

Activa el entorno virtual y lanza el backend:

```bash
source .venv/bin/activate
uvicorn app.main:app --host 0.0.0.0 --port 8080
```

La interfaz web quedará disponible en `http://PI:8080/`. El reproductor MJPEG consume el stream de uStreamer directamente en el puerto 8000.

## Servicio systemd

1. Copia el repositorio a `/opt/mini-dvr` (o la ruta preferida):
   ```bash
   sudo rsync -a --delete ./ /opt/mini-dvr/
   ```
2. Copia la unidad:
   ```bash
   sudo cp /opt/mini-dvr/systemd/mini-dvr.service /etc/systemd/system/
   ```
3. Ajusta las variables de entorno en el unit file (`User`, `Group`, rutas de `WorkingDirectory`, etc.).
4. Habilita y arranca el servicio:
   ```bash
   sudo systemctl daemon-reload
   sudo systemctl enable mini-dvr.service
   sudo systemctl start mini-dvr.service
   ```

El servicio utiliza `Restart=always` para mantener los procesos en marcha. Los logs quedan en `journalctl -u mini-dvr.service`.

## Limpieza de grabaciones

Utiliza el script `scripts/cleanup_recordings.sh` para eliminar segmentos y fotografías con más de 7 días:

```bash
bash scripts/cleanup_recordings.sh /home/pi/recordings
```

Para automatizarlo vía `cron` añade, por ejemplo:

```
0 3 * * * /home/pi/mini-dvr/scripts/cleanup_recordings.sh /home/pi/recordings >> /var/log/mini-dvr-cleanup.log 2>&1
```

Ajusta la ruta al repositorio según tu despliegue.

## Endpoints relevantes

- `GET /` – Interfaz web con la vista previa MJPEG y controles.
- `GET /health` – Health-check que verifica los procesos de uStreamer y FFmpeg.
- `GET /status` – Estado actual del sistema y metadatos de la grabación.
- `WS /ws` – Canal WebSocket para comandos de inicio/detención, captura de fotografías y notificaciones.
- `GET /api/controls` – Devuelve los controles V4L2 disponibles, incluyendo rangos, valores y opciones.
- `POST /api/controls/{id}` – Ajusta o restablece un control específico.
- `GET /api/media` – Lista las fotografías (JPG) y videos (MP4) disponibles en disco.
- `GET /media/{tipo}/{archivo}` – Descarga directa de una fotografía o segmento de video.
- `DELETE /api/media/{tipo}/{archivo}` – Elimina un recurso multimedia desde la galería web.

## Panel de ajustes de cámara

- La interfaz incorpora un *drawer* lateral con pestañas por categoría que agrupan los controles reportados por `v4l2-ctl`.
- Cada control muestra su valor actual, mínimo, máximo y predeterminado, con `slider`, listas desplegables o interruptores según el tipo.
- El botón **Restablecer** aplica el valor por defecto reportado por el driver sin interrumpir la vista previa ni la grabación en curso.
- Todos los cambios se envían mediante la API `/api/controls` y se validan en el backend para evitar valores fuera de rango.
- La API acepta el parámetro `?refresh=1` para forzar una lectura completa de `v4l2-ctl`; por defecto reutiliza un caché de 1 segundo controlado por `MINIDVR_CONTROLS_CACHE_TTL`.

## Vista previa interactiva

- La imagen en vivo incluye zoom digital (1× a 4×) controlado por un deslizador de respuesta inmediata.
- Un minimapa ROI cuadrado (200×200 px) fijado en la esquina superior derecha refleja el encuadre actual y admite clic o arrastre para reposicionarlo.
- Botones direccionales facilitan el *panning* fino incluso en pantallas táctiles; un botón central recentra la vista.
- Una marca de agua semitransparente con la leyenda **SURGICAM** identifica la transmisión sin obstruir el contenido quirúrgico.

## Capturas y galería de medios

- El botón **Capturar foto** solicita al backend una instantánea del flujo MJPEG (vía `/snapshot` de uStreamer) sin interrumpir la vista previa ni las grabaciones activas.
- Las fotografías se guardan en `recordings/photos/` con nomenclatura basada en la fecha y se publican inmediatamente en la galería, junto a los segmentos MP4 existentes.
- Desde la galería web es posible descargar o eliminar fotos y videos; cualquier cambio se replica al resto de clientes en tiempo real mediante eventos WebSocket.
- La ruta de almacenamiento puede redefinirse mediante la variable de entorno `MINIDVR_SNAPSHOTS_DIR` si se requiere un volumen distinto.

## Operación y métricas

- La vista previa permanece activa aun cuando las grabaciones se inician o detienen.
- Los segmentos MP4 incluyen `moov` al inicio (`+faststart`) y rotan cada 10 minutos.
- FFmpeg limpia la señal MJPEG con `scale=640:-1` antes de codificar a H.264 (`libx264`), garantizando compatibilidad plena con reproductores HTML5 y conservando baja latencia.
- Los parámetros del codificador (`preset`, `tune`, `pix_fmt`, `crf`) y el ancho de la escala son configurables mediante variables de entorno para equilibrar calidad y CPU.

## Pipeline de grabación por defecto

El gestor de procesos lanza FFmpeg con el siguiente perfil, equivalente a ejecutar:

```bash
ffmpeg -hide_banner -loglevel warning \
       -fflags nobuffer -flags low_delay -tcp_nodelay 1 \
       -f mpjpeg -i http://127.0.0.1:8000/stream \
       -vf scale=640:-1 \
       -c:v libx264 -preset ultrafast -tune zerolatency -pix_fmt yuv420p \
       -f segment -segment_time 600 -segment_atclocktime 1 -reset_timestamps 1 \
       -movflags +faststart -strftime 1 \
       recordings/%Y%m%d_%H%M%S.mp4
```

Si se aplica un ROI, el filtro `crop` se inserta antes de la escala manteniendo la misma tubería de codificación.

## ROI sincronizado con las grabaciones

- Al presionar **Iniciar grabación**, el navegador envía el ROI normalizado (posición, ancho, alto y zoom) junto con el comando.
- El backend valida el ROI, calcula el recorte en píxeles para la resolución fuente definida en `MINIDVR_RESOLUTION` y lo aplica con `-vf crop`.
- El evento de inicio incluye el ROI y la región recortada (`x`, `y`, `width`, `height`) para trazabilidad vía WebSocket o `/status`.
- Variables de entorno relevantes:
  - `MINIDVR_ENCODER` (por defecto `libx264`).
  - `MINIDVR_ENCODER_PRESET` (por defecto `ultrafast`).
  - `MINIDVR_ENCODER_TUNE` (por defecto `zerolatency`).
  - `MINIDVR_ENCODER_CRF` (opcional, sin valor por defecto).
  - `MINIDVR_ENCODER_PIX_FMT` (por defecto `yuv420p`).
  - `MINIDVR_SCALE_WIDTH` (por defecto `640`).
  - `MINIDVR_FFMPEG_LOGLEVEL` (por defecto `warning`).
  Ajusta estos valores si utilizas aceleración por hardware u otro códec o necesitas priorizar calidad sobre latencia.

## Troubleshooting

- **Sin imagen en la vista previa:** verifica `journalctl -u mini-dvr.service` y confirma que `ustreamer` detecte la cámara (`v4l2-ctl --list-formats-ext`).
- **Grabación no inicia:** inspecciona permisos de escritura en `~/recordings` y que la URL de `MINIDVR_STREAM_URL` sea accesible desde la Raspberry.
- **Latencia alta:** revisa la red local y confirma que no haya recortes innecesarios; el sistema siempre recodifica con `libx264` en modo `ultrafast`/`zerolatency`, por lo que puedes ampliar `MINIDVR_ENCODER_PRESET` o reducir la resolución (`MINIDVR_SCALE_WIDTH`) si el hardware va justo de CPU.
- **No aparecen capturas en la galería:** verifica los permisos de `recordings/photos/` y comprueba que `http://127.0.0.1:8000/snapshot` responda desde la Raspberry Pi.

## Licencia

Proyecto entregado como referencia técnica. Ajusta según políticas internas antes de desplegar en producción.
