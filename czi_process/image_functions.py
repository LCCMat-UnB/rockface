import ctypes
import os
import platform
import gc
import re
import numpy as np
import json
import cv2
from pathlib import Path
from PIL import Image
from typing import Any, Dict, List, Optional, Tuple
import xml.etree.ElementTree as ET


# ============================================================
# CZI METADATA EXTRACTION
# ============================================================

def get_metadata_root(czi) -> Any:
    """
    Return the root of the CZI metadata as an XML element.
    """
    metadata = czi.meta
    if isinstance(metadata, str):
        return ET.fromstring(metadata)
    return metadata

def extract_pixel_sizes_from_metadata(root: Any) -> Tuple[Optional[float], Optional[float]]:
    """
    Extract the pixel size (X and Y) from the CZI metadata (in meters).
    """
    pixel_size_x = None
    pixel_size_y = None

    if root is None:
        return pixel_size_x, pixel_size_y

    for distance in root.findall(".//Distance"):
        distance_id = distance.get("Id")
        value_element = distance.find("Value")

        if value_element is None or value_element.text is None:
            continue

        try:
            value = float(value_element.text)
        except ValueError:
            continue

        if distance_id == "X":
            pixel_size_x = value
        elif distance_id == "Y":
            pixel_size_y = value

    return pixel_size_x, pixel_size_y

def save_image_metadata_from_overview(
    czi_path: Path,
    channel: int,
    patch_coords: List[Tuple[int, int]],
    selected_coords: List[Tuple[int, int]],
    patch_size: int,
    stride: int,
    output_dir: Path,
    overview: Dict[str, Any],
) -> Path:
    """
    Save CZI metadata as JSON.
    Saves the essential metadata of the image and patch generation in JSON format.
    Removed visual saving configurations and validation flags.
    """
    czi_path = Path(czi_path)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    bbox = overview["bbox"]

    metadata = {
        "source_file": czi_path.name,
        "channel": channel,
        "image_structure": {
            "full_width_px": bbox["w"],
            "full_height_px": bbox["h"],
            "global_x_origin": bbox["x"],
            "global_y_origin": bbox["y"],
            "dimensions_order": overview.get("dims", ""),
        },
        "physical_scaling": {
            "pixel_size_x_meters": overview["pixel_size_x_meters"],
            "pixel_size_y_meters": overview["pixel_size_y_meters"],
            "unit": "meters",
        },
        "patch_generation": {
            "patch_size_px": patch_size,
            "stride_px": stride,
            "overlap_px": patch_size - stride,
            "total_patches": len(patch_coords),
        },
        "analysis_info": {
            "project": "RockFace",
            "institution": "LCCMat / Petrobras",
            "engine_version": "Alfa 1.0",
        }
    }

    output_path = output_dir / f"{czi_path.stem}_metadata.json"

    with open(output_path, "w", encoding="utf-8") as file:
        json.dump(metadata, file, indent=4, ensure_ascii=False)

    return output_path

# ============================================================
# IO & ATOMIC OPERATIONS
# ============================================================

def atomic_save_npy(output_path: Path, array: np.ndarray) -> None:
    """Save NPY atomically to avoid leaving corrupted files if the process dies."""
    output_path = Path(output_path)
    tmp_path = output_path.with_name(output_path.name + ".tmp")
    try:
        with open(tmp_path, "wb") as file:
            np.save(file, array)
        os.replace(tmp_path, output_path)
    finally:
        if tmp_path.exists():
            try: tmp_path.unlink()
            except OSError: pass

def atomic_save_image(image: Image.Image, output_path: Path, lossless: bool, jpeg_quality: int = 95) -> None:
    """Save PNG/JPEG atomically to avoid incomplete image files."""
    output_path = Path(output_path)
    tmp_path = output_path.with_name(output_path.name + ".tmp")
    try:
        if lossless:
            image.save(tmp_path, format="PNG")
        else:
            image.save(tmp_path, format="JPEG", quality=jpeg_quality, subsampling=0)
        os.replace(tmp_path, output_path)
    finally:
        if tmp_path.exists():
            try: tmp_path.unlink()
            except OSError: pass

def convert_bgr_to_rgb_uint8(array_bgr: np.ndarray) -> np.ndarray:
    """Convert BGR/grayscale array to RGB uint8 for PIL."""
    if array_bgr.ndim == 3 and array_bgr.shape[-1] == 3:
        array_rgb = np.ascontiguousarray(array_bgr[..., ::-1])
    else:
        array_rgb = np.ascontiguousarray(array_bgr)
    if array_rgb.dtype != np.uint8:
        array_rgb = np.clip(array_rgb, 0, 255).astype(np.uint8, copy=False)
    return array_rgb

# ============================================================
# GEOMETRY & PATCH LOGIC
# ============================================================

def get_patches(image_shape: Tuple[int, int], patch_size: int, stride: int) -> List[Tuple[int, int]]:
    """Generate deterministic patch coordinates (y, x)."""
    height, width = image_shape[:2]
    coordinates = []
    for y in range(0, height, stride):
        for x in range(0, width, stride):
            y_start = max(0, min(y, height - patch_size))
            x_start = max(0, min(x, width - patch_size))
            coordinates.append((y_start, x_start))
            if x_start + patch_size >= width: break
        if y_start + patch_size >= height: break
    return sorted(set(coordinates))

# ============================================================
# SEGMENTATION & MASKS
# ============================================================

def generate_pore_mask(bgr_array: np.ndarray) -> np.ndarray:
    """Main segmentation logic for resin-impregnated pores."""
    blurred = cv2.GaussianBlur(bgr_array, (5, 5), 0)
    hsv = cv2.cvtColor(blurred, cv2.COLOR_BGR2HSV)
    h, s, v = cv2.split(hsv)
    
    # Thresholding based on hue (cyan/blue)
    lower_hue, upper_hue = 75, 125
    coarse_mask = cv2.inRange(h, lower_hue, upper_hue)
    
    # S x V refinement
    s_norm = s.astype(float) / 255.0
    v_norm = v.astype(float) / 255.0
    sv_product = s_norm * v_norm
    
    dt = 0.1 
    refined_mask = np.where((coarse_mask > 0) & (sv_product >= dt), 255, 0).astype(np.uint8)
    
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
    return cv2.morphologyEx(refined_mask, cv2.MORPH_OPEN, kernel)

def generate_transparent_overlay(mask: np.ndarray, color: Tuple[int, int, int] = (0, 255, 0), filled_mode: bool = False, thickness: int = 2) -> np.ndarray:
    """Creates a transparent RGBA overlay, skipping patch borders in outline mode."""
    h, w = mask.shape[:2]
    overlay = np.zeros((h, w, 4), dtype=np.uint8)
    bgra_color = (*color, 255)

    if filled_mode:
        overlay[mask == 255] = bgra_color
    else:
        contours, _ = cv2.findContours(mask, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
        for cnt in contours:
            for i in range(len(cnt)):
                pt1, pt2 = cnt[i][0], cnt[(i + 1) % len(cnt)][0]
                on_border = (pt1[0] <= 0 and pt2[0] <= 0) or (pt1[0] >= w-1 and pt2[0] >= w-1) or \
                            (pt1[1] <= 0 and pt2[1] <= 0) or (pt1[1] >= h-1 and pt2[1] >= h-1)
                if not on_border:
                    cv2.line(overlay, tuple(pt1), tuple(pt2), bgra_color, thickness)
    return overlay