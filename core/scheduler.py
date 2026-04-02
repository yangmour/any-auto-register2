"""定时任务调度 - 账号有效性检测、trial 到期提醒"""
from datetime import datetime, timezone
from sqlmodel import Session, select
from .db import engine, AccountModel
from .registry import get, load_all
from .base_platform import Account, AccountStatus, RegisterConfig
import threading
import time
import json


class Scheduler:
    def __init__(self):
        self._running = False
        self._thread: threading.Thread = None
        self._last_trial_check_ts = 0.0
        self._last_cpa_cleanup_ts = 0.0

    def start(self):
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        print("[Scheduler] 已启动")

    def stop(self):
        self._running = False

    def _loop(self):
        while self._running:
            try:
                now = time.time()
                if now - self._last_trial_check_ts >= 3600:
                    self.check_trial_expiry()
                    self._last_trial_check_ts = now
                self.cleanup_cpa_if_due(now)
            except Exception as e:
                print(f"[Scheduler] 错误: {e}")
            time.sleep(30)

    def _get_config(self, key: str, default: str = "") -> str:
        try:
            from .config_store import config_store
            return config_store.get(key, default)
        except Exception:
            return default

    def _get_bool(self, key: str, default: bool = False) -> bool:
        value = str(self._get_config(key, str(default).lower()) or "").strip().lower()
        return value in {"1", "true", "yes", "on"}

    def _get_int(self, key: str, default: int) -> int:
        value = self._get_config(key, str(default))
        try:
            return int(float(value))
        except Exception:
            return default

    def check_trial_expiry(self):
        """检查 trial 到期账号，更新状态"""
        now = int(datetime.now(timezone.utc).timestamp())
        with Session(engine) as s:
            accounts = s.exec(
                select(AccountModel).where(AccountModel.status == "trial")
            ).all()
            updated = 0
            for acc in accounts:
                if acc.trial_end_time and acc.trial_end_time < now:
                    acc.status = AccountStatus.EXPIRED.value
                    acc.updated_at = datetime.now(timezone.utc)
                    s.add(acc)
                    updated += 1
            s.commit()
            if updated:
                print(f"[Scheduler] {updated} 个 trial 账号已到期")

    def cleanup_cpa_if_due(self, now_ts: float = None):
        if not self._get_bool("cpa_cleanup_enabled", False):
            return

        interval_seconds = max(60, self._get_int("cpa_cleanup_interval_seconds", 43200))
        now_ts = now_ts if now_ts is not None else time.time()
        if self._last_cpa_cleanup_ts and now_ts - self._last_cpa_cleanup_ts < interval_seconds:
            return

        self._last_cpa_cleanup_ts = now_ts
        self.cleanup_cpa()

    def cleanup_cpa(self):
        from platforms.chatgpt.cpa_upload import cleanup_cpa_auth_files

        with Session(engine) as s:
            accounts = s.exec(
                select(AccountModel).where(AccountModel.platform == "chatgpt")
            ).all()

        candidates = []
        for acc in accounts:
            if acc.status not in {AccountStatus.INVALID.value, AccountStatus.EXPIRED.value}:
                continue
            extra = json.loads(acc.extra_json or "{}")
            if extra.get("cpa_cleanup_deleted"):
                continue
            if acc.email:
                candidates.append((acc.id, acc.email))

        if not candidates:
            return

        ok, result = cleanup_cpa_auth_files([email for _, email in candidates])
        if not ok:
            print(f"[Scheduler] CPA 定时清理失败: {result.get('error') if isinstance(result, dict) else result}")
            return

        deleted_emails = set(result.get("deleted") or [])
        failed = result.get("failed") or []

        if deleted_emails:
            with Session(engine) as s:
                for account_id, email in candidates:
                    if email not in deleted_emails:
                        continue
                    acc = s.get(AccountModel, account_id)
                    if not acc:
                        continue
                    extra = json.loads(acc.extra_json or "{}")
                    extra["cpa_cleanup_deleted"] = True
                    extra["cpa_cleanup_deleted_at"] = datetime.now(timezone.utc).isoformat()
                    acc.extra_json = json.dumps(extra, ensure_ascii=False)
                    acc.updated_at = datetime.now(timezone.utc)
                    s.add(acc)
                s.commit()

        print(
            f"[Scheduler] CPA 定时清理完成: 尝试 {result.get('attempted', 0)} 个, "
            f"成功 {len(deleted_emails)} 个, 失败 {len(failed)} 个"
        )

    def check_accounts_valid(self, platform: str = None, limit: int = 50):
        """批量检测账号有效性"""
        load_all()
        with Session(engine) as s:
            q = select(AccountModel).where(
                AccountModel.status.in_(["registered", "trial", "subscribed"])
            )
            if platform:
                q = q.where(AccountModel.platform == platform)
            accounts = s.exec(q.limit(limit)).all()

        results = {"valid": 0, "invalid": 0, "error": 0}
        for acc in accounts:
            try:
                PlatformCls = get(acc.platform)
                plugin = PlatformCls(config=RegisterConfig())
                import json
                account_obj = Account(
                    platform=acc.platform,
                    email=acc.email,
                    password=acc.password,
                    user_id=acc.user_id,
                    region=acc.region,
                    token=acc.token,
                    extra=json.loads(acc.extra_json or "{}"),
                )
                valid = plugin.check_valid(account_obj)
                with Session(engine) as s:
                    a = s.get(AccountModel, acc.id)
                    if a:
                        a.status = acc.status if valid else AccountStatus.INVALID.value
                        a.updated_at = datetime.now(timezone.utc)
                        s.add(a)
                        s.commit()
                if valid:
                    results["valid"] += 1
                else:
                    results["invalid"] += 1
            except Exception:
                results["error"] += 1
        return results


scheduler = Scheduler()
