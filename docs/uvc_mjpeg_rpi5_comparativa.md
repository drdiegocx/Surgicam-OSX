# Comparativa de flujos UVC MJPEG para IMX708 en Raspberry Pi 5

La cámara IMX708 en modo UVC entrega un flujo MJPEG que la Raspberry Pi 5 puede decodificar con baja carga de CPU siempre que se eviten conversiones innecesarias. A continuación se comparan tres estrategias habituales para grabar y visualizar la señal en paralelo.

## Resumen de criterios

| Estrategia | Consumo de CPU | Estabilidad | Facilidad de implementación | Flexibilidad |
| --- | --- | --- | --- | --- |
| 1. `ffmpeg` con `tee` a archivo y `ffplay` (pipe/UDP) | Medio: la decodificación para `ffplay` añade ~10-20 % adicional, especialmente si se hace `-f matroska`/pipe. | Buena si los sockets o pipes se mantienen; errores en el consumidor pueden bloquear el `tee`. | Fácil: un solo comando de `ffmpeg` y `ffplay`. | Media: cambiar destinos requiere reescribir el pipeline manualmente. |
| 2. `ffmpeg` con salida multiplexada (archivo + stream resiliente) | Bajo-medio: se puede evitar decodificar reenviando MJPEG directo (`-codec copy`), lo que reduce el uso de CPU <15 %. | Alta: buffers independientes y reconexión UDP/RTSP permiten tolerar caídas sin detener la grabación. | Media: exige configurar `-map`, `-codec copy`, `-f tee`/`-filter_complex` y opciones de reconexión. | Alta: `ffmpeg` soporta múltiples protocolos y targets. |
| 3. `gstreamer` con `tee` para grabar MJPEG crudo y preview | Bajo: `v4l2src` + `tee` + `queue` + `jpegparse` evita decodificar; `autovideosink` usa aceleración. | Muy alta: las colas (`queue`) desacoplan ramas y el pipeline reintenta automáticamente si se agregan `reconnect`. | Media-Alta: requiere script en `gst-launch-1.0` o archivo `.gst`, pero los plugins están disponibles en Pi OS. | Muy alta: se pueden agregar ramas a archivo, RTSP, appsink, etc. |

## Detalles por estrategia

### 1. `ffmpeg` con `tee` y `ffplay`
- **Consumo de CPU:** El flujo MJPEG se copia directo al archivo (`-c copy`), pero para visualizar con `ffplay` el pipeline suele demultiplexar y decodificar en software. En Raspberry Pi 5 esto representa alrededor de un núcleo parcialmente ocupado al reproducir 1080p30.
- **Estabilidad:** El `tee` de `ffmpeg` comparte el buffer entre salidas; si `ffplay` se cierra abruptamente, el comando puede fallar si no se usa la opción `onfail=ignore`. Las pipes bloqueadas también pueden detener la grabación.
- **Facilidad:** Ejecutar `ffmpeg -f v4l2 -input_format mjpeg -i /dev/video0 -c copy -f tee "record.mkv|[f=mpegts]udp://..."` y un `ffplay` aparte es directo.
- **Flexibilidad:** Cambiar el destino requiere editar manualmente la cadena `tee`. No es trivial agregar más consumidores dinámicamente.

### 2. `ffmpeg` con multiplexado tolerante a caídas
- **Consumo de CPU:** Manteniendo `-codec copy` para la rama de archivo y `-codec copy`/`-f mpegts` para streaming se evita recodificar, dejando el costo en empaquetar el MJPEG. Usar `-f tee` con `onfail=ignore` y `-async 1` ayuda a mantener buffers bajos.
- **Estabilidad:** Se pueden configurar `-f tee "[f=mp4:onfail=ignore]record.mp4|[f=mpegts:onfail=ignore]udp://..."` y añadir `-reconnect 1 -reconnect_streamed 1` para tolerar cortes. Cada salida administra su propio buffer, por lo que la grabación continúa aunque el stream falle.
- **Facilidad:** Requiere conocer las opciones de `ffmpeg` y manejar scripts de reinicio del proceso ante reconexiones; sin embargo, se ejecuta con un único comando y es fácil de automatizar con systemd.
- **Flexibilidad:** `ffmpeg` soporta casi cualquier protocolo (SRT, RTMP, HLS, archivo), basta con ajustar la cadena del tee o maps adicionales.

### 3. `gstreamer` con `tee`
- **Consumo de CPU:** Un pipeline típico `gst-launch-1.0 v4l2src device=/dev/video0 io-mode=dmabuf ! image/jpeg ! tee name=t t. ! queue ! multifilesink ... t. ! queue ! jpegdec ! autovideosink` aprovecha la copia directa del MJPEG. Sólo la rama de preview decodifica el video, que puede usar aceleración vía `glimagesink`.
- **Estabilidad:** `queue` en cada rama evita bloqueo entre grabación y preview. Los elementos soportan reconexión y se pueden monitorear con `bus` para reiniciar en caso de fallo.
- **Facilidad:** Se necesita familiaridad con sintaxis de `gstreamer`, pero en Raspberry Pi 5 los paquetes `gstreamer1.0-tools` y plugins `good/bad` están en los repositorios. Los pipelines pueden guardarse en scripts o servicios.
- **Flexibilidad:** Es sencillo añadir ramas (por ejemplo, `rtmpsink`, `appsink` para OpenCV) cambiando parámetros sin reescribir lógica. También permite manipular metadata y filtros en tiempo real.

## Recomendación
- Para un prototipo rápido con mínima configuración, la opción 1 es suficiente, pero se debe aceptar el riesgo de que la vista previa interfiera con la grabación.
- Para producción donde la grabación es prioritaria y se necesita resiliencia, la opción 2 ofrece el mejor equilibrio entre CPU y robustez si se configuran correctamente los flags de reconexión.
- Para máxima flexibilidad y pipelines más complejos (por ejemplo, múltiples vistas previas, análisis en vivo), `gstreamer` (opción 3) es superior; además, mantiene un consumo de CPU muy bajo cuando se conserva el MJPEG crudo.
