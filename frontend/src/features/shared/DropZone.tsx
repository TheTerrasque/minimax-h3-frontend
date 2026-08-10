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

// Wraps a file-picker <label> (its <input type="file"> child is left
// untouched, so click-to-browse keeps working exactly as before) so the
// same slot also accepts drag-and-drop and clipboard paste. Paste has no
// dedicated target of its own -- it fires wherever focus currently is, and
// bubbles -- so this needs to be focusable itself (tabIndex, for
// keyboard-only use) and also catches paste while its child <input> has
// focus (e.g. right after a click-to-browse), since that event bubbles up
// to this label too.
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
    </label>
  );
}
