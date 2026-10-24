"""Data extraction from local files and optional PDF tables."""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

import pandas as pd
from pydantic import BaseModel, Field


class DataSource(BaseModel):
    """Configuration for a data source."""

    name: str
    type: str  # csv, excel, xlsx, json, pdf, sql, api, mock
    path: str | None = None
    query: str | None = None
    params: dict[str, Any] = Field(default_factory=dict)


class ExtractionResult(BaseModel):
    """Result of data extraction."""

    source_name: str
    dataframe: pd.DataFrame
    metadata: dict[str, Any] = Field(default_factory=dict)
    rows_extracted: int
    columns: list[str]

    model_config = {"arbitrary_types_allowed": True}


class DataExtractor(ABC):
    """Abstract interface for data extractors."""

    @abstractmethod
    def extract(self, source: DataSource) -> ExtractionResult:
        """Extract data from a source."""


def _require_path(source: DataSource) -> str:
    if not source.path:
        raise ValueError(f"A file path is required for {source.type} extraction")
    return source.path


def _result(source: DataSource, dataframe: pd.DataFrame, metadata: dict[str, Any]) -> ExtractionResult:
    return ExtractionResult(
        source_name=source.name,
        dataframe=dataframe,
        metadata=metadata,
        rows_extracted=len(dataframe),
        columns=[str(column) for column in dataframe.columns],
    )


class CSVExtractor(DataExtractor):
    """Extract data from CSV files."""

    def extract(self, source: DataSource) -> ExtractionResult:
        path = _require_path(source)
        dataframe = pd.read_csv(path, **source.params)
        return _result(source, dataframe, {"file_path": path, "file_type": "csv"})


class ExcelExtractor(DataExtractor):
    """Extract data from XLS/XLSX files."""

    def extract(self, source: DataSource) -> ExtractionResult:
        path = _require_path(source)
        dataframe = pd.read_excel(path, **source.params)
        return _result(source, dataframe, {"file_path": path, "file_type": "excel"})


class JSONExtractor(DataExtractor):
    """Extract records from JSON files."""

    def extract(self, source: DataSource) -> ExtractionResult:
        path = _require_path(source)
        with Path(path).open("r", encoding="utf-8") as json_file:
            data = json.load(json_file)
        if isinstance(data, list):
            dataframe = pd.DataFrame(data)
        elif isinstance(data, dict):
            dataframe = pd.DataFrame([data])
        else:
            raise ValueError("JSON source must contain an object or list of objects")
        return _result(source, dataframe, {"file_path": path, "file_type": "json"})


class PDFExtractor(DataExtractor):
    """Extract tables from PDFs when optional ``pdfplumber`` is installed."""

    def extract(self, source: DataSource) -> ExtractionResult:
        path = _require_path(source)
        try:
            import pdfplumber
        except ImportError:
            return _result(
                source,
                pd.DataFrame(),
                {
                    "file_path": path,
                    "file_type": "pdf",
                    "skipped": True,
                    "reason": "pdfplumber is not installed",
                },
            )

        tables: list[pd.DataFrame] = []
        with pdfplumber.open(path) as pdf:
            for page_number, page in enumerate(pdf.pages, start=1):
                for table_number, table in enumerate(page.extract_tables(), start=1):
                    if len(table) < 2:
                        continue
                    header, *rows = table
                    table_df = pd.DataFrame(rows, columns=header)
                    table_df["_pdf_page"] = page_number
                    table_df["_pdf_table"] = table_number
                    tables.append(table_df)
        dataframe = pd.concat(tables, ignore_index=True) if tables else pd.DataFrame()
        return _result(
            source,
            dataframe,
            {"file_path": path, "file_type": "pdf", "tables_found": len(tables)},
        )


class SQLExtractor(DataExtractor):
    """Extract data from SQL databases."""

    def __init__(self, connection_string: str):
        self.connection_string = connection_string

    def extract(self, source: DataSource) -> ExtractionResult:
        if not source.query:
            raise ValueError("SQL extraction requires a query")
        try:
            import sqlalchemy
        except ImportError as error:
            raise RuntimeError("SQL extraction requires sqlalchemy") from error
        engine = sqlalchemy.create_engine(self.connection_string)
        dataframe = pd.read_sql(source.query, engine, params=source.params)
        return _result(
            source,
            dataframe,
            {"connection": self.connection_string, "query": source.query},
        )


class APIExtractor(DataExtractor):
    """Extract data from REST APIs."""

    def extract(self, source: DataSource) -> ExtractionResult:
        path = _require_path(source)
        try:
            import requests
        except ImportError as error:
            raise RuntimeError("API extraction requires requests") from error
        response = requests.get(path, params=source.params, timeout=30)
        response.raise_for_status()
        data = response.json()
        if isinstance(data, list):
            dataframe = pd.DataFrame(data)
        elif isinstance(data, dict):
            dataframe = pd.DataFrame([data])
        else:
            raise ValueError("API response must be a list or dict")
        return _result(source, dataframe, {"url": path, "params": source.params})


class MockExtractor(DataExtractor):
    """Mock extractor for offline tests."""

    def __init__(self, dataframes: dict[str, pd.DataFrame] | None = None):
        self.dataframes = dataframes or {}

    def extract(self, source: DataSource) -> ExtractionResult:
        dataframe = self.dataframes.get(source.name, pd.DataFrame()).copy()
        return _result(source, dataframe, {"mock": True})


def pull_attachments(email: Any) -> list[Path]:
    """Return existing attachment paths referenced by a mock-mail email."""

    attachments = getattr(email, "attachments", None)
    if attachments is None and isinstance(email, dict):
        attachments = email.get("attachments", [])
    paths: list[Path] = []
    project_root = Path(__file__).resolve().parents[2]
    for attachment in attachments or []:
        value = attachment.get("path") if isinstance(attachment, dict) else attachment
        if not value:
            continue
        path = Path(str(value))
        if not path.is_absolute():
            path = project_root / path
        if path.exists() and path.is_file():
            paths.append(path)
    return paths


def get_extractor(source_type: str, **kwargs: Any) -> DataExtractor:
    """Return an extractor for a source type."""

    normalized = source_type.lower().lstrip(".")
    extractors: dict[str, Any] = {
        "csv": CSVExtractor,
        "excel": ExcelExtractor,
        "xls": ExcelExtractor,
        "xlsx": ExcelExtractor,
        "json": JSONExtractor,
        "pdf": PDFExtractor,
        "sql": lambda: SQLExtractor(kwargs.get("connection_string", "")),
        "api": APIExtractor,
        "mock": lambda: MockExtractor(kwargs.get("dataframes")),
    }
    if normalized not in extractors:
        raise ValueError(f"Unknown extractor type: {source_type}")
    return extractors[normalized]()
