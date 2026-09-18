/**
 * Turn a camera/file photo into base64 for the backend vision route.
 *
 * Preferred path: decode + downscale through createImageBitmap/canvas so a 12MP phone
 * photo does not travel over a store's flaky wifi. Some browsers (older Safari, some
 * webviews) cannot decode a Blob that way, so we fall back to sending the original
 * bytes rather than failing the verification the picker is standing there waiting for.
 */
export async function fileToBase64(file, maxEdge = 1280) {
  try {
    return await downscale(file, maxEdge);
  } catch (_) {
    return await readAsBase64(file);
  }
}

async function downscale(file, maxEdge) {
  if (typeof createImageBitmap !== "function") throw new Error("no bitmap decoder");
  const bitmap = await createImageBitmap(file);
  const scale = Math.min(1, maxEdge / Math.max(bitmap.width, bitmap.height));
  const canvas = document.createElement("canvas");
  canvas.width = Math.max(1, Math.round(bitmap.width * scale));
  canvas.height = Math.max(1, Math.round(bitmap.height * scale));
  canvas.getContext("2d").drawImage(bitmap, 0, 0, canvas.width, canvas.height);
  const data = canvas.toDataURL("image/jpeg", 0.82).split(",")[1];
  if (!data) throw new Error("empty canvas export");
  return { base64: data, mime: "image/jpeg" };
}

function readAsBase64(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => {
      const url = String(reader.result || "");
      const base64 = url.split(",")[1];
      if (!base64) reject(new Error("unreadable file"));
      else resolve({ base64, mime: file.type || "image/jpeg" });
    };
    reader.onerror = () => reject(new Error("unreadable file"));
    reader.readAsDataURL(file);
  });
}
