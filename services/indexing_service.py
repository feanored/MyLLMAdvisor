"""Service: indexação, em segundo plano, dos PDFs enviados pela tela /upload.

Cada usuário tem a sua base (ver UserStorage): os PDFs dele são salvos na
pasta dele e indexados na collection dele, com as figuras na pasta de
figuras dele; a lista de envios e a exclusão só alcançam os dele.

Os envios entram numa fila única; uma só thread de trabalho indexa um PDF por
vez (Docling e os modelos do Ollama são pesados, e dois ao mesmo tempo só
disputariam CPU/GPU) com o mesmo código da CLI (`rag_papers.index_pdf`), mas
gravando pelo HttpClient do servidor Chroma — nunca abrindo a pasta
`.chromadb` direto, que o servidor já tem aberta (ver get_collection em
rag_papers.py). O estado de cada envio (job) fica em memória do processo:
some num restart do servidor.
"""
import queue
import re
import shutil
import threading
import time
import unicodedata
import uuid
from pathlib import Path

from factories import ChromaClientFactory
from models import DEFAULT_BASE, iter_collection_metadatas, refresh_filter_fields_cache

from .user_storage import UserStorage

_PDF_MAGIC = b"%PDF-"
# \w em Python é Unicode: acentos ficam ("Análise de Fourier.pdf" continua legível).
_UNSAFE_CHARS_RE = re.compile(r"[^\w\-. ()]+")
_PENDING = ("queued", "running")
_ADOPT_BATCH = 200  # trechos copiados por vez em adopt_collection


def _safe_filename(name):
    """Nome de arquivo seguro pra gravar na pasta do usuário: sem caminho
    (nada de "../" ou "C:\\"), só letras/dígitos/espaço/-_.() e sempre
    terminando em .pdf. Vira também o `source` do documento na base (o nome
    exibido nas telas)."""
    base = Path((name or "").replace("\\", "/")).name
    stem = unicodedata.normalize("NFC", Path(base).stem)
    stem = _UNSAFE_CHARS_RE.sub("_", stem).strip(" ._") or "documento"
    return f"{stem[:150]}.pdf"


class IndexingService:
    _lock = threading.Lock()
    # Uma escrita por vez na collection de cada usuário: indexação de um PDF
    # (worker) ou exclusão de um documento. Excluir no meio de uma indexação
    # falhava (as duas mexem nos trechos e no cache de metadados ao mesmo tempo).
    _write_locks = {}     # user_id -> threading.Lock
    _jobs = {}            # job_id -> estado do envio (ver _new_job)
    _order = []           # job_ids, do mais recente pro mais antigo
    _queue = queue.Queue()
    _worker = None
    _pipeline = None      # rag_papers.build_pipeline(), montado no 1º envio

    # --- envio ---------------------------------------------------------------

    @classmethod
    def submit(cls, user_id, files):
        """Valida e grava na pasta do usuário os arquivos enviados
        (FileStorage do Flask) e os põe na fila. Devolve (jobs criados,
        rejeitados com o motivo)."""
        folder = UserStorage.upload_dir(user_id)
        folder.mkdir(parents=True, exist_ok=True)
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
                pending = any(j["user_id"] == user_id and j["file"] == name and j["status"] in _PENDING
                              for j in cls._jobs.values())
            if pending:
                rejected.append({"file": original, "reason": "Esse PDF já está na fila de indexação."})
                continue

            path = folder / name
            storage.save(path)
            job = cls._new_job(user_id, name, path)
            jobs.append(cls._public(job))
            cls._queue.put(job["id"])

        if jobs:
            cls._ensure_worker()
        return jobs, rejected

    @classmethod
    def list_jobs(cls, user_id):
        """Os envios do usuário desde que o servidor subiu, do mais recente pro mais antigo."""
        with cls._lock:
            return [cls._public(cls._jobs[job_id]) for job_id in cls._order
                    if cls._jobs[job_id]["user_id"] == user_id]

    # --- exclusão ---------------------------------------------------------------

    @classmethod
    def remove_document(cls, user_id, source):
        """Tira da base do usuário todos os trechos do PDF `source` (texto,
        figuras e equações) e apaga as imagens das figuras dele — o caminho
        pra reindexar um PDF que já está na base com o mesmo conteúdo (o
        envio dele seria pulado, ver is_indexed). O arquivo enviado fica.
        Devolve (nº de trechos removidos, erro)."""
        source = (source or "").strip()
        if not source:
            return 0, "Informe o documento."
        lock = cls._write_lock(user_id)
        if cls.indexing_in_progress(user_id) or not lock.acquire(blocking=False):
            return 0, "Há uma indexação em andamento: espere terminar para excluir documentos."
        try:
            return cls._remove_document(user_id, source)
        finally:
            lock.release()

    @classmethod
    def indexing_in_progress(cls, user_id):
        """Se algum PDF do usuário está na fila ou sendo indexado agora."""
        with cls._lock:
            return any(j["user_id"] == user_id and j["status"] in _PENDING for j in cls._jobs.values())

    @classmethod
    def _remove_document(cls, user_id, source):
        col = cls._collection(user_id)
        got = col.get(where={"source": source}, include=["metadatas"])
        ids = got.get("ids") or []
        if not ids:
            return 0, "Esse documento não está na base."
        figures = {m.get("figure_path") for m in got.get("metadatas") or [] if m and m.get("figure_path")}

        col.delete(where={"source": source})
        refresh_filter_fields_cache(col)

        # Só imagens dentro da pasta de figuras do usuário, nunca um caminho qualquer vindo da base.
        root = UserStorage.figures_dir(user_id).resolve()
        for path in figures:
            target = Path(path).resolve()
            if target.parent == root:
                target.unlink(missing_ok=True)
        return len(ids), None

    # --- base sem dono -------------------------------------------------------------

    @classmethod
    def adopt_collection(cls, user_id, source_name=DEFAULT_BASE):
        """Passa ao usuário os documentos de uma collection sem dono (a do
        `rag_papers.py index` de linha de comando, ou a base única de antes
        das contas): copia os trechos, com os embeddings já calculados (nada
        é reindexado), para a collection dele, move as imagens das figuras e
        os PDFs para as pastas dele e só então apaga a collection de origem.
        Um PDF de mesmo nome que o usuário já tenha é substituído. Usado por
        `manage_users.py adopt-documents`. Devolve (nº de PDFs, nº de trechos)."""
        import rag_papers
        client = ChromaClientFactory.create()
        if source_name not in {c.name for c in client.list_collections()}:
            raise LookupError(f'não existe a base "{source_name}" (nada a passar).')
        ef = rag_papers.BGEEmbeddingFunction(model_name=rag_papers.EMBED_MODEL)
        source = rag_papers.get_collection(ef, name=source_name, create=False, client=client)
        target = rag_papers.get_collection(ef, name=UserStorage.collection(user_id), create=True, client=client)
        figures_dir = UserStorage.figures_dir(user_id)
        upload_dir = UserStorage.upload_dir(user_id)
        figures_dir.mkdir(parents=True, exist_ok=True)
        upload_dir.mkdir(parents=True, exist_ok=True)

        sources = {m.get("source") for m in iter_collection_metadatas(source) if m and m.get("source")}
        for name in sources:
            target.delete(where={"source": name})

        chunks, offset = 0, 0
        while True:
            got = source.get(include=["documents", "metadatas", "embeddings"], limit=_ADOPT_BATCH, offset=offset)
            ids = got.get("ids") or []
            if not ids:
                break
            metadatas = []
            for meta in got["metadatas"]:
                meta = dict(meta or {})
                if meta.get("figure_path"):
                    meta["figure_path"] = cls._move_into(meta["figure_path"], figures_dir)
                if meta.get("source_path"):
                    meta["source_path"] = cls._move_into(meta["source_path"], upload_dir)
                metadatas.append(meta)
            target.upsert(ids=ids, documents=got["documents"], metadatas=metadatas, embeddings=got["embeddings"])
            chunks += len(ids)
            offset += len(ids)
            if len(ids) < _ADOPT_BATCH:
                break

        refresh_filter_fields_cache(target)
        client.delete_collection(name=source_name)
        return len(sources), chunks

    @staticmethod
    def delete_user_documents(user_id):
        """Apaga a base do usuário, os PDFs que ele enviou e as imagens das
        figuras (conta apagada pelo manage_users.py)."""
        client = ChromaClientFactory.create()
        name = UserStorage.collection(user_id)
        if name in {c.name for c in client.list_collections()}:
            client.delete_collection(name=name)
        for folder in (UserStorage.upload_dir(user_id), UserStorage.figures_dir(user_id)):
            shutil.rmtree(folder, ignore_errors=True)

    @staticmethod
    def _move_into(path, folder):
        """Move o arquivo `path` para `folder`, se ele ainda estiver onde a
        base diz (vários trechos apontam para o mesmo PDF), e devolve o
        caminho novo."""
        old = Path(path)
        new = folder / old.name
        if old.is_file() and old.resolve() != new.resolve():
            old.replace(new)
        return str(new)

    # --- estado dos jobs --------------------------------------------------------

    @classmethod
    def _new_job(cls, user_id, name, path):
        job = {
            "id": uuid.uuid4().hex, "user_id": user_id, "file": name, "path": path, "status": "queued",
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
        """O que vai pro front-end (sem o dono nem o caminho no disco do servidor)."""
        end = job["finished"] or time.time()
        elapsed = round(end - job["started"], 1) if job["started"] else None
        return {key: job[key] for key in ("id", "file", "status", "message", "chunks")} | {"elapsed": elapsed}

    # --- trabalho em segundo plano -----------------------------------------------

    @classmethod
    def _write_lock(cls, user_id):
        with cls._lock:
            return cls._write_locks.setdefault(user_id, threading.Lock())

    @classmethod
    def _ensure_worker(cls):
        with cls._lock:
            if cls._worker is None or not cls._worker.is_alive():
                cls._worker = threading.Thread(target=cls._run, name="indexing-worker", daemon=True)
                cls._worker.start()

    @staticmethod
    def _collection(user_id):
        import rag_papers
        ef = rag_papers.BGEEmbeddingFunction(model_name=rag_papers.EMBED_MODEL)
        return rag_papers.get_collection(ef, name=UserStorage.collection(user_id), create=True,
                                         client=ChromaClientFactory.create())

    @classmethod
    def _run(cls):
        import rag_papers

        while True:
            job_id = cls._queue.get()
            with cls._lock:
                user_id, path = cls._jobs[job_id]["user_id"], cls._jobs[job_id]["path"]

            def log(message, job_id=job_id):
                print(f"[upload] {message}")
                cls._update(job_id, message=message)

            cls._update(job_id, status="running", started=time.time(), message="Preparando a indexação")
            lock = cls._write_lock(user_id)
            lock.acquire()  # espera uma exclusão que já tenha começado
            try:
                col = cls._collection(user_id)
                if rag_papers.is_indexed(col, path):
                    cls._update(job_id, status="skipped",
                                message="Esse PDF já está na base, sem alterações. Para reindexá-lo, "
                                        "exclua-o da lista ao lado e envie de novo.")
                    continue
                if cls._pipeline is None:
                    log("Carregando o Docling e os modelos")
                    cls._pipeline = rag_papers.build_pipeline()
                chunks = rag_papers.index_pdf(col, path, cls._pipeline, log=log,
                                              figures_dir=UserStorage.figures_dir(user_id))
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
                lock.release()
                cls._update(job_id, finished=time.time())
