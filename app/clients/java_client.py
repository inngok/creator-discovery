import os

import httpx
from dotenv import load_dotenv

load_dotenv()

from app.core.logger import setup_logger

logger = setup_logger("java_client")


class JavaClient:
    def __init__(self):
        self.backend_url = os.environ.get(
            "JAVA_BACKEND_URL", "https://api.influencermatch.site/api/v1/creators/ingest"
        )
        self.secret_key = os.environ.get("JAVA_BACKEND_SECRET", "secret-key-123")

    def send_creator_data(self, payload: dict) -> bool:
        payload = dict(payload)
        if isinstance(payload.get("userName"), str):
            payload["userName"] = payload["userName"].strip().lstrip("@").strip()
        try:
            response = httpx.post(
                self.backend_url,
                json=payload,
                headers={"X-Service-Key": self.secret_key},
                timeout=8.0,
            )
            if response.status_code not in [200, 201]:
                logger.error(
                    f"Failed to POST to backend. Status: {response.status_code}, Msg: {response.text}"
                )
                return False

            try:
                res_json = response.json()
                data_obj = res_json.get("data", {})
                if res_json.get("success") is False or not isinstance(data_obj, dict) or data_obj.get("status") != "SUCCESS":
                    logger.warning("Backend did not confirm successful creator ingestion")
                    return False
            except (ValueError, AttributeError, TypeError):
                logger.warning("Backend returned an invalid ingestion response")
                return False

            logger.info("Successfully sent payload to Java backend!")
            return True
        except Exception as e:
            logger.error(f"Failed to connect to backend: {e}")
            return False

    def send_post_metrics(self, payload: dict, endpoint: str | None = None) -> bool:
        """
        Bắn kết quả bóc tách chỉ số bài đăng (Ground Truth) sang Java Backend.
        Java Backend sẽ lưu snapshot vào DB và thực hiện logic so sánh KPI của Campaign.
        """
        url = endpoint or os.environ.get(
            "JAVA_POST_METRICS_SYNC_URL",
            self.backend_url.replace("/creators/ingest", "/campaigns/posts/metrics-sync"),
        )
        try:
            response = httpx.post(
                url,
                json=payload,
                headers={"X-Service-Key": self.secret_key},
                timeout=10.0,
            )
            if response.status_code not in [200, 201]:
                logger.error(
                    f"Failed to POST post metrics to backend. Status: {response.status_code}, Msg: {response.text}"
                )
                return False

            logger.info("Successfully sent post metrics payload to Java backend!")
            return True
        except Exception as e:
            logger.error(f"Failed to connect to backend for post metrics: {e}")
            return False


java_client = JavaClient()
