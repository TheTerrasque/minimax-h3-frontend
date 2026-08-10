import { useEffect, useState } from "react";

// Creates an object URL for a locally-staged file (thumbnail preview before
// upload) and revokes it on change/unmount -- object URLs otherwise leak for
// the page's lifetime.
export function useObjectUrl(file: File | null): string | null {
  const [url, setUrl] = useState<string | null>(null);
  useEffect(() => {
    if (!file) {
      setUrl(null);
      return;
    }
    const objectUrl = URL.createObjectURL(file);
    setUrl(objectUrl);
    return () => URL.revokeObjectURL(objectUrl);
  }, [file]);
  return url;
}

export function useObjectUrls(files: File[]): string[] {
  const [urls, setUrls] = useState<string[]>([]);
  useEffect(() => {
    const objectUrls = files.map((f) => URL.createObjectURL(f));
    setUrls(objectUrls);
    return () => {
      for (const u of objectUrls) URL.revokeObjectURL(u);
    };
  }, [files]);
  return urls;
}

// Re-downloads an already-uploaded file (used by e.g. "Redo") and repacks
// it as a File -- a browser has no way to hand back the original File
// object for something already uploaded, but the bytes are still sitting
// at their (same-origin, session-cookie-authed) media URL, so refetching
// them is the only way to actually restore it rather than leaving the slot
// empty.
export async function fetchAsFile(url: string, fallbackName: string): Promise<File> {
  const resp = await fetch(url, { credentials: "include" });
  if (!resp.ok) throw new Error(`Failed to fetch ${url}: ${resp.status}`);
  const blob = await resp.blob();
  const filename = url.split("/").pop()?.split("?")[0] || fallbackName;
  return new File([blob], filename, { type: blob.type });
}
