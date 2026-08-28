"""
petrophysical_properties.py
==================================================================
Cálculo quantitativo de porosidade do projeto **RockFace**.

A partir da imagem **normal** de cada patch (área de rocha) e da máscara
de poros correspondente, computa a área de rocha, a área de poros e a
porosidade relativa, convertendo de pixels para mm² com a escala física.

Para evitar contagem dupla na sobreposição entre patches, cada patch é
recortado para uma janela de lado ``stride`` (canto superior esquerdo).
"""

import numpy as np                     # núcleo numérico
from pathlib import Path               # caminhos portáveis
from PIL import Image                  # leitura das máscaras PNG


def calculate_petrophysical_stats(patch_dir: Path, mask_dir: Path, stride: int, metadata: dict):
    """Calcula área de rocha, área de poros e porosidade (sem sobreposição).

    Usa recorte por ``stride`` para que a área efetivamente contada de cada
    patch não se sobreponha à do patch vizinho.

    Args:
        patch_dir: Diretório com as imagens normais (``*_normal.npy``).
        mask_dir: Diretório com as máscaras (``*_mask.png``).
        stride: Passo usado no patching (define a janela efetiva de contagem).
        metadata: Metadados (JSON) com a escala física do pixel.

    Returns:
        Dicionário com ``area_rocha_mm2``, ``area_poros_mm2`` e
        ``porosidade_final`` (em %).

    Raises:
        ValueError: Se a escala física do pixel não estiver nos metadados.
    """
    total_rock_pixels = 0                              # acumulador de pixels de rocha
    total_pore_pixels = 0                              # acumulador de pixels de poro

    # Escala física do pixel (em metros) a partir dos metadados
    pixel_size_m = metadata.get("physical_scaling", {}).get("pixel_size_x_meters")

    # Sem escala física não há como converter para unidades reais
    if not pixel_size_m:
        raise ValueError(
            "'pixel_size_x_meters' ausente nos metadados. "
            "Impossível converter para unidades físicas."
        )

    # Converte metros -> micrômetros (1 m = 1.000.000 µm)
    pixel_size_um = pixel_size_m * 1e6

    # Itera sobre as imagens normais (uma por patch)
    normal_files = list(patch_dir.glob("*_normal.npy"))

    for npy_path in normal_files:
        arr = np.load(npy_path)                        # carrega a imagem normal

        # Localiza a máscara correspondente: patch_..._normal.npy -> patch_..._mask.png
        base = npy_path.stem.replace("_normal", "")
        mask_path = mask_dir / f"{base}_mask.png"
        if not mask_path.exists():                     # sem máscara, pula o patch
            continue

        mask_rgba = np.array(Image.open(mask_path))    # lê a máscara (RGBA esperado)

        # Extrai uma máscara 2D a partir do canal alfa (ou do máximo dos canais)
        if mask_rgba.ndim == 3 and mask_rgba.shape[-1] == 4:
            mask_2d = mask_rgba[:, :, 3]               # canal alfa = região de poro
        else:
            mask_2d = np.max(mask_rgba, axis=-1) if mask_rgba.ndim == 3 else mask_rgba

        # RECORTE POR STRIDE: janela efetiva (evita dupla contagem na sobreposição)
        effective_arr = arr[0:stride, 0:stride]
        effective_mask = mask_2d[0:stride, 0:stride]

        # 1) ÁREA DE ROCHA: pixels não-fundo (qualquer canal > 5)
        rock_mask = np.any(effective_arr > 5, axis=-1)
        total_rock_pixels += np.sum(rock_mask)

        # 2) ÁREA DE POROS: poros que caem dentro da rocha
        pore_count = np.sum((effective_mask > 0) & rock_mask)
        total_pore_pixels += pore_count

    # CONVERSÃO DE MÉTRICA (pixels -> mm²)
    pixel_area_um2 = pixel_size_um ** 2                # área de 1 pixel em µm²
    pixel_to_mm2 = pixel_area_um2 / 1_000_000          # µm² -> mm² (1 mm² = 1e6 µm²)

    area_rocha_mm2 = total_rock_pixels * pixel_to_mm2  # área total de rocha
    area_poros_mm2 = total_pore_pixels * pixel_to_mm2  # área total de poros

    # Porosidade relativa à área efetiva de rocha (evita divisão por zero)
    porosidade_relativa = (
        (area_poros_mm2 / area_rocha_mm2) * 100 if area_rocha_mm2 > 0 else 0
    )

    return {
        "area_rocha_mm2": round(area_rocha_mm2, 3),
        "area_poros_mm2": round(area_poros_mm2, 3),
        "porosidade_final": round(porosidade_relativa, 2),
    }
