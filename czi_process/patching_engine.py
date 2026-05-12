import numpy as np
import image_functions as imf
from aicspylibczi import CziFile 
from PIL import Image


def read_czi_overview(czi_path):
    """
    Lê os metadados do arquivo CZI para determinar as dimensões da imagem.
    Essencial para o projeto RockFace.
    """
    try:
        czi = CziFile(str(czi_path))
        bbox = czi.get_mosaic_bounding_box()
        
        # Extract XML metadata root and physical pixel sizes
        root = imf.get_metadata_root(czi)
        px_x, px_y = imf.extract_pixel_sizes_from_metadata(root)
        
        return {
            "bbox": {
                "x": bbox.x,
                "y": bbox.y,
                "w": bbox.w,
                "h": bbox.h
            },
            # Append expected physical scaling keys
            "pixel_size_x_meters": px_x,
            "pixel_size_y_meters": px_y,
            "dims": "" # Fallback to empty string if exact dimension order is not strictly required
        }
    except Exception as e:
        print(f"Erro ao ler metadados do CZI: {e}")
        raise e
    
def process_single_patch(coord, czi_path, channel, patch_size, bbox_origin, output_dir):
    """Worker function to extract and save one patch using aicspylibczi."""
    y_rel, x_rel = coord
    abs_x, abs_y = bbox_origin[0] + x_rel, bbox_origin[1] + y_rel
    
    # Define the extraction region
    region = (abs_x, abs_y, patch_size, patch_size)
    filename = f"patch_y{y_rel}_x{x_rel}_c{channel}"
    
    try:
        # Open the CZI file
        czi = CziFile(str(czi_path))
        
        # Read the mosaic region
        # aicspylibczi usually returns a 4D or 5D array (S, T, C, Z, Y, X)
        patch_data = czi.read_mosaic(region=region, scale_factor=1.0, C=channel)
        
        # Convert to numpy and clean dimensions
        array = np.squeeze(patch_data)
        
        # If it's an RGB image, ensure the color channel is at the end
        if array.ndim == 3 and array.shape[0] == 3:
            array = np.moveaxis(array, 0, -1)
        
        # Save Raw NPY for AI processing
        imf.atomic_save_npy(output_dir / f"{filename}.npy", array)
        
        # Save Visual PNG for the UI mosaic
        rgb = imf.convert_bgr_to_rgb_uint8(array)
        imf.atomic_save_image(Image.fromarray(rgb), output_dir / f"{filename}.png", lossless=True)
        
        return {"status": "ok"}
    except Exception as e:
        print(f"Error on patch {filename}: {e}")
        return {"status": "error", "error": str(e)}