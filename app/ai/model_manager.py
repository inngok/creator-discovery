import json
import os

import joblib

from app.core.logger import setup_logger

logger = setup_logger("model_manager")


class ModelManager:
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._load_models()
        return cls._instance

    def _load_models(self):
        self.cat_model = None
        self.cat_classes = []
        self.ct_model = None

        logger.info("Loading ML models into memory...")
        BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        # Cấu trúc: app/ai/model_manager.py -> app/ai -> app/ai/models/category/
        CATEGORY_MODEL_DIR = os.path.join(BASE_DIR, "ai", "models", "category")
        MODEL_PATH = os.path.join(CATEGORY_MODEL_DIR, "category_model.joblib")
        CLASSES_PATH = os.path.join(CATEGORY_MODEL_DIR, "label_classes.json")

        CREATOR_TYPE_MODEL_DIR = os.path.join(BASE_DIR, "ai", "models", "creator_type")
        CT_MODEL_PATH = os.path.join(
            CREATOR_TYPE_MODEL_DIR, "creator_type_model.joblib"
        )

        try:
            if os.path.exists(MODEL_PATH) and os.path.exists(CLASSES_PATH):
                self.cat_model = joblib.load(MODEL_PATH)
                with open(CLASSES_PATH, "r", encoding="utf-8") as f:
                    self.cat_classes = json.load(f)
                logger.info("Successfully loaded Category model (v1.0.0)!")
            else:
                logger.warning(
                    f"Could not find {MODEL_PATH}! Please run the Train and Export process."
                )

            if os.path.exists(CT_MODEL_PATH):
                self.ct_model = joblib.load(CT_MODEL_PATH)
                logger.info("Successfully loaded CreatorType model!")
            else:
                logger.warning(
                    f"Could not find {CT_MODEL_PATH}! Please run the Train and Export process."
                )

        except Exception as e:
            logger.error(f"Critical error loading AI models: {e}")


# Singleton instance
model_manager = ModelManager()
