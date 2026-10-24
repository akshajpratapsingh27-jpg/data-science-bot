"""Data connectors for dsbot."""

from abc import ABC, abstractmethod
from dsbot.data.extract import DataSource, ExtractionResult, DataExtractor
from dsbot.data.extract import get_extractor as get_base_extractor
from typing import Any


class DataConnector(ABC):
    """Abstract interface for data connectors."""

    @abstractmethod
    def extract(self, source: DataSource) -> ExtractionResult:
        """Extract data from a source."""
        ...

    @abstractmethod
    def test_connection(self, source: DataSource) -> bool:
        """Test if connection to source works."""
        ...


class FileDataConnector(DataConnector):
    """Connector for file-based data sources."""

    def extract(self, source: DataSource) -> ExtractionResult:
        extractor = get_base_extractor(source.type)
        return extractor.extract(source)

    def test_connection(self, source: DataSource) -> bool:
        from pathlib import Path
        return Path(source.path).exists() if source.path else False


class DatabaseConnector(DataConnector):
    """Connector for database sources."""

    def __init__(self, connection_string: str):
        self.connection_string = connection_string

    def extract(self, source: DataSource) -> ExtractionResult:
        from dsbot.data.extract import SQLExtractor
        extractor = SQLExtractor(self.connection_string)
        return extractor.extract(source)

    def test_connection(self, source: DataSource) -> bool:
        try:
            import sqlalchemy
            engine = sqlalchemy.create_engine(self.connection_string)
            with engine.connect() as conn:
                conn.execute(sqlalchemy.text("SELECT 1"))
            return True
        except Exception:
            return False


class APIConnector(DataConnector):
    """Connector for API sources."""

    def __init__(self, base_url: str, headers: dict[str, str] | None = None):
        self.base_url = base_url
        self.headers = headers or {}

    def extract(self, source: DataSource) -> ExtractionResult:
        from dsbot.data.extract import APIExtractor
        if source.path is None:
            raise ValueError("API source path is required")
        extractor = APIExtractor()
        # Modify source path to be full URL
        full_path = f"{self.base_url.rstrip('/')}/{source.path.lstrip('/')}"
        source_with_url = source.model_copy(update={"path": full_path})
        return extractor.extract(source_with_url)

    def test_connection(self, source: DataSource) -> bool:
        import requests
        try:
            if source.path is None:
                return False
            full_url = f"{self.base_url.rstrip('/')}/{source.path.lstrip('/')}"
            response = requests.head(full_url, headers=self.headers, timeout=5)
            return response.status_code < 400
        except Exception:
            return False


class MockDataConnector(DataConnector):
    """Mock connector for testing."""

    def __init__(self, dataframes: dict[str, Any] | None = None):
        from dsbot.data.extract import MockExtractor
        self.extractor = MockExtractor(dataframes)

    def extract(self, source: DataSource) -> ExtractionResult:
        return self.extractor.extract(source)

    def test_connection(self, source: DataSource) -> bool:
        return source.name in (self.extractor.dataframes if hasattr(self.extractor, 'dataframes') else {})


def get_connector(connector_type: str, **kwargs) -> DataConnector:
    """Factory function to get connector by type."""
    connectors = {
        "file": FileDataConnector,
        "database": lambda: DatabaseConnector(kwargs.get("connection_string", "")),
        "api": lambda: APIConnector(kwargs.get("base_url", ""), kwargs.get("headers")),
        "mock": lambda: MockDataConnector(kwargs.get("dataframes")),
    }

    if connector_type not in connectors:
        raise ValueError(f"Unknown connector type: {connector_type}")

    return connectors[connector_type]()
