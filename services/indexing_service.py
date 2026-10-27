"""Service: indexação, em segundo plano, dos PDFs enviados pela tela /upload.

Os PDFs são salvos em UPLOAD_DIR e entram numa fila; uma única thread de
trabalho indexa um por vez (Docling e os modelos do Ollama são pesados, e
dois ao mesmo tempo só disputariam CPU/GPU) com o mesmo código da CLI
(`rag_papers.index_pdf`), mas gravando pelo HttpClient do servidor Chroma —
nunca abrindo a pasta `.chromadb` direto, que o servidor já tem aberta (ver
get_collection em rag_papers.py). O estado de cada envio (job) fica em
memória do processo: some num restart do servidor.
"""
import queue
import re
import threading
import time
import unicodedata
import uuid
from pathlib import Path

from factories import ChromaClientFactory
from models import UPLOAD_DIR, refresh_filter_fields_cache

_PDF_MAGIC = b"%PDF-"
# \w em Python é Unicode: acentos ficam ("Análise de Fourier.pdf" continua legível).
_UNSAFE_CHARS_RE = re.compile(r"[^\w\-. ()]+")
_PENDING = ("queued", "running")


def _safe_filename(name):
    """Nome de arquivo seguro pra gravar em UPLOAD_DIR: sem caminho (nada de
    "../" ou "C:\\"), só letras/dígitos/espaço/-_.() e sempre terminando em .pdf.
    Vira também o `source` do documento na base (o nome exibido nas telas)."""
    base = Path((name or "").replace("\\", "/")).name
    stem = unicodedata.normalize("NFC", Path(base).stem)
    stem = _UNSAFE_CHARS_RE.sub("_", stem).strip(" ._") or "documento"
    return f"{stem[:150]}.pdf"


class IndexingService:
    _lock = threading.Lock()
    # Uma escrita na collection por vez: indexação de um PDF (worker) ou
    # exclusão de um documento. Excluir no meio de uma indexação falhava
    # (as duas mexem nos trechos e no cache de metadados ao mesmo tempo).
    _write_lock = threading.Lock()
    _jobs = {}            # job_id -> estado do envio (ver _new_job)
    _order = []           # job_ids, do mais recente pro mais antigo
    _queue = queue.Queue()
    _worker = None
    _pipeline = None      # rag_papers.build_pipeline(), montado no 1º envio

    # --- envio ---------------------------------------------------------------

    @classmethod
    def submit(cls, files):
        """Valida e grava os arquivos enviados (FileStorage do Flask) e os põe
        na fila. Devolve (jobs criados, rejeitados com o motivo)."""
        UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
        jobs, rejected = [], []
        for storage in files:
            original = storage.filename or ""
            if not original.lower().endswith(".pdf"):
                rejected.append({"file": original, "reason": "Não é um arquivo .pdf."})
                continue
            head = storage.stream.read(len(_PDF_MAGIC))
            storage.stream.seek(0)
            if head != _PDF_MAGIC:
                rejected.append({"file": original, "reason": "O conteúdo não é de um PDF."})
                continue

            name = _safe_filename(original)
            with cls._lock:
                # Regravar o arquivo enquanto ele é indexado corromperia a indexação.
                pending = any(j["file"] == name and j["status"] in _PENDING for j in cls._jobs.values())
            if pending:
                rejected.append({"file": original, "reason": "Esse PDF já está na fila de indexação."})
                continue

            path = UPLOAD_DIR / name
            storage.save(path)
            job = cls._new_job(name, path)
            jobs.append(cls._public(job))
            cls._queue.put(job["id"])

        if jobs:
            cls._ensure_worker()
        return jobs, rejected

    @classmethod
    def list_jobs(cls):
        with cls._lock:
            return [cls._public(cls._jobs[job_id]) for job_id in cls._order]

    # --- exclusão ---------------------------------------------------------------

    @classmethod
    def remove_document(cls, source):
        """Tira da base todos os trechos do PDF `source` (texto, figuras e
        equações) e apaga as imagens das figuras dele — o caminho pra
        reindexar um PDF que já está na base com o mesmo conteúdo (o envio
        dele seria pulado, ver is_indexed). O arquivo em UPLOAD_DIR fica.
        Devolve (nº de trechos removidos, erro)."""
        source = (source or "").strip()
        if not source:
            return 0, "Informe o documento."
        if cls.indexing_in_progress() or not cls._write_lock.acquire(blocking=False):
            return 0, "Há uma indexação em andamento: espere terminar para excluir documentos."
        try:
            return cls._remove_document(source)
        finally:
            cls._write_lock.release()

    @classmethod
    def indexing_in_progress(cls):
        """Se algum PDF está na fila ou sendo indexado agora."""
        with cls._lock:
            return any(j["status"] in _PENDING for j in cls._jobs.values())

    @classmethod
    def _remove_document(cls, source):
        import rag_papers
        col = cls._collection()
        got = col.get(where={"source": source}, include=["metadatas"])
        ids = got.get("ids") or []
        if not ids:
            return 0, "Esse documento não está na base."
        figures = {m.get("figure_path") for m in got.get("metadatas") or [] if m and m.get("figure_path")}

        col.delete(where={"source": source})
        refresh_filter_fields_cache(col)

        # Só imagens dentro da pasta de figuras do indexador, nunca um caminho qualquer vindo da base.
        root = rag_papers.FIGURES_DIR.resolve()
        for path in figures:
            target = Path(path).resolve()
            if target.parent == root:
                target.unlink(missing_ok=True)
        return len(ids), None

    # --- estado dos jobs --------------------------------------------------------

    @classmethod
    def _new_job(cls, name, path):
        job = {
            "id": uuid.uuid4().hex, "file": name, "path": path, "status": "queued",
            "message": "Na fila", "chunks": None,
            "created": time.time(), "started": None, "finished": None,
        }
        with cls._lock:
            cls._jobs[job["id"]] = job
            cls._order.insert(0, job["id"])
        return job

    @classmethod
    def _update(cls, job_id, **fields):
        with cls._lock:
            cls._jobs[job_id].update(fields)

    @staticmethod
    def _public(job):
        """O que vai pro front-end (sem o caminho no disco do servidor)."""
        end = job["finished"] or time.time()
        elapsed = round(end - job["started"], 1) if job["started"] else None
        return {key: job[key] for key in ("id", "file", "status", "message", "chunks")} | {"elapsed": elapsed}

    # --- trabalho em segundo plano -----------------------------------------------

    @classmethod
    def _ensure_worker(cls):
        with cls._lock:
            if cls._worker is None or not cls._worker.is_alive():
                cls._worker = threading.Thread(target=cls._run, name="indexing-worker", daemon=True)
                cls._worker.start()

    @staticmethod
    def _collection():
        import rag_papers
        ef = rag_papers.BGEEmbeddingFunction(model_name=rag_papers.EMBED_MODEL)
        return rag_papers.get_collection(ef, create=True, client=ChromaClientFactory.create())

    @classmethod
    def _run(cls):
        import rag_papers

        while True:
            job_id = cls._queue.get()
            with cls._lock:
                path = cls._jobs[job_id]["path"]

            def log(message, job_id=job_id):
                print(f"[upload] {message}")
                cls._update(job_id, message=message)

            cls._update(job_id, status="running", started=time.time(), message="Preparando a indexação")
            cls._write_lock.acquire()  # espera uma exclusão que já tenha começado
            try:
                col = cls._collection()
                if rag_papers.is_indexed(col, path):
                    cls._update(job_id, status="skipped",
                                message="Esse PDF já está na base, sem alterações. Para reindexá-lo, "
                                        "exclua-o da lista ao lado e envie de novo.")
                    continue
                if cls._pipeline is None:
                    log("Carregando o Docling e os modelos (só no primeiro envio)")
                    cls._pipeline = rag_papers.build_pipeline()
                chunks = rag_papers.index_pdf(col, path, cls._pipeline, log=log)
                log("Atualizando a lista de documentos")
                refresh_filter_fields_cache(col)
                cls._update(job_id, status="done", chunks=chunks, message=f"{chunks} trechos indexados")
            except Exception as exc:
                message = f"Falha na indexação: {exc}"
                if "Chroma server" in str(exc):
                    message = ("Falha na indexação: o servidor do Chroma não está no ar. "
                               "Suba-o com: chroma run --path ./.chromadb --port 8000")
                cls._update(job_id, status="error", message=message)
            finally:
                cls._write_lock.release()
                cls._update(job_id, finished=time.time())
