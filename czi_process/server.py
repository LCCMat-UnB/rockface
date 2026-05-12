from fastapi import FastAPI, BackgroundTasks, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pathlib import Path
import shutil
import json
import main as workflow

app = FastAPI()

# CORS configuration for Vite/React frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Directory constants
UPLOAD_DIR = Path("./uploads")
OUTPUT_DIR = Path("./output_project")

# Ensure directories exist on startup
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

# Mount the output folder as a static route to serve images and masks
app.mount("/results", StaticFiles(directory=str(OUTPUT_DIR)), name="results")


def has_files(directory: Path) -> bool:
    """
    Verifica se uma pasta existe e possui ao menos um arquivo.
    Usa rglob para também detectar arquivos em subpastas.
    """
    return directory.exists() and any(path.is_file() for path in directory.rglob("*"))


def is_valid_nonempty_json(json_path: Path) -> bool:
    """
    Verifica se o arquivo JSON existe, não está vazio e contém um JSON válido.
    """
    if not json_path.exists():
        return False

    if json_path.stat().st_size == 0:
        return False

    try:
        with open(json_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        return bool(data)

    except Exception:
        return False


def check_existing_results_for_file(filename: str) -> bool:
    """
    Verifica se já existem resultados significativos associados ao arquivo CZI.

    Critérios:
    1. OUTPUT_DIR/patches deve existir e conter arquivos.
    2. OUTPUT_DIR/masks deve existir e conter arquivos.
    3. Deve existir um arquivo:
       {nome_do_CZI_sem_extensao}_metadata.json
       não vazio e com JSON válido.
    """
    safe_filename = Path(filename).name
    czi_stem = Path(safe_filename).stem

    patches_dir = OUTPUT_DIR / "patches"
    masks_dir = OUTPUT_DIR / "masks"
    metadata_path = OUTPUT_DIR / f"{czi_stem}_metadata.json"

    has_patches = has_files(patches_dir)
    has_masks = has_files(masks_dir)
    has_metadata = is_valid_nonempty_json(metadata_path)

    return has_patches and has_masks and has_metadata


def clear_directory(directory: Path):
    """
    Limpa completamente uma pasta, mas recria a pasta ao final.
    """
    if directory.exists():
        shutil.rmtree(directory)

    directory.mkdir(parents=True, exist_ok=True)


# Initial Global State
processing_status = {
    "progress": 0,
    "message": "Aguardando início...",
    "status": "idle",
    "filename": None,
    "restored": False,
}

@app.get("/check-results")
async def check_results(filename: str):
    global processing_status

    safe_filename = Path(filename).name

    if check_existing_results_for_file(safe_filename):
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
    """
    Returns the current processing progress or restoration status.
    """
    return processing_status


@app.get("/metadata")
async def get_current_metadata(filename: str | None = None):
    """
    Busca os metadados da sessão atual.

    Se filename for informado, busca:
    {filename_sem_extensao}_metadata.json

    Caso contrário, usa o filename salvo em processing_status.
    """
    target_filename = filename or processing_status.get("filename")

    if target_filename:
        czi_stem = Path(Path(target_filename).name).stem
        metadata_path = OUTPUT_DIR / f"{czi_stem}_metadata.json"

        if metadata_path.exists():
            with open(metadata_path, "r", encoding="utf-8") as f:
                return json.load(f)

    # Fallback: procura qualquer metadata existente
    metadata_files = list(OUTPUT_DIR.glob("*_metadata.json"))

    if metadata_files:
        with open(metadata_files[0], "r", encoding="utf-8") as f:
            return json.load(f)

    return {"error": "Nenhum metadado encontrado"}


@app.post("/upload")
async def handle_upload(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...)
):
    """
    Fluxo implementado:

    1. Recebe o nome do arquivo submetido.
    2. Verifica se já existem resultados associados a esse arquivo.
    3. Se existirem:
       - restaura a sessão;
       - não limpa diretórios;
       - não dispara o main.py;
       - retorna status restored para o frontend redirecionar.
    4. Se não existirem:
       - limpa uploads e OUTPUT_DIR;
       - salva o novo arquivo;
       - dispara o main.py.
    """
    global processing_status

    safe_filename = Path(file.filename).name

    processing_status = {
        "progress": 0,
        "message": "Verificando resultados prévios...",
        "status": "checking",
        "filename": safe_filename,
        "restored": False,
    }

    # 1. Verifica se já existem resultados significativos para esse arquivo
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

    # 2. Se não houver resultados significativos, limpa antes de processar
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
    file_path = UPLOAD_DIR / safe_filename

    with file_path.open("wb") as buffer:
        shutil.copyfileobj(file.file, buffer)

    processing_status["message"] = "Arquivo recebido. Iniciando processamento..."
    processing_status["progress"] = 0
    processing_status["status"] = "running"

    # 4. Dispara o workflow apenas quando não há resultados prévios
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
    """
    Serves the grid manifest for the React ImageViewer.
    """
    manifest_path = OUTPUT_DIR / "manifest.json"

    if manifest_path.exists():
        with open(manifest_path, "r", encoding="utf-8") as f:
            return json.load(f)

    return []


@app.get("/petrophysics")
async def get_petrophysics():
    """
    Serves the quantitative results for the Dashboard component.
    """
    results_path = OUTPUT_DIR / "petrophysical_results.json"

    if results_path.exists():
        with open(results_path, "r", encoding="utf-8") as f:
            return json.load(f)

    return {"error": "No data available"}


@app.post("/reset")
async def reset_session():
    """
    Deletes current output files and uploads, then resets the server to idle state.
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
        return {
            "status": "error",
            "message": str(e),
        }