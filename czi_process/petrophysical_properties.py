import numpy as np
from pathlib import Path
from PIL import Image

def calculate_petrophysical_stats(patch_dir: Path, mask_dir: Path, stride: int, metadata: dict):
    """
    Calculate the real rock area and pore area without overlap by using a stride-based crop.
    
    Args:
        patch_dir (Path): Directory containing original .npy patches.
        mask_dir (Path): Directory containing generated .png pore masks.
        stride (int): The step size used during patching (determines effective counting area).
        metadata (dict): Parsed JSON metadata containing physical scaling information.
    """
    total_rock_pixels = 0
    total_pore_pixels = 0
    
    # Extract physical pixel size in meters from the metadata dictionary
    pixel_size_m = metadata.get("physical_scaling", {}).get("pixel_size_x_meters")
    
    # Validation to prevent calculation errors if metadata is missing
    if not pixel_size_m:
        raise ValueError("Missing 'pixel_size_x_meters' in metadata. Cannot convert to physical units.")
    
    # Convert meters to micrometers (1 meter = 1,000,000 micrometers)
    pixel_size_um = pixel_size_m * 1e6
    
    # List all original patches (.npy files)
    npy_files = list(patch_dir.glob("*.npy"))
    
    for npy_path in npy_files:
        # Load the raw color patch data
        arr = np.load(npy_path)
        
        # Match with the corresponding mask
        mask_path = mask_dir / f"{npy_path.stem}_mask.png"
        if not mask_path.exists():
            continue
            
        mask_rgba = np.array(Image.open(mask_path))

        if mask_rgba.ndim == 3 and mask_rgba.shape[-1] == 4:
            mask_2d = mask_rgba[:, :, 3] 
        else:
            mask_2d = np.max(mask_rgba, axis=-1) if mask_rgba.ndim == 3 else mask_rgba
        
        # CROP TO STRIDE
        effective_arr = arr[0:stride, 0:stride]
        effective_mask = mask_2d[0:stride, 0:stride]
        
        # 1. ROCK AREA CALCULATION
        rock_mask = np.any(effective_arr > 5, axis=-1) 
        total_rock_pixels += np.sum(rock_mask)
        
        # 2. PORE AREA CALCULATION
        pore_count = np.sum((effective_mask > 0) & rock_mask)
        total_pore_pixels += pore_count

    # METRIC CONVERSION (Pixels -> mm²)
    pixel_area_um2 = pixel_size_um ** 2
    pixel_to_mm2 = pixel_area_um2 / 1_000_000 
    
    area_rocha_mm2 = total_rock_pixels * pixel_to_mm2
    area_poros_mm2 = total_pore_pixels * pixel_to_mm2
    
    # Calculate relative porosity based only on effective rock area
    porosidade_relativa = (area_poros_mm2 / area_rocha_mm2) * 100 if area_rocha_mm2 > 0 else 0
    
    return {
        "area_rocha_mm2": round(area_rocha_mm2, 3),
        "area_poros_mm2": round(area_poros_mm2, 3),
        "porosidade_final": round(porosidade_relativa, 2)
    }