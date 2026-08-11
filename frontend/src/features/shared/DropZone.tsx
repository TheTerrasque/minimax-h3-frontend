import { useState, type ReactNode } from "react";

// e.g. "image/*" -> files whose type starts with "image/". Matches what the
// sibling <input accept="..."> already restricts the native picker to, so
// drag-and-drop/paste can't sneak in a file type the form doesn't expect.
function filesMatchingAccept(files: Iterable<File>, accept: string): File[] {
  const prefix = accept.endsWith("/*") ? accept.slice(0, -1) : accept;
  return Array.from(files).filter((f) => f.type.startsWith(prefix));
}

interface DropZoneProps {
  accept: string;
  onFiles: (files: File[]) => void;
  className?: string;
  children: ReactNode;
}

// Async Clipboard API's read() needs an explicit user gesture and a secure
// context; not implemented at all in some browsers (notably Firefox without
// a flag). Feature-detected once at module load rather than per-render.
const CLIPBOARD_READ_SUPPORTED = typeof navigator !== "undefined" && "clipboard" in navigator && "read" in navigator.clipboard;

// Wraps a file-picker <label> (its <input type="file"> child is left
// untouched, so click-to-browse keeps working exactly as before) so the
// same slot also accepts drag-and-drop and clipboard paste. Paste has no
// dedicated target of its own -- it fires wherever focus currently is, and
// bubbles -- so this needs to be focusable itself (tabIndex, for
// keyboard-only use) and also catches paste while its child <input> has
// focus (e.g. right after a click-to-browse), since that event bubbles up
// to this label too.
//
// Keyboard paste (Ctrl/Cmd+V) only fires here once this exact element has
// focus, which isn't always obvious to a user coming from another app --
// a "Paste image" button (Async Clipboard API, when supported) gives an
// explicit, always-visible alternative that doesn't depend on focus at all.
export function DropZone({ accept, onFiles, className, children }: DropZoneProps) {
  const [isOver, setIsOver] = useState(false);

  function handleDrop(e: React.DragEvent<HTMLLabelElement>) {
    e.preventDefault();
    setIsOver(false);
    const files = filesMatchingAccept(e.dataTransfer.files, accept);
    if (files.length) onFiles(files);
  }

  function handlePaste(e: React.ClipboardEvent<HTMLLabelElement>) {
    const pasted = Array.from(e.clipboardData.items)
      .filter((item) => item.kind === "file")
      .map((item) => item.getAsFile())
      .filter((f): f is File => f != null);
    const files = filesMatchingAccept(pasted, accept);
    if (files.length) onFiles(files);
  }

  async function handlePasteButtonClick(e: React.MouseEvent) {
    // Also a <label>, so a plain click would otherwise open the file
    // picker (its child <input>'s default behavior) at the same time.
    e.preventDefault();
    e.stopPropagation();
    try {
      const items = await navigator.clipboard.read();
      const found: File[] = [];
      for (const item of items) {
        for (const type of item.types) {
          if (!filesMatchingAccept([new File([], "", { type })], accept).length) continue;
          found.push(new File([await item.getType(type)], `pasted.${type.split("/")[1] ?? "bin"}`, { type }));
        }
      }
      if (found.length) onFiles(found);
    } catch {
      // Permission denied, or nothing on the clipboard matching `accept` --
      // no toast, matches this component's existing quiet no-op-paste behavior.
    }
  }

  return (
    <label
      className={["file-drop", className, isOver ? "drag-over" : ""].filter(Boolean).join(" ")}
      tabIndex={0}
      onDragOver={(e) => {
        e.preventDefault();
        setIsOver(true);
      }}
      onDragLeave={() => setIsOver(false)}
      onDrop={handleDrop}
      onPaste={handlePaste}
    >
      {children}
      {CLIPBOARD_READ_SUPPORTED && (
        <button type="button" className="file-drop-paste-button" onClick={(e) => void handlePasteButtonClick(e)}>
          📋 Paste
        </button>
      )}
    </label>
  );
}
