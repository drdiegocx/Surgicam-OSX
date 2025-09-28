const previewImage = document.getElementById("preview-stream");
const statusMessage = document.getElementById("status-message");
const startButton = document.getElementById("start-recording");
const stopButton = document.getElementById("stop-recording");
const eventsList = document.getElementById("events");
const recordingInfo = document.getElementById("recording-info");

const CONTROL_PROTOCOL = window.location.protocol === "https:" ? "wss" : "ws";
const ws = new WebSocket(`${CONTROL_PROTOCOL}://${window.location.host}/ws`);

let latestStatus = null;
let controlConnected = false;
let previewConnected = false;
let currentFrameUrl = null;
let previewSocket = null;
let reconnectTimer = null;

const PREVIEW_RETRY_MS = 2000;

const updateConnectionStatus = (status = latestStatus) => {
  if (!controlConnected) {
    statusMessage.textContent = "Control desconectado";
    return;
  }
  if (!previewConnected) {
    statusMessage.textContent = "Conectando vista previa...";
    return;
  }
  const previewActive = status && typeof status.preview_active === "boolean"
    ? status.preview_active
    : true;
  statusMessage.textContent = previewActive
    ? "Vista previa activa"
    : "Vista previa detenida";
};

const handlePreviewFrame = async (data) => {
  let blob;
  if (data instanceof Blob) {
    blob = data;
  } else if (data instanceof ArrayBuffer) {
    blob = new Blob([data], { type: "image/jpeg" });
  } else {
    console.warn("Tipo de datos de vista previa no soportado", data);
    return;
  }

  if (currentFrameUrl) {
    URL.revokeObjectURL(currentFrameUrl);
  }
  currentFrameUrl = URL.createObjectURL(blob);
  previewImage.src = currentFrameUrl;
};

const connectPreviewSocket = () => {
  if (
    previewSocket &&
    (previewSocket.readyState === WebSocket.OPEN || previewSocket.readyState === WebSocket.CONNECTING)
  ) {
    return;
  }

  const protocol = window.location.protocol === "https:" ? "wss" : "ws";
  previewSocket = new WebSocket(`${protocol}://${window.location.host}/preview-stream`);
  previewSocket.binaryType = "arraybuffer";

  previewSocket.addEventListener("open", () => {
    previewConnected = true;
    updateConnectionStatus();
  });

  previewSocket.addEventListener("message", async (event) => {
    if (typeof event.data === "string") {
      console.warn("Mensaje de texto inesperado en vista previa", event.data);
      return;
    }
    await handlePreviewFrame(event.data);
  });

  const scheduleReconnect = () => {
    previewConnected = false;
    updateConnectionStatus();
    if (reconnectTimer) {
      clearTimeout(reconnectTimer);
    }
    previewSocket = null;
    reconnectTimer = setTimeout(connectPreviewSocket, PREVIEW_RETRY_MS);
  };

  previewSocket.addEventListener("close", scheduleReconnect);
  previewSocket.addEventListener("error", () => {
    const socket = previewSocket;
    scheduleReconnect();
    if (socket && socket.readyState !== WebSocket.CLOSED) {
      socket.close();
    }
  });
};

connectPreviewSocket();

const formatTimestamp = (isoString) => {
  if (!isoString) {
    return "";
  }
  const date = new Date(isoString);
  return date.toLocaleString();
};

const addEvent = (message) => {
  const item = document.createElement("li");
  item.textContent = `${new Date().toLocaleTimeString()} - ${message}`;
  eventsList.prepend(item);
  while (eventsList.childElementCount > 10) {
    eventsList.removeChild(eventsList.lastChild);
  }
};

const updateButtons = (recording) => {
  startButton.disabled = recording;
  stopButton.disabled = !recording;
};

const updateRecordingInfo = (status) => {
  if (!status.recording) {
    recordingInfo.textContent = "";
    return;
  }
  const started = formatTimestamp(status.recording_started_at);
  recordingInfo.textContent = `Grabando desde ${started}`;
};

const updateStatus = (status) => {
  latestStatus = status;
  updateButtons(status.recording);
  updateRecordingInfo(status);
  updateConnectionStatus(status);
};

ws.addEventListener("open", () => {
  controlConnected = true;
  updateConnectionStatus();
});

ws.addEventListener("message", (event) => {
  const payload = JSON.parse(event.data);
  switch (payload.type) {
    case "status":
      updateStatus(payload);
      break;
    case "recording_started":
      addEvent(`Grabación iniciada: ${payload.path || "desconocido"}`);
      break;
    case "recording_stopped":
      if (payload.path) {
        addEvent(`Grabación guardada en ${payload.path}`);
      } else {
        addEvent("Grabación detenida");
      }
      break;
    case "error":
      addEvent(`Error: ${payload.detail}`);
      break;
    default:
      console.warn("Mensaje desconocido", payload);
  }
});

ws.addEventListener("close", () => {
  controlConnected = false;
  updateButtons(false);
  updateConnectionStatus();
});

startButton.addEventListener("click", () => {
  ws.send(JSON.stringify({ action: "start_recording" }));
});

stopButton.addEventListener("click", () => {
  ws.send(JSON.stringify({ action: "stop_recording" }));
});
