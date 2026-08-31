"""
patching_engine.py
==================================================================
Camada de acesso ao arquivo CZI do projeto **RockFace**.

Responsabilidades:
  * Ler os metadados/dimensões do mosaico (:func:`read_czi_overview`).
  * Descobrir quantos canais de polarização existem
    (:func:`get_available_channels`).
  * Extrair, para cada coordenada de patch, **todas as polarizações**
    disponíveis e sintetizar a imagem "normal"
    (:func:`process_single_patch`).

Cada patch gera, em ``output_dir``:
  * ``patch_y{Y}_x{X}_pol{C}.npy`` / ``.png`` — uma por polarização.
  * ``patch_y{Y}_x{X}_normal.npy`` / ``.png`` — imagem normal sintetizada.
A imagem segmentada é produzida posteriormente, na etapa de máscaras
(ver ``main.mask_worker``), a partir da imagem normal.
"""

import numpy as np                       # núcleo numérico
import image_functions as imf            # utilitários de I/O, cor e síntese
from aicspylibczi import CziFile         # leitura nativa de arquivos CZI
from PIL import Image                    # conversão de array -> imagem para gravação


def get_available_channels(czi) -> list:
    """Descobre os índices de canal (dimensão ``C``) presentes no CZI.

    Em aquisições de luz polarizada, cada ângulo do polarizador costuma
    ser armazenado como um canal distinto. Esta função consulta a forma
    das dimensões para listar os canais reais, evitando assumir um número
    fixo de polarizações.

    Args:
        czi: Instância de ``aicspylibczi.CziFile`` já aberta.

    Returns:
        Lista de índices de canal (ex.: ``[0, 1, 2, 3, 4, 5]``). Em caso de
        falha na introspecção, retorna ``[0]`` como fallback seguro.
    """
    try:
        dims_shape = czi.get_dims_shape()    # ex.: [{'C': (0, 6), 'Y': (0, N), ...}]
        for block in dims_shape:             # pode haver mais de um bloco de cenas
            if "C" in block:                 # localiza a dimensão de canal
                start, end = block["C"]      # intervalo [start, end)
                return list(range(start, end))
    except Exception as e:
        # Falha de introspecção não deve derrubar o pipeline; loga e segue
        print(f"Aviso: não foi possível ler a dimensão de canais (C): {e}")
    return [0]                                # fallback: ao menos o canal 0


def read_czi_overview(czi_path):
    """Lê os metadados do CZI para determinar dimensões e escala física.

    Essencial para o projeto RockFace: define a caixa delimitadora do
    mosaico, a escala física do pixel e os canais de polarização.

    Args:
        czi_path: Caminho do arquivo CZI.

    Returns:
        Dicionário com ``bbox`` (x, y, w, h), escalas físicas em metros,
        ordem das dimensões (``dims``) e lista de canais (``channels``).

    Raises:
        Exception: Repropaga qualquer erro de leitura do CZI.
    """
    try:
        czi = CziFile(str(czi_path))                 # abre o arquivo CZI
        bbox = czi.get_mosaic_bounding_box()         # caixa delimitadora do mosaico

        # Extrai a raiz XML dos metadados e a escala física do pixel
        root = imf.get_metadata_root(czi)
        px_x, px_y = imf.extract_pixel_sizes_from_metadata(root)

        # Descobre os canais (polarizações) reais do arquivo
        channels = get_available_channels(czi)

        # Tenta capturar a ordem real das dimensões (ex.: "BCYX")
        dims = getattr(czi, "dims", "") or ""

        return {
            "bbox": {
                "x": bbox.x,                 # origem X global do mosaico
                "y": bbox.y,                 # origem Y global do mosaico
                "w": bbox.w,                 # largura total
                "h": bbox.h,                 # altura total
            },
            "pixel_size_x_meters": px_x,     # escala física no eixo X (m)
            "pixel_size_y_meters": px_y,     # escala física no eixo Y (m)
            "dims": dims,                    # ordem das dimensões do CZI
            "channels": channels,            # índices de canal/polarização
        }
    except Exception as e:
        print(f"Erro ao ler metadados do CZI: {e}")
        raise e                              # repropaga para o orquestrador tratar


def _read_channel_region(czi, region, channel) -> np.ndarray:
    """Lê uma região do mosaico para um canal e normaliza as dimensões.

    Args:
        czi: ``CziFile`` aberto.
        region: Tupla ``(abs_x, abs_y, w, h)`` da região absoluta.
        channel: Índice do canal a ler.

    Returns:
        Array da imagem com o canal de cor no final (HxWx3) ou 2D (HxW).
    """
    patch_data = czi.read_mosaic(region=region, scale_factor=1.0, C=channel)
    array = np.squeeze(patch_data)           # remove dimensões unitárias (S,T,Z...)

    # Se for RGB com canal de cor à frente (3xHxW), move-o para o final
    if array.ndim == 3 and array.shape[0] == 3:
        array = np.moveaxis(array, 0, -1)
    return array


def process_single_patch(
    coord,
    czi_path,
    patch_size,
    bbox_origin,
    output_dir,
    polarization_channels,
    normal_mode="mean",
):
    """Extrai um patch em **todas as polarizações** e gera a imagem normal.

    Para a coordenada informada, lê cada canal de polarização disponível,
    grava os arquivos por polarização (``.npy`` + ``.png``) e, ao final,
    sintetiza e grava a imagem "normal" (combinação da pilha).

    Args:
        coord: Coordenada do patch ``(y_rel, x_rel)`` relativa à bbox.
        czi_path: Caminho do arquivo CZI.
        patch_size: Lado do patch (pixels).
        bbox_origin: Origem global ``(x, y)`` do mosaico.
        output_dir: Diretório onde os arquivos do patch serão gravados.
        polarization_channels: Índices de canal desejados (polarizações).
        normal_mode: Estratégia de síntese da imagem normal ("mean"/"max").

    Returns:
        Dicionário de status: ``{"status": "ok", "channels": [...]}`` em caso
        de sucesso, ou ``{"status": "error", "error": "..."}`` em falha.
    """
    y_rel, x_rel = coord                                 # coordenada relativa
    abs_x = bbox_origin[0] + x_rel                       # converte para X absoluto
    abs_y = bbox_origin[1] + y_rel                       # converte para Y absoluto

    region = (abs_x, abs_y, patch_size, patch_size)      # região absoluta a extrair
    base = f"patch_y{y_rel}_x{x_rel}"                    # prefixo comum dos arquivos

    try:
        czi = CziFile(str(czi_path))                     # abre o CZI neste worker

        # Restringe os canais pedidos aos realmente existentes no arquivo
        available = set(get_available_channels(czi))
        channels = [c for c in polarization_channels if c in available]
        if not channels:                                 # fallback se nada coincidir
            channels = sorted(available)

        polarized_stack = []                             # acumula as polarizações lidas

        # --- Lê e grava cada polarização individualmente ---
        for c in channels:
            array = _read_channel_region(czi, region, c)  # imagem da polarização c
            polarized_stack.append(array)

            # NPY cru (BGR/native) para processamento posterior (segmentação/IA)
            imf.atomic_save_npy(output_dir / f"{base}_pol{c}.npy", array)

            # PNG visual (convertido para RGB) para a UI
            rgb = imf.convert_bgr_to_rgb_uint8(array)
            imf.atomic_save_image(
                Image.fromarray(rgb), output_dir / f"{base}_pol{c}.png", lossless=True
            )

        # --- Sintetiza e grava a imagem "normal" a partir da pilha ---
        normal = imf.synthesize_normal_image(polarized_stack, mode=normal_mode)
        imf.atomic_save_npy(output_dir / f"{base}_normal.npy", normal)
        rgb_normal = imf.convert_bgr_to_rgb_uint8(normal)
        imf.atomic_save_image(
            Image.fromarray(rgb_normal), output_dir / f"{base}_normal.png", lossless=True
        )

        return {"status": "ok", "channels": channels}
    except Exception as e:
        print(f"Erro no patch {base}: {e}")
        return {"status": "error", "error": str(e)}
