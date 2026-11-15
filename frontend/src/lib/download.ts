// Saving files from the browser without navigating away. Everything goes through a Blob and an
// object URL, which the Content Security Policy allows; data: URLs are decoded locally first.

export function saveBlob(blob: Blob, name: string): void {
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = name;
  document.body.appendChild(link);
  link.click();
  link.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), 1000);
}

export function dataUrlToBlob(dataUrl: string): Blob {
  const comma = dataUrl.indexOf(",");
  const header = dataUrl.slice(0, comma);
  const mime = /^data:([^;,]+)/.exec(header)?.[1] ?? "application/octet-stream";
  const payload = dataUrl.slice(comma + 1);
  if (!header.includes(";base64")) return new Blob([decodeURIComponent(payload)], { type: mime });
  const binary = atob(payload);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i += 1) bytes[i] = binary.charCodeAt(i);
  return new Blob([bytes], { type: mime });
}

export function saveDataUrl(dataUrl: string, name: string): void {
  saveBlob(dataUrlToBlob(dataUrl), name);
}

/** Save a canvas as PNG on an opaque background, so transparent areas do not turn black or white. */
export function saveCanvas(source: HTMLCanvasElement, name: string, background: string): Promise<boolean> {
  const copy = document.createElement("canvas");
  copy.width = source.width;
  copy.height = source.height;
  const context = copy.getContext("2d");
  if (!context || source.width === 0 || source.height === 0) return Promise.resolve(false);
  context.fillStyle = background;
  context.fillRect(0, 0, copy.width, copy.height);
  try {
    context.drawImage(source, 0, 0);
  } catch {
    return Promise.resolve(false);
  }
  return new Promise((resolve) => {
    try {
      copy.toBlob((blob) => {
        if (!blob) {
          resolve(false);
          return;
        }
        saveBlob(blob, name);
        resolve(true);
      }, "image/png");
    } catch {
      // A canvas tainted by an image without cross origin permission cannot be exported.
      resolve(false);
    }
  });
}

/** A file name fragment that is safe on every platform. */
export function fileSlug(text: string): string {
  const slug = text
    .normalize("NFKD")
    .replace(/[^\x20-\x7e]/g, "")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "");
  return slug.slice(0, 48) || "area";
}
