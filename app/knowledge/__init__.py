"""
Knowledge Base Module
Implements UDT (Unified Diagnostic Taxonomy) parsing, knowledge base management,
baseline validation, and the index construction pipeline (T-GA-DATA-01).
"""
from app.knowledge.baseline_annotation import BaselineAnnotator
from app.knowledge.baseline_validation import BaselineReport, BaselineValidator
from app.knowledge.builder import (
    KnowledgeIndexBuilderImpl,
    MockEmbeddingService,
)
from app.knowledge.chunker import (
    ChunkerStrategy,
    FixedWindowChunker,
    KnowledgeSourceDoc,
    SemanticChunker,
    SentenceBoundaryChunker,
    create_chunker,
)
from app.knowledge.feature_extractor import ExtractedFeatures, FeatureExtractor
from app.knowledge.graph_query import GraphQueryAdapter, PgVectorGraphAdapter
from app.knowledge.hybrid_indexer import (
    HybridIndexer,
    PGVectorHybridIndexer,
)
from app.knowledge.protocols import (
    IndexBuildError,
    IndexBuildErrorCode,
    IndexBuildRequest,
    IndexBuildResult,
    KnowledgeChunk,
    KnowledgeIndexBuilder,
)
from app.knowledge.segmentation_validation import F1Metrics, SegBackend, SegmentationValidator
from app.knowledge.udt_parser import MeasurementValue, UDTParser, UDTParserConfig
from app.knowledge.version import KBVersion, parse_kb_version
from app.knowledge.vocabulary import UDAFVocabulary

__all__ = [
    'BaselineAnnotator',
    'BaselineReport',
    'BaselineValidator',
    'ChunkerStrategy',
    'ExtractedFeatures',
    'F1Metrics',
    'FeatureExtractor',
    'FixedWindowChunker',
    'GraphQueryAdapter',
    'HybridIndexer',
    'IndexBuildError',
    'IndexBuildErrorCode',
    'IndexBuildRequest',
    'IndexBuildResult',
    'KBVersion',
    'KnowledgeChunk',
    'KnowledgeIndexBuilder',
    'KnowledgeIndexBuilderImpl',
    'KnowledgeSourceDoc',
    'MeasurementValue',
    'MockEmbeddingService',
    'PGVectorHybridIndexer',
    'PgVectorGraphAdapter',
    'SegBackend',
    'SegmentationValidator',
    'SemanticChunker',
    'SentenceBoundaryChunker',
    'UDAFVocabulary',
    'UDTParser',
    'UDTParserConfig',
    'create_chunker',
    'parse_kb_version',
]
