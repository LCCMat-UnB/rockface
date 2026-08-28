"""
server.py
==================================================================
API HTTP (FastAPI) do projeto **RockFace**.

Responsabilidades:
  * Receber o upload do arquivo CZI e disparar o pipeline em background.
  * Restaurar sessões anteriores quando já existem resultados.
  * Servir, via rota estática ``/results``, as imagens (polarizações,
    normal e máscaras) geradas pelo pipeline.
  * Expor status, metadados, manifesto e resultados petrofísicos para o
    front-end React/Vite.

Observação: as imagens de polarização e normal ficam em
``output_project/patches`` e as máscaras em ``output_project/masks``;
ambas são acessíveis sob ``/results``.
"""

from fastapi import FastAPI, BackgroundTasks, UploadFile, File   # framework web
from fastapi.middleware.cors import CORSMiddleware               # liberação de origens (CORS)
from fastapi.staticfiles import StaticFiles                      # servir arquivos estáticos
from pathlib import Path                                         # caminhos portáveis
import shutil                                                    # cópia/remoção de arquivos
import json                                                      # leitura de JSONs
import main as workflow                                          # pipeline de processamento

app = FastAPI()                                                  # instância principal da API

# Configuração de CORS para o front-end (Vite/React).
# OBS.: allow_origins=["*"] é conveniente em dev; restrinja em produção.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Constantes de diretório
UPLOAD_DIR = Path("./uploads")            # arquivos CZI enviados
OUTPUT_DIR = Path("./output_project")     # artefatos gerados pelo pipeline

# Garante a existência dos diretórios na inicialização
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

# Monta a pasta de saída como rota estática (serve imagens e máscaras)
app.mount("/results", StaticFiles(directory=str(OUTPUT_DIR)), name="results")


def has_files(directory: Path) -> bool:
    """Verifica se um diretório existe e contém ao menos um arquivo.

    Usa ``rglob`` para detectar arquivos também em subpastas.

    Args:
        directory: Diretório a inspecionar.

    Returns:
        ``True`` se houver pelo menos um arquivo; ``False`` caso contrário.
    """
    return directory.exists() and any(path.is_file() for path in directory.rglob("*"))


def is_valid_nonempty_json(json_path: Path) -> bool:
    """Verifica se um JSON existe, não está vazio e é válido.

    Args:
        json_path: Caminho do arquivo JSON.

    Returns:
        ``True`` se o arquivo existir, tiver conteúdo e parsear com sucesso.
    """
    if not json_path.exists():               # arquivo inexistente
        return False

    if json_path.stat().st_size == 0:        # arquivo vazio
        return False

    try:
        with open(json_path, "r", encoding="utf-8") as f:
            data = json.load(f)              # tenta parsear
        return bool(data)                    # exige conteúdo não-falsy
    except Exception:
        return False                         # JSON inválido


def check_existing_results_for_file(filename: str) -> bool:
    """Verifica se já há resultados significativos para o CZI informado.

    Critérios (todos obrigatórios):
        1. ``OUTPUT_DIR/patches`` existe e contém arquivos.
        2. ``OUTPUT_DIR/masks`` existe e contém arquivos.
        3. Existe ``{stem_do_CZI}_metadata.json`` válido e não vazio.

    Args:
        filename: Nome (ou caminho) do arquivo CZI.

    Returns:
        ``True`` se houver resultados prévios completos.
    """
    safe_filename = Path(filename).name      # remove componentes de caminho
    czi_stem = Path(safe_filename).stem      # nome sem extensão

    patches_dir = OUTPUT_DIR / "patches"
    masks_dir = OUTPUT_DIR / "masks"
    metadata_path = OUTPUT_DIR / f"{czi_stem}_metadata.json"

    has_patches = has_files(patches_dir)              # há patches?
    has_masks = has_files(masks_dir)                  # há máscaras?
    has_metadata = is_valid_nonempty_json(metadata_path)  # há metadados válidos?

    return has_patches and has_masks and has_metadata


def clear_directory(directory: Path):
    """Limpa completamente um diretório e o recria vazio.

    Args:
        directory: Diretório a ser zerado.
    """
    if directory.exists():
        shutil.rmtree(directory)             # remove recursivamente
    directory.mkdir(parents=True, exist_ok=True)  # recria vazio


# Estado global inicial do processamento (lido pelo endpoint /status).
# OBS.: estado mutável global — ver MELHORIAS.md (condição de corrida).
processing_status = {
    "progress": 0,
    "message": "Aguardando início...",
    "status": "idle",
    "filename": None,
    "restored": False,
}


@app.get("/check-results")
async def check_results(filename: str):
    """Verifica resultados prévios e, se existirem, restaura a sessão.

    Args:
        filename: Nome do arquivo CZI a checar.

    Returns:
        Dicionário indicando existência, status e eventual redirecionamento.
    """
    global processing_status

    safe_filename = Path(filename).name      # higieniza o nome recebido

    if check_existing_results_for_file(safe_filename):
        # Resultados encontrados: marca a sessão como restaurada
        processing_status = {
            "progress": 100,
            "message": "Sessão anterior restaurada.",
            "status": "complete",
            "filename": safe_filename,
            "restored": True,
        }
        return {
            "exists": True,
            "status": "restored",
            "message": "Resultados prévios encontrados. Sessão restaurada.",
            "filename": safe_filename,
            "redirect": "/dashboard",
        }

    # Nenhum resultado prévio: estado ocioso aguardando upload
    processing_status = {
        "progress": 0,
        "message": "Nenhum resultado prévio encontrado. Aguardando upload...",
        "status": "idle",
        "filename": safe_filename,
        "restored": False,
    }
    return {
        "exists": False,
        "status": "missing",
        "message": "Nenhum resultado prévio encontrado.",
        "filename": safe_filename,
    }


@app.get("/status")
async def get_status():
    """Retorna o progresso/estado atual do processamento.

    Returns:
        O dicionário global ``processing_status``.
    """
    return processing_status


@app.get("/metadata")
async def get_current_metadata(filename: str | None = None):
    """Busca os metadados da sessão atual (ou de um arquivo específico).

    Se ``filename`` for informado, procura ``{stem}_metadata.json``.
    Caso contrário, usa o filename salvo em ``processing_status`` e, por
    fim, qualquer metadado existente como fallback.

    Args:
        filename: Nome do CZI (opcional).

    Returns:
        Conteúdo do JSON de metadados, ou um erro quando não há metadados.
    """
    target_filename = filename or processing_status.get("filename")

    if target_filename:
        czi_stem = Path(Path(target_filename).name).stem
        metadata_path = OUTPUT_DIR / f"{czi_stem}_metadata.json"

        if metadata_path.exists():
            with open(metadata_path, "r", encoding="utf-8") as f:
                return json.load(f)

    # Fallback: qualquer metadado disponível no diretório de saída
    metadata_files = list(OUTPUT_DIR.glob("*_metadata.json"))
    if metadata_files:
        with open(metadata_files[0], "r", encoding="utf-8") as f:
            return json.load(f)

    return {"error": "Nenhum metadado encontrado"}


@app.post("/upload")
async def handle_upload(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
):
    """Recebe o CZI e inicia o processamento (ou restaura sessão prévia).

    Fluxo:
        1. Higieniza o nome do arquivo.
        2. Se já houver resultados: restaura a sessão (não reprocessa).
        3. Caso contrário: limpa diretórios, salva o arquivo e dispara o
           pipeline em background.

    Args:
        background_tasks: Gerenciador de tarefas em segundo plano do FastAPI.
        file: Arquivo CZI enviado via multipart/form-data.

    Returns:
        Dicionário com o status do disparo (restored/started).
    """
    global processing_status

    safe_filename = Path(file.filename).name     # higieniza o nome

    # Estado intermediário: verificando resultados prévios
    processing_status = {
        "progress": 0,
        "message": "Verificando resultados prévios...",
        "status": "checking",
        "filename": safe_filename,
        "restored": False,
    }

    # 1. Há resultados significativos para este arquivo? -> restaura
    if check_existing_results_for_file(safe_filename):
        processing_status = {
            "progress": 100,
            "message": "Sessão anterior restaurada.",
            "status": "complete",
            "filename": safe_filename,
            "restored": True,
        }
        return {
            "status": "restored",
            "message": "Resultados prévios encontrados. Sessão restaurada.",
            "filename": safe_filename,
            "redirect": "/dashboard",
        }

    # 2. Sem resultados: prepara o ambiente (limpeza)
    processing_status = {
        "progress": 0,
        "message": "Nenhum resultado prévio encontrado. Limpando diretórios...",
        "status": "running",
        "filename": safe_filename,
        "restored": False,
    }
    clear_directory(UPLOAD_DIR)
    clear_directory(OUTPUT_DIR)

    # 3. Salva o novo arquivo após a limpeza
    # OBS.: copyfileobj é bloqueante; para CZIs grandes prefira leitura em
    # chunks/threadpool para não travar o event loop (ver MELHORIAS.md).
    file_path = UPLOAD_DIR / safe_filename
    with file_path.open("wb") as buffer:
        shutil.copyfileobj(file.file, buffer)

    # Atualiza o status para "processando"
    processing_status["message"] = "Arquivo recebido. Iniciando processamento..."
    processing_status["progress"] = 0
    processing_status["status"] = "running"

    # 4. Dispara o pipeline em background (passa o mesmo dict de status)
    background_tasks.add_task(
        workflow.main,
        str(file_path.absolute()),
        processing_status,
    )

    return {
        "status": "started",
        "message": "Novo processamento iniciado.",
        "filename": safe_filename,
    }


@app.get("/manifest")
async def get_manifest():
    """Serve o manifesto da grade para o visualizador (React).

    Returns:
        Conteúdo de ``manifest.json``, ou lista vazia se ainda não existir.
    """
    manifest_path = OUTPUT_DIR / "manifest.json"
    if manifest_path.exists():
        with open(manifest_path, "r", encoding="utf-8") as f:
            return json.load(f)
    return []


@app.get("/petrophysics")
async def get_petrophysics():
    """Serve os resultados quantitativos para o componente Dashboard.

    Returns:
        Conteúdo de ``petrophysical_results.json``, ou erro se inexistente.
    """
    results_path = OUTPUT_DIR / "petrophysical_results.json"
    if results_path.exists():
        with open(results_path, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"error": "No data available"}


@app.post("/reset")
async def reset_session():
    """Apaga saídas e uploads e retorna o servidor ao estado ocioso.

    Returns:
        ``{"status": "cleared"}`` em sucesso ou um erro descritivo.
    """
    global processing_status
    try:
        clear_directory(UPLOAD_DIR)
        clear_directory(OUTPUT_DIR)

        processing_status = {
            "progress": 0,
            "message": "Aguardando início...",
            "status": "idle",
            "filename": None,
            "restored": False,
        }
        return {"status": "cleared"}
    except Exception as e:
        return {"status": "error", "message": str(e)}
