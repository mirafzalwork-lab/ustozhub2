"""Клиент платёжного шлюза Multicard.

Документация: https://docs.multicard.uz/

Поток онлайн-пополнения кошелька:
  1. auth()                  → JWT-токен (кешируется ~24ч), заголовок Authorization: Bearer
  2. create_invoice(...)     → checkout_url, на который редиректим клиента
  3. <клиент платит на странице Multicard>
  4. Multicard шлёт callback → verify_sign() + зачисление DEPOSIT (см. views)

ВАЖНО: суммы в API Multicard — в ТИЙИНАХ (1 сум = 100 тийин).
"""
from __future__ import annotations

import hashlib
import hmac
import logging
from decimal import Decimal

import httpx
from django.conf import settings
from django.core.cache import cache

logger = logging.getLogger(__name__)

# Токен живёт 24ч; кешируем чуть меньше, чтобы не упереться в 401 на границе.
_TOKEN_CACHE_KEY = 'multicard:token'
_TOKEN_TTL_SECONDS = 23 * 60 * 60


class MulticardError(Exception):
    """Базовая ошибка интеграции Multicard."""

    def __init__(self, message: str, *, code: str = '', details: str = ''):
        super().__init__(message)
        self.code = code
        self.details = details


class MulticardConfigError(MulticardError):
    """Не заданы обязательные настройки (application_id / secret / store_id)."""


def sum_to_tiyin(amount) -> int:
    """UZS (сум) → тийины. 1 сум = 100 тийин."""
    return int((Decimal(str(amount)) * 100).to_integral_value())


def tiyin_to_sum(tiyin) -> Decimal:
    """Тийины → UZS (сум)."""
    return (Decimal(int(tiyin)) / 100).quantize(Decimal('0.01'))


def compute_sign(store_id, invoice_id: str, amount, secret: str = '') -> str:
    """Подпись callback Multicard: md5('{store_id}{invoice_id}{amount}{secret}').

    ВАЖНО: реальная формула выяснена эмпирически и отличается от документации
    (там указан sha1 с uuid). На практике Multicard шлёт
    md5(store_id + invoice_id + amount + secret), где:
      * store_id — ЧИСЛОВОЙ store_id из callback (поле "store_id", напр. 6),
        НЕ наш UUID магазина;
      * amount   — целое число тийинов;
    все части конкатенируются строкой без разделителей.
    """
    secret = secret or settings.MULTICARD_SECRET
    raw = f'{store_id}{invoice_id}{amount}{secret}'
    return hashlib.md5(raw.encode('utf-8')).hexdigest()


def verify_sign(store_id, invoice_id: str, amount, sign: str, secret: str = '') -> bool:
    """Проверка подписи callback (constant-time сравнение)."""
    if not sign:
        return False
    expected = compute_sign(store_id, invoice_id, amount, secret)
    return hmac.compare_digest(expected, str(sign).lower())


class MulticardClient:
    """Тонкий синхронный клиент поверх httpx."""

    def __init__(
        self,
        *,
        base_url: str = '',
        application_id: str = '',
        secret: str = '',
        timeout: int | None = None,
    ):
        self.base_url = (base_url or settings.MULTICARD_BASE_URL).rstrip('/')
        self.application_id = application_id or settings.MULTICARD_APPLICATION_ID
        self.secret = secret or settings.MULTICARD_SECRET
        self.timeout = timeout or getattr(settings, 'MULTICARD_HTTP_TIMEOUT', 20)
        if not (self.application_id and self.secret):
            raise MulticardConfigError(
                'MULTICARD_APPLICATION_ID / MULTICARD_SECRET не заданы'
            )

    # ---------- низкоуровневое ----------------------------------------------

    def _post(self, path: str, *, json: dict, token: str | None = None) -> dict:
        headers = {'Content-Type': 'application/json'}
        if token:
            # Эндпоинты /payment/* требуют именно Authorization: Bearer
            # (вопреки примерам с X-Access-Token в документации).
            headers['Authorization'] = f'Bearer {token}'
        url = f'{self.base_url}{path}'
        try:
            with httpx.Client(timeout=self.timeout) as client:
                resp = client.post(url, json=json, headers=headers)
        except httpx.HTTPError as exc:
            logger.error('Multicard POST %s сетевая ошибка: %s', path, exc)
            raise MulticardError(f'Сетевая ошибка Multicard: {exc}') from exc
        return self._parse(resp, path)

    def _get(self, path: str, *, token: str | None = None) -> dict:
        headers = {}
        if token:
            headers['Authorization'] = f'Bearer {token}'
        url = f'{self.base_url}{path}'
        try:
            with httpx.Client(timeout=self.timeout) as client:
                resp = client.get(url, headers=headers)
        except httpx.HTTPError as exc:
            logger.error('Multicard GET %s сетевая ошибка: %s', path, exc)
            raise MulticardError(f'Сетевая ошибка Multicard: {exc}') from exc
        return self._parse(resp, path)

    @staticmethod
    def _parse(resp: httpx.Response, path: str) -> dict:
        try:
            payload = resp.json()
        except ValueError:
            logger.error('Multicard %s: не-JSON ответ (%s): %s', path, resp.status_code, resp.text[:500])
            raise MulticardError(f'Некорректный ответ Multicard (HTTP {resp.status_code})')

        # Формат /auth отличается от остальных: токен лежит в корне, без success.
        if 'token' in payload and 'success' not in payload:
            return payload

        if payload.get('success'):
            return payload.get('data', {})

        error = payload.get('error') or {}
        code = error.get('code', '')
        details = error.get('details') or payload.get('errors') or resp.text[:300]
        logger.warning('Multicard %s ошибка: code=%s details=%s', path, code, details)
        raise MulticardError(f'Multicard вернул ошибку: {details}', code=code, details=str(details))

    # ---------- авторизация --------------------------------------------------

    def auth(self, *, force: bool = False) -> str:
        """Получить JWT-токен (с кешем). force=True игнорирует кеш."""
        if not force:
            cached = cache.get(_TOKEN_CACHE_KEY)
            if cached:
                return cached
        data = self._post('/auth', json={
            'application_id': self.application_id,
            'secret': self.secret,
        })
        token = data.get('token')
        if not token:
            raise MulticardError('Multicard /auth не вернул token')
        cache.set(_TOKEN_CACHE_KEY, token, _TOKEN_TTL_SECONDS)
        return token

    def _with_token(self, fn):
        """Выполнить запрос, при 401/expired — обновить токен и повторить один раз."""
        token = self.auth()
        try:
            return fn(token)
        except MulticardError as exc:
            details = (exc.details or str(exc)).lower()
            if 'token' in details or 'unauthor' in details or '401' in details:
                token = self.auth(force=True)
                return fn(token)
            raise

    # ---------- инвойсы ------------------------------------------------------

    def create_invoice(
        self,
        *,
        store_id: str,
        amount_tiyin: int,
        invoice_id: str,
        callback_url: str,
        ofd: list[dict],
        return_url: str = '',
        return_error_url: str = '',
        lang: str = 'ru',
        ttl: int | None = None,
    ) -> dict:
        """POST /payment/invoice. Возвращает data (uuid, checkout_url, ...).

        amount_tiyin — сумма в ТИЙИНАХ. ofd — массив фискальных строк.
        """
        body = {
            'store_id': store_id,
            'amount': int(amount_tiyin),
            'invoice_id': str(invoice_id),
            'callback_url': callback_url,
            'ofd': ofd,
            'lang': lang,
        }
        if return_url:
            body['return_url'] = return_url
        if return_error_url:
            body['return_error_url'] = return_error_url
        if ttl:
            body['ttl'] = int(ttl)
        return self._with_token(lambda t: self._post('/payment/invoice', json=body, token=t))

    def get_payment(self, uuid: str) -> dict:
        """GET /payment/{uuid}. Возвращает PaymentModel."""
        return self._with_token(lambda t: self._get(f'/payment/{uuid}', token=t))

    def delete_invoice(self, uuid: str) -> dict:
        """DELETE /payment/invoice/{uuid}. Аннулирует неоплаченный инвойс."""
        def _do(token):
            url = f'{self.base_url}/payment/invoice/{uuid}'
            try:
                with httpx.Client(timeout=self.timeout) as client:
                    resp = client.delete(url, headers={'Authorization': f'Bearer {token}'})
            except httpx.HTTPError as exc:
                raise MulticardError(f'Сетевая ошибка Multicard: {exc}') from exc
            return self._parse(resp, f'/payment/invoice/{uuid}')

        return self._with_token(_do)


def build_topup_ofd(amount_tiyin: int) -> list[dict]:
    """Сформировать ofd-строку для пополнения кошелька из настроек."""
    return [{
        'qty': 1,
        'price': int(amount_tiyin),
        'total': int(amount_tiyin),
        'name': settings.MULTICARD_OFD_NAME,
        'mxik': settings.MULTICARD_OFD_MXIK,
        'package_code': settings.MULTICARD_OFD_PACKAGE_CODE,
        'vat_percent': settings.MULTICARD_OFD_VAT_PERCENT,
    }]
