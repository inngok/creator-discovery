import os

import httpx

from app.core.logger import setup_logger

logger = setup_logger("java_client")


class JavaClient:
    def __init__(self):
        self.backend_url = os.environ.get(
            "JAVA_BACKEND_URL", "http://localhost:8080/api/v1/creators/ingest"
        )
        self.secret_key = os.environ.get("JAVA_BACKEND_SECRET", "")

    def send_creator_data(self, payload: dict) -> bool:
        try:
            response = httpx.post(
                self.backend_url,
                json=payload,
                headers={"X-Service-Key": self.secret_key},
                timeout=15.0,
            )
            if response.status_code not in [200, 201]:
                logger.error(
                    f"Failed to POST to backend. Status: {response.status_code}, Msg: {response.text}"
                )
                return False
            else:
                logger.info("Successfully sent payload to Java backend!")
                return True
        except Exception as e:
            logger.error(f"Failed to connect to backend: {e}")
            return False


java_client = JavaClient()
