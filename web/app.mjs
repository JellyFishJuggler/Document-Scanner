import { scan } from './api.mjs';
import { FRAME_CORNERS, formatDownloadName, normalizePointer, sameCorners, validateImageFile } from './geometry.mjs';

const elements = Object.fromEntries([
  'home-screen', 'camera-screen', 'adjust-screen', 'preview-screen', 'open-camera',
  'upload-trigger', 'image-picker', 'home-error', 'camera-image-picker', 'camera-video',
  'camera-message', 'capture-button', 'flip-camera-button', 'image-stage', 'source-image', 'corner-overlay',
  'corner-polygon', 'corner-handles', 'adjust-status', 'adjust-error', 'redetect-button',
  'continue-button', 'preview-back', 'result-image', 'preview-error', 'retake-button',
  'adjust-again-button', 'download-button',
].map(id => [id, document.getElementById(id)]));

const screens = [elements['home-screen'], elements['camera-screen'], elements['adjust-screen'], elements['preview-screen']];
const MAX_IMAGE_PIXELS = 50_000_000;
let imageBlob = null;
let imageUrl = null;
let sourceBase64 = null;
let corners = FRAME_CORNERS.map(point => [...point]);
let cornerSnapshot = null;
let detectedResult = null;
let stream = null;
let cameraFacingMode = 'environment';
let cameraSwitchInProgress = false;
let draggingIndex = null;
let scanInProgress = false;
let downloadUrl = null;
let resizeObserver = null;

function showScreen(screen) {
  screens.forEach(item => { item.hidden = item !== screen; });
}

function setError(target, message = '') {
  target.textContent = message;
  target.hidden = !message;
}

function stopCamera() {
  if (stream) stream.getTracks().forEach(track => track.stop());
  stream = null;
  elements['camera-video'].srcObject = null;
  elements['capture-button'].disabled = true;
  elements['flip-camera-button'].disabled = true;
}

function releaseCurrentImage() {
  if (imageUrl) URL.revokeObjectURL(imageUrl);
  imageUrl = null;
  imageBlob = null;
  sourceBase64 = null;
  corners = FRAME_CORNERS.map(point => [...point]);
  cornerSnapshot = null;
  detectedResult = null;
}

function openHome() {
  stopCamera();
  setError(elements['home-error']);
  showScreen(elements['home-screen']);
}

function updateCornerOverlay() {
  if (elements['adjust-screen'].hidden || !elements['source-image'].naturalWidth) return;
  const stageRect = elements['image-stage'].getBoundingClientRect();
  const imageRect = elements['source-image'].getBoundingClientRect();
  const points = corners.map(([x, y]) => [
    imageRect.left - stageRect.left + x * imageRect.width,
    imageRect.top - stageRect.top + y * imageRect.height,
  ]);
  const viewWidth = Math.max(1, stageRect.width);
  const viewHeight = Math.max(1, stageRect.height);
  elements['corner-overlay'].setAttribute('viewBox', `0 0 ${viewWidth} ${viewHeight}`);
  elements['corner-polygon'].setAttribute('points', points.map(point => point.join(',')).join(' '));
  if (!elements['corner-handles'].childElementCount) {
    for (let index = 0; index < 4; index += 1) {
      const group = document.createElementNS('http://www.w3.org/2000/svg', 'g');
      const hit = document.createElementNS('http://www.w3.org/2000/svg', 'circle');
      hit.setAttribute('r', '25');
      hit.setAttribute('class', 'corner-handle-hit');
      hit.dataset.cornerIndex = String(index);
      const visible = document.createElementNS('http://www.w3.org/2000/svg', 'circle');
      visible.setAttribute('r', '9');
      visible.setAttribute('class', 'corner-handle');
      visible.dataset.cornerIndex = String(index);
      group.append(hit, visible);
      elements['corner-handles'].append(group);
    }
  }
  points.forEach(([x, y], index) => {
    const group = elements['corner-handles'].children[index];
    for (const circle of group.children) {
      circle.setAttribute('cx', String(x));
      circle.setAttribute('cy', String(y));
    }
  });
}

function setCurrentImage(blob, label) {
  stopCamera();
  releaseCurrentImage();
  imageBlob = blob;
  imageUrl = URL.createObjectURL(blob);
  elements['source-image'].alt = label || 'Selected document';
  elements['source-image'].src = imageUrl;
  elements['source-image'].onload = () => {
    const pixels = elements['source-image'].naturalWidth * elements['source-image'].naturalHeight;
    if (pixels > MAX_IMAGE_PIXELS) {
      releaseCurrentImage();
      setError(elements['home-error'], 'This image has too many pixels to scan. Choose a smaller image.');
      openHome();
      return;
    }
    corners = FRAME_CORNERS.map(point => [...point]);
    elements['adjust-status'].textContent = 'Drag the four points to fit your page, or re-detect automatically.';
    setError(elements['adjust-error']);
    showScreen(elements['adjust-screen']);
    requestAnimationFrame(updateCornerOverlay);
  };
  elements['source-image'].onerror = () => {
    releaseCurrentImage();
    openHome();
    setError(elements['home-error'], 'This image could not be opened. Choose a valid JPEG or PNG.');
  };
}

function fileAsBase64(blob) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onerror = () => reject(new Error('Could not read this image. Please choose it again.'));
    reader.onload = () => {
      const dataUrl = String(reader.result || '');
      const comma = dataUrl.indexOf(',');
      if (comma < 0) reject(new Error('Could not read image data.'));
      else resolve(dataUrl.slice(comma + 1));
    };
    reader.readAsDataURL(blob);
  });
}

async function getImageBase64() {
  if (!imageBlob) throw new Error('Choose or capture an image first.');
  if (!sourceBase64) sourceBase64 = await fileAsBase64(imageBlob);
  return sourceBase64;
}

function setScanBusy(busy, button, label) {
  scanInProgress = busy;
  elements['redetect-button'].disabled = busy;
  elements['continue-button'].disabled = busy;
  button.textContent = busy ? label : (button === elements['continue-button'] ? 'Continue →' : 'Re-detect');
  if (button === elements['redetect-button']) elements['continue-button'].textContent = busy ? 'Scanning…' : 'Continue →';
}

function displayScan(result) {
  detectedResult = result;
  corners = result.corners.map(point => [...point]);
  cornerSnapshot = corners.map(point => [...point]);
  elements['adjust-status'].textContent = result.used_fallback
    ? 'Page not found. Drag the corners manually to fit the page.'
    : `Corners detected by Scanly. Adjust them if needed${result.corners_source ? ` · ${result.corners_source}` : ''}.`;
  setError(elements['adjust-error']);
  requestAnimationFrame(updateCornerOverlay);
}

async function redetect() {
  if (scanInProgress) return;
  setError(elements['adjust-error']);
  setScanBusy(true, elements['redetect-button'], 'Detecting…');
  elements['adjust-status'].textContent = 'Looking for the page…';
  try {
    const result = await scan({ image: await getImageBase64() });
    displayScan(result);
  } catch (error) {
    elements['adjust-status'].textContent = 'Automatic detection could not be completed.';
    setError(elements['adjust-error'], error.message);
  } finally {
    setScanBusy(false, elements['redetect-button'], 'Detecting…');
  }
}

function base64ToBlob(base64, mime) {
  const binary = atob(base64);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i += 1) bytes[i] = binary.charCodeAt(i);
  return new Blob([bytes], { type: mime });
}

function showPreview(result) {
  if (downloadUrl) URL.revokeObjectURL(downloadUrl);
  const blob = base64ToBlob(result.scan_b64, 'image/png');
  downloadUrl = URL.createObjectURL(blob);
  elements['result-image'].src = downloadUrl;
  elements['download-button'].href = downloadUrl;
  elements['download-button'].download = formatDownloadName().replace(/\.jpg$/, '.png');
  setError(elements['preview-error']);
  showScreen(elements['preview-screen']);
}

async function continueToPreview() {
  if (scanInProgress) return;
  setError(elements['adjust-error']);
  setScanBusy(true, elements['continue-button'], 'Scanning…');
  elements['adjust-status'].textContent = 'Straightening your page…';
  try {
    let result;
    if (detectedResult && sameCorners(corners, cornerSnapshot)) {
      result = detectedResult;
    } else {
      result = await scan({ image: await getImageBase64(), corners });
    }
    showPreview(result);
  } catch (error) {
    elements['adjust-status'].textContent = 'Your image is ready to try again.';
    setError(elements['adjust-error'], error.message);
  } finally {
    setScanBusy(false, elements['continue-button'], 'Scanning…');
  }
}

function cameraErrorMessage(error) {
  if (!window.isSecureContext) return 'Camera access requires a secure connection (HTTPS). Use Upload Image here, or open this page over HTTPS.';
  if (error?.name === 'NotAllowedError' || error?.name === 'SecurityError') return 'Camera permission was denied. Allow camera access in your browser settings, or upload an image instead.';
  if (error?.name === 'NotFoundError' || error?.name === 'OverconstrainedError') return 'No usable camera was found on this device. Upload an image instead.';
  if (error?.name === 'NotReadableError' || error?.name === 'AbortError') return 'The camera could not start. Close other apps using it and try again.';
  return 'The camera is unavailable in this browser. Check camera permissions or upload an image instead.';
}

async function openCamera() {
  releaseCurrentImage();
  setError(elements['home-error']);
  showScreen(elements['camera-screen']);
  elements['camera-message'].textContent = 'Starting camera…';
  elements['camera-message'].hidden = false;
  if (!navigator.mediaDevices?.getUserMedia) {
    elements['camera-message'].textContent = 'This browser does not support camera access. Upload an image instead.';
    return;
  }
  if (!window.isSecureContext) {
    elements['camera-message'].textContent = cameraErrorMessage();
    return;
  }
  cameraFacingMode = 'environment';
  await startCameraStream();
}

async function startCameraStream() {
  stopCamera();
  elements['camera-message'].textContent = 'Starting camera…';
  elements['camera-message'].hidden = false;
  elements['flip-camera-button'].disabled = true;
  try {
    stream = await navigator.mediaDevices.getUserMedia({
      video: { facingMode: { ideal: cameraFacingMode } },
      audio: false,
    });
    elements['camera-video'].srcObject = stream;
    await elements['camera-video'].play();
    elements['camera-message'].hidden = true;
    elements['capture-button'].disabled = false;
    elements['flip-camera-button'].disabled = false;
    elements['flip-camera-button'].setAttribute('aria-label', `Switch to ${cameraFacingMode === 'environment' ? 'front' : 'rear'} camera`);
  } catch (error) {
    stopCamera();
    elements['camera-message'].textContent = cameraErrorMessage(error);
  }
}

async function flipCamera() {
  if (cameraSwitchInProgress || !stream) return;
  cameraSwitchInProgress = true;
  cameraFacingMode = cameraFacingMode === 'environment' ? 'user' : 'environment';
  await startCameraStream();
  cameraSwitchInProgress = false;
}

async function captureFrame() {
  const video = elements['camera-video'];
  if (!video.videoWidth || !video.videoHeight) {
    elements['camera-message'].hidden = false;
    elements['camera-message'].textContent = 'Camera is not ready yet. Please wait a moment and try again.';
    return;
  }
  const scale = Math.min(1, 2000 / Math.max(video.videoWidth, video.videoHeight));
  const canvas = document.createElement('canvas');
  canvas.width = Math.round(video.videoWidth * scale);
  canvas.height = Math.round(video.videoHeight * scale);
  canvas.getContext('2d').drawImage(video, 0, 0, canvas.width, canvas.height);
  const blob = await new Promise(resolve => canvas.toBlob(resolve, 'image/jpeg', 0.9));
  if (!blob) {
    elements['camera-message'].hidden = false;
    elements['camera-message'].textContent = 'The photo could not be captured. Please try again.';
    return;
  }
  setCurrentImage(blob, 'Captured document');
}

function handleFile(file) {
  setError(elements['home-error']);
  try {
    validateImageFile(file);
    setCurrentImage(file, file.name || 'Selected document');
  } catch (error) {
    stopCamera();
    openHome();
    setError(elements['home-error'], error.message);
  }
}

elements['open-camera'].addEventListener('click', openCamera);
elements['flip-camera-button'].addEventListener('click', flipCamera);
elements['upload-trigger'].addEventListener('click', () => elements['image-picker'].click());
elements['image-picker'].addEventListener('change', event => {
  const [file] = event.target.files || [];
  if (file) handleFile(file);
  event.target.value = '';
});
elements['camera-image-picker'].addEventListener('change', event => {
  const [file] = event.target.files || [];
  if (file) handleFile(file);
  event.target.value = '';
});
elements['capture-button'].addEventListener('click', () => { void captureFrame(); });
document.querySelectorAll('[data-go-home]').forEach(button => button.addEventListener('click', openHome));
elements['redetect-button'].addEventListener('click', () => { void redetect(); });
elements['continue-button'].addEventListener('click', () => { void continueToPreview(); });
elements['preview-back'].addEventListener('click', () => showScreen(elements['adjust-screen']));
elements['adjust-again-button'].addEventListener('click', () => showScreen(elements['adjust-screen']));
elements['retake-button'].addEventListener('click', () => { void openCamera(); });

elements['corner-overlay'].addEventListener('pointerdown', event => {
  const handle = event.target.closest('[data-corner-index]');
  if (!handle) return;
  draggingIndex = Number(handle.dataset.cornerIndex);
  event.preventDefault();
  elements['corner-overlay'].setPointerCapture?.(event.pointerId);
});
elements['corner-overlay'].addEventListener('pointermove', event => {
  if (draggingIndex === null) return;
  const imageRect = elements['source-image'].getBoundingClientRect();
  corners[draggingIndex] = normalizePointer(event.clientX, event.clientY, imageRect);
  updateCornerOverlay();
});
const finishDrag = () => { draggingIndex = null; };
elements['corner-overlay'].addEventListener('pointerup', finishDrag);
elements['corner-overlay'].addEventListener('pointercancel', finishDrag);

elements['source-image'].addEventListener('load', updateCornerOverlay);
window.addEventListener('resize', updateCornerOverlay);
if ('ResizeObserver' in window) {
  resizeObserver = new ResizeObserver(updateCornerOverlay);
  resizeObserver.observe(elements['image-stage']);
}
window.addEventListener('pagehide', () => {
  stopCamera();
  if (imageUrl) URL.revokeObjectURL(imageUrl);
  if (downloadUrl) URL.revokeObjectURL(downloadUrl);
  resizeObserver?.disconnect();
});
