"""
llm_manager.py
--------------
Key Rotation + Retry + Fallback สำหรับ CrewAI + Gemini
"""

import time
import random
import logging
from dataclasses import dataclass
from collections import defaultdict
from crewai import LLM

logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)


@dataclass
class KeyConfig:
    api_key: str
    label: str
    daily_limit: int = 500
    requests_today: int = 0
    cooldown_until: float = 0.0
    failed_count: int = 0


class LLMManager:
    PRIMARY_MODEL  = "gemini-3.1-flash-lite"
    FALLBACK_MODEL = "gemini-1.5-flash"
    RPM_LIMIT      = 15
    COOLDOWN_SEC   = 65
    MAX_RETRIES    = 5

    def __init__(self, api_keys: list[str]):
        if not api_keys:
            raise ValueError("ต้องมีอย่างน้อย 1 API Key")
        self.keys: list[KeyConfig] = [
            KeyConfig(api_key=k, label=f"key_{i+1}")
            for i, k in enumerate(api_keys)
        ]
        self._current_index = 0
        self._rpm_tracker: dict[str, list[float]] = defaultdict(list)

    # ── Public ──────────────────────────────────────────────────────────

    def get_llm(self, agent_role: str = "", use_fallback: bool = False) -> LLM:
        """ดึง LLM instance พร้อม key ที่ว่างอยู่"""
        key_cfg = self._pick_available_key()
        model = self.FALLBACK_MODEL if use_fallback else self.PRIMARY_MODEL
        logger.info(f"[{agent_role or 'agent'}] ใช้ {key_cfg.label} | {model}")
        return self._build_llm(key_cfg.api_key, model)

    def call_with_retry(self, func, *args, agent_role: str = "", **kwargs):
        """เรียก function พร้อม retry + key rotation + fallback อัตโนมัติ"""
        last_error = None

        for attempt in range(1, self.MAX_RETRIES + 1):
            key_cfg    = self._pick_available_key()
            model      = self.PRIMARY_MODEL if attempt <= 3 else self.FALLBACK_MODEL
            is_fallback = model == self.FALLBACK_MODEL

            logger.info(
                f"[{agent_role}] attempt {attempt}/{self.MAX_RETRIES} | "
                f"{key_cfg.label} | {'FALLBACK: ' if is_fallback else ''}{model}"
            )

            try:
                self._track_rpm(key_cfg)
                result = func(*args, **kwargs)
                key_cfg.failed_count = 0
                return result

            except Exception as e:
                last_error = e
                err_str = str(e).lower()

                if self._is_rate_limit_error(err_str):
                    logger.warning(f"[{key_cfg.label}] Rate limit → cooldown {self.COOLDOWN_SEC}s")
                    key_cfg.cooldown_until = time.time() + self.COOLDOWN_SEC
                    key_cfg.failed_count += 1
                    continue  # ลอง key อื่นก่อน ไม่ต้อง sleep

                elif self._is_quota_error(err_str):
                    logger.warning(f"[{key_cfg.label}] Quota หมดวันนี้ → ตัดออก")
                    key_cfg.daily_limit = 0
                    continue

                else:
                    delay = self._backoff_delay(attempt)
                    logger.error(f"[{agent_role}] Error: {e} | retry ใน {delay:.1f}s")
                    time.sleep(delay)

        raise RuntimeError(
            f"[{agent_role}] ล้มเหลวหลัง {self.MAX_RETRIES} attempts | {last_error}"
        )

    def status(self) -> None:
        print("\n─── Key Status ───────────────────────────")
        now = time.time()
        for k in self.keys:
            cooldown_left = max(0, k.cooldown_until - now)
            s = "✅ พร้อม" if cooldown_left == 0 else f"⏳ cooldown {cooldown_left:.0f}s"
            print(f"  {k.label}: RPD {k.requests_today}/{k.daily_limit} | fails: {k.failed_count} | {s}")
        print("──────────────────────────────────────────\n")

    # ── Private ─────────────────────────────────────────────────────────

    def _pick_available_key(self) -> KeyConfig:
        now   = time.time()
        total = len(self.keys)
        for _ in range(total):
            idx = self._current_index % total
            k   = self.keys[idx]
            self._current_index += 1
            if k.cooldown_until <= now and k.requests_today < k.daily_limit and self._check_rpm(k):
                k.requests_today += 1
                return k
        wait_key = min(self.keys, key=lambda k: k.cooldown_until)
        wait_sec = max(1.0, wait_key.cooldown_until - now)
        logger.warning(f"ทุก key ไม่ว่าง → รอ {wait_sec:.0f}s")
        time.sleep(wait_sec)
        wait_key.requests_today += 1
        return wait_key

    def _check_rpm(self, key_cfg: KeyConfig) -> bool:
        now = time.time()
        self._rpm_tracker[key_cfg.label] = [
            t for t in self._rpm_tracker[key_cfg.label] if now - t < 60.0
        ]
        return len(self._rpm_tracker[key_cfg.label]) < self.RPM_LIMIT

    def _track_rpm(self, key_cfg: KeyConfig) -> None:
        self._rpm_tracker[key_cfg.label].append(time.time())

    def _build_llm(self, api_key: str, model: str) -> LLM:
        litellm_model = model if "/" in model else f"gemini/{model}"
        return LLM(
            model=litellm_model,
            api_key=api_key,
            temperature=0.3,
            max_retries=0,
        )

    @staticmethod
    def _is_rate_limit_error(err: str) -> bool:
        return any(k in err for k in ["429", "rate limit", "resource_exhausted", "quota exceeded per minute"])

    @staticmethod
    def _is_quota_error(err: str) -> bool:
        return any(k in err for k in ["daily limit", "quota exceeded", "billing", "per day"])

    @staticmethod
    def _backoff_delay(attempt: int) -> float:
        return (2.0 ** attempt) + random.uniform(0, 1.0)
