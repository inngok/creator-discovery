import os
import threading

os.environ["HF_HUB_DISABLE_PROGRESS_BARS"] = "1"
os.environ["TRANSFORMERS_VERBOSITY"] = "error"

import numpy as np
from sentence_transformers import SentenceTransformer

from app.core.logger import setup_logger

logger = setup_logger(__name__)


class EmbeddingService:
    """
    Singleton Class quan ly Embedding Model.
    Dam bao model chi duoc tai len RAM dung 1 lan duy nhat.
    """

    _instance = None
    _lock = threading.Lock()

    def __new__(cls):
        with cls._lock:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
                cls._instance._model = None
                cls._instance._search_model = None
        return cls._instance

    def get_embedding(self, text: str | list[str]) -> np.ndarray:
        """
        Chuyen doi van ban thanh Vector (384 chieu).
        Dung chung cho viec nhan dien Category, Niche va Tim kiem Ngu nghia.
        """
        if self._model is None:
            with self._lock:
                if self._model is None:
                    logger.info(
                        "[EmbeddingService] Dang tai mo hinh paraphrase-multilingual-MiniLM-L12-v2 vao RAM..."
                    )
                    self._model = SentenceTransformer(
                        "paraphrase-multilingual-MiniLM-L12-v2"
                    )
                    logger.info("[EmbeddingService] Da tai xong mo hinh!")

        if isinstance(text, str):
            text = [text]

        embeddings = self._model.encode(text)
        return embeddings

    def get_search_embedding(self, text: str | list[str]) -> list[float] | list[list[float]]:
        """
        Chuyen doi van ban thanh Vector 768 chieu bang mo hinh BKAI Vietnamese Bi-Encoder.
        Phuc vu Vector Search va Semantic Matching giua Brand va Creator.
        """
        if self._search_model is None:
            with self._lock:
                if self._search_model is None:
                    logger.info(
                        "[EmbeddingService] Dang tai mo hinh BKAI Bi-Encoder (768 chieu) vao RAM..."
                    )
                    self._search_model = SentenceTransformer(
                        "bkai-foundation-models/vietnamese-bi-encoder"
                    )
                    logger.info("[EmbeddingService] Da tai xong mo hinh BKAI!")

        is_single = isinstance(text, str)
        input_texts = [text] if is_single else text

        embeddings = self._search_model.encode(input_texts, convert_to_numpy=True)
        if is_single:
            return embeddings[0].tolist()
        return embeddings.tolist()


# Khoi tao san mot object de cac file khac import dung chung (Singleton Pattern)
embedding_service = EmbeddingService()
