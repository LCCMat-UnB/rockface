"""
image_functions.py
==================================================================
Módulo utilitário do projeto **RockFace** (LCCMat / Petrobras).

Reúne funções de baixo nível usadas pelo restante do pipeline:

* Extração de metadados do arquivo CZI (XML do microscópio Zeiss).
* Operações de I/O atômicas (gravação segura de .npy / .png / .jpg).
* Geometria de geração de *patches* (recortes da imagem do mosaico).
* Segmentação de poros (resina impregnada azul/ciano) por limiar HSV.
* Síntese da imagem "normal" a partir da pilha de polarizações.
* Geração de *overlays* transparentes (máscaras coloridas) para a UI.

Convenção de cor
----------------
Arquivos CZI RGB normalmente usam o tipo de pixel ``Bgr24`` — ou seja,
os dados retornados pela biblioteca chegam em ordem **BGR**. Por isso:
  * a segmentação (:func:`generate_pore_mask`) assume BGR;
  * :func:`convert_bgr_to_rgb_uint8` converte para RGB só na hora de
    salvar PNG/JPEG (que o PIL espera em RGB).
Se o seu microscópio gravar em ordem RGB, ajuste essa convenção.
"""

import ctypes                      # mantido por compatibilidade com chamadas nativas externas
import os                          # operações de sistema de arquivos (os.replace, os.unlink)
import platform                    # detecção de SO (uso futuro / dependências nativas)
import gc                          # coletor de lixo (liberação explícita de memória)
import re                          # expressões regulares (parsing de nomes de patch)
import numpy as np                 # núcleo numérico de todo o pipeline
import json                        # serialização dos metadados
import cv2                         # OpenCV: blur, conversão de cor, contornos, morfologia
from pathlib import Path           # manipulação de caminhos de forma portável
from PIL import Image              # leitura/gravação de imagens (PNG/JPEG)
from typing import Any, Dict, List, Optional, Tuple  # anotações de tipo
import xml.etree.ElementTree as ET  # parsing do XML de metadados do CZI


# ============================================================
# EXTRAÇÃO DE METADADOS DO CZI
# ============================================================

def get_metadata_root(czi) -> Any:
    """Retorna a raiz dos metadados do CZI como um elemento XML.

    A biblioteca pode expor os metadados já como objeto XML ou como
    string crua; este wrapper normaliza os dois casos.

    Args:
        czi: Instância de ``aicspylibczi.CziFile`` já aberta.

    Returns:
        Elemento raiz (``xml.etree.ElementTree.Element``) dos metadados.
    """
    metadata = czi.meta                      # acessa o bloco de metadados do CZI
    if isinstance(metadata, str):            # se vier como texto bruto...
        return ET.fromstring(metadata)       # ...converte para árvore XML
    return metadata                          # caso contrário, já é um elemento XML


def extract_pixel_sizes_from_metadata(root: Any) -> Tuple[Optional[float], Optional[float]]:
    """Extrai o tamanho físico do pixel (X e Y) dos metadados, em metros.

    Percorre todos os nós ``<Distance>`` do XML procurando pelos eixos
    ``X`` e ``Y`` e lê o valor numérico contido em ``<Value>``.

    Args:
        root: Elemento XML raiz dos metadados (pode ser ``None``).

    Returns:
        Tupla ``(pixel_size_x, pixel_size_y)`` em metros. Cada elemento é
        ``None`` quando o valor não pôde ser determinado.
    """
    pixel_size_x = None                      # valor padrão caso o eixo X não exista
    pixel_size_y = None                      # valor padrão caso o eixo Y não exista

    if root is None:                         # sem metadados não há o que extrair
        return pixel_size_x, pixel_size_y

    # Itera sobre cada nó <Distance> em qualquer profundidade da árvore
    for distance in root.findall(".//Distance"):
        distance_id = distance.get("Id")     # identifica o eixo ("X" ou "Y")
        value_element = distance.find("Value")  # nó com o valor numérico

        # Pula entradas sem valor textual válido
        if value_element is None or value_element.text is None:
            continue

        try:
            value = float(value_element.text)  # converte o texto em float
        except ValueError:
            continue                          # ignora valores não numéricos

        if distance_id == "X":               # atribui ao eixo correspondente
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
    polarization_channels: Optional[List[int]] = None,
    normal_mode: str = "mean",
) -> Path:
    """Salva os metadados essenciais da imagem e da geração de patches em JSON.

    Persiste a estrutura física da imagem (dimensões, origem global e
    escala em metros) e os parâmetros de geração de patches. Inclui, ainda,
    a configuração de polarização usada nesta execução.

    Args:
        czi_path: Caminho do arquivo CZI de origem.
        channel: Canal de referência (mantido por compatibilidade).
        patch_coords: Lista de coordenadas (y, x) de todos os patches.
        selected_coords: Subconjunto de coordenadas efetivamente usado.
        patch_size: Tamanho do patch (lado, em pixels).
        stride: Passo entre patches (em pixels).
        output_dir: Diretório de saída do JSON.
        overview: Dicionário-resumo do CZI (ver ``read_czi_overview``).
        polarization_channels: Índices de canal correspondentes às
            polarizações; usado apenas para registro nos metadados.
        normal_mode: Modo de síntese da imagem normal ("mean"/"max").

    Returns:
        Caminho do arquivo JSON gravado.
    """
    czi_path = Path(czi_path)                # garante objeto Path
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)  # cria o diretório se faltar

    bbox = overview["bbox"]                  # caixa delimitadora do mosaico

    # Monta o dicionário de metadados que será serializado
    metadata = {
        "source_file": czi_path.name,        # nome do arquivo CZI de origem
        "channel": channel,                  # canal de referência
        "image_structure": {
            "full_width_px": bbox["w"],      # largura total em pixels
            "full_height_px": bbox["h"],     # altura total em pixels
            "global_x_origin": bbox["x"],    # origem X global do mosaico
            "global_y_origin": bbox["y"],    # origem Y global do mosaico
            "dimensions_order": overview.get("dims", ""),  # ordem das dimensões
        },
        "physical_scaling": {
            "pixel_size_x_meters": overview["pixel_size_x_meters"],  # escala X
            "pixel_size_y_meters": overview["pixel_size_y_meters"],  # escala Y
            "unit": "meters",
        },
        "patch_generation": {
            "patch_size_px": patch_size,                 # lado do patch
            "stride_px": stride,                         # passo
            "overlap_px": patch_size - stride,           # sobreposição resultante
            "total_patches": len(patch_coords),          # quantidade de patches
        },
        # Bloco que documenta a configuração de luz polarizada desta execução
        "polarization": {
            "channels": polarization_channels if polarization_channels is not None else [],
            "count": len(polarization_channels) if polarization_channels else 0,
            "normal_mode": normal_mode,
        },
        "analysis_info": {
            "project": "RockFace",
            "institution": "LCCMat / Petrobras",
            "engine_version": "Alfa 1.1",    # incrementado: suporte a polarização
        },
    }

    # Caminho final: <stem-do-czi>_metadata.json
    output_path = output_dir / f"{czi_path.stem}_metadata.json"

    # Gravação do JSON com indentação e acentuação preservada
    with open(output_path, "w", encoding="utf-8") as file:
        json.dump(metadata, file, indent=4, ensure_ascii=False)

    return output_path


# ============================================================
# I/O & OPERAÇÕES ATÔMICAS
# ============================================================

def atomic_save_npy(output_path: Path, array: np.ndarray) -> None:
    """Salva um ``.npy`` de forma atômica (evita arquivos corrompidos).

    Grava primeiro em um arquivo temporário e, só depois, faz ``os.replace``
    — operação atômica que impede deixar um arquivo pela metade caso o
    processo seja morto durante a escrita.

    Args:
        output_path: Caminho final do arquivo ``.npy``.
        array: Array NumPy a ser persistido.
    """
    output_path = Path(output_path)
    tmp_path = output_path.with_name(output_path.name + ".tmp")  # arquivo temporário
    try:
        with open(tmp_path, "wb") as file:   # escreve no temporário
            np.save(file, array)
        os.replace(tmp_path, output_path)    # troca atômica para o destino final
    finally:
        if tmp_path.exists():                # limpeza defensiva do temporário
            try:
                tmp_path.unlink()
            except OSError:
                pass                         # ignora falha de remoção


def atomic_save_image(image: Image.Image, output_path: Path, lossless: bool, jpeg_quality: int = 95) -> None:
    """Salva PNG/JPEG de forma atômica (evita imagens incompletas).

    Args:
        image: Imagem PIL a ser gravada.
        output_path: Caminho final do arquivo.
        lossless: Se ``True`` grava PNG (sem perdas); senão grava JPEG.
        jpeg_quality: Qualidade JPEG (0–100), usada apenas quando não lossless.
    """
    output_path = Path(output_path)
    tmp_path = output_path.with_name(output_path.name + ".tmp")  # arquivo temporário
    try:
        if lossless:                         # caminho sem perdas
            image.save(tmp_path, format="PNG")
        else:                                # caminho com perdas (mais leve)
            image.save(tmp_path, format="JPEG", quality=jpeg_quality, subsampling=0)
        os.replace(tmp_path, output_path)    # troca atômica
    finally:
        if tmp_path.exists():                # limpeza do temporário
            try:
                tmp_path.unlink()
            except OSError:
                pass


def convert_bgr_to_rgb_uint8(array_bgr: np.ndarray) -> np.ndarray:
    """Converte array BGR/escala-de-cinza para RGB ``uint8`` (formato do PIL).

    Args:
        array_bgr: Array de imagem em BGR (HxWx3) ou em tons de cinza (HxW).

    Returns:
        Array contíguo em RGB ``uint8`` pronto para ``Image.fromarray``.
    """
    # Se for colorido (3 canais), inverte a ordem dos canais (BGR -> RGB)
    if array_bgr.ndim == 3 and array_bgr.shape[-1] == 3:
        array_rgb = np.ascontiguousarray(array_bgr[..., ::-1])
    else:                                    # escala de cinza permanece como está
        array_rgb = np.ascontiguousarray(array_bgr)

    # Garante o tipo uint8 (com saturação) exigido pelo PIL
    if array_rgb.dtype != np.uint8:
        array_rgb = np.clip(array_rgb, 0, 255).astype(np.uint8, copy=False)
    return array_rgb


def synthesize_normal_image(polarized_stack: List[np.ndarray], mode: str = "mean") -> np.ndarray:
    """Sintetiza a imagem "normal" a partir da pilha de polarizações.

    Em microscopia de luz polarizada, combinar várias rotações do
    analisador aproxima uma visão "não polarizada" / com iluminação
    orientacionalmente média da seção delgada. Esta função produz essa
    imagem de referência a partir das N imagens polarizadas do patch.

    Args:
        polarized_stack: Lista de arrays (mesma forma) das polarizações.
        mode: Estratégia de combinação:
            * ``"mean"`` — média por pixel (visão média, padrão).
            * ``"max"``  — máximo por pixel (realça respostas brilhantes).

    Returns:
        Array ``uint8`` na mesma ordem de cor da entrada (tipicamente BGR).

    Raises:
        ValueError: Se a pilha estiver vazia ou as formas forem incompatíveis.
    """
    if not polarized_stack:                  # nada para combinar
        raise ValueError("A pilha de polarizações está vazia.")

    # Verifica consistência de formato entre todas as polarizações
    base_shape = polarized_stack[0].shape
    if any(layer.shape != base_shape for layer in polarized_stack):
        raise ValueError("As imagens polarizadas têm formatos incompatíveis.")

    # Empilha em um eixo extra e calcula em ponto flutuante para evitar overflow
    stack = np.stack([layer.astype(np.float32) for layer in polarized_stack], axis=0)

    if mode == "max":                        # combinação por máximo
        combined = stack.max(axis=0)
    else:                                    # combinação por média (padrão)
        combined = stack.mean(axis=0)

    # Retorna como uint8 saturado (faixa válida de imagem)
    return np.clip(combined, 0, 255).astype(np.uint8)


# ============================================================
# GEOMETRIA & LÓGICA DE PATCHES
# ============================================================

def get_patches(image_shape: Tuple[int, int], patch_size: int, stride: int) -> List[Tuple[int, int]]:
    """Gera coordenadas determinísticas de patches no formato (y, x).

    Varre a imagem em passos de ``stride`` e, nas bordas, "ancora" o patch
    para que ele não ultrapasse os limites da imagem (gerando sobreposição
    controlada na última linha/coluna).

    Args:
        image_shape: ``(altura, largura)`` da imagem (em pixels).
        patch_size: Lado do patch (em pixels).
        stride: Passo entre patches (em pixels).

    Returns:
        Lista ordenada e sem duplicatas de coordenadas ``(y_start, x_start)``.
    """
    height, width = image_shape[:2]          # extrai altura e largura
    coordinates = []                         # acumula as coordenadas
    y_start = 0                              # inicializado para evitar NameError em bordas

    for y in range(0, height, stride):       # percorre o eixo vertical
        for x in range(0, width, stride):    # percorre o eixo horizontal
            # Ancora o patch dentro dos limites da imagem
            y_start = max(0, min(y, height - patch_size))
            x_start = max(0, min(x, width - patch_size))
            coordinates.append((y_start, x_start))
            # Se já cobriu a borda direita, encerra a varredura horizontal
            if x_start + patch_size >= width:
                break
        # Se já cobriu a borda inferior, encerra a varredura vertical
        if y_start + patch_size >= height:
            break

    # Remove duplicatas (bordas podem repetir coordenadas) e ordena
    return sorted(set(coordinates))


# ============================================================
# SEGMENTAÇÃO & MÁSCARAS
# ============================================================

def generate_pore_mask(bgr_array: np.ndarray) -> np.ndarray:
    """Segmenta poros impregnados com resina (azul/ciano) em uma imagem BGR.

    Estratégia:
        1. Suaviza a imagem (reduz ruído) com filtro Gaussiano.
        2. Converte para HSV e limiariza pelo matiz (faixa ciano/azul).
        3. Refina exigindo saturação*valor mínimos (descarta regiões opacas).
        4. Abertura morfológica para remover ruído isolado.

    Args:
        bgr_array: Imagem do patch em ordem **BGR** (HxWx3, uint8).

    Returns:
        Máscara binária ``uint8`` (0 ou 255), onde 255 indica poro.
    """
    blurred = cv2.GaussianBlur(bgr_array, (5, 5), 0)     # suaviza ruído
    hsv = cv2.cvtColor(blurred, cv2.COLOR_BGR2HSV)       # converte BGR -> HSV
    h, s, v = cv2.split(hsv)                             # separa os canais

    # Limiar por matiz: faixa ciano/azul típica da resina de impregnação
    lower_hue, upper_hue = 75, 125
    coarse_mask = cv2.inRange(h, lower_hue, upper_hue)   # máscara grosseira

    # Refinamento por S*V: descarta pixels pouco saturados/escuros
    s_norm = s.astype(float) / 255.0                     # saturação normalizada
    v_norm = v.astype(float) / 255.0                     # valor normalizado
    sv_product = s_norm * v_norm                         # produto S*V

    dt = 0.1                                             # limiar mínimo de S*V
    refined_mask = np.where(
        (coarse_mask > 0) & (sv_product >= dt), 255, 0
    ).astype(np.uint8)

    # Abertura morfológica (erosão seguida de dilatação) remove ruído pequeno
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
    return cv2.morphologyEx(refined_mask, cv2.MORPH_OPEN, kernel)


def generate_transparent_overlay(
    mask: np.ndarray,
    color: Tuple[int, int, int] = (0, 255, 0),
    filled_mode: bool = False,
    thickness: int = 2,
) -> np.ndarray:
    """Cria um overlay RGBA transparente a partir de uma máscara binária.

    Em modo contorno, ignora segmentos que coincidem com a borda do patch
    (evita linhas artificiais nas junções do mosaico).

    Args:
        mask: Máscara binária (HxW, valores 0/255).
        color: Cor BGR do overlay.
        filled_mode: Se ``True`` preenche a região; senão desenha contornos.
        thickness: Espessura da linha (apenas no modo contorno).

    Returns:
        Array RGBA ``uint8`` (HxWx4) com fundo transparente.
    """
    h, w = mask.shape[:2]                                # dimensões da máscara
    overlay = np.zeros((h, w, 4), dtype=np.uint8)        # canvas transparente
    bgra_color = (*color, 255)                           # cor com alfa opaco

    if filled_mode:                                      # modo preenchido
        overlay[mask == 255] = bgra_color                # pinta toda a área
    else:                                                # modo contorno
        contours, _ = cv2.findContours(mask, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
        for cnt in contours:                             # para cada contorno
            for i in range(len(cnt)):                    # para cada segmento
                pt1, pt2 = cnt[i][0], cnt[(i + 1) % len(cnt)][0]
                # Detecta segmentos que estão exatamente na borda do patch
                on_border = (pt1[0] <= 0 and pt2[0] <= 0) or (pt1[0] >= w - 1 and pt2[0] >= w - 1) or \
                            (pt1[1] <= 0 and pt2[1] <= 0) or (pt1[1] >= h - 1 and pt2[1] >= h - 1)
                if not on_border:                        # desenha só fora da borda
                    cv2.line(overlay, tuple(pt1), tuple(pt2), bgra_color, thickness)
    return overlay
