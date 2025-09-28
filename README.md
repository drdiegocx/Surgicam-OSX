# Mini-DVR Web para Raspberry Pi

Solución end-to-end para previsualización MJPEG de baja latencia y grabación segmentada en alta resolución utilizando una Raspberry Pi con cámara ArduCAM UVC IMX708. La aplicación se basa en FastAPI con WebSockets para mantener el control sin interrumpir la vista previa.

## Arquitectura

- **uStreamer** expone la vista previa MJPEG a `http://PI:8000/stream`.
- **FastAPI** sirve la interfaz web, la API REST y la gestión de procesos.
- **FFmpeg** captura el flujo MJPEG y genera segmentos MP4 de 10 minutos sin recodificación.
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
  .gitkeep
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
4. Asegura el directorio `recordings/` con permisos de escritura.

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

Utiliza el script `scripts/cleanup_recordings.sh` para eliminar segmentos con más de 7 días:

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
- `WS /ws` – Canal WebSocket para comandos de inicio/detención y notificaciones.
- `GET /api/controls` – Devuelve los controles V4L2 disponibles, incluyendo rangos, valores y opciones.
- `POST /api/controls/{id}` – Ajusta o restablece un control específico.

## Panel de ajustes de cámara

- La interfaz incorpora un *drawer* lateral con pestañas por categoría que agrupan los controles reportados por `v4l2-ctl`.
- Cada control muestra su valor actual, mínimo, máximo y predeterminado, con `slider`, listas desplegables o interruptores según el tipo.
- El botón **Restablecer** aplica el valor por defecto reportado por el driver sin interrumpir la vista previa ni la grabación en curso.
- Todos los cambios se envían mediante la API `/api/controls` y se validan en el backend para evitar valores fuera de rango.
- La API acepta el parámetro `?refresh=1` para forzar una lectura completa de `v4l2-ctl`; por defecto reutiliza un caché de 1 segundo controlado por `MINIDVR_CONTROLS_CACHE_TTL`.

## Vista previa interactiva

- La imagen en vivo incluye zoom digital (1× a 4×) controlado por un deslizador de respuesta inmediata.
- Un minimapa ROI superpuesto indica el encuadre activo y permite reubicarlo con un clic o arrastre.
- Botones direccionales facilitan el *panning* fino incluso en pantallas táctiles; un botón central recentra la vista.

## Operación y métricas

- La vista previa permanece activa aun cuando las grabaciones se inician o detienen.
- Los segmentos MP4 incluyen `moov` al inicio (`+faststart`) y rotan cada 10 minutos.
- El consumo de CPU se mantiene bajo al no transcodificar los flujos.

## Troubleshooting

- **Sin imagen en la vista previa:** verifica `journalctl -u mini-dvr.service` y confirma que `ustreamer` detecte la cámara (`v4l2-ctl --list-formats-ext`).
- **Grabación no inicia:** inspecciona permisos de escritura en `~/recordings` y que la URL de `MINIDVR_STREAM_URL` sea accesible desde la Raspberry.
- **Latencia alta:** revisa la red local y confirma que no haya transcodificación (el comando FFmpeg usa `-c copy`).

## Licencia

Proyecto entregado como referencia técnica. Ajusta según políticas internas antes de desplegar en producción.
