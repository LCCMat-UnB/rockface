import image_functions as imf
import petrophysical_properties as ppp
import patching_engine as engine
from pathlib import Path
from PIL import Image
import numpy as np
import re
import gc
import json
import multiprocessing
from functools import partial
import time

# Project Constants
OUTPUT_BASE = Path("./output_project")
PATCH_SIZE = 4096
STRIDE = 3800

# Resource management: use physical cores or limit to 16
MAX_WORKERS = min(16, multiprocessing.cpu_count())

def mask_worker(npy_path, mask_dir):
    """
    Global worker function for the Pool to handle pore segmentation.
    """
    try:
        # Load raw patch and process mask
        arr = np.load(npy_path)
        mask = imf.generate_pore_mask(arr)
        
        # Create visual transparency overlay for the mosaic
        overlay = imf.generate_transparent_overlay(mask, filled_mode=True)
        imf.atomic_save_image(Image.fromarray(overlay), mask_dir / f"{npy_path.stem}_mask.png", True)
        return True
    except Exception as e:
        print(f"Error processing mask for {npy_path.name}: {e}")
        return False
    
def generate_manifest(patch_dir: Path, mask_dir: Path, output_path: Path):
    """
    Generates the coordinate manifest for the React-based digital mosaic viewer.
    """
    pattern = re.compile(r'patch_y(\d+)_x(\d+)_c0')
    manifest = []
    
    patches = list(patch_dir.glob("*.png"))
    print(f"DEBUG: Generating manifest for {len(patches)} patches.")

    for p in patches:
        m = pattern.search(p.name)
        if m:
            y, x = int(m.group(1)), int(m.group(2))
            manifest.append({
                "id": p.stem,
                "x": x,
                "y": y,
                "original_url": f"http://localhost:8000/results/patches/{p.name}",
                "mask_url": f"http://localhost:8000/results/masks/{p.name.replace('.png', '_mask.png')}"
            })
    
    # Sort by coordinates to ensure consistent rendering
    manifest.sort(key=lambda x: (x['y'], x['x']))

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=4)
    
    return len(manifest)

def main(czi_path_input: str, status_ref: dict):
    """
    Main orchestration of the RockFace workflow.
    """
    status_ref.update({"progress": 5, "message": "Abrindo arquivo CZI...", "status": "running"})
    
    czi_file = Path(czi_path_input)
    patch_dir, mask_dir = OUTPUT_BASE / "patches", OUTPUT_BASE / "masks"
    for d in [patch_dir, mask_dir]: d.mkdir(parents=True, exist_ok=True)

    # --- STAGE 0: METADATA & GRID CONFIGURATION ---
    # Fetch physical scaling and bounding box
    meta = engine.read_czi_overview(czi_file)
    coords = imf.get_patches((meta["bbox"]["h"], meta["bbox"]["w"]), PATCH_SIZE, STRIDE)
    total_patches = len(coords)

    # Save extraction parameters to JSON
    metadata_path = imf.save_image_metadata_from_overview(
                        czi_path=czi_file,
                        channel=0,
                        patch_coords=coords,
                        selected_coords=coords,
                        patch_size=PATCH_SIZE,
                        stride=STRIDE,
                        output_dir=OUTPUT_BASE,
                        overview=meta
                    )
    
    # Load metadata into dictionary for calculations
    with open(metadata_path, 'r', encoding='utf-8') as f:
        data = json.load(f)

    # --- STAGE 1: PATCHING (10% to 50%) ---
    # Extract sub-regions from the CZI mosaic in parallel
    patch_func = partial(engine.process_single_patch, 
                         czi_path=czi_file, channel=0, patch_size=PATCH_SIZE, 
                         bbox_origin=(meta["bbox"]["x"], meta["bbox"]["y"]), output_dir=patch_dir)
    
    with multiprocessing.Pool(MAX_WORKERS) as pool:
        for i, _ in enumerate(pool.imap_unordered(patch_func, coords), 1):
            status_ref["progress"] = 10 + int((i / total_patches) * 40)
            status_ref["message"] = f"Extraindo fatias: {i}/{total_patches}"

    # --- STAGE 2: MASKING (50% to 90%) ---
    # Apply CV logic to identify porosity
    status_ref.update({"progress": 50, "message": "Analisando porosidade..."})
    npy_files = list(patch_dir.glob("*.npy"))
    total_masks = len(npy_files)
    
    mask_func = partial(mask_worker, mask_dir=mask_dir)
    
    with multiprocessing.Pool(MAX_WORKERS) as pool:
        for i, _ in enumerate(pool.imap_unordered(mask_func, npy_files), 1):
            status_ref["progress"] = 50 + int((i / total_masks) * 40)
            status_ref["message"] = f"Gerando máscaras: {i}/{total_masks}"

    # --- STAGE 3: PETROPHYSICS & FINALIZATION (90% to 100%) ---
    status_ref.update({"progress": 92, "message": "Finalizando mosaico digital..."})

    try:
        # Calculate quantitative porosity metrics using the effective stride area
        stats = ppp.calculate_petrophysical_stats(
            patch_dir=patch_dir, 
            mask_dir=mask_dir, 
            stride=STRIDE, 
            metadata=data
        )
        
        # Store results for audit and reporting
        with open(OUTPUT_BASE / "petrophysical_results.json", "w", encoding="utf-8") as f:
            json.dump(stats, f, indent=4)

        # Build the manifest for the UI
        count = generate_manifest(patch_dir, mask_dir, OUTPUT_BASE / "manifest.json")
        
        status_ref.update({
            "progress": 100, 
            "message": f"Concluído: {count} patches e {stats['porosidade_final']}% de porosidade.", 
            "status": "complete"
        })
        print(f"Workflow finished. Petrophysical stats: {stats}")

    except Exception as e:
        print(f"Error during finalization: {e}")
        status_ref.update({
            "progress": 90, 
            "message": f"Erro no encerramento: {str(e)}", 
            "status": "error"
        })
    finally:
        gc.collect()
