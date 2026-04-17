"""
Image Compression Utility
─────────────────────────
Compresses images to a target file size using binary search on JPEG quality.
Skips PDFs, SVGs, and already-small images.
"""

import os
from PIL import Image

# Default target: 200 KB
DEFAULT_TARGET_KB = 200

# Skip compression for files smaller than this
SKIP_BELOW_KB = 50

# Image extensions we can compress (raster formats)
COMPRESSIBLE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tiff"}


# Map extensions to Pillow format names and whether they support quality param
_FORMAT_MAP = {
    ".jpg":  ("JPEG", True),
    ".jpeg": ("JPEG", True),
    ".png":  ("PNG",  False),
    ".webp": ("WEBP", True),
    ".bmp":  ("BMP",  False),
    ".tiff": ("TIFF", False),
}


def compress_image(filepath, target_kb=DEFAULT_TARGET_KB):
    """Compress an image file in-place to fit within *target_kb*.

    The original file format is preserved — no format conversion.

    Parameters
    ----------
    filepath : str
        Absolute path to the image file.
    target_kb : int
        Maximum file size in kilobytes.

    Returns
    -------
    str
        Path to the compressed file (same path, same extension).
    """
    if not filepath or not os.path.isfile(filepath):
        return filepath

    ext = os.path.splitext(filepath)[1].lower()

    # Don't touch non-image files (PDFs, SVGs, etc.)
    if ext not in COMPRESSIBLE_EXTS:
        return filepath

    size_kb = os.path.getsize(filepath) / 1024
    if size_kb <= target_kb:
        # Already small enough
        return filepath

    # Use decimal KB because many CMS validators treat 1KB as 1000 bytes.
    target_bytes = int(target_kb * 1000)
    fmt, supports_quality = _FORMAT_MAP.get(ext, ("JPEG", True))

    try:
        img = Image.open(filepath)

        # For JPEG output, ensure RGB mode
        if fmt == "JPEG" and img.mode not in ("RGB", "L"):
            img = img.convert("RGB")

        if supports_quality:
            # Binary search on quality
            low, high = 5, 95
            best_quality = low

            while low <= high:
                mid = (low + high) // 2
                img.save(filepath, format=fmt, quality=mid, optimize=True)
                size = os.path.getsize(filepath)

                if size > target_bytes:
                    high = mid - 1
                else:
                    best_quality = mid
                    low = mid + 1

            # Final save at the best quality that fits
            img.save(filepath, format=fmt, quality=best_quality, optimize=True)
            # If still above target, progressively resize + save at low quality.
            while os.path.getsize(filepath) > target_bytes:
                w, h = img.size
                new_w = int(w * 0.85)
                new_h = int(h * 0.85)
                if new_w < 16 or new_h < 16:
                    break
                img = img.resize((new_w, new_h), Image.LANCZOS)
                img.save(filepath, format=fmt, quality=25, optimize=True)
        else:
            # PNG/BMP/TIFF — resize to reduce file size
            while os.path.getsize(filepath) > target_bytes:
                w, h = img.size
                new_w = int(w * 0.85)
                new_h = int(h * 0.85)
                if new_w < 16 or new_h < 16:
                    break
                img = img.resize((new_w, new_h), Image.LANCZOS)
                save_kwargs = {"optimize": True} if fmt == "PNG" else {}
                img.save(filepath, format=fmt, **save_kwargs)
            # PNG-specific extra squeeze for stubborn files.
            if fmt == "PNG" and os.path.getsize(filepath) > target_bytes:
                try:
                    png_img = img.convert("P", palette=Image.ADAPTIVE, colors=128)
                    png_img.save(filepath, format="PNG", optimize=True)
                except Exception:
                    pass

        final_kb = os.path.getsize(filepath) / 1024
        if os.path.getsize(filepath) > target_bytes:
            print(f"    ⚠ Could not fully reach target ({target_kb}KB): final {final_kb:.0f}KB")
        else:
            print(f"    📦 Compressed {size_kb:.0f}KB → {final_kb:.0f}KB")
        return filepath

    except Exception as e:
        print(f"    ⚠ Compression failed: {e}")
        return filepath
