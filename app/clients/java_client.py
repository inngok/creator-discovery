import os

import httpx
from dotenv import load_dotenv

load_dotenv()

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

            try:
                res_json = response.json()
                data_obj = res_json.get("data", {})
                if isinstance(data_obj, dict) and data_obj.get("status") == "REJECTED":
                    logger.warning(
                        f"Backend REJECTED ingest: {data_obj.get('message', 'No message')}"
                    )
                    return False
            except Exception:
                pass

            logger.info("Successfully sent payload to Java backend!")
            return True
        except Exception as e:
            logger.error(f"Failed to connect to backend: {e}")
            return False


java_client = JavaClient()
