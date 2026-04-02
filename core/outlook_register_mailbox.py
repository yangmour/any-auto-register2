"""OutlookRegister mailbox provider."""

from __future__ import annotations

import random
import secrets
import string
import time
from typing import Optional

from .base_mailbox import BaseMailbox, MailboxAccount


FIRST_NAMES = [
    "James", "John", "Robert", "Michael", "William",
    "David", "Richard", "Joseph", "Thomas", "Charles",
    "Emma", "Olivia", "Ava", "Sophia", "Mia",
    "Charlotte", "Amelia", "Harper", "Evelyn", "Grace",
]

LAST_NAMES = [
    "Smith", "Johnson", "Williams", "Brown", "Jones",
    "Garcia", "Miller", "Davis", "Wilson", "Anderson",
    "Taylor", "Thomas", "Moore", "Martin", "Lee",
]


def _generate_password(length: int = 14) -> str:
    charset = string.ascii_letters + string.digits + "!@#$%^&*"
    while True:
        password = "".join(secrets.choice(charset) for _ in range(length))
        if (
            any(c.islower() for c in password)
            and any(c.isupper() for c in password)
            and any(c.isdigit() for c in password)
            and any(c in "!@#$%^&*" for c in password)
        ):
            return password


def _generate_local_part(length: int = 12) -> str:
    first = random.choice(string.ascii_lowercase)
    rest = []
    for _ in range(max(7, length) - 1):
        if random.random() < 0.08:
            rest.append(random.choice(string.digits))
        else:
            rest.append(random.choice(string.ascii_lowercase))
    return first + "".join(rest)


def _to_int(value, default: int) -> int:
    try:
        return int(float(value))
    except Exception:
        return default


class OutlookRegisterMailbox(BaseMailbox):
    """Register a fresh outlook.com mailbox and reuse the browser session to read OTP mails."""

    def __init__(
        self,
        browser_path: str = "",
        bot_protection_wait: int = 12,
        max_captcha_retries: int = 2,
        proxy: str = None,
    ):
        self.browser_path = str(browser_path or "").strip()
        self.bot_protection_wait_ms = max(0, _to_int(bot_protection_wait, 12) * 1000)
        self.max_captcha_retries = max(0, _to_int(max_captcha_retries, 2))
        self.proxy = str(proxy or "").strip()
        self._playwright = None
        self._browser = None
        self._context = None
        self._page = None
        self._account: Optional[MailboxAccount] = None

    def _proxy_settings(self):
        if not self.proxy:
            return None
        return {
            "server": self.proxy,
            "bypass": "localhost",
        }

    def _start(self) -> None:
        if self._page is not None:
            return

        from patchright.sync_api import sync_playwright

        self._playwright = sync_playwright().start()
        launch_kwargs = {
            "headless": False,
            "args": ["--lang=zh-CN"],
        }
        proxy_settings = self._proxy_settings()
        if proxy_settings:
            launch_kwargs["proxy"] = proxy_settings
        if self.browser_path:
            launch_kwargs["executable_path"] = self.browser_path
        self._browser = self._playwright.chromium.launch(**launch_kwargs)
        self._context = self._browser.new_context(locale="zh-CN")
        self._page = self._context.new_page()

    def close(self) -> None:
        for attr in ("_page", "_context", "_browser", "_playwright"):
            obj = getattr(self, attr, None)
            if not obj:
                continue
            try:
                close_fn = getattr(obj, "close", None) or getattr(obj, "stop", None)
                if callable(close_fn):
                    close_fn()
            except Exception:
                pass
            setattr(self, attr, None)

    def __del__(self):
        self.close()

    def _wait_ms(self, factor: float) -> None:
        if self._page is None or self.bot_protection_wait_ms <= 0:
            return
        self._page.wait_for_timeout(max(50, int(self.bot_protection_wait_ms * factor)))

    def _get_page_url(self) -> str:
        if self._page is None:
            return ""
        try:
            return str(self._page.url or "")
        except Exception:
            return ""

    def _collect_challenge_markers(self) -> list[str]:
        if self._page is None:
            return []

        markers: list[str] = []
        locator_checks = [
            ('iframe[title="验证质询"]', "challenge-frame"),
            ("iframe#enforcementFrame", "challenge-frame"),
            ('iframe[src*="challenge"]', "challenge-frame"),
            ('iframe[src*="captcha"]', "challenge-frame"),
            ('iframe[src*="arkoselabs"]', "challenge-frame"),
            ('iframe[name*="challenge"]', "challenge-frame"),
            ('iframe[id*="challenge"]', "challenge-frame"),
            ('iframe[class*="challenge"]', "challenge-frame"),
        ]
        for selector, marker in locator_checks:
            try:
                if self._page.locator(selector).count() > 0:
                    markers.append(marker)
            except Exception:
                continue

        try:
            for frame in self._page.frames:
                if frame == self._page.main_frame:
                    continue
                descriptor = " ".join(
                    part for part in (getattr(frame, "name", ""), getattr(frame, "url", "")) if part
                ).lower()
                if any(token in descriptor for token in ("challenge", "captcha", "enforcement", "arkoselabs")):
                    markers.append("challenge-frame")
                    break
        except Exception:
            pass

        return list(dict.fromkeys(markers))

    def _get_human_verification_reason(self) -> str:
        if self._page is None:
            return ""

        page_text = self._capture_page_text()
        page_url = self._get_page_url()
        on_signup_live = "signup.live.com" in page_url.lower()
        challenge_markers = self._collect_challenge_markers()

        strong_markers = [
            "证明你不是机器人",
            "长按该按钮",
            "验证质询",
            "请帮忙验证",
        ]
        soft_markers = [
            "请再试一次",
            "遇到问题",
        ]
        strong_hits = [marker for marker in strong_markers if marker in page_text]
        soft_hits = [marker for marker in soft_markers if marker in page_text]

        if not strong_hits and not challenge_markers and not (soft_hits and on_signup_live):
            return ""

        observed = []
        if strong_hits:
            observed.extend(strong_hits[:2])
        if soft_hits:
            observed.extend(soft_hits[:2])
        observed.extend(challenge_markers)
        observed_text = ", ".join(dict.fromkeys(observed)) or "未识别特征"

        return (
            "Outlook 命中 Microsoft 风控挑战，当前会话未通过人工验证，"
            f"url={page_url or 'unknown'}，特征={observed_text}"
        )

    def _is_human_verification_gate(self) -> bool:
        return bool(self._get_human_verification_reason())

    def _log_page_diagnostics(self, prefix: str) -> None:
        page_url = self._get_page_url()
        page_text = self._capture_page_text()
        snippet = " ".join(page_text.split())[:220] if page_text else ""
        if page_url:
            self._log(f"{prefix} URL: {page_url}")
        if snippet:
            self._log(f"{prefix} 页面摘要: {snippet}")

    def _raise_if_human_verification_gate(self) -> None:
        reason = self._get_human_verification_reason()
        if reason:
            self._log_page_diagnostics("[OutlookRegister] Microsoft 风控挑战")
            raise RuntimeError(reason)

    def _click_primary_button(self, timeout: int = 5000) -> None:
        if self._page is None:
            raise RuntimeError("Outlook 浏览器未启动")

        self._raise_if_human_verification_gate()
        button = self._page.locator('[data-testid="primaryButton"]')
        button.wait_for(state="visible", timeout=timeout)
        self._page.wait_for_function(
            """
            () => {
              const button = document.querySelector('[data-testid="primaryButton"]');
              return !!button && !button.disabled && button.getAttribute('aria-disabled') !== 'true';
            }
            """,
            timeout=timeout,
        )
        self._raise_if_human_verification_gate()
        button.click(timeout=timeout)

    def _dismiss_optional_dialogs(self) -> None:
        if self._page is None:
            return

        for text in ("以后再说", "取消", "稍后再说", "Not now", "No thanks"):
            try:
                btn = self._page.get_by_text(text)
                if btn.count() > 0:
                    btn.first.click(timeout=1500)
                    self._page.wait_for_timeout(300)
            except Exception:
                pass

    def _ensure_inbox_ready(self) -> None:
        if self._page is None:
            return

        self._dismiss_optional_dialogs()
        try:
            self._page.locator('[aria-label="新邮件"]').wait_for(timeout=30000)
            return
        except Exception:
            pass
        try:
            self._page.goto(
                "https://outlook.live.com/mail/0/",
                timeout=30000,
                wait_until="domcontentloaded",
            )
            self._page.locator('[aria-label="新邮件"]').wait_for(timeout=30000)
        except Exception as e:
            raise RuntimeError(f"Outlook 邮箱未初始化完成: {e}") from e

    def _register_account(self, local_part: str, password: str) -> None:
        if self._page is None:
            raise RuntimeError("Outlook 浏览器未启动")

        page = self._page
        last_name = random.choice(LAST_NAMES)
        first_name = random.choice(FIRST_NAMES)
        year = str(random.randint(1960, 2005))
        month = str(random.randint(1, 12))
        day = str(random.randint(1, 28))

        try:
            page.goto(
                "https://outlook.live.com/mail/0/?prompt=create_account",
                timeout=20000,
                wait_until="domcontentloaded",
            )
            self._raise_if_human_verification_gate()
            page.get_by_text("同意并继续").wait_for(timeout=30000)
            self._wait_ms(0.10)
            page.get_by_text("同意并继续").click(timeout=30000)
        except Exception as e:
            raise RuntimeError(f"IP 质量不佳，无法进入 Outlook 注册界面: {e}") from e

        try:
            started_at = time.time()
            page.locator('[aria-label="新建电子邮件"]').type(
                local_part,
                delay=max(10, int(self.bot_protection_wait_ms * 0.0006)),
                timeout=10000,
            )
            self._click_primary_button(timeout=8000)
            self._wait_ms(0.02)
            page.locator('[type="password"]').type(
                password,
                delay=max(8, int(self.bot_protection_wait_ms * 0.0004)),
                timeout=10000,
            )
            self._wait_ms(0.02)
            self._click_primary_button(timeout=8000)

            self._wait_ms(0.03)
            page.locator('[name="BirthYear"]').fill(year, timeout=10000)
            try:
                self._wait_ms(0.02)
                page.locator('[name="BirthMonth"]').select_option(value=month, timeout=1000)
                self._wait_ms(0.05)
                page.locator('[name="BirthDay"]').select_option(value=day)
            except Exception:
                page.locator('[name="BirthMonth"]').click()
                self._wait_ms(0.02)
                page.locator(f'[role="option"]:text-is("{month}月")').click()
                self._wait_ms(0.04)
                page.locator('[name="BirthDay"]').click()
                self._wait_ms(0.03)
                page.locator(f'[role="option"]:text-is("{day}日")').click()

            self._click_primary_button(timeout=8000)
            page.locator("#lastNameInput").type(
                last_name,
                delay=max(6, int(self.bot_protection_wait_ms * 0.0002)),
                timeout=10000,
            )
            self._wait_ms(0.02)
            page.locator("#firstNameInput").fill(first_name, timeout=10000)

            elapsed_ms = int((time.time() - started_at) * 1000)
            if elapsed_ms < self.bot_protection_wait_ms:
                page.wait_for_timeout(self.bot_protection_wait_ms - elapsed_ms)

            self._click_primary_button(timeout=8000)
            try:
                page.locator('span > [href="https://go.microsoft.com/fwlink/?LinkID=521839"]').wait_for(
                    state="detached",
                    timeout=22000,
                )
            except Exception:
                pass
            page.wait_for_timeout(400)
        except Exception as e:
            raise RuntimeError(f"Outlook 表单填写失败: {e}") from e

        self._raise_if_human_verification_gate()

        if page.get_by_text("一些异常活动").count() > 0 or page.get_by_text("此站点正在维护，暂时无法使用，请稍后重试。").count() > 0:
            raise RuntimeError("当前 IP 注册频率过快，Outlook 页面已拦截")

        self._raise_if_human_verification_gate()

        self._log(f"[OutlookRegister] 注册成功: {local_part}@outlook.com")

        self._dismiss_optional_dialogs()
        self._ensure_inbox_ready()

    def get_email(self) -> MailboxAccount:
        if self._account is not None:
            return self._account

        self._start()
        local_part = _generate_local_part(random.randint(12, 14))
        password = _generate_password(random.randint(11, 15))
        try:
            self._register_account(local_part, password)
        except Exception:
            self.close()
            raise

        self._account = MailboxAccount(
            email=f"{local_part}@outlook.com",
            account_id=f"{local_part}@outlook.com",
            extra={
                "password": password,
                "provider": "outlook_register",
            },
        )
        return self._account

    def get_current_ids(self, account: MailboxAccount) -> set:
        return set()

    def _capture_page_text(self) -> str:
        if self._page is None:
            return ""
        try:
            return self._page.locator("body").inner_text(timeout=5000)
        except Exception:
            try:
                return self._page.content()
            except Exception:
                return ""

    def _try_open_recent_messages(self) -> list[str]:
        if self._page is None:
            return []

        texts = []
        selectors = [
            '[role="main"] [role="option"]',
            '[role="main"] [data-convid]',
            '[role="main"] [role="row"]',
        ]
        for selector in selectors:
            try:
                locator = self._page.locator(selector)
                count = min(locator.count(), 5)
                if count <= 0:
                    continue
                for index in range(count):
                    try:
                        locator.nth(index).click(timeout=2000)
                        self._page.wait_for_timeout(1000)
                        text = self._capture_page_text()
                        if text:
                            texts.append(text)
                    except Exception:
                        continue
                if texts:
                    return texts
            except Exception:
                continue
        return texts

    def _extract_code(self, text: str, keyword: str, code_pattern: str, exclude_codes: set[str]) -> Optional[str]:
        text = str(text or "")
        if not text:
            return None
        if keyword and keyword.lower() not in text.lower():
            return None
        code = self._safe_extract(text, code_pattern)
        if code and code not in exclude_codes:
            return code
        return None

    def wait_for_code(
        self,
        account: MailboxAccount,
        keyword: str = "",
        timeout: int = 120,
        before_ids: set = None,
        code_pattern: str = None,
        **kwargs,
    ) -> str:
        if self._page is None:
            raise RuntimeError("OutlookRegister 浏览器会话不存在，无法继续收码")

        exclude_codes = {str(code) for code in (kwargs.get("exclude_codes") or set()) if code}
        start = time.time()
        while time.time() - start < timeout:
            try:
                self._dismiss_optional_dialogs()
                self._page.goto(
                    "https://outlook.live.com/mail/0/",
                    timeout=30000,
                    wait_until="domcontentloaded",
                )
                self._ensure_inbox_ready()
            except Exception:
                pass

            candidates = [self._capture_page_text()]
            candidates.extend(self._try_open_recent_messages())

            for text in candidates:
                code = self._extract_code(text, keyword, code_pattern, exclude_codes)
                if code:
                    self._log(f"[OutlookRegister] 收到验证码: {code}")
                    return code

            time.sleep(4)

        raise TimeoutError(f"等待 Outlook 邮件验证码超时 ({timeout}s)")
