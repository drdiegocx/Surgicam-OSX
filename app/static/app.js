const previewImage = document.getElementById("preview-stream");
const statusMessage = document.getElementById("status-message");
const startButton = document.getElementById("start-recording");
const stopButton = document.getElementById("stop-recording");
const eventsList = document.getElementById("events");
const recordingInfo = document.getElementById("recording-info");

const protocol = window.location.protocol === "https:" ? "wss" : "ws";
const baseUrl = `${protocol}://${window.location.host}`;
const controlSocket = new WebSocket(`${baseUrl}/ws`);
const previewSocket = new WebSocket(`${baseUrl}/ws/preview`);
previewSocket.binaryType = "arraybuffer";

let controlConnected = false;
let previewConnected = false;
let latestStatus = null;

const updateConnectionStatus = () => {
  if (previewConnected && (latestStatus?.preview_active ?? true)) {
    const fpsValue = Number(latestStatus.preview_fps || 0);
    const fps = fpsValue > 0 ? ` @ ${fpsValue.toFixed(1).replace(/\.0$/, "")} fps` : "";
    statusMessage.textContent = `Vista previa activa${fps}`;
  } else if (previewConnected) {
    statusMessage.textContent = "Vista previa conectada";
  } else if (controlConnected) {
    statusMessage.textContent = "Control conectado";
  } else {
    statusMessage.textContent = "Desconectado";
  }
};

const sendControlMessage = (payload) => {
  if (controlSocket.readyState !== WebSocket.OPEN) {
    addEvent("Control desconectado. No se pudo enviar el comando.");
    return;
  }
  controlSocket.send(JSON.stringify(payload));
};

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
  updateConnectionStatus();
  updateButtons(status.recording);
  updateRecordingInfo(status);
};

controlSocket.addEventListener("open", () => {
  controlConnected = true;
  updateConnectionStatus();
});

controlSocket.addEventListener("message", (event) => {
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

controlSocket.addEventListener("close", () => {
  controlConnected = false;
  updateConnectionStatus();
  updateButtons(false);
});

controlSocket.addEventListener("error", () => {
  addEvent("Error en la conexión de control");
});

startButton.addEventListener("click", () => {
  sendControlMessage({ action: "start_recording" });
});

stopButton.addEventListener("click", () => {
  sendControlMessage({ action: "stop_recording" });
});

previewSocket.addEventListener("open", () => {
  previewConnected = true;
  updateConnectionStatus();
});

previewSocket.addEventListener("close", () => {
  previewConnected = false;
  updateConnectionStatus();
});

previewSocket.addEventListener("error", () => {
  addEvent("Error en la conexión de vista previa");
});

previewSocket.addEventListener("message", (event) => {
  if (typeof event.data === "string") {
    return;
  }
  const blob = new Blob([event.data], { type: "image/jpeg" });
  const url = URL.createObjectURL(blob);
  previewImage.src = url;
  previewImage.onload = () => {
    URL.revokeObjectURL(url);
  };
});
