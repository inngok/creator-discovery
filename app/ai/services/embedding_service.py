import threading

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


# Khoi tao san mot object de cac file khac import dung chung (Singleton Pattern)
embedding_service = EmbeddingService()
