const previewImage = document.getElementById("preview-stream");
const statusMessage = document.getElementById("status-message");
const startButton = document.getElementById("start-recording");
const stopButton = document.getElementById("stop-recording");
const eventsList = document.getElementById("events");
const recordingInfo = document.getElementById("recording-info");

const protocol = window.location.protocol === "https:" ? "wss" : "ws";
const ws = new WebSocket(`${protocol}://${window.location.host}/ws`);

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
  if (status.preview_url) {
    previewImage.src = status.preview_url;
  }
  statusMessage.textContent = status.preview_active
    ? "Vista previa activa"
    : "Vista previa detenida";
  updateButtons(status.recording);
  updateRecordingInfo(status);
};

ws.addEventListener("open", () => {
  statusMessage.textContent = "Conectado";
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
  statusMessage.textContent = "Desconectado";
  updateButtons(false);
});

startButton.addEventListener("click", () => {
  ws.send(JSON.stringify({ action: "start_recording" }));
});

stopButton.addEventListener("click", () => {
  ws.send(JSON.stringify({ action: "stop_recording" }));
});
