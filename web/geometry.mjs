export const FRAME_CORNERS = [
  [0.04, 0.04],
  [0.96, 0.04],
  [0.96, 0.96],
  [0.04, 0.96],
];

export function clamp01(value) {
  return Math.max(0, Math.min(1, value));
}

export function normalizePointer(clientX, clientY, imageRect) {
  if (!imageRect || imageRect.width <= 0 || imageRect.height <= 0) {
    throw new Error('The image is not ready for corner adjustment.');
  }
  return [
    clamp01((clientX - imageRect.left) / imageRect.width),
    clamp01((clientY - imageRect.top) / imageRect.height),
  ];
}

export function sameCorners(left, right, epsilon = 0.0005) {
  return Array.isArray(left)
    && Array.isArray(right)
    && left.length === 4
    && right.length === 4
    && left.every((point, index) => point.every((value, axis) => Math.abs(value - right[index][axis]) <= epsilon));
}

export function validateImageFile(file) {
  if (!file) throw new Error('Choose an image to continue.');
  if (!['image/jpeg', 'image/png'].includes(file.type)) {
    throw new Error('Please choose a JPEG or PNG image.');
  }
  // Base64 and JSON framing add about one third to the file size. Keep a margin
  // under the backend's 12 MiB request-body cap rather than sending a doomed body.
  if (file.size > 8 * 1024 * 1024) {
    throw new Error('This image is larger than 8 MB. Choose a smaller JPEG or PNG.');
  }
  return file;
}

export function formatDownloadName(date = new Date()) {
  const pad = value => String(value).padStart(2, '0');
  return `Scan_${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}_${pad(date.getHours())}-${pad(date.getMinutes())}.jpg`;
}
