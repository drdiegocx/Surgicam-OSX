(function () {
  const body = document.body;
  const previewPort = body.dataset.previewPort;
  const sourceWidth = Number(body.dataset.sourceWidth) || 1280;
  const sourceHeight = Number(body.dataset.sourceHeight) || 720;
  const sourceRatio = sourceHeight && sourceWidth ? sourceHeight / sourceWidth : 9 / 16;
  const previewImg = document.getElementById('preview');
  const previewFrame = document.getElementById('previewFrame');
  const miniMap = document.getElementById('roiMiniMap');
  const miniMapImg = document.getElementById('roiMiniMapImg');
  const roiIndicator = document.getElementById('roiIndicator');
  const zoomSlider = document.getElementById('zoomSlider');
  const zoomValue = document.getElementById('zoomValue');
  const startBtn = document.getElementById('startBtn');
  const stopBtn = document.getElementById('stopBtn');
  const previewState = document.getElementById('previewState');
  const recordState = document.getElementById('recordState');
  const fileInfo = document.getElementById('fileInfo');
  const alerts = document.getElementById('alerts');

  const controlsDrawer = document.getElementById('controlsDrawer');
  const controlsLoading = document.getElementById('controlsLoading');
  const controlsContent = document.getElementById('controlsContent');
  const controlsAlert = document.getElementById('controlsAlert');
  const controlsTabNav = document.getElementById('controlsTabNav');
  const controlsTabContent = document.getElementById('controlsTabContent');

  const controlElements = new Map();
  let isLoadingControls = false;
  let controlsLoaded = false;
  let controlsMessageTimer;

  const protocol = window.location.protocol === 'https:' ? 'https://' : 'http://';
  const streamUrl = `${protocol}${window.location.hostname}:${previewPort}/stream`;
  const snapshotUrl = `${protocol}${window.location.hostname}:${previewPort}/snapshot`;
  previewImg.src = streamUrl;

  const MINI_MAP_REFRESH_MS = 4000;
  let miniMapIntervalId;
  let lastMiniMapUpdate = 0;

  function refreshMiniMap(force) {
    if (!miniMapImg) {
      return;
    }
    const now = Date.now();
    if (!force && now - lastMiniMapUpdate < 500) {
      return;
    }
    lastMiniMapUpdate = now;
    miniMapImg.src = `${snapshotUrl}?_=${now}`;
  }

  function startMiniMapTimer() {
    if (!miniMapImg) {
      return;
    }
    if (miniMapIntervalId) {
      window.clearInterval(miniMapIntervalId);
    }
    refreshMiniMap(true);
    miniMapIntervalId = window.setInterval(function () {
      refreshMiniMap(false);
    }, MINI_MAP_REFRESH_MS);
  }

  startMiniMapTimer();

  let socket;
  let reconnectTimer;
  const messageQueue = [];
  const pendingControlUpdates = new Set();

  const panButtons = document.querySelectorAll('[data-pan]');
  const minZoom = zoomSlider ? Math.max(1, (Number(zoomSlider.min) / 100) || 1) : 1;
  const maxZoom = zoomSlider ? Math.max(minZoom, (Number(zoomSlider.max) / 100) || minZoom) : 1;
  const defaultZoom = zoomSlider
    ? Math.min(maxZoom, Math.max(minZoom, (Number(zoomSlider.value) / 100) || minZoom))
    : 1;
  let zoomLevel = defaultZoom;
  let panX = 0;
  let panY = 0;

  startBtn.disabled = true;
  stopBtn.disabled = true;

  function clampPan() {
    const maxOffset = Math.max(0, 1 - 1 / zoomLevel);
    panX = Math.min(Math.max(panX, 0), maxOffset);
    panY = Math.min(Math.max(panY, 0), maxOffset);
  }

  function updateZoomDisplay() {
    if (zoomValue) {
      zoomValue.textContent = `${zoomLevel.toFixed(1)}x`;
    }
    if (zoomSlider) {
      const sliderValue = Math.round(zoomLevel * 100);
      if (Number(zoomSlider.value) !== sliderValue) {
        zoomSlider.value = String(sliderValue);
      }
    }
  }

  function applyPanZoom() {
    clampPan();
    if (previewImg) {
      previewImg.style.transform = `translate(${-panX * 100}%, ${-panY * 100}%) scale(${zoomLevel})`;
    }
    if (roiIndicator && miniMap) {
      const frameWidth = miniMap.clientWidth;
      const frameHeight = miniMap.clientHeight;
      if (frameWidth && frameHeight) {
        let displayWidth = frameWidth;
        let displayHeight = frameWidth * sourceRatio;
        let offsetX = 0;
        let offsetY = 0;
        if (displayHeight > frameHeight) {
          displayHeight = frameHeight;
          displayWidth = frameHeight / sourceRatio;
          offsetX = (frameWidth - displayWidth) / 2;
        } else {
          offsetY = (frameHeight - displayHeight) / 2;
        }

        const viewportWidthPx = displayWidth / zoomLevel;
        const viewportHeightPx = displayHeight / zoomLevel;
        const leftPx = offsetX + displayWidth * panX;
        const topPx = offsetY + displayHeight * panY;

        roiIndicator.style.width = `${viewportWidthPx}px`;
        roiIndicator.style.height = `${viewportHeightPx}px`;
        roiIndicator.style.left = `${leftPx}px`;
        roiIndicator.style.top = `${topPx}px`;
      }
    }
    refreshMiniMap(true);
    updateZoomDisplay();
  }

  function getCurrentRoi() {
    clampPan();
    const width = Math.min(1, 1 / zoomLevel);
    const height = Math.min(1, 1 / zoomLevel);
    const maxPanX = Math.max(0, 1 - width);
    const maxPanY = Math.max(0, 1 - height);
    const x = Math.min(Math.max(panX, 0), maxPanX);
    const y = Math.min(Math.max(panY, 0), maxPanY);
    return {
      x: Number(x.toFixed(4)),
      y: Number(y.toFixed(4)),
      width: Number(width.toFixed(4)),
      height: Number(height.toFixed(4)),
      zoom: Number(zoomLevel.toFixed(4)),
    };
  }

  function setZoomLevel(value) {
    const desired = Number.isFinite(value) ? value : zoomLevel;
    const bounded = Math.min(Math.max(desired, minZoom), maxZoom);
    if (bounded === zoomLevel) {
      applyPanZoom();
      return;
    }
    const currentCenterX = panX + 0.5 / zoomLevel;
    const currentCenterY = panY + 0.5 / zoomLevel;
    zoomLevel = bounded;
    panX = currentCenterX - 0.5 / zoomLevel;
    panY = currentCenterY - 0.5 / zoomLevel;
    applyPanZoom();
  }

  function centerPan() {
    const maxOffset = Math.max(0, 1 - 1 / zoomLevel);
    panX = maxOffset / 2;
    panY = maxOffset / 2;
    applyPanZoom();
  }

  function nudgePan(deltaX, deltaY) {
    panX += deltaX;
    panY += deltaY;
    applyPanZoom();
  }

  function handleMiniMapClick(event) {
    if (!miniMap) {
      return;
    }
    const rect = miniMap.getBoundingClientRect();
    if (!rect.width || !rect.height) {
      return;
    }
    const frameWidth = rect.width;
    const frameHeight = rect.height;
    let displayWidth = frameWidth;
    let displayHeight = frameWidth * sourceRatio;
    let offsetX = 0;
    let offsetY = 0;
    if (displayHeight > frameHeight) {
      displayHeight = frameHeight;
      displayWidth = frameHeight / sourceRatio;
      offsetX = (frameWidth - displayWidth) / 2;
    } else {
      offsetY = (frameHeight - displayHeight) / 2;
    }
    if (!displayWidth || !displayHeight) {
      return;
    }
    const clickX = event.clientX - rect.left;
    const clickY = event.clientY - rect.top;
    const localX = Math.min(Math.max(clickX - offsetX, 0), displayWidth);
    const localY = Math.min(Math.max(clickY - offsetY, 0), displayHeight);
    const relativeX = localX / displayWidth;
    const relativeY = localY / displayHeight;
    panX = relativeX - 0.5 / zoomLevel;
    panY = relativeY - 0.5 / zoomLevel;
    applyPanZoom();
  }

  function handlePreviewClick(event) {
    if (!previewFrame) {
      return;
    }
    if (event.target && (event.target.closest('.roi-controls') || event.target.closest('.roi-mini-map'))) {
      return;
    }
    const rect = previewFrame.getBoundingClientRect();
    if (!rect.width || !rect.height) {
      return;
    }
    const relativeX = (event.clientX - rect.left) / rect.width;
    const relativeY = (event.clientY - rect.top) / rect.height;
    panX = relativeX - 0.5 / zoomLevel;
    panY = relativeY - 0.5 / zoomLevel;
    applyPanZoom();
  }

  if (zoomSlider) {
    zoomSlider.addEventListener('input', function (event) {
      const value = Number(event.target.value);
      if (!Number.isNaN(value)) {
        setZoomLevel(value / 100);
      }
    });
  }

  if (miniMap) {
    miniMap.addEventListener('click', handleMiniMapClick);
    miniMap.addEventListener('mousemove', function (event) {
      if (event.buttons === 1) {
        handleMiniMapClick(event);
      }
    });
  }

  if (previewFrame) {
    previewFrame.addEventListener('click', handlePreviewClick);
  }

  window.addEventListener('resize', function () {
    applyPanZoom();
  });

  if (panButtons && panButtons.length) {
    panButtons.forEach(function (button) {
      button.addEventListener('click', function () {
        const direction = button.dataset.pan;
        if (!direction) {
          return;
        }
        const step = 0.18 / zoomLevel;
        switch (direction) {
          case 'up':
            nudgePan(0, -step);
            break;
          case 'down':
            nudgePan(0, step);
            break;
          case 'left':
            nudgePan(-step, 0);
            break;
          case 'right':
            nudgePan(step, 0);
            break;
          case 'center':
            centerPan();
            break;
          default:
            break;
        }
      });
    });
  }

  applyPanZoom();

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
    if (data.status === 'controls') {
      const scope = data.scope || 'list';
      if (scope === 'update') {
        handleControlsUpdate(data);
      } else {
        handleControlsList(data);
      }
      return;
    }

    if (data.status === 'controls:error') {
      handleControlsError(data);
      return;
    }

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

  function handleControlsList(data) {
    const controls = Array.isArray(data.controls) ? data.controls : [];
    buildControls(controls);
    controlsLoaded = true;
    isLoadingControls = false;
    pendingControlUpdates.clear();
    setControlsLoading(false);
    showControlsContent(true);
    showControlsMessage('');
  }

  function handleControlsUpdate(data) {
    if (!data || !data.control) {
      return;
    }
    const control = data.control;
    updateControlUI(control);
    const identifier = control.identifier;
    if (identifier) {
      setControlBusy(identifier, false);
      if (pendingControlUpdates.has(identifier)) {
        const entry = controlElements.get(identifier);
        const controlName = entry && entry.name ? `"${entry.name}"` : 'el control';
        showControlsMessage(`Se aplicó ${controlName}.`, 'success');
        pendingControlUpdates.delete(identifier);
      }
    }
  }

  function handleControlsError(data) {
    const scope = data && data.scope ? data.scope : 'list';
    const detail = (data && data.detail) || 'No se pudo procesar la petición.';
    if (scope === 'update') {
      if (data && data.identifier) {
        pendingControlUpdates.delete(data.identifier);
        setControlBusy(data.identifier, false);
      }
      showControlsMessage(detail, 'danger');
      if (data && data.refresh) {
        window.setTimeout(function () {
          loadControls(true);
        }, 350);
      }
      return;
    }
    isLoadingControls = false;
    controlsLoaded = false;
    setControlsLoading(false);
    showControlsContent(false);
    showControlsMessage(detail, 'danger');
  }

  function flushQueue() {
    if (!socket || socket.readyState !== WebSocket.OPEN) {
      return;
    }
    while (messageQueue.length > 0) {
      socket.send(messageQueue.shift());
    }
  }

  function sendCommand(command, payload = {}, options = {}) {
    const config = options || {};
    const allowQueue = Boolean(config.allowQueue);
    const message = JSON.stringify({ command, ...payload });
    if (socket && socket.readyState === WebSocket.OPEN) {
      socket.send(message);
      return true;
    }
    if (allowQueue) {
      if (messageQueue.length >= 8) {
        messageQueue.shift();
      }
      messageQueue.push(message);
      return true;
    }
    return false;
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
      flushQueue();
      if (!controlsLoaded) {
        loadControls();
      } else if (
        controlsDrawer &&
        controlsDrawer.classList.contains('show') &&
        !isLoadingControls
      ) {
        loadControls(true);
      }
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
      pendingControlUpdates.forEach(function (controlId) {
        setControlBusy(controlId, false);
      });
      pendingControlUpdates.clear();
      if (isLoadingControls) {
        isLoadingControls = false;
        setControlsLoading(false);
      }
      controlsLoaded = false;
      messageQueue.length = 0;
      showControlsMessage('Conexión perdida. Reintentando…', 'warning');
      reconnectTimer = setTimeout(connect, 2000);
    };

    socket.onerror = function () {
      alerts.textContent = 'Error en la comunicación con el backend.';
      showControlsMessage('Error de comunicación con el backend.', 'danger');
    };
  }

  function showControlsMessage(message, variant = 'danger') {
    if (!controlsAlert) {
      return;
    }

    if (controlsMessageTimer) {
      clearTimeout(controlsMessageTimer);
      controlsMessageTimer = undefined;
    }

    controlsAlert.textContent = '';
    controlsAlert.className = 'alert d-none';

    if (!message) {
      return;
    }

    const supported = new Set(['success', 'danger', 'warning', 'info']);
    const selected = supported.has(variant) ? variant : 'danger';
    controlsAlert.classList.add(`alert-${selected}`);
    controlsAlert.classList.remove('d-none');
    controlsAlert.textContent = message;

    if (selected === 'success') {
      controlsMessageTimer = setTimeout(function () {
        showControlsMessage('');
      }, 4000);
    }
  }

  function setControlsLoading(state) {
    if (controlsLoading) {
      controlsLoading.classList.toggle('d-none', !state);
    }
  }

  function showControlsContent(state) {
    if (controlsContent) {
      controlsContent.classList.toggle('d-none', !state);
    }
  }

  function formatValue(value, type) {
    if (value === null || value === undefined) {
      return '-';
    }
    const normalizedType = (type || '').toLowerCase();
    if (normalizedType === 'bool' || normalizedType === 'boolean') {
      const boolValue = value === true || value === 1 || value === '1';
      return boolValue ? 'Activado' : 'Desactivado';
    }
    if (typeof value === 'number') {
      if (Number.isInteger(value)) {
        return String(value);
      }
      return Number(value).toFixed(2).replace(/\.00$/, '').replace(/\.0$/, '');
    }
    return String(value);
  }

  function toComparable(value, type) {
    if (value === null || value === undefined) {
      return value;
    }
    const normalizedType = (type || '').toLowerCase();
    if (normalizedType === 'bool' || normalizedType === 'boolean') {
      if (typeof value === 'boolean') {
        return value;
      }
      if (typeof value === 'number') {
        return value !== 0;
      }
      const lowered = String(value).trim().toLowerCase();
      return lowered === '1' || lowered === 'true' || lowered === 'si' || lowered === 'sí';
    }
    if (
      normalizedType === 'menu' ||
      normalizedType === 'intmenu' ||
      normalizedType === 'integer_menu' ||
      normalizedType === 'integer menu' ||
      normalizedType === 'int' ||
      normalizedType === 'integer' ||
      normalizedType === 'int64'
    ) {
      return Number(value);
    }
    if (normalizedType === 'float' || normalizedType === 'double') {
      return Number(value);
    }
    return value;
  }

  function isAtDefault(control) {
    if (control.default === null || control.default === undefined) {
      return false;
    }
    return toComparable(control.value, control.type) === toComparable(control.default, control.type);
  }

  function updateDefaultButtonState(entry) {
    if (!entry || !entry.defaultButton) {
      return;
    }
    const shouldDisable = entry.busy || !entry.hasDefault || entry.isDefault;
    entry.defaultButton.disabled = shouldDisable;
  }

  function registerControl(control, entry) {
    entry.controlType = control.type;
    entry.hasDefault = control.default !== null && control.default !== undefined;
    entry.isDefault = isAtDefault(control);
    entry.busy = false;
    updateDefaultButtonState(entry);
    entry.name = control.name;
    controlElements.set(control.id, entry);
  }

  function updateControlUI(control) {
    const entry = controlElements.get(control.id);
    if (!entry) {
      return;
    }
    entry.hasDefault = control.default !== null && control.default !== undefined;
    entry.isDefault = isAtDefault(control);
    if (entry.valueElement) {
      entry.valueElement.textContent = formatValue(control.value, control.type);
    }
    if (entry.minElement) {
      entry.minElement.textContent = formatValue(control.min, control.type);
    }
    if (entry.maxElement) {
      entry.maxElement.textContent = formatValue(control.max, control.type);
    }
    if (entry.defaultElement) {
      entry.defaultElement.textContent = formatValue(control.default, control.type);
    }
    if (entry.input && entry.inputType === 'range') {
      const numericValue = Number(control.value ?? control.default ?? control.min ?? 0);
      entry.input.value = numericValue;
    }
    if (entry.input && entry.inputType === 'select') {
      const value = control.value ?? control.default;
      entry.input.value = value !== undefined && value !== null ? String(value) : '';
    }
    if (entry.input && entry.inputType === 'toggle') {
      const current = toComparable(control.value, control.type);
      entry.input.checked = Boolean(current);
    }
    entry.wrapper.dataset.value = control.value;
    entry.wrapper.dataset.default = control.default;
    entry.wrapper.dataset.min = control.min;
    entry.wrapper.dataset.max = control.max;
    updateDefaultButtonState(entry);
  }

  function setControlBusy(controlId, busy) {
    const entry = controlElements.get(controlId);
    if (!entry) {
      return;
    }
    entry.busy = busy;
    if (entry.input) {
      entry.input.disabled = busy;
    }
    if (entry.wrapper) {
      entry.wrapper.classList.toggle('control-updating', busy);
    }
    updateDefaultButtonState(entry);
  }

  function buildMetadataRow(control) {
    const wrapper = document.createElement('div');
    wrapper.className = 'small text-muted mt-3';

    const valueLabel = document.createElement('span');
    valueLabel.innerHTML = 'Valor actual: ';
    const valueSpan = document.createElement('span');
    valueSpan.className = 'fw-semibold';
    valueSpan.textContent = formatValue(control.value, control.type);
    valueLabel.appendChild(valueSpan);
    wrapper.appendChild(valueLabel);

    const minSpan = document.createElement('span');
    minSpan.className = 'ms-3';
    minSpan.innerHTML = 'Mínimo: ';
    const minValue = document.createElement('span');
    minValue.textContent = formatValue(control.min, control.type);
    minSpan.appendChild(minValue);
    wrapper.appendChild(minSpan);

    const maxSpan = document.createElement('span');
    maxSpan.className = 'ms-3';
    maxSpan.innerHTML = 'Máximo: ';
    const maxValue = document.createElement('span');
    maxValue.textContent = formatValue(control.max, control.type);
    maxSpan.appendChild(maxValue);
    wrapper.appendChild(maxSpan);

    const defaultSpan = document.createElement('span');
    defaultSpan.className = 'ms-3';
    defaultSpan.innerHTML = 'Predeterminado: ';
    const defaultValue = document.createElement('span');
    defaultValue.textContent = formatValue(control.default, control.type);
    defaultSpan.appendChild(defaultValue);
    wrapper.appendChild(defaultSpan);

    return {
      wrapper,
      valueElement: valueSpan,
      minElement: minValue,
      maxElement: maxValue,
      defaultElement: defaultValue,
    };
  }

  function createControlElement(control) {
    const card = document.createElement('div');
    card.className = 'control-card rounded-3 border border-light-subtle bg-dark-subtle p-3 mb-3';
    card.dataset.controlId = control.id;

    const header = document.createElement('div');
    header.className = 'd-flex justify-content-between align-items-start gap-3';

    const title = document.createElement('div');
    title.innerHTML = `<h3 class="h6 mb-1">${control.name}</h3>`;
    const typeBadge = document.createElement('span');
    typeBadge.className = 'badge text-bg-secondary';
    typeBadge.textContent = control.type;
    title.appendChild(typeBadge);
    header.appendChild(title);

    const defaultButton = document.createElement('button');
    defaultButton.type = 'button';
    defaultButton.className = 'btn btn-outline-light btn-sm';
    defaultButton.textContent = 'Restablecer';
    defaultButton.addEventListener('click', function () {
      sendControlUpdate(control.id, { action: 'default' });
    });
    header.appendChild(defaultButton);

    card.appendChild(header);

    let inputElement = null;
    let inputType = null;

    const metadata = buildMetadataRow(control);

    const normalizedType = (control.type || '').toLowerCase();
    if (normalizedType === 'bool' || normalizedType === 'boolean') {
      const formSwitch = document.createElement('div');
      formSwitch.className = 'form-check form-switch mt-2';
      const input = document.createElement('input');
      input.type = 'checkbox';
      input.className = 'form-check-input';
      input.checked = Boolean(toComparable(control.value, control.type));
      input.addEventListener('change', function () {
        sendControlUpdate(control.id, { value: input.checked });
      });
      formSwitch.appendChild(input);
      const label = document.createElement('label');
      label.className = 'form-check-label';
      label.textContent = 'Activo';
      formSwitch.appendChild(label);
      card.appendChild(formSwitch);
      inputElement = input;
      inputType = 'toggle';
    } else if (
      normalizedType === 'menu' ||
      normalizedType === 'intmenu' ||
      normalizedType === 'integer_menu' ||
      normalizedType === 'integer menu'
    ) {
      const select = document.createElement('select');
      select.className = 'form-select form-select-sm mt-2';
      if (Array.isArray(control.options)) {
        control.options.forEach(function (option) {
          const opt = document.createElement('option');
          opt.value = String(option.value);
          opt.textContent = `${option.value} — ${option.label}`;
          select.appendChild(opt);
        });
      }
      const currentOption = control.value ?? control.default;
      if (currentOption !== undefined && currentOption !== null) {
        select.value = String(currentOption);
      }
      if (select.options.length > 0 && select.selectedIndex === -1) {
        select.selectedIndex = 0;
      }
      select.addEventListener('change', function () {
        const selectedValue = parseInt(select.value, 10);
        sendControlUpdate(control.id, { value: selectedValue });
      });
      card.appendChild(select);
      inputElement = select;
      inputType = 'select';
    } else if (
      normalizedType === 'int' ||
      normalizedType === 'integer' ||
      normalizedType === 'int64' ||
      normalizedType === 'float' ||
      normalizedType === 'double'
    ) {
      const range = document.createElement('input');
      range.type = 'range';
      range.className = 'form-range mt-2';
      if (control.min !== null && control.min !== undefined) {
        range.min = control.min;
      }
      if (control.max !== null && control.max !== undefined) {
        range.max = control.max;
      }
      const step = control.step !== null && control.step !== undefined ? control.step : 1;
      range.step = step;
      const current = Number(control.value ?? control.default ?? control.min ?? 0);
      range.value = current;
      range.addEventListener('input', function () {
        if (metadata.valueElement) {
          metadata.valueElement.textContent = formatValue(Number(range.value), control.type);
        }
      });
      range.addEventListener('change', function () {
        const raw = normalizedType === 'float' || normalizedType === 'double'
          ? parseFloat(range.value)
          : parseInt(range.value, 10);
        sendControlUpdate(control.id, { value: raw });
      });
      card.appendChild(range);
      inputElement = range;
      inputType = 'range';
    } else if (normalizedType === 'button') {
      const info = document.createElement('p');
      info.className = 'text-muted small mt-2';
      info.textContent = 'Este control solo es accionable desde el dispositivo físico.';
      card.appendChild(info);
    } else {
      const fallback = document.createElement('p');
      fallback.className = 'text-muted small mt-2';
      fallback.textContent = 'Este tipo de control no es editable desde la interfaz.';
      card.appendChild(fallback);
    }

    card.appendChild(metadata.wrapper);

    registerControl(control, {
      wrapper: card,
      input: inputElement,
      inputType,
      valueElement: metadata.valueElement,
      minElement: metadata.minElement,
      maxElement: metadata.maxElement,
      defaultElement: metadata.defaultElement,
      defaultButton,
    });

    return card;
  }

  function buildControls(controls) {
    controlElements.clear();
    controlsTabNav.innerHTML = '';
    controlsTabContent.innerHTML = '';

    const categories = new Map();
    controls.forEach(function (control) {
      const category = control.category || 'General';
      if (!categories.has(category)) {
        categories.set(category, []);
      }
      categories.get(category).push(control);
    });

    const sortedCategories = Array.from(categories.keys()).sort(function (a, b) {
      return a.localeCompare(b, 'es', { sensitivity: 'base' });
    });

    if (sortedCategories.length === 0) {
      controlsTabNav.classList.add('d-none');
      const empty = document.createElement('div');
      empty.className = 'text-center text-muted py-5';
      empty.textContent = 'No se encontraron controles disponibles para este dispositivo.';
      controlsTabContent.appendChild(empty);
      showControlsContent(true);
      return;
    }

    controlsTabNav.classList.remove('d-none');

    sortedCategories.forEach(function (category, index) {
      const controlsForCategory = categories.get(category) || [];
      controlsForCategory.sort(function (a, b) {
        return a.name.localeCompare(b.name, 'es', { sensitivity: 'base' });
      });

      const tabId = `controls-${category.toLowerCase().replace(/[^a-z0-9]+/g, '-')}-${index}`;

      const navItem = document.createElement('li');
      navItem.className = 'nav-item';
      const navButton = document.createElement('button');
      navButton.className = `nav-link${index === 0 ? ' active' : ''}`;
      navButton.dataset.bsToggle = 'tab';
      navButton.dataset.bsTarget = `#${tabId}`;
      navButton.type = 'button';
      navButton.role = 'tab';
      navButton.ariaControls = tabId;
      navButton.ariaSelected = index === 0 ? 'true' : 'false';
      navButton.textContent = category;
      navItem.appendChild(navButton);
      controlsTabNav.appendChild(navItem);

      const tabPane = document.createElement('div');
      tabPane.className = `tab-pane fade${index === 0 ? ' show active' : ''} p-3`;
      tabPane.id = tabId;
      tabPane.role = 'tabpanel';

      controlsForCategory.forEach(function (control) {
        const card = createControlElement(control);
        tabPane.appendChild(card);
      });

      if (controlsForCategory.length === 0) {
        const emptyCategory = document.createElement('div');
        emptyCategory.className = 'text-muted text-center py-4';
        emptyCategory.textContent = 'No hay controles disponibles en esta categoría.';
        tabPane.appendChild(emptyCategory);
      }

      controlsTabContent.appendChild(tabPane);
    });

    showControlsContent(true);
    if (controlsTabContent) {
      controlsTabContent.scrollTop = 0;
    }
  }

  function loadControls(force = false) {
    if (isLoadingControls) {
      return;
    }
    if (!force && controlsLoaded) {
      return;
    }
    controlsLoaded = false;
    isLoadingControls = true;
    showControlsMessage('');
    setControlsLoading(true);
    showControlsContent(false);
    const sent = sendCommand(
      'controls:list',
      { refresh: Boolean(force) },
      { allowQueue: true }
    );
    if (!sent) {
      isLoadingControls = false;
      setControlsLoading(false);
      showControlsMessage('No hay conexión con el backend.', 'danger');
    }
  }

  function sendControlUpdate(controlId, payload) {
    setControlBusy(controlId, true);
    pendingControlUpdates.add(controlId);
    showControlsMessage('');
    const commandPayload = { identifier: controlId };
    if (Object.prototype.hasOwnProperty.call(payload, 'action')) {
      commandPayload.action = payload.action;
    }
    if (Object.prototype.hasOwnProperty.call(payload, 'value')) {
      commandPayload.value = payload.value;
    }
    const sent = sendCommand('controls:update', commandPayload);
    if (!sent) {
      pendingControlUpdates.delete(controlId);
      setControlBusy(controlId, false);
      showControlsMessage('No hay conexión con el backend.', 'danger');
    }
  }

  startBtn.addEventListener('click', function () {
    const sent = sendCommand('start', { roi: getCurrentRoi() });
    if (!sent) {
      alerts.textContent = 'No hay conexión con el backend.';
    }
  });

  stopBtn.addEventListener('click', function () {
    const sent = sendCommand('stop');
    if (!sent) {
      alerts.textContent = 'No hay conexión con el backend.';
    }
  });

  if (controlsDrawer) {
    controlsDrawer.addEventListener('show.bs.offcanvas', function () {
      loadControls(true);
    });
  }

  window.addEventListener('beforeunload', function () {
    if (reconnectTimer) {
      clearTimeout(reconnectTimer);
    }
    if (socket) {
      socket.close();
    }
  });

  connect();
  loadControls();
})();
