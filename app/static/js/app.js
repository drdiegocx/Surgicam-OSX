(function () {
  const body = document.body;
  const previewPort = body.dataset.previewPort;
  const previewImg = document.getElementById('preview');
  const startBtn = document.getElementById('startBtn');
  const stopBtn = document.getElementById('stopBtn');
  const previewState = document.getElementById('previewState');
  const recordState = document.getElementById('recordState');
  const fileInfo = document.getElementById('fileInfo');
  const alerts = document.getElementById('alerts');

  const protocol = window.location.protocol === 'https:' ? 'https://' : 'http://';
  const streamUrl = `${protocol}${window.location.hostname}:${previewPort}/stream`;
  previewImg.src = streamUrl;

  let socket;
  let reconnectTimer;

  startBtn.disabled = true;
  stopBtn.disabled = true;

  function setBadge(element, state) {
    element.textContent = state === 'running' ? 'Activo' : 'Detenido';
    element.classList.remove('bg-success', 'bg-danger', 'bg-secondary');
    if (state === 'running') {
      element.classList.add('bg-success');
    } else {
      element.classList.add('bg-danger');
    }
  }

  function setRecordingState(state, file) {
    setBadge(recordState, state === 'recording' ? 'running' : 'stopped');
    if (state === 'recording') {
      fileInfo.textContent = `Grabando en ${file || 'segmento en curso'}`;
      startBtn.disabled = true;
      stopBtn.disabled = false;
    } else {
      if (file) {
        fileInfo.textContent = `Último archivo: ${file}`;
      } else {
        fileInfo.textContent = '';
      }
      startBtn.disabled = false;
      stopBtn.disabled = true;
    }
  }

  function handleEvent(data) {
    if (data.preview) {
      setBadge(previewState, data.preview === 'running' ? 'running' : 'stopped');
    }
    if (data.status === 'snapshot') {
      if (data.recording === 'running') {
        setRecordingState('recording', data.current_file);
      } else {
        setRecordingState('idle');
      }
      return;
    }
    if (data.status === 'recording') {
      setRecordingState('recording', data.file);
      alerts.textContent = '';
      return;
    }
    if (data.status === 'idle') {
      setRecordingState('idle', data.file);
      alerts.textContent = '';
      return;
    }
    if (data.status === 'error') {
      alerts.textContent = data.detail || 'Error no especificado.';
      if (data.recording) {
        setRecordingState(data.recording, data.file);
      }
      return;
    }
  }

  function connect() {
    const wsProtocol = window.location.protocol === 'https:' ? 'wss://' : 'ws://';
    socket = new WebSocket(`${wsProtocol}${window.location.host}/ws`);

    socket.onopen = function () {
      if (reconnectTimer) {
        clearTimeout(reconnectTimer);
        reconnectTimer = undefined;
      }
      alerts.textContent = '';
    };

    socket.onmessage = function (event) {
      try {
        const data = JSON.parse(event.data);
        handleEvent(data);
      } catch (error) {
        console.error('Error al procesar evento', error);
      }
    };

    socket.onclose = function () {
      alerts.textContent = 'Reconectando con el servidor...';
      startBtn.disabled = true;
      stopBtn.disabled = true;
      reconnectTimer = setTimeout(connect, 2000);
    };

    socket.onerror = function () {
      alerts.textContent = 'Error en la comunicación con el backend.';
    };
  }

  startBtn.addEventListener('click', function () {
    if (socket && socket.readyState === WebSocket.OPEN) {
      socket.send(JSON.stringify({ command: 'start' }));
    }
  });

  stopBtn.addEventListener('click', function () {
    if (socket && socket.readyState === WebSocket.OPEN) {
      socket.send(JSON.stringify({ command: 'stop' }));
    }
  });

  window.addEventListener('beforeunload', function () {
    if (reconnectTimer) {
      clearTimeout(reconnectTimer);
    }
    if (socket) {
      socket.close();
    }
  });

  connect();
})();
