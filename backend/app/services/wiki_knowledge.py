from __future__ import annotations

import hashlib
import json
import math
import os
import re
import sqlite3
import threading
from contextlib import closing
from dataclasses import asdict, dataclass
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Any
from urllib.parse import quote
from uuid import uuid4

import yaml

from app.config import WikiConfig


_FRONTMATTER = re.compile(r"\A---\s*\r?\n(.*?)\r?\n---\s*\r?\n", re.DOTALL)
_WIKILINK = re.compile(r"\[\[([^\]|#]+)(?:#[^\]|]+)?(?:\|[^\]]+)?\]\]")
_HEADING = re.compile(r"^(#{1,3})\s+(.+?)\s*$")
_DATAVIEW_BLOCK = re.compile(r"```dataview.*?```", re.IGNORECASE | re.DOTALL)
_CJK_SEQUENCE = re.compile(r"[\u3400-\u9fff]{2,}")
_ASCII_TOKEN = re.compile(r"[a-zA-Z_][a-zA-Z0-9_.-]{2,}")
_EXCLUDED_TYPES = {"moc", "dashboard", "guide"}
_EXCLUDED_HEADINGS = {"图谱关系", "相关笔记", "面试可讲表达", "我还没搞懂的地方"}
_QUERY_FILLERS = (
    "请问", "帮我", "告诉我", "介绍一下", "为什么", "有什么区别", "怎么计算",
    "如何计算", "怎么来的", "如何工作", "哪些", "什么是", "是什么",
)


class WikiKnowledgeError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class WikiStatus:
    available: bool
    root_path: str
    index_path: str
    note_count: int
    chunk_count: int
    indexed_at: str | None
    source_fingerprint: str | None
    tokenizer: str | None
    allowed_statuses: list[str]
    error_type: str | None = None


@dataclass(frozen=True, slots=True)
class WikiHit:
    note_id: str
    title: str
    heading: str
    excerpt: str
    content: str
    relative_path: str
    absolute_path: str
    obsidian_uri: str
    status: str
    note_type: str
    module: str | None
    topic: str | None
    updated: str | None
    score: float
    via_wikilink: bool = False

    def citation(self) -> dict[str, Any]:
        payload = asdict(self)
        payload.pop("content", None)
        return payload


@dataclass(frozen=True, slots=True)
class WikiSearchResult:
    query: str
    index_fingerprint: str
    hits: list[WikiHit]

    def context(self, *, max_chars: int = 8_000) -> str:
        blocks: list[str] = []
        used = 0
        for index, hit in enumerate(self.hits, start=1):
            block = (
                f"[知识来源 {index}]\n"
                f"标题：{hit.title}\n"
                f"章节：{hit.heading}\n"
                f"路径：{hit.relative_path}\n"
                f"内容：\n{hit.content[:2200]}"
            )
            if used + len(block) > max_chars:
                break
            blocks.append(block)
            used += len(block)
        return "\n\n".join(blocks)

    @property
    def citations(self) -> list[dict[str, Any]]:
        return [hit.citation() for hit in self.hits]


def _scalar(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, (str, int, float, bool)):
        text = str(value).strip()
        return text or None
    return None


def _strings(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, (list, tuple, set)):
        return [str(item) for item in value if _scalar(item)]
    if isinstance(value, dict):
        result: list[str] = []
        for key, item in value.items():
            result.append(str(key))
            result.extend(_strings(item))
        return result
    return []


def _parse_note(path: Path, root: Path) -> dict[str, Any] | None:
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return None
    match = _FRONTMATTER.match(text)
    if not match:
        return None
    try:
        metadata = yaml.safe_load(match.group(1)) or {}
    except yaml.YAMLError:
        return None
    if not isinstance(metadata, dict):
        return None
    body = text[match.end() :]
    title_match = re.search(r"^#\s+(.+?)\s*$", body, re.MULTILINE)
    title = title_match.group(1).strip() if title_match else path.stem
    relative_path = path.relative_to(root).as_posix()
    links = sorted({item.strip() for item in _WIKILINK.findall(body) if item.strip()})
    return {
        "metadata": metadata,
        "body": body,
        "title": title,
        "relative_path": relative_path,
        "links": links,
    }


def _clean_markdown(value: str) -> str:
    value = _DATAVIEW_BLOCK.sub("", value)
    value = re.sub(r"```(?:[\w.+-]+)?\s*\n", "", value)
    value = value.replace("```", "")
    value = re.sub(r"!\[([^]]*)\]\([^)]*\)", r"\1", value)
    value = re.sub(r"\[([^]]+)\]\([^)]*\)", r"\1", value)
    value = re.sub(r"\[\[([^\]|#]+)(?:#[^\]|]+)?(?:\|([^\]]+))?\]\]", lambda m: m.group(2) or m.group(1), value)
    value = re.sub(r"^[>-]\s*", "", value, flags=re.MULTILINE)
    value = re.sub(r"\n{3,}", "\n\n", value)
    return value.strip()


def _split_long_chunk(value: str, *, limit: int = 2600) -> list[str]:
    if len(value) <= limit:
        return [value]
    paragraphs = re.split(r"\n\s*\n", value)
    chunks: list[str] = []
    buffer = ""
    for paragraph in paragraphs:
        candidate = f"{buffer}\n\n{paragraph}".strip()
        if buffer and len(candidate) > limit:
            chunks.append(buffer)
            buffer = paragraph
        else:
            buffer = candidate
    if buffer:
        chunks.append(buffer)
    return chunks


def _note_chunks(body: str) -> list[tuple[str, str]]:
    chunks: list[tuple[str, str]] = []
    heading = "概述"
    lines: list[str] = []
    excluded = False

    def flush() -> None:
        nonlocal lines
        if excluded:
            lines = []
            return
        content = _clean_markdown("\n".join(lines))
        lines = []
        if len(content) < 40:
            return
        for piece in _split_long_chunk(content):
            chunks.append((heading, piece))

    for line in body.splitlines():
        match = _HEADING.match(line)
        if match:
            level = len(match.group(1))
            if level == 1:
                continue
            flush()
            heading = match.group(2).strip()
            excluded = heading in _EXCLUDED_HEADINGS
            continue
        lines.append(line)
    flush()
    return chunks


def _character_grams(value: str) -> set[str]:
    normalized = re.sub(r"\s+", "", value.casefold())
    grams: set[str] = set(_ASCII_TOKEN.findall(normalized))
    for sequence in _CJK_SEQUENCE.findall(normalized):
        if len(sequence) == 2:
            grams.add(sequence)
        else:
            grams.update(sequence[index : index + 2] for index in range(len(sequence) - 1))
            grams.update(sequence[index : index + 3] for index in range(len(sequence) - 2))
    return grams


def _searchable_query(value: str) -> str:
    result = value.casefold()
    for filler in _QUERY_FILLERS:
        result = result.replace(filler, " ")
    return result.strip() or value


def _fts_query(value: str, tokenizer: str | None) -> str:
    terms: set[str] = set(_ASCII_TOKEN.findall(value.casefold()))
    for sequence in _CJK_SEQUENCE.findall(value):
        if tokenizer == "trigram" and len(sequence) >= 3:
            terms.update(sequence[index : index + 3] for index in range(len(sequence) - 2))
        else:
            terms.add(sequence)
    escaped = [f'"{term.replace(chr(34), chr(34) * 2)}"' for term in sorted(terms) if term]
    return " OR ".join(escaped[:48])


class WikiKnowledgeService:
    def __init__(self, config: WikiConfig) -> None:
        self._config = config
        self._lock = threading.RLock()

    def _source_files(self) -> list[Path]:
        source_root = self._config.root_path / "01_Odoo"
        if not source_root.is_dir():
            return []
        return sorted(
            path
            for path in source_root.rglob("*.md")
            if not any(part.startswith(".") for part in path.relative_to(source_root).parts)
        )

    def _fingerprint(self, files: list[Path]) -> str:
        digest = hashlib.sha256()
        for path in files:
            stat = path.stat()
            digest.update(path.relative_to(self._config.root_path).as_posix().encode("utf-8"))
            digest.update(str(stat.st_size).encode("ascii"))
            digest.update(str(stat.st_mtime_ns).encode("ascii"))
        return digest.hexdigest()

    @staticmethod
    def _connect(path: Path) -> sqlite3.Connection:
        connection = sqlite3.connect(path)
        connection.row_factory = sqlite3.Row
        return connection

    @staticmethod
    def _meta(connection: sqlite3.Connection) -> dict[str, str]:
        try:
            rows = connection.execute("SELECT key, value FROM meta").fetchall()
        except sqlite3.DatabaseError:
            return {}
        return {str(row["key"]): str(row["value"]) for row in rows}

    def ensure_index(self, *, force: bool = False) -> WikiStatus:
        with self._lock:
            files = self._source_files()
            if not files:
                return WikiStatus(
                    available=False,
                    root_path=str(self._config.root_path),
                    index_path=str(self._config.index_path),
                    note_count=0,
                    chunk_count=0,
                    indexed_at=None,
                    source_fingerprint=None,
                    tokenizer=None,
                    allowed_statuses=list(self._config.allowed_statuses),
                    error_type="WikiDirectoryNotFound",
                )
            fingerprint = self._fingerprint(files)
            if self._config.index_path.exists() and not force:
                with closing(self._connect(self._config.index_path)) as connection:
                    meta = self._meta(connection)
                if meta.get("source_fingerprint") == fingerprint:
                    return self._status_from_meta(meta)
            return self._rebuild(files, fingerprint)

    def _status_from_meta(self, meta: dict[str, str]) -> WikiStatus:
        return WikiStatus(
            available=True,
            root_path=str(self._config.root_path),
            index_path=str(self._config.index_path),
            note_count=int(meta.get("note_count", 0)),
            chunk_count=int(meta.get("chunk_count", 0)),
            indexed_at=meta.get("indexed_at"),
            source_fingerprint=meta.get("source_fingerprint"),
            tokenizer=meta.get("tokenizer"),
            allowed_statuses=list(self._config.allowed_statuses),
        )

    def _rebuild(self, files: list[Path], fingerprint: str) -> WikiStatus:
        self._config.index_path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = self._config.index_path.with_name(
            f"{self._config.index_path.name}.{os.getpid()}.{uuid4().hex}.tmp"
        )
        tokenizer = "trigram"
        try:
            with closing(self._connect(temp_path)) as connection:
                connection.executescript(
                    """
                    CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                    CREATE TABLE notes (
                        note_id TEXT PRIMARY KEY,
                        title TEXT NOT NULL,
                        relative_path TEXT NOT NULL UNIQUE,
                        status TEXT NOT NULL,
                        note_type TEXT NOT NULL,
                        module TEXT,
                        topic TEXT,
                        updated TEXT,
                        metadata_text TEXT NOT NULL,
                        links_json TEXT NOT NULL
                    );
                    CREATE TABLE chunks (
                        chunk_id TEXT PRIMARY KEY,
                        note_id TEXT NOT NULL,
                        heading TEXT NOT NULL,
                        content TEXT NOT NULL,
                        FOREIGN KEY(note_id) REFERENCES notes(note_id)
                    );
                    """
                )
                try:
                    connection.execute(
                        "CREATE VIRTUAL TABLE chunks_fts USING fts5("
                        "chunk_id UNINDEXED, title, heading, content, metadata_text, "
                        "tokenize='trigram')"
                    )
                except sqlite3.OperationalError:
                    tokenizer = "unicode61"
                    connection.execute(
                        "CREATE VIRTUAL TABLE chunks_fts USING fts5("
                        "chunk_id UNINDEXED, title, heading, content, metadata_text, "
                        "tokenize='unicode61')"
                    )

                note_count = 0
                chunk_count = 0
                for path in files:
                    parsed = _parse_note(path, self._config.root_path)
                    if parsed is None:
                        continue
                    metadata = parsed["metadata"]
                    status = str(metadata.get("status") or "").casefold()
                    note_type = str(metadata.get("type") or "").casefold()
                    if status not in self._config.allowed_statuses or note_type in _EXCLUDED_TYPES:
                        continue
                    note_id = hashlib.sha1(
                        parsed["relative_path"].encode("utf-8"), usedforsecurity=False
                    ).hexdigest()
                    metadata_values = [
                        _scalar(metadata.get("module")),
                        _scalar(metadata.get("topic")),
                        *_strings(metadata.get("tags")),
                        *_strings(metadata.get("aliases")),
                        *_strings(metadata.get("odoo_models")),
                        *_strings(metadata.get("odoo_fields")),
                        *_strings(metadata.get("bi_metrics")),
                    ]
                    metadata_text = " ".join(value for value in metadata_values if value)
                    connection.execute(
                        "INSERT INTO notes VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (
                            note_id,
                            parsed["title"],
                            parsed["relative_path"],
                            status,
                            note_type,
                            _scalar(metadata.get("module")),
                            _scalar(metadata.get("topic")),
                            _scalar(metadata.get("updated")),
                            metadata_text,
                            json.dumps(parsed["links"], ensure_ascii=False),
                        ),
                    )
                    note_count += 1
                    for position, (heading, content) in enumerate(_note_chunks(parsed["body"])):
                        chunk_id = f"{note_id}:{position}"
                        connection.execute(
                            "INSERT INTO chunks VALUES (?, ?, ?, ?)",
                            (chunk_id, note_id, heading, content),
                        )
                        connection.execute(
                            "INSERT INTO chunks_fts VALUES (?, ?, ?, ?, ?)",
                            (chunk_id, parsed["title"], heading, content, metadata_text),
                        )
                        chunk_count += 1

                indexed_at = datetime.now().astimezone().isoformat()
                meta = {
                    "source_fingerprint": fingerprint,
                    "indexed_at": indexed_at,
                    "note_count": str(note_count),
                    "chunk_count": str(chunk_count),
                    "tokenizer": tokenizer,
                }
                connection.executemany(
                    "INSERT INTO meta(key, value) VALUES (?, ?)",
                    list(meta.items()),
                )
                connection.commit()
            temp_path.replace(self._config.index_path)
            return self._status_from_meta(meta)
        except Exception as exc:
            temp_path.unlink(missing_ok=True)
            raise WikiKnowledgeError(type(exc).__name__) from exc

    def status(self) -> WikiStatus:
        try:
            return self.ensure_index()
        except WikiKnowledgeError as exc:
            return WikiStatus(
                available=False,
                root_path=str(self._config.root_path),
                index_path=str(self._config.index_path),
                note_count=0,
                chunk_count=0,
                indexed_at=None,
                source_fingerprint=None,
                tokenizer=None,
                allowed_statuses=list(self._config.allowed_statuses),
                error_type=str(exc),
            )

    def search(self, query: str, *, limit: int | None = None) -> WikiSearchResult:
        with self._lock:
            return self._search_locked(query, limit=limit)

    def _search_locked(self, query: str, *, limit: int | None = None) -> WikiSearchResult:
        status = self.ensure_index()
        if not status.available or not status.source_fingerprint:
            return WikiSearchResult(query=query, index_fingerprint="", hits=[])
        result_limit = max(1, min(limit or self._config.max_results, 12))
        searchable_query = _searchable_query(query)
        query_grams = _character_grams(searchable_query)
        if not query_grams:
            return WikiSearchResult(
                query=query,
                index_fingerprint=status.source_fingerprint,
                hits=[],
            )

        with closing(self._connect(self._config.index_path)) as connection:
            fts_bonus: dict[str, float] = {}
            match_query = _fts_query(searchable_query, status.tokenizer)
            if match_query:
                try:
                    matches = connection.execute(
                        "SELECT chunk_id, bm25(chunks_fts, 0.0, 5.0, 3.0, 1.0, 2.0) AS rank "
                        "FROM chunks_fts WHERE chunks_fts MATCH ? ORDER BY rank LIMIT 100",
                        (match_query,),
                    ).fetchall()
                    for match in matches:
                        fts_bonus[str(match["chunk_id"])] = 2.0 + max(
                            0.0,
                            min(3.0, -float(match["rank"])),
                        )
                except sqlite3.OperationalError:
                    fts_bonus = {}
            rows = connection.execute(
                """
                SELECT
                    chunks.chunk_id,
                    chunks.heading,
                    chunks.content,
                    notes.note_id,
                    notes.title,
                    notes.relative_path,
                    notes.status,
                    notes.note_type,
                    notes.module,
                    notes.topic,
                    notes.updated,
                    notes.metadata_text,
                    notes.links_json
                FROM chunks
                JOIN notes ON notes.note_id = chunks.note_id
                """
            ).fetchall()

            ranked: list[tuple[float, sqlite3.Row]] = []
            for row in rows:
                title_grams = _character_grams(str(row["title"]))
                heading_grams = _character_grams(str(row["heading"]))
                metadata_grams = _character_grams(str(row["metadata_text"]))
                content_grams = _character_grams(str(row["content"]))
                title_overlap = len(query_grams & title_grams)
                heading_overlap = len(query_grams & heading_grams)
                metadata_overlap = len(query_grams & metadata_grams)
                content_overlap = len(query_grams & content_grams)
                exact_bonus = 0.0
                query_lower = query.casefold()
                if str(row["title"]).casefold() in query_lower:
                    exact_bonus += 5.0
                score = (
                    title_overlap * 4.0
                    + heading_overlap * 3.0
                    + metadata_overlap * 2.0
                    + content_overlap
                    + exact_bonus
                    + fts_bonus.get(str(row["chunk_id"]), 0.0)
                ) / math.sqrt(max(len(query_grams), 1))
                if score > 0:
                    ranked.append((score, row))
            ranked.sort(key=lambda item: (-item[0], str(item[1]["relative_path"])))

            selected: list[WikiHit] = []
            seen_notes: set[str] = set()
            for score, row in ranked:
                note_id = str(row["note_id"])
                if note_id in seen_notes:
                    continue
                selected.append(self._hit(row, score=score))
                seen_notes.add(note_id)
                if len(selected) >= result_limit:
                    break

            # Obsidian links carry deliberate knowledge relationships. Add one
            # neighbour when room is available, without displacing direct hits.
            if selected and len(selected) < result_limit:
                link_titles = json.loads(str(ranked[0][1]["links_json"]))
                for link_title in link_titles:
                    neighbour = connection.execute(
                        """
                        SELECT
                            chunks.chunk_id, chunks.heading, chunks.content,
                            notes.note_id, notes.title, notes.relative_path,
                            notes.status, notes.note_type, notes.module, notes.topic,
                            notes.updated, notes.metadata_text, notes.links_json
                        FROM notes
                        JOIN chunks ON chunks.note_id = notes.note_id
                        WHERE notes.title = ?
                        ORDER BY chunks.chunk_id
                        LIMIT 1
                        """,
                        (link_title,),
                    ).fetchone()
                    if neighbour is None or str(neighbour["note_id"]) in seen_notes:
                        continue
                    selected.append(self._hit(neighbour, score=0.01, via_wikilink=True))
                    break

        return WikiSearchResult(
            query=query,
            index_fingerprint=status.source_fingerprint,
            hits=selected,
        )

    def _hit(
        self,
        row: sqlite3.Row,
        *,
        score: float,
        via_wikilink: bool = False,
    ) -> WikiHit:
        relative_path = str(row["relative_path"])
        content = str(row["content"])
        relative_without_suffix = str(Path(relative_path).with_suffix("")).replace("\\", "/")
        obsidian_uri = (
            "obsidian://open?vault="
            + quote(self._config.root_path.name)
            + "&file="
            + quote(relative_without_suffix)
        )
        return WikiHit(
            note_id=str(row["note_id"]),
            title=str(row["title"]),
            heading=str(row["heading"]),
            excerpt=content[:420] + ("…" if len(content) > 420 else ""),
            content=content,
            relative_path=relative_path,
            absolute_path=str(self._config.root_path / relative_path),
            obsidian_uri=obsidian_uri,
            status=str(row["status"]),
            note_type=str(row["note_type"]),
            module=_scalar(row["module"]),
            topic=_scalar(row["topic"]),
            updated=_scalar(row["updated"]),
            score=round(score, 4),
            via_wikilink=via_wikilink,
        )


@lru_cache(maxsize=4)
def get_wiki_service(config: WikiConfig) -> WikiKnowledgeService:
    return WikiKnowledgeService(config)
