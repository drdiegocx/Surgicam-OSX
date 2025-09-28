import asyncio
import datetime
import logging
import pathlib
import threading
import time
from dataclasses import dataclass
from typing import AsyncIterator, List, Optional

import gi
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

gi.require_version("Gst", "1.0")
from gi.repository import Gst

logging.basicConfig(level=logging.INFO)

RECORDINGS_DIR = pathlib.Path("recordings")


@dataclass
class RecordingBranch:
    pad: Gst.Pad
    elements: List[Gst.Element]
    location: pathlib.Path


class CameraController:
    def __init__(self, device: str = "/dev/video0") -> None:
        Gst.init(None)
        self._device = device
        self._pipeline = Gst.Pipeline.new("camera-pipeline")
        self._tee = Gst.ElementFactory.make("tee", "tee")
        self._preview_sink = Gst.ElementFactory.make("appsink", "preview-sink")
        if not self._tee or not self._preview_sink:
            raise RuntimeError("Failed to create GStreamer elements")

        self._configure_pipeline()

        self._frame_lock = threading.Lock()
        self._latest_frame: Optional[bytes] = None
        self._clients: List[asyncio.Queue[bytes]] = []
        self._recording: Optional[RecordingBranch] = None
        self._recording_lock = threading.Lock()

        self._running = threading.Event()
        self._shutdown = threading.Event()
        self._preview_thread = threading.Thread(target=self._preview_loop, daemon=True)
        self._preview_thread.start()

    def _configure_pipeline(self) -> None:
        src = Gst.ElementFactory.make("v4l2src", "source")
        capsfilter = Gst.ElementFactory.make("capsfilter", "source-caps")
        if not src or not capsfilter:
            raise RuntimeError("Failed to create source elements")

        src.set_property("device", self._device)
        caps = Gst.Caps.from_string(
            "image/jpeg,width=4608,height=2592,framerate=10/1"
        )
        capsfilter.set_property("caps", caps)

        queue_preview = Gst.ElementFactory.make("queue", "preview-queue")
        jpegdec = Gst.ElementFactory.make("jpegdec", "preview-dec")
        videoscale = Gst.ElementFactory.make("videoscale", "preview-scale")
        videorate = Gst.ElementFactory.make("videorate", "preview-rate")
        videoconvert = Gst.ElementFactory.make("videoconvert", "preview-convert")
        preview_caps = Gst.ElementFactory.make("capsfilter", "preview-caps")
        jpegenc = Gst.ElementFactory.make("jpegenc", "preview-enc")

        if not all(
            [
                queue_preview,
                jpegdec,
                videoscale,
                videorate,
                videoconvert,
                preview_caps,
                jpegenc,
            ]
        ):
            raise RuntimeError("Failed to create preview elements")

        queue_preview.set_property("max-size-buffers", 1)
        queue_preview.set_property("max-size-bytes", 0)
        queue_preview.set_property("max-size-time", 0)
        queue_preview.set_property("leaky", "downstream")
        videorate.set_property("drop-only", True)
        jpegdec.set_property("idct-method", "ifast")

        preview_caps.set_property(
            "caps",
            Gst.Caps.from_string(
                "video/x-raw,format=I420,width=1280,height=720,framerate=10/1"
            ),
        )
        self._preview_sink.set_property("emit-signals", False)
        self._preview_sink.set_property("max-buffers", 1)
        self._preview_sink.set_property("drop", True)
        self._preview_sink.set_property("sync", False)

        jpegenc.set_property("quality", 55)

        self._pipeline.add(src)
        self._pipeline.add(capsfilter)
        self._pipeline.add(self._tee)
        for element in (
            queue_preview,
            jpegdec,
            videoscale,
            videorate,
            videoconvert,
            preview_caps,
            jpegenc,
            self._preview_sink,
        ):
            self._pipeline.add(element)

        if not src.link(capsfilter):
            raise RuntimeError("Failed to link camera source")
        if not capsfilter.link(self._tee):
            raise RuntimeError("Failed to link capsfilter to tee")

        if not queue_preview.link(jpegdec):
            raise RuntimeError("Failed to link preview queue")
        if not jpegdec.link(videoscale):
            raise RuntimeError("Failed to link jpegdec to videoscale")
        if not videoscale.link(videorate):
            raise RuntimeError("Failed to link videoscale to videorate")
        if not videorate.link(videoconvert):
            raise RuntimeError("Failed to link videorate to videoconvert")
        if not videoconvert.link(preview_caps):
            raise RuntimeError("Failed to link videoconvert to capsfilter")
        if not preview_caps.link(jpegenc):
            raise RuntimeError("Failed to link preview caps to encoder")
        if not jpegenc.link(self._preview_sink):
            raise RuntimeError("Failed to link preview encoder to sink")

        tee_pad = self._tee.get_request_pad("src_%u")
        queue_pad = queue_preview.get_static_pad("sink")
        if tee_pad is None or queue_pad is None:
            raise RuntimeError("Failed to request tee pad")
        if tee_pad.link(queue_pad) != Gst.PadLinkReturn.OK:
            raise RuntimeError("Failed to link tee to preview queue")

    def start(self) -> None:
        if self._running.is_set():
            return
        self._running.set()
        if self._pipeline.set_state(Gst.State.PLAYING) == Gst.StateChangeReturn.FAILURE:
            self._running.clear()
            raise RuntimeError("Unable to start camera pipeline")

    def stop(self) -> None:
        self._running.clear()
        self._pipeline.set_state(Gst.State.NULL)

    def shutdown(self) -> None:
        self.stop()
        self._shutdown.set()
        self._preview_thread.join(timeout=2)

    def register_queue(self, queue: asyncio.Queue[bytes]) -> None:
        self._clients.append(queue)

    def unregister_queue(self, queue: asyncio.Queue[bytes]) -> None:
        if queue in self._clients:
            self._clients.remove(queue)

    def _preview_loop(self) -> None:
        while not self._shutdown.is_set():
            if not self._running.is_set():
                time.sleep(0.05)
                continue
            sample = self._preview_sink.emit("try-pull-sample", Gst.SECOND // 2)
            if sample is None:
                continue
            buffer = sample.get_buffer()
            success, map_info = buffer.map(Gst.MapFlags.READ)
            if not success:
                continue
            frame_bytes = bytes(map_info.data)
            buffer.unmap(map_info)
            with self._frame_lock:
                self._latest_frame = frame_bytes
            for queue in list(self._clients):
                try:
                    queue.put_nowait(frame_bytes)
                except asyncio.QueueFull:
                    try:
                        queue.get_nowait()
                    except asyncio.QueueEmpty:
                        pass
                    try:
                        queue.put_nowait(frame_bytes)
                    except asyncio.QueueFull:
                        logging.warning("Dropping frame for client")

    async def frame_generator(self) -> AsyncIterator[bytes]:
        queue: asyncio.Queue[bytes] = asyncio.Queue(maxsize=1)
        self.register_queue(queue)
        try:
            while True:
                frame = await queue.get()
                yield frame
        finally:
            self.unregister_queue(queue)

    def get_latest_frame(self) -> Optional[bytes]:
        with self._frame_lock:
            return self._latest_frame

    def start_recording(self, location: pathlib.Path) -> None:
        with self._recording_lock:
            if self._recording is not None:
                raise RuntimeError("Recording already in progress")

            queue = Gst.ElementFactory.make("queue", None)
            jpegparse = Gst.ElementFactory.make("jpegparse", None)
            mux = Gst.ElementFactory.make("avimux", None)
            filesink = Gst.ElementFactory.make("filesink", None)

            if not all([queue, jpegparse, mux, filesink]):
                raise RuntimeError("Failed to allocate recording elements")

            filesink.set_property("location", str(location))
            filesink.set_property("sync", False)
            filesink.set_property("async", False)
            queue.set_property("flush-on-eos", True)
            queue.set_property("max-size-buffers", 0)
            queue.set_property("max-size-bytes", 0)
            queue.set_property("max-size-time", 0)

            for element in (queue, jpegparse, mux, filesink):
                self._pipeline.add(element)

            if not queue.link(jpegparse):
                raise RuntimeError("Failed to link queue to jpegparse")
            if not jpegparse.link(mux):
                raise RuntimeError("Failed to link jpegparse to mux")
            if not mux.link(filesink):
                raise RuntimeError("Failed to link mux to filesink")

            tee_pad = self._tee.get_request_pad("src_%u")
            queue_pad = queue.get_static_pad("sink")
            if tee_pad is None or queue_pad is None:
                raise RuntimeError("Failed to obtain recording pads")
            if tee_pad.link(queue_pad) != Gst.PadLinkReturn.OK:
                raise RuntimeError("Failed to link tee to recording branch")

            for element in (queue, jpegparse, mux, filesink):
                element.sync_state_with_parent()

            self._recording = RecordingBranch(
                pad=tee_pad,
                elements=[queue, jpegparse, mux, filesink],
                location=location,
            )

    def stop_recording(self) -> pathlib.Path:
        with self._recording_lock:
            branch = self._recording
            if branch is None:
                raise RuntimeError("No recording in progress")

            self._recording = None

            queue_element = branch.elements[0]
            queue_sink_pad = queue_element.get_static_pad("sink")
            queue_src_pad = queue_element.get_static_pad("src")
            eos_event = threading.Event()

            def _block_probe(_pad: Gst.Pad, _info: Gst.PadProbeInfo) -> Gst.PadProbeReturn:
                if queue_src_pad and queue_src_pad.is_linked():
                    success = queue_src_pad.push_event(Gst.Event.new_eos())
                    if not success:
                        logging.warning("Failed to push EOS to recording branch")
                eos_event.set()
                return Gst.PadProbeReturn.REMOVE

            probe_id = branch.pad.add_probe(
                Gst.PadProbeType.BLOCK_DOWNSTREAM | Gst.PadProbeType.IDLE,
                _block_probe,
            )

            if not eos_event.wait(timeout=2):
                logging.warning(
                    "Timed out waiting to drain recording branch; forcing shutdown"
                )
                branch.pad.remove_probe(probe_id)

            if branch.pad.is_linked() and queue_sink_pad and queue_sink_pad.is_linked():
                branch.pad.unlink(queue_sink_pad)

            bus = self._pipeline.get_bus()
            deadline = time.time() + 5
            while time.time() < deadline:
                message = bus.timed_pop_filtered(
                    Gst.SECOND // 2,
                    Gst.MessageType.EOS | Gst.MessageType.ERROR,
                )
                if message is None:
                    continue
                if message.type == Gst.MessageType.ERROR:
                    err, debug = message.parse_error()
                    logging.error("Error stopping recording: %s %s", err, debug)
                    break
                if message.type == Gst.MessageType.EOS and message.src in branch.elements:
                    break

            for element in reversed(branch.elements):
                element.set_state(Gst.State.NULL)
                self._pipeline.remove(element)

            self._tee.release_request_pad(branch.pad)

            return branch.location

    @property
    def is_recording(self) -> bool:
        return self._recording is not None


camera = CameraController()
app = FastAPI(title="Surgicam Streaming")


@app.on_event("startup")
async def startup_event() -> None:
    camera.start()


@app.on_event("shutdown")
async def shutdown_event() -> None:
    camera.shutdown()


@app.get("/")
async def index() -> HTMLResponse:
    html_path = pathlib.Path(__file__).with_name("static").joinpath("index.html")
    if not html_path.exists():
        raise HTTPException(status_code=500, detail="Missing UI")
    return HTMLResponse(html_path.read_text(encoding="utf-8"))


@app.websocket("/ws/preview")
async def preview_ws(websocket: WebSocket) -> None:
    await websocket.accept()
    try:
        async for frame in camera.frame_generator():
            await websocket.send_bytes(frame)
    except WebSocketDisconnect:
        logging.info("WebSocket disconnected")


@app.post("/recordings/start")
async def start_recording() -> dict:
    if camera.is_recording:
        raise HTTPException(status_code=400, detail="Recording already in progress")

    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    output_dir = RECORDINGS_DIR
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / f"recording_{timestamp}.avi"
    camera.start_recording(path)
    return {"status": "recording", "file": str(path)}


@app.post("/recordings/stop")
async def stop_recording() -> dict:
    if not camera.is_recording:
        raise HTTPException(status_code=400, detail="No active recording")
    path = camera.stop_recording()
    return {"status": "stopped", "file": str(path)}


app.mount(
    "/static",
    StaticFiles(directory=pathlib.Path(__file__).with_name("static")),
    name="static",
)

recordings_dir = RECORDINGS_DIR
recordings_dir.mkdir(parents=True, exist_ok=True)
app.mount(
    "/recordings",
    StaticFiles(directory=recordings_dir),
    name="recordings",
)
