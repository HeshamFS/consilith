import type { ImageSequence } from "./imageSequence";

type Frame = { url: string; extension: string };

/** At most three original frames are retained, with one room-authorized request in flight. */
export class SequenceCache {
  private frames = new Map<string, Frame>();
  private failed = new Set<string>();
  private wanted: string[] = [];
  private running = false;
  private active = true;

  constructor(
    private sequence: ImageSequence,
    private load: (sha: string) => Promise<Blob>,
    private changed: (frames: ReadonlyMap<string, Frame>) => void,
    private failure: (sha: string, message: string) => void,
  ) {}

  request(index: number) {
    this.wanted = [...new Set(this.sequence.frames.slice(index, index + 3).map((f) => f.sha256))];
    for (const [hash, frame] of this.frames) {
      if (!this.wanted.includes(hash)) {
        URL.revokeObjectURL(frame.url);
        this.frames.delete(hash);
      }
    }
    this.changed(new Map(this.frames));
    void this.pump();
  }

  dispose() {
    this.active = false;
    for (const frame of this.frames.values()) URL.revokeObjectURL(frame.url);
    this.frames.clear();
  }

  private async pump() {
    if (this.running || !this.active) return;
    this.running = true;
    try {
      while (this.active) {
        const hash = this.wanted.find((h) => !this.frames.has(h) && !this.failed.has(h));
        if (!hash) break;
        try {
          const blob = await this.load(hash);
          if (!this.active || !this.wanted.includes(hash)) continue;
          const extension = { "image/png": "png", "image/jpeg": "jpg", "image/webp": "webp" }[
            blob.type
          ];
          if (!extension)
            throw new Error("Sequence frames must be stored PNG, JPEG or WebP images");
          if (blob.size > 16 * 1024 * 1024)
            throw new Error("This frame exceeds the 16 MiB preview limit");
          const digest = await crypto.subtle.digest("SHA-256", await blob.arrayBuffer());
          const actual = [...new Uint8Array(digest)]
            .map((byte) => byte.toString(16).padStart(2, "0"))
            .join("");
          if (!this.active || !this.wanted.includes(hash)) continue;
          if (actual !== hash) throw new Error("Stored frame integrity check failed");
          this.frames.set(hash, { url: URL.createObjectURL(blob), extension });
          this.changed(new Map(this.frames));
        } catch (error) {
          if (!this.active) return;
          this.failed.add(hash);
          if (this.wanted.includes(hash))
            this.failure(
              hash,
              error instanceof Error ? error.message : "Sequence frame unavailable",
            );
        }
      }
    } finally {
      this.running = false;
    }
  }
}
