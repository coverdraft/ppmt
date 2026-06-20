"""PPMT Data Layer: Storage, Collection, Asset Classification, and Bulk Download."""

from ppmt.data.storage import PPMTStorage
from ppmt.data.collector import DataCollector
from ppmt.data.classifier import AssetClassifier
from ppmt.data.bulk_downloader import BulkDownloader

# Lazy import to avoid circular dependency (sequential_builder → ppmt.engine → ppmt.data)
def build_all_tries(*args, **kwargs):
    from ppmt.data.sequential_builder import build_all_tries as _build
    return _build(*args, **kwargs)

__all__ = [
    "PPMTStorage",
    "DataCollector",
    "AssetClassifier",
    "BulkDownloader",
    "build_all_tries",
]
