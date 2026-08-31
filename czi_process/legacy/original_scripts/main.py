"""
main.py
==================================================================
Orquestração do fluxo de processamento do projeto **RockFace**.

Pipeline (com luz polarizada):
  STAGE 0  Metadados & grade de patches.
  STAGE 1  Patching paralelo: para cada coordenada extrai as 6
           polarizações + a imagem normal sintetizada.
  STAGE 2  Mascaramento paralelo: gera a imagem segmentada (poros) a
           partir da imagem normal de cada patch.
  STAGE 3  Petrofísica (porosidade) + manifesto da UI + finalização.

Saída por patch (em ``output_project/patches`` e ``.../masks``):
  * 6 imagens de luz polarizada  -> ``patch_y{Y}_x{X}_pol{C}.png/.npy``
  * 1 imagem normal              -> ``patch_y{Y}_x{X}_normal.png/.npy``
  * 1 imagem segmentada (máscara)-> ``patch_y{Y}_x{X}_mask.png``
"""

import image_functions as imf            # utilitários de imagem/metadados
import petrophysical_properties as ppp   # cálculo de porosidade
import patching_engine as engine         # leitura do CZI e extração de patches
from pathlib import Path                 # caminhos portáveis
from PIL import Image                    # gravação de imagens
import numpy as np                       # núcleo numérico
import re                                # parsing de nomes de patch (manifesto)
import gc                                # coleta de lixo na finalização
import json                              # leitura/gravação de JSON (metadados, resultados, manifesto)
import multiprocessing                   # paralelismo por processos
from functools import partial            # fixa argumentos das funções worker
import time                              # (reservado para medições/telemetria)

# --- Constantes do projeto ---
OUTPUT_BASE = Path("./output_project")   # raiz de saída de todos os artefatos
PATCH_SIZE = 4096                        # lado do patch (pixels)
STRIDE = 3800                            # passo entre patches (gera ~296px de overlap)

# --- Configuração de luz polarizada ---
# Índices de canal correspondentes às 6 polarizações disponíveis no CZI.
# Ajuste conforme a estrutura real do seu arquivo (ver get_available_channels).
POLARIZATION_CHANNELS = [0, 1, 2, 3, 4, 5]
# Como sintetizar a imagem "normal" a partir da pilha de polarizações:
# "mean" (média, padrão) ou "max" (máximo por pixel).
NORMAL_MODE = "mean"

# Gerência de recursos: usa os núcleos disponíveis, limitado a 16.
# OBS.: ler 6 polarizações por patch multiplica o uso de memória; reduza
# MAX_WORKERS se houver estouro de RAM (ver MELHORIAS.md).
MAX_WORKERS = min(16, multiprocessing.cpu_count())


def mask_worker(source_npy_path, mask_dir):
    """Worker de segmentação: gera a máscara de poros de um patch.

    Recebe o ``.npy`` da imagem **normal** do patch, executa a segmentação
    e grava o overlay transparente como imagem segmentada.

    Args:
        source_npy_path: Caminho do ``patch_..._normal.npy``.
        mask_dir: Diretório de saída das máscaras.

    Returns:
        ``True`` em caso de sucesso, ``False`` em falha.
    """
    try:
        arr = np.load(source_npy_path)                       # carrega a imagem normal
        mask = imf.generate_pore_mask(arr)                   # segmenta os poros (BGR)

        # Overlay transparente preenchido, usado pelo mosaico digital da UI
        overlay = imf.generate_transparent_overlay(mask, filled_mode=True)

        # Deriva o nome-base removendo o sufixo "_normal"
        base = source_npy_path.stem.replace("_normal", "")
        imf.atomic_save_image(
            Image.fromarray(overlay), mask_dir / f"{base}_mask.png", True
        )
        return True
    except Exception as e:
        print(f"Erro ao processar máscara de {source_npy_path.name}: {e}")
        return False


def generate_manifest(patch_dir: Path, mask_dir: Path, output_path: Path):
    """Gera o manifesto de coordenadas para o visualizador (React).

    Cada entrada referencia a imagem normal (base do mosaico), a máscara
    segmentada e a lista de URLs das polarizações.

    Args:
        patch_dir: Diretório com as imagens dos patches.
        mask_dir: Diretório com as máscaras.
        output_path: Caminho do ``manifest.json`` a ser gravado.

    Returns:
        Número de entradas (patches) escritas no manifesto.
    """
    # Reconhece a imagem normal de cada patch: patch_y{Y}_x{X}_normal.png
    pattern = re.compile(r"patch_y(\d+)_x(\d+)_normal")
    manifest = []

    normals = list(patch_dir.glob("*_normal.png"))           # base de cada tile
    print(f"DEBUG: Gerando manifesto para {len(normals)} patches.")

    for p in normals:
        m = pattern.search(p.name)
        if not m:
            continue
        y, x = int(m.group(1)), int(m.group(2))              # coordenadas do tile
        base = f"patch_y{y}_x{x}"

        # Coleta as URLs de todas as polarizações deste patch, ordenadas
        pol_files = sorted(patch_dir.glob(f"{base}_pol*.png"))
        polarized_urls = [
            f"http://localhost:8000/results/patches/{pf.name}" for pf in pol_files
        ]

        manifest.append({
            "id": base,
            "x": x,
            "y": y,
            "normal_url": f"http://localhost:8000/results/patches/{p.name}",
            "mask_url": f"http://localhost:8000/results/masks/{base}_mask.png",
            "polarized_urls": polarized_urls,
        })

    # Ordena por coordenadas para renderização consistente do mosaico
    manifest.sort(key=lambda item: (item["y"], item["x"]))

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=4)

    return len(manifest)


def main(czi_path_input: str, status_ref: dict):
    """Orquestra o fluxo completo do RockFace para um arquivo CZI.

    Atualiza ``status_ref`` (compartilhado com o servidor) com progresso e
    mensagens em cada etapa.

    Args:
        czi_path_input: Caminho do arquivo CZI a processar.
        status_ref: Dicionário de status mutável (lido pelo endpoint /status).
    """
    status_ref.update({"progress": 5, "message": "Abrindo arquivo CZI...", "status": "running"})

    czi_file = Path(czi_path_input)                          # caminho do CZI
    patch_dir, mask_dir = OUTPUT_BASE / "patches", OUTPUT_BASE / "masks"
    for d in [patch_dir, mask_dir]:                          # garante diretórios
        d.mkdir(parents=True, exist_ok=True)

    # --- STAGE 0: METADADOS & CONFIGURAÇÃO DA GRADE ---
    meta = engine.read_czi_overview(czi_file)                # escala física + bbox + canais

    # Define quais canais (polarizações) serão usados de fato
    available = meta.get("channels", [0])
    pol_channels = [c for c in POLARIZATION_CHANNELS if c in available] or available

    # Gera as coordenadas determinísticas dos patches
    coords = imf.get_patches((meta["bbox"]["h"], meta["bbox"]["w"]), PATCH_SIZE, STRIDE)
    total_patches = len(coords)

    # Persiste os parâmetros de extração em JSON (inclui config de polarização)
    metadata_path = imf.save_image_metadata_from_overview(
        czi_path=czi_file,
        channel=0,
        patch_coords=coords,
        selected_coords=coords,
        patch_size=PATCH_SIZE,
        stride=STRIDE,
        output_dir=OUTPUT_BASE,
        overview=meta,
        polarization_channels=pol_channels,
        normal_mode=NORMAL_MODE,
    )

    # Recarrega os metadados como dicionário (usados na petrofísica)
    with open(metadata_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    # --- STAGE 1: PATCHING (10% a 50%) ---
    # Para cada coordenada, extrai 6 polarizações + imagem normal.
    patch_func = partial(
        engine.process_single_patch,
        czi_path=czi_file,
        patch_size=PATCH_SIZE,
        bbox_origin=(meta["bbox"]["x"], meta["bbox"]["y"]),
        output_dir=patch_dir,
        polarization_channels=pol_channels,
        normal_mode=NORMAL_MODE,
    )

    failed_patches = 0                                       # contador de falhas
    with multiprocessing.Pool(MAX_WORKERS) as pool:
        # imap_unordered: resultados chegam conforme terminam (melhor para progresso)
        for i, result in enumerate(pool.imap_unordered(patch_func, coords), 1):
            if isinstance(result, dict) and result.get("status") == "error":
                failed_patches += 1                          # agrega falhas de worker
            status_ref["progress"] = 10 + int((i / total_patches) * 40)
            status_ref["message"] = f"Extraindo fatias (6 polarizações): {i}/{total_patches}"

    if failed_patches:
        print(f"Aviso: {failed_patches} patch(es) falharam na extração.")

    # --- STAGE 2: MASCARAMENTO (50% a 90%) ---
    # Segmenta os poros a partir da imagem normal de cada patch.
    status_ref.update({"progress": 50, "message": "Analisando porosidade..."})
    normal_files = list(patch_dir.glob("*_normal.npy"))      # fonte da segmentação
    total_masks = len(normal_files)

    mask_func = partial(mask_worker, mask_dir=mask_dir)

    with multiprocessing.Pool(MAX_WORKERS) as pool:
        for i, _ in enumerate(pool.imap_unordered(mask_func, normal_files), 1):
            status_ref["progress"] = 50 + int((i / max(total_masks, 1)) * 40)
            status_ref["message"] = f"Gerando máscaras: {i}/{total_masks}"

    # --- STAGE 3: PETROFÍSICA & FINALIZAÇÃO (90% a 100%) ---
    status_ref.update({"progress": 92, "message": "Finalizando mosaico digital..."})

    try:
        # Calcula métricas quantitativas usando a área efetiva (recorte por stride)
        stats = ppp.calculate_petrophysical_stats(
            patch_dir=patch_dir,
            mask_dir=mask_dir,
            stride=STRIDE,
            metadata=data,
        )

        # Armazena os resultados para auditoria e relatório
        with open(OUTPUT_BASE / "petrophysical_results.json", "w", encoding="utf-8") as f:
            json.dump(stats, f, indent=4)

        # Constrói o manifesto consumido pela UI
        count = generate_manifest(patch_dir, mask_dir, OUTPUT_BASE / "manifest.json")

        status_ref.update({
            "progress": 100,
            "message": f"Concluído: {count} patches e {stats['porosidade_final']}% de porosidade.",
            "status": "complete",
        })
        print(f"Workflow finalizado. Estatísticas petrofísicas: {stats}")

    except Exception as e:
        print(f"Erro na finalização: {e}")
        status_ref.update({
            "progress": 90,
            "message": f"Erro no encerramento: {str(e)}",
            "status": "error",
        })
    finally:
        gc.collect()                                         # libera memória residual
